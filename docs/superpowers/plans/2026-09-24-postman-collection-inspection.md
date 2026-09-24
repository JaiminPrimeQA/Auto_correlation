# Postman Collection Inspection (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-only, fully tested `POST /api/v1/execution-jobs/inspect` endpoint that parses an uploaded Postman Collection (v2.0/v2.1) and optional Postman Environment, discovers folders and variables, classifies sensitive variables, detects unsupported Postman features, and validates every statically-resolvable request destination against a public-HTTPS-only network policy — all without executing a single HTTP request.

**Architecture:** Pure, synchronous domain services under `backend/app/services/` (parser → variable extractor → variable resolver → folder extractor → unsupported-feature detector → destination policy → inspector orchestrator) feeding one new FastAPI router. No job model, no Newman execution, no frontend work, and no Docker runner belong to this phase — those are Phase 2 (job state model + fake runner) and Phase 3 (Docker Newman runner) of the parent design. This phase is deliberately scoped so it ships working, independently testable software: upload a collection today, get back an accurate inspection report today.

**Tech Stack:** FastAPI (existing), Pydantic v2 (existing), Python stdlib `ipaddress`/`socket`/`re`/`urllib.parse` only — no new third-party dependencies.

**Spec:**
- [docs/specs/2026-09-24-postman-collection-execution-design.md](../../specs/2026-09-24-postman-collection-execution-design.md) — architecture (this plan implements design §5.1 items 1–3 and the `/inspect` half of §6; §10's unsupported-feature list)
- [docs/specs/2026-09-24-postman-collection-implementation-prompt.md](../../specs/2026-09-24-postman-collection-implementation-prompt.md) — process requirements (TDD, error taxonomy, secret handling)

## Global Constraints

- Preserve all existing behavior, APIs, tests, and the direct Newman-report upload workflow (`POST /api/v1/analyses`) exactly as-is; every existing test must still pass unmodified.
- Postman Collection v2.0/v2.1 JSON only.
- Limits (all via `Settings`, all configurable via `B11_`-prefixed env vars): collection ≤ 10 MiB, environment ≤ 2 MiB.
- Public HTTPS only in production; loopback, RFC1918 private IPv4, link-local, unique-local/private IPv6, multicast, unspecified, reserved, and cloud-metadata hostnames/IPs are always blocked regardless of environment.
- Never log collection/environment bodies, request/response bodies, Authorization values, tokens, API keys, passwords, client secrets, or any variable *value*. Variable *names* and structural counts are not secret and may be logged.
- The inspection response must never contain an imported variable's value — only its name, source, and sensitivity flag.
- Follow existing conventions exactly: RFC 9457 `ProblemException` errors (`app/core/errors.py`), `Settings` from `app/core/config.py`, ordered-pairs-not-dicts only where the existing domain model already does that (not applicable here), `from __future__ import annotations` in every new module, ruff line-length 120.
- TDD: write the failing test first, watch it fail for the stated reason, then implement.

---

### Task 1: Config limits and network-policy settings

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/test_config_postman_policy.py`

**Interfaces:**
- Produces: `Settings.max_collection_bytes: int`, `Settings.max_environment_bytes: int`, `Settings.allow_insecure_http_destinations: bool`, `Settings.blocked_hostnames: str`, `Settings.blocked_hostname_list: list[str]` (property), `Settings.https_only: bool` (property). Every later task in this plan reads these off a `Settings` instance.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_config_postman_policy.py
from app.core.config import Settings


def test_defaults_match_spec_limits():
    s = Settings()
    assert s.max_collection_bytes == 10 * 1024 * 1024
    assert s.max_environment_bytes == 2 * 1024 * 1024
    assert s.allow_insecure_http_destinations is False


def test_https_only_defaults_true_in_development():
    assert Settings().https_only is True


def test_https_only_forced_true_in_production_even_if_flag_set():
    s = Settings(environment="production", allow_insecure_http_destinations=True)
    assert s.https_only is True


def test_https_only_false_when_explicitly_allowed_in_development():
    s = Settings(allow_insecure_http_destinations=True)
    assert s.https_only is False


def test_blocked_hostname_list_parses_trims_and_lowercases():
    s = Settings(blocked_hostnames="Metadata.Google.Internal, 169.254.169.254 ,")
    assert s.blocked_hostname_list == ["metadata.google.internal", "169.254.169.254"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_config_postman_policy.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'max_collection_bytes'`

- [ ] **Step 3: Add the settings**

In `backend/app/core/config.py`, add below the existing `# --- Upload / parsing limits ---` block:

```python
    # --- Postman collection intake limits (Phase 1: inspection only) ---
    max_collection_bytes: int = 10 * MIB
    max_environment_bytes: int = 2 * MIB

    # --- Public-HTTPS destination policy ---
    # Never true in production regardless of this flag; see `https_only`.
    allow_insecure_http_destinations: bool = False
    # Comma-separated hostnames that are always blocked in addition to the
    # localhost/private/metadata-IP checks in `destination_policy`.
    blocked_hostnames: str = "169.254.169.254,metadata.google.internal,metadata.goog,metadata.azure.com"
```

Add below the existing `cors_origin_list` property:

```python
    @property
    def blocked_hostname_list(self) -> list[str]:
        return [h.strip().lower() for h in self.blocked_hostnames.split(",") if h.strip()]

    @property
    def https_only(self) -> bool:
        return self.is_production or not self.allow_insecure_http_destinations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_config_postman_policy.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/unit/test_config_postman_policy.py
git commit -m "feat(config): add Postman collection limits and destination policy settings"
```

---

### Task 2: Destination policy — IP and hostname classification

**Files:**
- Create: `backend/app/services/destination_policy.py`
- Test: `backend/tests/unit/test_destination_policy_classify.py`

**Interfaces:**
- Consumes: `Settings.blocked_hostname_list` (Task 1)
- Produces: `classify_ip(ip_str: str) -> str | None`, `is_ip_literal(host: str) -> bool`, `is_blocked_hostname(hostname: str, settings: Settings) -> bool`, `extract_hostname(url: str) -> str | None`. Task 3, Task 11 and the resolver in Task 3 all build on these.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_destination_policy_classify.py
import pytest

from app.core.config import Settings
from app.services.destination_policy import (
    classify_ip,
    extract_hostname,
    is_blocked_hostname,
    is_ip_literal,
)


@pytest.mark.parametrize("ip,expected", [
    ("127.0.0.1", "loopback"),
    ("10.1.2.3", "private"),
    ("172.16.0.5", "private"),
    ("192.168.1.1", "private"),
    ("169.254.169.254", "link_local"),
    ("224.0.0.1", "multicast"),
    ("0.0.0.0", "unspecified"),
    ("8.8.8.8", None),
    ("93.184.216.34", None),
    ("::1", "loopback"),
    ("fe80::1", "link_local"),
    ("fc00::1", "private"),
    ("2001:4860:4860::8888", None),
])
def test_classify_ip(ip, expected):
    assert classify_ip(ip) == expected


def test_is_ip_literal():
    assert is_ip_literal("127.0.0.1") is True
    assert is_ip_literal("::1") is True
    assert is_ip_literal("api.example.com") is False


def test_is_blocked_hostname_matches_localhost_variants_and_metadata():
    s = Settings()
    assert is_blocked_hostname("localhost", s) is True
    assert is_blocked_hostname("sub.localhost", s) is True
    assert is_blocked_hostname("LOCALHOST", s) is True
    assert is_blocked_hostname("169.254.169.254", s) is True
    assert is_blocked_hostname("metadata.google.internal", s) is True
    assert is_blocked_hostname("api.example.com", s) is False


def test_extract_hostname_handles_plain_and_ipv6():
    assert extract_hostname("https://api.example.com:443/x") == "api.example.com"
    assert extract_hostname("https://[::1]:8080/x") == "::1"
    assert extract_hostname("not a url") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_destination_policy_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.destination_policy'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/destination_policy.py
"""Public-HTTPS-only destination policy.

Blocks loopback, RFC1918/link-local/unique-local/multicast/unspecified/reserved
IP ranges and cloud-metadata hosts, in both literal-IP and DNS-resolved form.
This module makes no network calls itself except through the injectable
resolver in `validate_destination` (Task 3), which callers can replace with a
fake in tests to avoid depending on real DNS.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from ..core.config import Settings

_BLOCKED_IP_REASONS = ("is_loopback", "is_link_local", "is_multicast", "is_unspecified", "is_private", "is_reserved")
_REASON_NAMES = {
    "is_loopback": "loopback",
    "is_link_local": "link_local",
    "is_multicast": "multicast",
    "is_unspecified": "unspecified",
    "is_private": "private",
    "is_reserved": "reserved",
}


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def classify_ip(ip_str: str) -> str | None:
    """Return a blocked-category name for this IP literal, or None if it is a public address.

    Caller must ensure `ip_str` is a valid IP literal (check with `is_ip_literal` first).
    """
    ip = ipaddress.ip_address(ip_str)
    for attr in _BLOCKED_IP_REASONS:
        if getattr(ip, attr):
            return _REASON_NAMES[attr]
    return None


def is_blocked_hostname(hostname: str, settings: Settings) -> bool:
    h = hostname.strip().lower().rstrip(".")
    if h == "localhost" or h.endswith(".localhost"):
        return True
    return h in settings.blocked_hostname_list


def extract_hostname(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_destination_policy_classify.py -v`
Expected: PASS (all parametrized cases + 3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/destination_policy.py backend/tests/unit/test_destination_policy_classify.py
git commit -m "feat(destination-policy): classify blocked IP ranges and hostnames"
```

---

### Task 3: Destination policy — `validate_destination` with DNS-rebinding protection

**Files:**
- Modify: `backend/app/core/errors.py`
- Modify: `backend/app/services/destination_policy.py`
- Test: `backend/tests/unit/test_destination_policy_validate.py`

**Interfaces:**
- Consumes: `classify_ip`, `is_ip_literal`, `is_blocked_hostname`, `extract_hostname` (Task 2); `Settings.https_only` (Task 1)
- Produces: `Resolver = Callable[[str], list[str]]`, `default_resolver(hostname: str) -> list[str]`, `validate_destination(url: str, *, settings: Settings, resolver: Resolver | None = None) -> None` (raises `ProblemException`). Task 11 (`postman_domain_extractor`) calls `validate_destination` and passes through its own `resolver` parameter.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_destination_policy_validate.py
import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.services.destination_policy import validate_destination


def test_allows_public_https_literal_ip():
    validate_destination("https://93.184.216.34/health", settings=Settings())  # no exception


def test_rejects_plain_http_by_default():
    with pytest.raises(ProblemException) as exc:
        validate_destination("http://93.184.216.34/health", settings=Settings())
    assert exc.value.code == "blocked_destination"


def test_allows_http_when_explicitly_permitted_in_development():
    validate_destination("http://93.184.216.34/health", settings=Settings(allow_insecure_http_destinations=True))


def test_rejects_http_in_production_even_if_flag_set():
    with pytest.raises(ProblemException):
        validate_destination(
            "http://93.184.216.34/health",
            settings=Settings(environment="production", allow_insecure_http_destinations=True),
        )


def test_rejects_non_http_scheme():
    with pytest.raises(ProblemException) as exc:
        validate_destination("ws://93.184.216.34/socket", settings=Settings())
    assert exc.value.code == "blocked_destination"


def test_rejects_loopback_literal():
    with pytest.raises(ProblemException) as exc:
        validate_destination("https://127.0.0.1/admin", settings=Settings())
    assert "loopback" in exc.value.detail


def test_rejects_blocked_hostname_without_dns():
    with pytest.raises(ProblemException):
        validate_destination("https://localhost/admin", settings=Settings())


def test_rejects_hostname_resolving_to_private_range_dns_rebinding():
    def fake_resolver(hostname: str) -> list[str]:
        assert hostname == "evil.example.com"
        return ["10.0.0.5"]

    with pytest.raises(ProblemException) as exc:
        validate_destination("https://evil.example.com/", settings=Settings(), resolver=fake_resolver)
    assert "private" in exc.value.detail


def test_allows_hostname_resolving_to_public_ip_only():
    def fake_resolver(hostname: str) -> list[str]:
        return ["93.184.216.34"]

    validate_destination("https://api.example.com/", settings=Settings(), resolver=fake_resolver)


def test_rejects_if_any_resolved_ip_is_blocked():
    def fake_resolver(hostname: str) -> list[str]:
        return ["93.184.216.34", "127.0.0.1"]  # one public, one blocked -> reject

    with pytest.raises(ProblemException):
        validate_destination("https://multi.example.com/", settings=Settings(), resolver=fake_resolver)


def test_rejects_url_with_no_hostname():
    with pytest.raises(ProblemException) as exc:
        validate_destination("https:///no-host", settings=Settings())
    assert exc.value.code == "validation_error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_destination_policy_validate.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_destination'`

- [ ] **Step 3: Add `blocked_destination` error constructor**

In `backend/app/core/errors.py`, add next to `def bad_request(...)`:

```python
def blocked_destination(detail: str) -> ProblemException:
    return ProblemException(
        status=422, code="blocked_destination", title="Destination not permitted", detail=detail,
    )
```

- [ ] **Step 4: Implement `validate_destination`**

At the top of `backend/app/services/destination_policy.py`, add these imports alongside the existing ones (so `import ipaddress` / `import socket` stay grouped, and the `..core` imports stay grouped — ruff's isort rule will reorder them anyway on `ruff check --fix` if the grouping is off):

```python
import socket
from typing import Callable

from ..core.errors import ProblemException, blocked_destination, validation_error
```

so the full import block reads:

```python
from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlsplit

from ..core.config import Settings
from ..core.errors import ProblemException, blocked_destination, validation_error
```

Then append to the end of the file:

```python
Resolver = Callable[[str], list[str]]


def default_resolver(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise validation_error(f"Could not resolve host '{hostname}'.") from exc
    return sorted({info[4][0] for info in infos})


def validate_destination(url: str, *, settings: Settings, resolver: Resolver | None = None) -> None:
    resolve = resolver or default_resolver
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise blocked_destination(f"Unsupported protocol '{scheme or '(none)'}'; only HTTPS is permitted.")
    if scheme == "http" and settings.https_only:
        raise blocked_destination("Plain HTTP destinations are not permitted; use HTTPS.")

    host = parts.hostname
    if not host:
        raise validation_error("URL has no hostname.")
    if is_blocked_hostname(host, settings):
        raise blocked_destination(f"Destination host '{host}' is blocked.")

    if is_ip_literal(host):
        reason = classify_ip(host)
        if reason:
            raise blocked_destination(f"Destination IP is {reason.replace('_', ' ')}; not permitted.")
        return

    for ip_str in resolve(host):
        reason = classify_ip(ip_str) if is_ip_literal(ip_str) else None
        if reason:
            raise blocked_destination(
                f"Destination host '{host}' resolves to a {reason.replace('_', ' ')} address; not permitted."
            )
```

Move the `from urllib.parse import urlsplit` import already at the top of the file (added in Task 2) — no change needed there since it is already imported module-wide.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_destination_policy_validate.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/errors.py backend/app/services/destination_policy.py backend/tests/unit/test_destination_policy_validate.py
git commit -m "feat(destination-policy): validate_destination with DNS-rebinding protection"
```

---

### Task 4: Domain enums and models for Postman inspection

**Files:**
- Modify: `backend/app/domain/enums.py`
- Create: `backend/app/domain/postman_models.py`
- Test: `backend/tests/unit/test_postman_models.py`

**Interfaces:**
- Produces: `VariableSource(str, Enum)` = `SUPPLIED | ENVIRONMENT | COLLECTION | DYNAMIC | UNRESOLVED`; `UnsupportedFeatureKind(str, Enum)` = `LOCAL_DATA_FILE | INTERACTIVE_AUTH | CLIENT_CERTIFICATE | NON_HTTP_PROTOCOL | DYNAMIC_REQUEST_CONSTRUCTION`; `PostmanVariable(name: str, source: VariableSource, sensitive: bool, locations: list[str])`; `PostmanFolder(id: str, name: str, path: str, request_count: int)`; `UnsupportedFeature(kind: UnsupportedFeatureKind, detail: str, location: str)`; `CollectionInspection(collection_name, folders, variables, unresolved_variable_names, request_count_estimate, target_domains, domain_warnings, unsupported_features, warnings)`. Tasks 8–13 all import from here.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_postman_models.py
from app.domain.enums import UnsupportedFeatureKind, VariableSource
from app.domain.postman_models import (
    CollectionInspection,
    PostmanFolder,
    PostmanVariable,
    UnsupportedFeature,
)


def test_postman_variable_defaults():
    v = PostmanVariable(name="token", source=VariableSource.UNRESOLVED, sensitive=True)
    assert v.locations == []


def test_collection_inspection_defaults_are_empty_and_serializable():
    inspection = CollectionInspection(collection_name="Demo")
    assert inspection.folders == []
    assert inspection.variables == []
    assert inspection.unresolved_variable_names == []
    assert inspection.request_count_estimate == 0
    assert inspection.target_domains == []
    assert inspection.domain_warnings == []
    assert inspection.unsupported_features == []
    assert inspection.warnings == []


def test_folder_and_unsupported_feature_construct():
    PostmanFolder(id="auth", name="Auth", path="Auth", request_count=2)
    UnsupportedFeature(
        kind=UnsupportedFeatureKind.INTERACTIVE_AUTH,
        detail="OAuth2 implicit grant requires a browser redirect.",
        location="Auth > Login",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.postman_models'`

- [ ] **Step 3: Add the enums**

Append to `backend/app/domain/enums.py`:

```python
class VariableSource(str, Enum):
    """Where a discovered Postman variable's value would come from, in precedence order."""

    SUPPLIED = "supplied"
    ENVIRONMENT = "environment"
    COLLECTION = "collection"
    DYNAMIC = "dynamic"          # Postman built-in, e.g. {{$guid}} - resolved by Newman itself
    UNRESOLVED = "unresolved"


class UnsupportedFeatureKind(str, Enum):
    LOCAL_DATA_FILE = "local_data_file"
    INTERACTIVE_AUTH = "interactive_auth"
    CLIENT_CERTIFICATE = "client_certificate"
    NON_HTTP_PROTOCOL = "non_http_protocol"
    DYNAMIC_REQUEST_CONSTRUCTION = "dynamic_request_construction"
```

- [ ] **Step 4: Write the domain models**

```python
# backend/app/domain/postman_models.py
"""Domain models for the Postman-collection inspection stage (Phase 1).

These never carry a variable's *value* - only its name, source, and whether
it is classified sensitive - so the inspection response can never leak a
secret regardless of how it is presented.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import UnsupportedFeatureKind, VariableSource


class PostmanVariable(BaseModel):
    name: str
    source: VariableSource
    sensitive: bool
    locations: list[str] = Field(default_factory=list)


class PostmanFolder(BaseModel):
    id: str
    name: str
    path: str
    request_count: int


class UnsupportedFeature(BaseModel):
    kind: UnsupportedFeatureKind
    detail: str
    location: str


class CollectionInspection(BaseModel):
    collection_name: str
    folders: list[PostmanFolder] = Field(default_factory=list)
    variables: list[PostmanVariable] = Field(default_factory=list)
    unresolved_variable_names: list[str] = Field(default_factory=list)
    request_count_estimate: int = 0
    target_domains: list[str] = Field(default_factory=list)
    domain_warnings: list[str] = Field(default_factory=list)
    unsupported_features: list[UnsupportedFeature] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/enums.py backend/app/domain/postman_models.py backend/tests/unit/test_postman_models.py
git commit -m "feat(domain): add Postman inspection enums and models"
```

---

### Task 5: Extract shared JSON safety helpers (refactor, no behavior change)

**Files:**
- Create: `backend/app/utils/json_safety.py`
- Modify: `backend/app/services/newman_parser.py`
- Test: `backend/tests/unit/test_json_safety.py`

**Interfaces:**
- Produces: `decode_json(raw: bytes) -> object`, `check_json_complexity(node: object, settings: Settings) -> None`. Task 6 (`postman_parser`) reuses both instead of duplicating Newman's bounded-traversal logic.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_json_safety.py
import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.utils.json_safety import check_json_complexity, decode_json


def test_decode_json_tolerates_bom():
    raw = ("﻿" + '{"a": 1}').encode("utf-8")
    assert decode_json(raw) == {"a": 1}


def test_decode_json_rejects_invalid_json_with_path():
    with pytest.raises(ProblemException) as exc:
        decode_json(b"{not json")
    assert exc.value.errors[0]["path"] == "$"


def test_check_json_complexity_rejects_deep_nesting():
    settings = Settings(max_json_depth=2)
    with pytest.raises(ProblemException) as exc:
        check_json_complexity({"a": {"b": {"c": 1}}}, settings)
    assert "Depth exceeds limit" in exc.value.errors[0]["detail"]


def test_check_json_complexity_rejects_oversized_array():
    settings = Settings(max_array_length=2)
    with pytest.raises(ProblemException):
        check_json_complexity([1, 2, 3], settings)


def test_check_json_complexity_allows_within_bounds():
    check_json_complexity({"a": [1, 2, 3]}, Settings())  # no exception
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_json_safety.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.json_safety'`

- [ ] **Step 3: Extract the helpers verbatim from `newman_parser.py`**

```python
# backend/app/utils/json_safety.py
"""Shared bounded-traversal safety checks for untrusted JSON documents.

Used by every parser that accepts user-uploaded JSON (Newman reports, Postman
collections, Postman environments) so complexity limits are enforced
identically everywhere.
"""

from __future__ import annotations

import json

from ..core.config import Settings
from ..core.errors import validation_error


def decode_json(raw: bytes) -> object:
    text = raw.decode("utf-8-sig")  # tolerate UTF-8 BOM
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # precise location
        raise validation_error(
            "File is not valid JSON.",
            errors=[{"path": "$", "detail": f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"}],
        ) from exc


def check_json_complexity(node: object, settings: Settings) -> None:
    """Bounded traversal enforcing depth/size limits; raises on violation."""
    total = 0
    stack: list[tuple[object, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > settings.max_json_depth:
            raise validation_error(
                "JSON nesting is too deep.",
                errors=[{"path": "$", "detail": f"Depth exceeds limit ({settings.max_json_depth})."}],
            )
        if isinstance(current, dict):
            total += len(current)
            for v in current.values():
                stack.append((v, depth + 1))
        elif isinstance(current, list):
            if len(current) > settings.max_array_length:
                raise validation_error(
                    "JSON array is too large.",
                    errors=[{"path": "$", "detail": f"Array length exceeds limit ({settings.max_array_length})."}],
                )
            total += len(current)
            for v in current:
                stack.append((v, depth + 1))
        elif isinstance(current, str):
            if len(current) > settings.max_scalar_length:
                raise validation_error(
                    "A JSON string value is too large.",
                    errors=[{"path": "$", "detail": f"String length exceeds limit ({settings.max_scalar_length})."}],
                )
        if total > settings.max_total_values:
            raise validation_error(
                "JSON document has too many values.",
                errors=[{"path": "$", "detail": f"Total values exceed limit ({settings.max_total_values})."}],
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_json_safety.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Refactor `newman_parser.py` to use the shared helpers**

In `backend/app/services/newman_parser.py`: delete the private `_decode_json` and `_check_complexity` function definitions entirely, and add this import at the top of the file alongside the existing ones:

```python
from ..utils.json_safety import check_json_complexity, decode_json
```

Then update the two call sites inside `parse_report`:

```python
    data = decode_json(raw)
    ...
    check_json_complexity(data, settings)
```

(Remove the now-unused `import json` and `from dataclasses import dataclass` stays since `ParsedReport` still uses it.)

- [ ] **Step 6: Run the full existing Newman parser suite to confirm zero regressions**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_parser.py -v`
Expected: PASS — every test that passed before this refactor still passes, unchanged.

- [ ] **Step 7: Run the full backend suite as a broader regression check**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS — no test outside `test_parser.py` should even be affected, but confirm.

- [ ] **Step 8: Commit**

```bash
git add backend/app/utils/json_safety.py backend/app/services/newman_parser.py backend/tests/unit/test_json_safety.py
git commit -m "refactor: extract shared JSON safety checks from newman_parser"
```

---

### Task 6: Postman collection and environment parsers, plus test builders

**Files:**
- Create: `backend/app/services/postman_parser.py`
- Create: `backend/tests/fixtures/postman_builders.py`
- Test: `backend/tests/unit/test_postman_parser.py`

**Interfaces:**
- Consumes: `decode_json`, `check_json_complexity` (Task 5); `Settings.max_collection_bytes`, `Settings.max_environment_bytes` (Task 1)
- Produces: `ParsedCollection(data: dict, warnings: list[str])`, `ParsedEnvironment(data: dict, values: dict[str, str], warnings: list[str])`, `parse_collection(raw: bytes, *, filename: str, settings: Settings) -> ParsedCollection`, `parse_environment(raw: bytes, *, filename: str, settings: Settings) -> ParsedEnvironment`. Task 12 (`postman_inspector`) calls both. `tests/fixtures/postman_builders.py` (`pm_request`, `pm_folder`, `pm_collection`, `pm_environment`) is reused by every later test task and by the Task 14 integration tests.

- [ ] **Step 1: Write the test builders (not yet under test — they are fixtures)**

```python
# backend/tests/fixtures/postman_builders.py
"""Builders for Postman Collection v2.1 / Environment JSON fixtures used in tests."""

from __future__ import annotations


def pm_request(name: str, method: str, url, *, headers=None, body=None, auth=None, events=None) -> dict:
    req: dict = {"method": method, "header": headers or [], "url": url}
    if body is not None:
        req["body"] = body
    if auth is not None:
        req["auth"] = auth
    item: dict = {"name": name, "request": req}
    if events is not None:
        item["event"] = events
    return item


def pm_folder(name: str, items: list) -> dict:
    return {"name": name, "item": items}


def pm_collection(
    name: str,
    items: list,
    *,
    variables: list[dict] | None = None,
    schema: str = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
) -> dict:
    return {
        "info": {"name": name, "schema": schema},
        "item": items,
        "variable": variables or [],
    }


def pm_environment(name: str, values: dict[str, str], *, disabled: set[str] | None = None) -> dict:
    disabled = disabled or set()
    return {
        "name": name,
        "values": [{"key": k, "value": v, "enabled": k not in disabled} for k, v in values.items()],
    }
```

- [ ] **Step 2: Write the failing parser tests**

```python
# backend/tests/unit/test_postman_parser.py
import json

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.services.postman_parser import parse_collection, parse_environment
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_folder, pm_request


def _raw(obj) -> bytes:
    return json.dumps(obj).encode()


def test_valid_collection_parses_without_warnings():
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    parsed = parse_collection(_raw(collection), filename="c.json", settings=Settings())
    assert parsed.data["info"]["name"] == "Demo"
    assert parsed.warnings == []


def test_collection_missing_info_name_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_collection(_raw({"item": []}), filename="c.json", settings=Settings())
    assert exc.value.errors[0]["path"] == "$.info.name"


def test_collection_missing_item_array_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_collection(_raw({"info": {"name": "x"}}), filename="c.json", settings=Settings())
    assert exc.value.errors[0]["path"] == "$.item"


def test_collection_unknown_schema_warns_but_parses():
    collection = pm_collection("Demo", [], schema="")
    parsed = parse_collection(_raw(collection), filename="c.json", settings=Settings())
    assert any("schema version" in w for w in parsed.warnings)


def test_collection_empty_items_warns():
    collection = pm_collection("Demo", [])
    parsed = parse_collection(_raw(collection), filename="c.json", settings=Settings())
    assert any("no requests" in w for w in parsed.warnings)


def test_collection_oversize_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_collection(b"x" * 100, filename="c.json", settings=Settings(max_collection_bytes=10))
    assert exc.value.status == 413


def test_valid_environment_parses_enabled_values_only():
    env = pm_environment("dev", {"host": "api.example.com", "token": "abc"}, disabled={"token"})
    parsed = parse_environment(_raw(env), filename="e.json", settings=Settings())
    assert parsed.values == {"host": "api.example.com"}
    assert parsed.warnings == []


def test_environment_missing_values_array_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_environment(_raw({"name": "dev"}), filename="e.json", settings=Settings())
    assert exc.value.errors[0]["path"] == "$.values"


def test_environment_with_no_enabled_values_warns():
    env = pm_environment("dev", {"token": "abc"}, disabled={"token"})
    parsed = parse_environment(_raw(env), filename="e.json", settings=Settings())
    assert any("no enabled values" in w for w in parsed.warnings)


def test_environment_oversize_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_environment(b"x" * 100, filename="e.json", settings=Settings(max_environment_bytes=10))
    assert exc.value.status == 413
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_parser'`

- [ ] **Step 4: Implement the parsers**

```python
# backend/app/services/postman_parser.py
"""Parse and validate Postman Collection v2.0/2.1 and Postman Environment JSON.

Structural validation only - this module never executes requests, evaluates
scripts, or makes outbound network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import Settings
from ..core.errors import ProblemException, validation_error
from ..utils.json_safety import check_json_complexity, decode_json


@dataclass
class ParsedCollection:
    data: dict
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedEnvironment:
    data: dict
    values: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def parse_collection(raw: bytes, *, filename: str, settings: Settings) -> ParsedCollection:
    if len(raw) > settings.max_collection_bytes:
        raise ProblemException(
            status=413, code="payload_too_large", title="Payload too large",
            detail=f"'{filename}' exceeds the {settings.max_collection_bytes} byte collection limit.",
        )
    data = decode_json(raw)
    if not isinstance(data, dict):
        raise validation_error(
            "Root of a Postman collection must be a JSON object.",
            errors=[{"path": "$", "detail": "Expected an object with 'info' and 'item'."}],
        )
    check_json_complexity(data, settings)

    info = data.get("info")
    if not isinstance(info, dict) or not info.get("name"):
        raise validation_error(
            "Not a Postman collection: missing 'info.name'.",
            errors=[{"path": "$.info.name", "detail": "Expected a non-empty string."}],
        )
    items = data.get("item")
    if not isinstance(items, list):
        raise validation_error(
            "Not a Postman collection: 'item' must be an array.",
            errors=[{"path": "$.item", "detail": "Expected an array."}],
        )

    warnings: list[str] = []
    schema = info.get("schema", "")
    if not isinstance(schema, str) or "collection/v2" not in schema:
        warnings.append("Collection schema version could not be confirmed as v2.0 or v2.1; proceeding best-effort.")
    if not items:
        warnings.append("Collection has no requests.")
    return ParsedCollection(data=data, warnings=warnings)


def parse_environment(raw: bytes, *, filename: str, settings: Settings) -> ParsedEnvironment:
    if len(raw) > settings.max_environment_bytes:
        raise ProblemException(
            status=413, code="payload_too_large", title="Payload too large",
            detail=f"'{filename}' exceeds the {settings.max_environment_bytes} byte environment limit.",
        )
    data = decode_json(raw)
    if not isinstance(data, dict):
        raise validation_error(
            "Root of a Postman environment must be a JSON object.",
            errors=[{"path": "$", "detail": "Expected an object with a 'values' array."}],
        )
    check_json_complexity(data, settings)

    entries = data.get("values")
    if not isinstance(entries, list):
        raise validation_error(
            "Not a Postman environment: 'values' must be an array.",
            errors=[{"path": "$.values", "detail": "Expected an array."}],
        )

    values: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        if entry.get("enabled", True) is False:
            continue
        values[entry["key"]] = str(entry.get("value", ""))

    warnings: list[str] = []
    if not values:
        warnings.append("Environment has no enabled values.")
    return ParsedEnvironment(data=data, values=values, warnings=warnings)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_parser.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/postman_parser.py backend/tests/fixtures/postman_builders.py backend/tests/unit/test_postman_parser.py
git commit -m "feat(postman): parse and validate Postman collection and environment JSON"
```

---

### Task 7: Variable reference extraction

**Files:**
- Create: `backend/app/services/postman_variable_extractor.py`
- Test: `backend/tests/unit/test_postman_variable_extractor.py`

**Interfaces:**
- Produces: `VariableReference(name: str, location: str)`, `extract_variable_references(collection_data: dict) -> list[VariableReference]`. Task 8 consumes both.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_postman_variable_extractor.py
from app.services.postman_variable_extractor import extract_variable_references
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def test_extracts_variables_from_url_headers_body_and_auth():
    login = pm_request(
        "Login", "POST", "https://{{host}}/auth/login",
        headers=[{"key": "Content-Type", "value": "application/json"}],
        body={"mode": "raw", "raw": '{"user": "{{username}}", "pass": "{{password}}"}'},
    )
    profile = pm_request(
        "Get Profile", "GET", "https://{{host}}/me",
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}],
        auth={"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
    )
    collection = pm_collection("Login Flow", [pm_folder("Auth", [login, profile])])

    refs = extract_variable_references(collection)
    names = {r.name for r in refs}
    assert names == {"host", "username", "password", "token"}
    assert any(r.name == "host" and "Login" in r.location for r in refs)
    assert any(r.name == "token" and "auth" in r.location for r in refs)


def test_extracts_variables_from_query_and_path_object_url():
    request = pm_request("Get", "GET", {
        "raw": "https://api.example.com/orders/:id?limit={{pageSize}}",
        "host": ["api", "example", "com"],
        "path": ["orders", ":id"],
        "variable": [{"key": "id", "value": "{{orderId}}"}],
        "query": [{"key": "limit", "value": "{{pageSize}}"}],
    })
    collection = pm_collection("Demo", [request])
    names = {r.name for r in extract_variable_references(collection)}
    assert names == {"pageSize", "orderId"}


def test_ignores_file_form_fields_and_returns_urlencoded_values():
    request = pm_request("Upload", "POST", "https://api.example.com/upload", body={
        "mode": "formdata",
        "formdata": [
            {"key": "file", "type": "file", "src": "/tmp/x.png"},
            {"key": "note", "type": "text", "value": "{{noteText}}"},
        ],
    })
    collection = pm_collection("Demo", [request])
    names = {r.name for r in extract_variable_references(collection)}
    assert names == {"noteText"}


def test_no_variables_returns_empty_list():
    request = pm_request("Ping", "GET", "https://api.example.com/ping")
    collection = pm_collection("Demo", [request])
    assert extract_variable_references(collection) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_variable_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_variable_extractor'`

- [ ] **Step 3: Implement the extractor**

```python
# backend/app/services/postman_variable_extractor.py
"""Find every {{variable}} reference in a Postman collection's requests.

Pure string scanning - never evaluates scripts or resolves values. Postman's
built-in dynamic variables (e.g. {{$guid}}) are returned like any other
reference; `postman_variable_resolver` (Task 8) is what classifies them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VAR = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


@dataclass
class VariableReference:
    name: str
    location: str


def _names_in(text: object) -> list[str]:
    return _VAR.findall(text) if isinstance(text, str) else []


def _from_url(url: object, location: str) -> list[VariableReference]:
    refs: list[VariableReference] = []
    if isinstance(url, str):
        refs += [VariableReference(n, f"{location} > url") for n in _names_in(url)]
    elif isinstance(url, dict):
        refs += [VariableReference(n, f"{location} > url") for n in _names_in(url.get("raw"))]
        for part_key in ("host", "path"):
            for segment in url.get(part_key, []) or []:
                refs += [VariableReference(n, f"{location} > url.{part_key}") for n in _names_in(segment)]
        for q in url.get("query", []) or []:
            if isinstance(q, dict):
                refs += [VariableReference(n, f"{location} > url.query") for n in _names_in(q.get("value"))]
        for v in url.get("variable", []) or []:
            if isinstance(v, dict):
                refs += [VariableReference(n, f"{location} > url.variable") for n in _names_in(str(v.get("value", "")))]
    return refs


def _from_headers(headers: object, location: str) -> list[VariableReference]:
    refs: list[VariableReference] = []
    for h in headers or []:
        if isinstance(h, dict):
            refs += [VariableReference(n, f"{location} > header") for n in _names_in(h.get("value"))]
    return refs


def _from_body(body: object, location: str) -> list[VariableReference]:
    if not isinstance(body, dict):
        return []
    refs: list[VariableReference] = []
    mode = body.get("mode")
    if mode == "raw":
        refs += [VariableReference(n, f"{location} > body") for n in _names_in(body.get("raw"))]
    elif mode in ("urlencoded", "formdata"):
        for param in body.get(mode, []) or []:
            if isinstance(param, dict) and param.get("type") != "file":
                refs += [VariableReference(n, f"{location} > body") for n in _names_in(str(param.get("value", "")))]
    return refs


def _from_auth(auth: object, location: str) -> list[VariableReference]:
    if not isinstance(auth, dict):
        return []
    refs: list[VariableReference] = []
    for key, params in auth.items():
        if key == "type" or not isinstance(params, list):
            continue
        for p in params:
            if isinstance(p, dict):
                refs += [VariableReference(n, f"{location} > auth") for n in _names_in(str(p.get("value", "")))]
    return refs


def _walk(items: list, path: str) -> list[VariableReference]:
    refs: list[VariableReference] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        here = f"{path} > {it.get('name', 'unnamed')}" if path else it.get("name", "unnamed")
        if isinstance(it.get("item"), list):
            refs.extend(_walk(it["item"], here))
            continue
        request = it.get("request")
        if not isinstance(request, dict):
            continue
        refs.extend(_from_url(request.get("url"), here))
        refs.extend(_from_headers(request.get("header"), here))
        refs.extend(_from_body(request.get("body"), here))
        refs.extend(_from_auth(request.get("auth"), here))
    return refs


def extract_variable_references(collection_data: dict) -> list[VariableReference]:
    return _walk(collection_data.get("item", []), "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_variable_extractor.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_variable_extractor.py backend/tests/unit/test_postman_variable_extractor.py
git commit -m "feat(postman): extract {{variable}} references from a collection"
```

---

### Task 8: Variable resolution and sensitivity classification

**Files:**
- Create: `backend/app/services/postman_variable_resolver.py`
- Test: `backend/tests/unit/test_postman_variable_resolver.py`

**Interfaces:**
- Consumes: `VariableReference` (Task 7); `VariableSource`, `PostmanVariable` (Task 4); `is_sensitive_key` (existing, `app/utils/masking.py`)
- Produces: `resolve_variables(references, *, collection_variables, environment_values, supplied=None) -> list[PostmanVariable]`, `unresolved_names(variables: list[PostmanVariable]) -> list[str]`. Task 12 (`postman_inspector`) calls both.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_postman_variable_resolver.py
from app.domain.enums import VariableSource
from app.services.postman_variable_extractor import VariableReference
from app.services.postman_variable_resolver import resolve_variables, unresolved_names


def test_precedence_supplied_beats_environment_beats_collection():
    refs = [
        VariableReference("a", "loc"),
        VariableReference("b", "loc"),
        VariableReference("c", "loc"),
        VariableReference("d", "loc"),
    ]
    variables = resolve_variables(
        refs,
        collection_variables={"c": "from-collection", "d": "from-collection"},
        environment_values={"b": "from-env", "d": "from-env"},
        supplied={"a": "from-supplied", "d": "from-supplied"},
    )
    by_name = {v.name: v.source for v in variables}
    assert by_name["a"] == VariableSource.SUPPLIED
    assert by_name["b"] == VariableSource.ENVIRONMENT
    assert by_name["c"] == VariableSource.COLLECTION
    assert by_name["d"] == VariableSource.SUPPLIED  # supplied wins even though present everywhere


def test_unknown_variable_is_unresolved():
    refs = [VariableReference("mystery", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={})
    assert variables[0].source == VariableSource.UNRESOLVED
    assert unresolved_names(variables) == ["mystery"]


def test_dynamic_postman_variables_are_never_unresolved():
    refs = [VariableReference("$guid", "loc"), VariableReference("$timestamp", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={})
    assert all(v.source == VariableSource.DYNAMIC for v in variables)
    assert unresolved_names(variables) == []


def test_sensitive_names_are_flagged():
    refs = [VariableReference("password", "loc"), VariableReference("api_key", "loc"), VariableReference("username", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={"username": "alice"})
    by_name = {v.name: v.sensitive for v in variables}
    assert by_name["password"] is True
    assert by_name["api_key"] is True
    assert by_name["username"] is False


def test_locations_are_deduplicated_and_aggregated_across_references():
    refs = [VariableReference("token", "Login > header"), VariableReference("token", "Login > header"),
            VariableReference("token", "Profile > header")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={"token": "x"})
    assert variables[0].locations == ["Login > header", "Profile > header"]


def test_results_are_sorted_by_name():
    refs = [VariableReference("zeta", "loc"), VariableReference("alpha", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={})
    assert [v.name for v in variables] == ["alpha", "zeta"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_variable_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_variable_resolver'`

- [ ] **Step 3: Implement the resolver**

```python
# backend/app/services/postman_variable_resolver.py
"""Apply Postman variable precedence and classify sensitivity.

Precedence (highest to lowest): supplied runtime value, environment value,
collection-level variable. A name starting with '$' is a Postman built-in
dynamic variable (e.g. {{$guid}}) - Newman resolves these itself at
execution time, so they are never reported as unresolved.
"""

from __future__ import annotations

from ..domain.enums import VariableSource
from ..domain.postman_models import PostmanVariable
from ..utils.masking import is_sensitive_key
from .postman_variable_extractor import VariableReference


def resolve_variables(
    references: list[VariableReference],
    *,
    collection_variables: dict[str, str],
    environment_values: dict[str, str],
    supplied: dict[str, str] | None = None,
) -> list[PostmanVariable]:
    supplied = supplied or {}
    locations_by_name: dict[str, list[str]] = {}
    for ref in references:
        locations_by_name.setdefault(ref.name, []).append(ref.location)

    variables: list[PostmanVariable] = []
    for name, locations in locations_by_name.items():
        if name.startswith("$"):
            source = VariableSource.DYNAMIC
        elif name in supplied:
            source = VariableSource.SUPPLIED
        elif name in environment_values:
            source = VariableSource.ENVIRONMENT
        elif name in collection_variables:
            source = VariableSource.COLLECTION
        else:
            source = VariableSource.UNRESOLVED
        variables.append(PostmanVariable(
            name=name,
            source=source,
            sensitive=is_sensitive_key(name),
            locations=sorted(dict.fromkeys(locations)),
        ))
    return sorted(variables, key=lambda v: v.name)


def unresolved_names(variables: list[PostmanVariable]) -> list[str]:
    return sorted(v.name for v in variables if v.source == VariableSource.UNRESOLVED)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_variable_resolver.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_variable_resolver.py backend/tests/unit/test_postman_variable_resolver.py
git commit -m "feat(postman): resolve variable precedence and classify sensitivity"
```

---

### Task 9: Folder tree extraction

**Files:**
- Create: `backend/app/services/postman_folder_extractor.py`
- Test: `backend/tests/unit/test_postman_folder_extractor.py`

**Interfaces:**
- Consumes: `PostmanFolder` (Task 4)
- Produces: `count_requests(items: list) -> int`, `extract_folders(collection_data: dict) -> list[PostmanFolder]`. Task 12 uses `extract_folders` for the folder list and `count_requests` for the whole-collection request estimate.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_postman_folder_extractor.py
from app.services.postman_folder_extractor import count_requests, extract_folders
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def test_extracts_nested_folders_with_paths_and_counts():
    login = pm_request("Login", "POST", "https://api.example.com/login")
    refresh = pm_request("Refresh", "POST", "https://api.example.com/refresh")
    profile = pm_request("Get Profile", "GET", "https://api.example.com/me")
    collection = pm_collection("Demo", [
        pm_folder("Auth", [login, pm_folder("Tokens", [refresh])]),
        profile,
    ])

    folders = extract_folders(collection)
    by_path = {f.path: f for f in folders}
    assert set(by_path) == {"Auth", "Auth > Tokens"}
    assert by_path["Auth"].request_count == 2  # Login + nested Refresh
    assert by_path["Auth > Tokens"].request_count == 1
    assert by_path["Auth"].id == "auth"
    assert by_path["Auth > Tokens"].id == "auth-tokens"


def test_no_folders_returns_empty_list():
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    assert extract_folders(collection) == []


def test_count_requests_counts_all_nested_leaves():
    items = [
        pm_request("A", "GET", "https://x/a"),
        pm_folder("F", [pm_request("B", "GET", "https://x/b"), pm_folder("G", [pm_request("C", "GET", "https://x/c")])]),
    ]
    assert count_requests(items) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_folder_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_folder_extractor'`

- [ ] **Step 3: Implement the extractor**

```python
# backend/app/services/postman_folder_extractor.py
"""Extract the folder tree from a Postman collection, with per-folder request counts."""

from __future__ import annotations

import re

from ..domain.postman_models import PostmanFolder

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(path: str) -> str:
    return _SLUG_RE.sub("-", path.lower()).strip("-") or "folder"


def count_requests(items: list) -> int:
    total = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("item"), list):
            total += count_requests(it["item"])
        elif "request" in it:
            total += 1
    return total


def _walk(items: list, path: str) -> list[PostmanFolder]:
    folders: list[PostmanFolder] = []
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("item"), list):
            continue
        name = it.get("name", "unnamed")
        here = f"{path} > {name}" if path else name
        folders.append(PostmanFolder(id=_slugify(here), name=name, path=here, request_count=count_requests(it["item"])))
        folders.extend(_walk(it["item"], here))
    return folders


def extract_folders(collection_data: dict) -> list[PostmanFolder]:
    return _walk(collection_data.get("item", []), "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_folder_extractor.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_folder_extractor.py backend/tests/unit/test_postman_folder_extractor.py
git commit -m "feat(postman): extract folder tree with per-folder request counts"
```

---

### Task 10: Unsupported-feature detection

**Files:**
- Create: `backend/app/services/postman_unsupported.py`
- Test: `backend/tests/unit/test_postman_unsupported.py`

**Interfaces:**
- Consumes: `UnsupportedFeature`, `UnsupportedFeatureKind` (Task 4)
- Produces: `detect_unsupported_features(collection_data: dict, environment_data: dict | None = None) -> list[UnsupportedFeature]`. Task 12 calls this.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_postman_unsupported.py
from app.domain.enums import UnsupportedFeatureKind
from app.services.postman_unsupported import detect_unsupported_features
from tests.fixtures.postman_builders import pm_collection, pm_request


def test_flags_file_form_field():
    request = pm_request("Upload", "POST", "https://api.example.com/upload", body={
        "mode": "formdata",
        "formdata": [{"key": "file", "type": "file", "src": "/tmp/x.png"}],
    })
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.LOCAL_DATA_FILE


def test_flags_ntlm_auth_as_interactive():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={"type": "ntlm"})
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.INTERACTIVE_AUTH


def test_flags_oauth2_implicit_grant_as_interactive():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={
        "type": "oauth2",
        "oauth2": [{"key": "grantType", "value": "implicit"}],
    })
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.INTERACTIVE_AUTH


def test_does_not_flag_oauth2_client_credentials():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={
        "type": "oauth2",
        "oauth2": [{"key": "grantType", "value": "client_credentials"}],
    })
    assert detect_unsupported_features(pm_collection("Demo", [request])) == []


def test_flags_non_http_protocol():
    request = pm_request("Socket", "GET", "wss://api.example.com/socket")
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.NON_HTTP_PROTOCOL


def test_flags_pm_send_request_in_scripts():
    request = pm_request("Chained", "GET", "https://api.example.com/x", events=[
        {"listen": "prerequest", "script": {"exec": ["pm.sendRequest('https://x', function(e,r){});"]}},
    ])
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.DYNAMIC_REQUEST_CONSTRUCTION


def test_flags_client_certificates_on_collection_or_environment():
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    collection["clientCertificates"] = [{"name": "cert"}]
    findings = detect_unsupported_features(collection)
    assert findings[0].kind == UnsupportedFeatureKind.CLIENT_CERTIFICATE


def test_supported_collection_has_no_findings():
    request = pm_request("Ping", "GET", "https://api.example.com/ping")
    assert detect_unsupported_features(pm_collection("Demo", [request])) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_unsupported.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_unsupported'`

- [ ] **Step 3: Implement the detector**

```python
# backend/app/services/postman_unsupported.py
"""Detect Postman collection features this product cannot execute unattended.

Unsupported cases must be reported, never silently skipped (spec §10).
"""

from __future__ import annotations

from ..domain.enums import UnsupportedFeatureKind
from ..domain.postman_models import UnsupportedFeature

_INTERACTIVE_AUTH_TYPES = {"oauth1", "ntlm", "digest"}
_INTERACTIVE_OAUTH2_GRANTS = {"authorization_code_with_pkce", "implicit"}


def _auth_findings(auth: dict, location: str) -> list[UnsupportedFeature]:
    findings: list[UnsupportedFeature] = []
    auth_type = auth.get("type")
    if auth_type in _INTERACTIVE_AUTH_TYPES:
        findings.append(UnsupportedFeature(
            kind=UnsupportedFeatureKind.INTERACTIVE_AUTH,
            detail=f"Authentication type '{auth_type}' requires an interactive handshake that cannot run unattended.",
            location=location,
        ))
    elif auth_type == "oauth2":
        params = auth.get("oauth2", [])
        grant = next((p.get("value") for p in params if isinstance(p, dict) and p.get("key") == "grantType"), None)
        if grant in _INTERACTIVE_OAUTH2_GRANTS:
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.INTERACTIVE_AUTH,
                detail=f"OAuth2 grant type '{grant}' requires a browser redirect that cannot run unattended.",
                location=location,
            ))
    return findings


def _script_findings(item: dict, location: str) -> list[UnsupportedFeature]:
    findings: list[UnsupportedFeature] = []
    for event in item.get("event", []) or []:
        if not isinstance(event, dict):
            continue
        script = event.get("script", {})
        exec_lines = script.get("exec", []) if isinstance(script, dict) else []
        text = "\n".join(exec_lines) if isinstance(exec_lines, list) else str(exec_lines)
        if "pm.sendRequest" in text:
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.DYNAMIC_REQUEST_CONSTRUCTION,
                detail="A pre-request or test script issues its own HTTP calls (pm.sendRequest), "
                       "which cannot be fully discovered before execution.",
                location=location,
            ))
    return findings


def _url_scheme(request: dict) -> str:
    url = request.get("url")
    raw_url = url if isinstance(url, str) else (url.get("raw") if isinstance(url, dict) else None)
    if not isinstance(raw_url, str) or "://" not in raw_url:
        return ""
    return raw_url.split("://", 1)[0].split("{{")[0].lower()


def _walk(items: list, path: str) -> list[UnsupportedFeature]:
    findings: list[UnsupportedFeature] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name", "unnamed")
        here = f"{path} > {name}" if path else name
        if isinstance(it.get("item"), list):
            findings.extend(_walk(it["item"], here))
            continue
        findings.extend(_script_findings(it, here))
        request = it.get("request")
        if not isinstance(request, dict):
            continue
        auth = request.get("auth")
        if isinstance(auth, dict):
            findings.extend(_auth_findings(auth, here))
        body = request.get("body", {})
        if isinstance(body, dict) and body.get("mode") == "formdata":
            for param in body.get("formdata", []) or []:
                if isinstance(param, dict) and param.get("type") == "file":
                    findings.append(UnsupportedFeature(
                        kind=UnsupportedFeatureKind.LOCAL_DATA_FILE,
                        detail=f"Form field '{param.get('key', '?')}' uploads a local file, "
                               "which is not available to the execution worker.",
                        location=here,
                    ))
        scheme = _url_scheme(request)
        if scheme and scheme not in ("http", "https"):
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.NON_HTTP_PROTOCOL,
                detail=f"Request uses unsupported protocol '{scheme}'; only HTTP(S) is supported.",
                location=here,
            ))
    return findings


def detect_unsupported_features(collection_data: dict, environment_data: dict | None = None) -> list[UnsupportedFeature]:
    findings = _walk(collection_data.get("item", []), "")
    for source_name, data in (("collection", collection_data), ("environment", environment_data or {})):
        if isinstance(data, dict) and ("clientCertificates" in data or "certificate" in data):
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.CLIENT_CERTIFICATE,
                detail="Client-certificate authentication is referenced but not supported by the execution worker.",
                location=source_name,
            ))
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_unsupported.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_unsupported.py backend/tests/unit/test_postman_unsupported.py
git commit -m "feat(postman): detect unsupported collection features"
```

---

### Task 11: Target-domain extraction against the destination policy

**Files:**
- Create: `backend/app/services/postman_domain_extractor.py`
- Test: `backend/tests/unit/test_postman_domain_extractor.py`

**Interfaces:**
- Consumes: `destination_policy.validate_destination`, `destination_policy.extract_hostname`, `destination_policy.Resolver` (Task 3)
- Produces: `DomainReport(resolved_domains: list[str], warnings: list[str])`, `extract_target_domains(collection_data, *, environment_values, settings, resolver=None) -> DomainReport`. Task 12 calls this.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_postman_domain_extractor.py
from app.core.config import Settings
from app.services.postman_domain_extractor import extract_target_domains
from tests.fixtures.postman_builders import pm_collection, pm_request


def test_resolves_domain_from_environment_substituted_ip_literal():
    request = pm_request("Ping", "GET", "https://{{host}}/ping")
    report = extract_target_domains(
        pm_collection("Demo", [request]), environment_values={"host": "93.184.216.34"}, settings=Settings(),
    )
    assert report.resolved_domains == ["93.184.216.34"]
    assert report.warnings == []


def test_static_host_needs_no_environment():
    request = pm_request("Ping", "GET", "https://93.184.216.34/ping")
    report = extract_target_domains(pm_collection("Demo", [request]), environment_values={}, settings=Settings())
    assert report.resolved_domains == ["93.184.216.34"]


def test_unresolved_host_variable_produces_a_warning_not_an_exception():
    request = pm_request("Ping", "GET", "https://{{host}}/ping")
    report = extract_target_domains(pm_collection("Demo", [request]), environment_values={}, settings=Settings())
    assert report.resolved_domains == []
    assert "host" in report.warnings[0]
    assert "Ping" in report.warnings[0]


def test_blocked_destination_produces_a_warning_not_an_exception():
    request = pm_request("Ping", "GET", "https://localhost/ping")
    report = extract_target_domains(pm_collection("Demo", [request]), environment_values={}, settings=Settings())
    assert report.resolved_domains == []
    assert any("blocked" in w.lower() for w in report.warnings)


def test_dns_rebinding_hostname_is_reported_via_injected_resolver():
    request = pm_request("Ping", "GET", "https://evil.example.com/ping")

    def fake_resolver(hostname: str) -> list[str]:
        return ["127.0.0.1"]

    report = extract_target_domains(
        pm_collection("Demo", [request]), environment_values={}, settings=Settings(), resolver=fake_resolver,
    )
    assert report.resolved_domains == []
    assert any("loopback" in w for w in report.warnings)


def test_deduplicates_and_sorts_resolved_domains():
    requests = [pm_request("A", "GET", "https://93.184.216.34/a"), pm_request("B", "GET", "https://93.184.216.34/b")]
    report = extract_target_domains(pm_collection("Demo", requests), environment_values={}, settings=Settings())
    assert report.resolved_domains == ["93.184.216.34"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_domain_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_domain_extractor'`

- [ ] **Step 3: Implement the extractor**

```python
# backend/app/services/postman_domain_extractor.py
"""Resolve each request's target host (as far as statically possible) and
validate it against the public-HTTPS destination policy.

Unlike `validate_destination`, this module never raises: a blocked or
unresolvable target becomes a warning in the inspection report so the tester
can see it during review, before any request is ever sent (spec §7, §10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.config import Settings
from ..core.errors import ProblemException
from . import destination_policy

_VAR = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


@dataclass
class DomainReport:
    resolved_domains: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _substitute(raw: str, environment_values: dict[str, str]) -> str:
    return _VAR.sub(lambda m: environment_values.get(m.group(1), m.group(0)), raw)


def _iter_request_urls(items: list, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        here = f"{path} > {it.get('name', 'unnamed')}" if path else it.get("name", "unnamed")
        if isinstance(it.get("item"), list):
            out.extend(_iter_request_urls(it["item"], here))
            continue
        request = it.get("request")
        if not isinstance(request, dict):
            continue
        url = request.get("url")
        if isinstance(url, str):
            out.append((here, url))
        elif isinstance(url, dict) and isinstance(url.get("raw"), str):
            out.append((here, url["raw"]))
    return out


def extract_target_domains(
    collection_data: dict,
    *,
    environment_values: dict[str, str],
    settings: Settings,
    resolver: destination_policy.Resolver | None = None,
) -> DomainReport:
    resolved: set[str] = set()
    warnings: list[str] = []
    for location, raw_url in _iter_request_urls(collection_data.get("item", [])):
        substituted = _substitute(raw_url, environment_values)
        host = destination_policy.extract_hostname(substituted)
        if not host or "{{" in host:
            m = _VAR.search(substituted)
            var_name = m.group(1) if m else "?"
            warnings.append(
                f"Target host for '{location}' depends on variable '{var_name}'; "
                "supply a value to validate its destination before execution."
            )
            continue
        try:
            destination_policy.validate_destination(substituted, settings=settings, resolver=resolver)
        except ProblemException as exc:
            warnings.append(f"'{location}': {exc.detail}")
            continue
        resolved.add(host)
    return DomainReport(resolved_domains=sorted(resolved), warnings=warnings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_domain_extractor.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_domain_extractor.py backend/tests/unit/test_postman_domain_extractor.py
git commit -m "feat(postman): extract and validate target domains during inspection"
```

---

### Task 12: Inspector orchestration

**Files:**
- Create: `backend/app/services/postman_inspector.py`
- Test: `backend/tests/unit/test_postman_inspector.py`

**Interfaces:**
- Consumes: `parse_collection`, `parse_environment` (Task 6); `extract_variable_references` (Task 7); `resolve_variables`, `unresolved_names` (Task 8); `extract_folders`, `count_requests` (Task 9); `detect_unsupported_features` (Task 10); `extract_target_domains` (Task 11); `CollectionInspection` (Task 4)
- Produces: `inspect_collection(*, collection_raw, collection_filename, environment_raw, environment_filename, settings) -> CollectionInspection`. Task 13's API endpoint calls this — it is the only entry point the router needs.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_postman_inspector.py
import json

from app.core.config import Settings
from app.services.postman_inspector import inspect_collection
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_folder, pm_request


def _collection_with_variables():
    login = pm_request(
        "Login", "POST", "https://{{host}}/auth/login",
        headers=[{"key": "Content-Type", "value": "application/json"}],
        body={"mode": "raw", "raw": json.dumps({"user": "{{username}}", "pass": "{{password}}"})},
    )
    profile = pm_request("Get Profile", "GET", "https://{{host}}/me",
                          headers=[{"key": "Authorization", "value": "Bearer {{token}}"}])
    return pm_collection("Login Flow", [pm_folder("Auth", [login, profile])])


def test_inspect_without_environment_reports_all_variables_unresolved():
    raw = json.dumps(_collection_with_variables()).encode()
    inspection = inspect_collection(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None, settings=Settings(),
    )
    assert inspection.collection_name == "Login Flow"
    assert [f.name for f in inspection.folders] == ["Auth"]
    assert inspection.request_count_estimate == 2
    assert set(inspection.unresolved_variable_names) == {"host", "username", "password", "token"}
    password = next(v for v in inspection.variables if v.name == "password")
    assert password.sensitive is True
    assert inspection.target_domains == []


def test_inspect_with_environment_resolves_variables_and_domain():
    collection_raw = json.dumps(_collection_with_variables()).encode()
    env = pm_environment("dev", {
        "host": "93.184.216.34", "username": "alice", "password": "hunter2", "token": "abc",
    })
    environment_raw = json.dumps(env).encode()
    inspection = inspect_collection(
        collection_raw=collection_raw, collection_filename="c.json",
        environment_raw=environment_raw, environment_filename="e.json", settings=Settings(),
    )
    assert inspection.unresolved_variable_names == []
    assert inspection.target_domains == ["93.184.216.34"]


def test_inspect_surfaces_unsupported_features():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={"type": "ntlm"})
    raw = json.dumps(pm_collection("Demo", [request])).encode()
    inspection = inspect_collection(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None, settings=Settings(),
    )
    assert len(inspection.unsupported_features) == 1


def test_inspect_never_includes_a_variable_value():
    collection_raw = json.dumps(_collection_with_variables()).encode()
    env = pm_environment("dev", {"host": "93.184.216.34", "username": "alice", "password": "hunter2", "token": "abc"})
    inspection = inspect_collection(
        collection_raw=collection_raw, collection_filename="c.json",
        environment_raw=json.dumps(env).encode(), environment_filename="e.json", settings=Settings(),
    )
    dumped = inspection.model_dump_json()
    assert "hunter2" not in dumped
    assert "alice" not in dumped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_inspector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_inspector'`

- [ ] **Step 3: Implement the orchestrator**

```python
# backend/app/services/postman_inspector.py
"""Orchestrate the Postman-collection inspection stage.

Parses the collection (and optional environment), discovers folders and
variables, detects unsupported features, and validates every statically
resolvable target against the public-HTTPS destination policy - all without
sending a single HTTP request.
"""

from __future__ import annotations

from ..core.config import Settings
from ..domain.postman_models import CollectionInspection
from .postman_domain_extractor import extract_target_domains
from .postman_folder_extractor import count_requests, extract_folders
from .postman_parser import parse_collection, parse_environment
from .postman_unsupported import detect_unsupported_features
from .postman_variable_extractor import extract_variable_references
from .postman_variable_resolver import resolve_variables, unresolved_names


def inspect_collection(
    *,
    collection_raw: bytes,
    collection_filename: str,
    environment_raw: bytes | None,
    environment_filename: str | None,
    settings: Settings,
) -> CollectionInspection:
    parsed_collection = parse_collection(collection_raw, filename=collection_filename, settings=settings)
    collection_data = parsed_collection.data
    warnings = list(parsed_collection.warnings)

    environment_values: dict[str, str] = {}
    environment_data: dict | None = None
    if environment_raw is not None:
        parsed_env = parse_environment(
            environment_raw, filename=environment_filename or "environment.json", settings=settings,
        )
        environment_values = parsed_env.values
        environment_data = parsed_env.data
        warnings.extend(parsed_env.warnings)

    collection_variables = {
        v.get("key"): str(v.get("value", ""))
        for v in collection_data.get("variable", [])
        if isinstance(v, dict) and v.get("key")
    }

    folders = extract_folders(collection_data)
    references = extract_variable_references(collection_data)
    variables = resolve_variables(
        references, collection_variables=collection_variables, environment_values=environment_values,
    )
    domain_report = extract_target_domains(collection_data, environment_values=environment_values, settings=settings)

    return CollectionInspection(
        collection_name=str(collection_data.get("info", {}).get("name") or "Untitled collection"),
        folders=folders,
        variables=variables,
        unresolved_variable_names=unresolved_names(variables),
        request_count_estimate=count_requests(collection_data.get("item", [])),
        target_domains=domain_report.resolved_domains,
        domain_warnings=domain_report.warnings,
        unsupported_features=detect_unsupported_features(collection_data, environment_data),
        warnings=warnings,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_inspector.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_inspector.py backend/tests/unit/test_postman_inspector.py
git commit -m "feat(postman): orchestrate collection inspection"
```

---

### Task 13: `POST /api/v1/execution-jobs/inspect` endpoint

**Files:**
- Create: `backend/app/api/v1/execution_jobs.py`
- Modify: `backend/app/schemas/presenters.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `inspect_collection` (Task 12); `CollectionInspection`, `PostmanVariable`, `PostmanFolder`, `UnsupportedFeature` (Task 4); `enforce_rate_limit` (existing, `app/api/deps.py`)
- Produces: `postman_inspection_dto(inspection: CollectionInspection) -> dict` in `presenters.py`; router `execution_jobs.router` mounted at `{api_prefix}/execution-jobs`. Task 14's integration tests call this endpoint over HTTP.

- [ ] **Step 1: Add the presenter**

At the top of `backend/app/schemas/presenters.py`, add this import alongside the existing `from ..domain...` import:

```python
from ..domain.postman_models import CollectionInspection, PostmanFolder, PostmanVariable, UnsupportedFeature
```

Then append these functions to the end of the file:

```python
def postman_variable_dto(v: PostmanVariable) -> dict:
    return {"name": v.name, "source": v.source.value, "sensitive": v.sensitive, "locations": v.locations}


def postman_folder_dto(f: PostmanFolder) -> dict:
    return {"id": f.id, "name": f.name, "path": f.path, "request_count": f.request_count}


def postman_unsupported_feature_dto(u: UnsupportedFeature) -> dict:
    return {"kind": u.kind.value, "detail": u.detail, "location": u.location}


def postman_inspection_dto(inspection: CollectionInspection) -> dict:
    return {
        "collection_name": inspection.collection_name,
        "folders": [postman_folder_dto(f) for f in inspection.folders],
        "variables": [postman_variable_dto(v) for v in inspection.variables],
        "unresolved_variable_names": inspection.unresolved_variable_names,
        "request_count_estimate": inspection.request_count_estimate,
        "target_domains": inspection.target_domains,
        "domain_warnings": inspection.domain_warnings,
        "unsupported_features": [postman_unsupported_feature_dto(u) for u in inspection.unsupported_features],
        "warnings": inspection.warnings,
    }
```

- [ ] **Step 2: Add the router**

```python
# backend/app/api/v1/execution_jobs.py
"""Execution-job endpoints for the Postman-collection input mode.

Only the pre-execution inspection endpoint exists so far. Job creation,
status, and cancellation (the asynchronous execution job model) are Phase 2
of the parent design and are not implemented here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from ...schemas import presenters
from ...services.postman_inspector import inspect_collection
from ..deps import enforce_rate_limit

router = APIRouter(prefix="/execution-jobs", tags=["execution-jobs"])
log = get_logger("execution_jobs")


@router.post("/inspect")
async def inspect(
    collection: UploadFile = File(...),
    environment: UploadFile | None = File(None),
    settings: Settings = Depends(get_settings),
    _: None = Depends(enforce_rate_limit),
) -> dict:
    collection_raw = await collection.read()
    environment_raw = await environment.read() if environment is not None else None
    environment_filename = environment.filename if environment is not None else None

    inspection = inspect_collection(
        collection_raw=collection_raw,
        collection_filename=collection.filename or "collection.json",
        environment_raw=environment_raw,
        environment_filename=environment_filename,
        settings=settings,
    )
    log.info(
        "collection inspected",
        extra={
            "stage": "inspect",
            "folder_count": len(inspection.folders),
            "variable_count": len(inspection.variables),
            "unresolved_count": len(inspection.unresolved_variable_names),
            "unsupported_count": len(inspection.unsupported_features),
        },
    )
    return presenters.postman_inspection_dto(inspection)
```

- [ ] **Step 3: Wire the router into the app**

In `backend/app/main.py`, change:

```python
from .api.v1 import analyses, downloads, rules
```

to:

```python
from .api.v1 import analyses, downloads, execution_jobs, rules
```

and add, next to the other `include_router` calls:

```python
    app.include_router(execution_jobs.router, prefix=settings.api_prefix)
```

- [ ] **Step 4: Manually smoke-test the endpoint**

Run: `cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 &` then, in another shell:

```bash
curl -s -F 'collection=@/dev/stdin;filename=c.json;type=application/json' \
  http://127.0.0.1:8000/api/v1/execution-jobs/inspect \
  <<< '{"info":{"name":"Smoke","schema":"https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},"item":[{"name":"Ping","request":{"method":"GET","url":"https://93.184.216.34/ping"}}]}'
```

Expected: `200` with a JSON body whose `"collection_name"` is `"Smoke"` and `"target_domains"` is `["93.184.216.34"]`. Stop the background uvicorn process afterward.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/execution_jobs.py backend/app/schemas/presenters.py backend/app/main.py
git commit -m "feat(api): add POST /api/v1/execution-jobs/inspect endpoint"
```

---

### Task 14: Integration tests and full-suite regression check

**Files:**
- Create: `backend/tests/integration/test_execution_jobs_inspect.py`

**Interfaces:**
- Consumes: everything from Tasks 1–13, exercised only through the public HTTP surface (`TestClient`), following the existing pattern in `tests/integration/test_flow.py`.

- [ ] **Step 1: Write the integration tests**

```python
# backend/tests/integration/test_execution_jobs_inspect.py
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_folder, pm_request


@pytest.fixture
def client():
    return TestClient(create_app())


def _collection_with_variables():
    login = pm_request(
        "Login", "POST", "https://{{host}}/auth/login",
        headers=[{"key": "Content-Type", "value": "application/json"}],
        body={"mode": "raw", "raw": json.dumps({"user": "{{username}}", "pass": "{{password}}"})},
    )
    profile = pm_request("Get Profile", "GET", "https://{{host}}/me",
                          headers=[{"key": "Authorization", "value": "Bearer {{token}}"}])
    return pm_collection("Login Flow", [pm_folder("Auth", [login, profile])])


def _files(collection, environment=None):
    files = [("collection", ("collection.json", json.dumps(collection).encode(), "application/json"))]
    if environment is not None:
        files.append(("environment", ("environment.json", json.dumps(environment).encode(), "application/json")))
    return files


def test_inspect_reports_folders_and_unresolved_variables(client):
    resp = client.post("/api/v1/execution-jobs/inspect", files=_files(_collection_with_variables()))
    assert resp.status_code == 200
    body = resp.json()
    assert body["collection_name"] == "Login Flow"
    assert body["request_count_estimate"] == 2
    assert [f["name"] for f in body["folders"]] == ["Auth"]
    assert {v["name"] for v in body["variables"]} == {"host", "username", "password", "token"}
    assert set(body["unresolved_variable_names"]) == {"host", "username", "password", "token"}
    password_var = next(v for v in body["variables"] if v["name"] == "password")
    assert password_var["sensitive"] is True


def test_inspect_resolves_variables_from_environment_and_never_echoes_values(client):
    env = pm_environment("dev", {"host": "93.184.216.34", "username": "alice", "password": "hunter2", "token": "abc"})
    resp = client.post("/api/v1/execution-jobs/inspect", files=_files(_collection_with_variables(), env))
    assert resp.status_code == 200
    body = resp.json()
    assert body["unresolved_variable_names"] == []
    assert body["target_domains"] == ["93.184.216.34"]
    assert "hunter2" not in resp.text
    assert "alice" not in resp.text


def test_inspect_reports_blocked_localhost_target_as_a_warning_not_a_failure(client):
    collection = pm_collection("Local", [pm_request("Ping", "GET", "https://localhost/health")])
    resp = client.post("/api/v1/execution-jobs/inspect", files=_files(collection))
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_domains"] == []
    assert any("blocked" in w.lower() for w in body["domain_warnings"])


def test_inspect_rejects_malformed_collection_with_actionable_error(client):
    resp = client.post(
        "/api/v1/execution-jobs/inspect",
        files=[("collection", ("c.json", b'{"not": "a collection"}', "application/json"))],
    )
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["path"] == "$.info.name"


def test_existing_direct_newman_report_upload_still_works(client):
    from tests.fixtures import builders as b

    scenario = b.report("Smoke", [b.execution("Ping", "GET", "https://api.example.com/ping", position=0)])
    raw = json.dumps(scenario).encode()
    resp = client.post("/api/v1/analyses", files=[("files", ("baseline.json", raw, "application/json"))])
    assert resp.status_code == 201
```

- [ ] **Step 2: Run the new integration tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_execution_jobs_inspect.py -v`
Expected: PASS (5 tests)

- [ ] **Step 3: Run the complete backend test suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS — every test added in Tasks 1–14 plus every pre-existing test (Newman parsing, normalization, alignment, correlation, JMX build/validate, rules, downloads) passes with zero failures.

- [ ] **Step 4: Run lint and type checks**

Run: `cd backend && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy app`
Expected: both exit 0. Fix any findings before proceeding (do not silence them with inline ignores unless an existing file already establishes that pattern).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/integration/test_execution_jobs_inspect.py
git commit -m "test: integration coverage for POST /api/v1/execution-jobs/inspect + regression check"
```

---

## Definition of Done for Phase 1

- A tester (via `curl` or the API docs at `/api/docs`) can upload a Postman collection and optional environment and receive back: collection name, folder tree with request counts, every discovered variable with its resolution source and sensitivity flag, unresolved variable names, an estimated request count, statically-resolvable target domains, domain-policy warnings for blocked/unresolvable targets, unsupported-feature findings, and structural warnings — with no variable value ever appearing in the response or in logs.
- `POST /api/v1/analyses` (the existing direct Newman-report upload) is untouched and its full test suite still passes.
- The full backend suite, ruff, and mypy are all green.
- Nothing in this phase executes an HTTP request, spawns Newman, or touches Docker — that begins in Phase 2 (job state model + fake runner) and Phase 3 (Docker Newman runner), which get their own plans once this one is reviewed and merged.
