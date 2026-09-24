# Docker Newman Runner (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Phase 2 fake runner with a real, isolated Docker-based Newman 6.2.2 runner that executes a collection twice from identical starting input, captures each run's JSON report, and feeds both into the existing analysis pipeline — so a tester's uploaded collection produces a real correlation analysis.

**Architecture:** A `DockerNewmanRunner` implements the existing `NewmanRunner` Protocol (`app/domain/execution_job.py`). For each run it creates a private per-run workspace (collection + an environment file that carries the supplied values, so no secret ever reaches process arguments), builds a hardened `docker run` argument array (no shell, read-only root, all capabilities dropped, resource limits, DNS disabled except for `--add-host` pins of the exact addresses the destination policy validated), supervises the process with a wall-clock deadline and the job's cancellation probe, and turns the generated report into a `RunOutcome` through a small report adapter. Execution moves off FastAPI `BackgroundTasks` onto a bounded job dispatcher. Variables that collection scripts set at runtime (`pm.environment.set(...)`) are recognised so that ordinary producer/consumer collections are no longer rejected as "unresolved".

**Tech Stack:** Python stdlib (`subprocess`, `tempfile`, `concurrent.futures`), Docker CLI, a pinned `node:22-alpine` + `newman@6.2.2` image built from a repo Dockerfile, FastAPI, pytest.

**Spec:** [docs/specs/2026-09-24-postman-collection-execution-design.md](../../specs/2026-09-24-postman-collection-execution-design.md) — this plan implements §13 item 3 ("Isolated Docker Newman runner and generated-report adapter"): §5.1 item 5 (Newman runner interface — local isolated-container implementation), §5.1 item 3's pre-execution DNS revalidation carried to connect time, §3 steps 9–13, §8 (isolation and security) for the local Docker worker, and §9 limits. It builds on Phase 2's job model (`docs/superpowers/plans/2026-09-24-execution-job-model.md`).

## Global Constraints

- Newman runtime pinned to **6.2.2** (spec §5.1.5, §9). The image is built from `docker/newman/Dockerfile` and tagged `baseline11/newman:6.2.2`.
- Uses argument arrays with shell execution disabled — `subprocess.Popen(argv_list, shell=False)`; never a command string (spec §5.1.5).
- Never place secret values in process arguments, logs, filenames, or status messages (spec §8). Supplied values reach Newman only through the per-run environment file.
- Use a non-root container with a read-only root filesystem and a temporary writable working directory; disable privileged mode, host mounts (other than the per-run workspace), Docker socket access, and unnecessary Linux capabilities; apply CPU, memory, process, file-size, and wall-clock limits (spec §8).
- Use per-job temporary files with restrictive permissions; delete job files when the run completes, is cancelled, or fails (spec §8).
- Generated report: **25 MiB per run** (`Settings.max_report_bytes`); runtime: **5 minutes per run** (`Settings.job_run_timeout_seconds`) (spec §9).
- Run B starts from the original uploaded environment and supplied values; mutations are preserved only within one run (spec §3 steps 10–11). Phase 2 already builds a fresh deep-copied `RunInput` per run — each run also gets its own fresh workspace.
- Newman reports stay private: report bytes live only in memory for the `run_job` call and are deleted from disk with the workspace (spec §8).
- `RunOutcome.error_detail` must be a sanitized, user-safe message — never a secret, a variable value, or raw Newman/process stdout/stderr (Phase 2 contract on `RunOutcome`).
- A real runner MUST enforce `timeout_seconds` and honour `should_cancel` (Phase 2 final review: a runner that never returns pins the owner's concurrency slot forever).
- Newman exit code 1 means "the run completed but assertions failed" — that is a successful execution with a report; business success is judged by the existing run-health analysis, never by the exit code (spec §4: "treat HTTP 2xx alone as proof of business success" is forbidden — equally, an assertion failure is not a runner failure).
- Unit tests never start Docker or do real DNS. Real-Docker tests carry the `docker` pytest marker and run only when `B11_RUN_DOCKER_TESTS=1` and the Docker daemon plus the pinned image are available.
- Follow existing conventions: `Settings` in `app/core/config.py`, RFC 9457 `ProblemException` errors, `from __future__ import annotations`, ruff line-length 120, mypy clean.
- TDD: write the failing test first, watch it fail for the stated reason, then implement.
- Run commands from `backend/` with the venv interpreter: `./.venv/Scripts/python.exe -m pytest ...` (Windows; there is no `python` on PATH).

## Known limitations accepted for the local Docker worker (documented in README, Task 9)

- Redirects are not followed (`--ignore-redirects`), so a redirect can never reach an unvalidated address; a collection that depends on following redirects sees the 3xx response.
- A pre-request/test script that builds a URL from a raw IP literal is not blocked by the local container's network (only DNS names are pinned). Network-level egress controls (blocking private and metadata ranges) belong to the Phase 5 AWS worker's security groups.
- A request host that comes only from a script-set variable cannot be validated before execution, so the job fails destination validation (fail-closed) with a warning naming the variable.

---

### Task 1: Recognise variables that collection scripts set at runtime

**Files:**
- Modify: `backend/app/domain/enums.py` (`VariableSource`)
- Create: `backend/app/services/postman_script_variables.py`
- Modify: `backend/app/services/postman_variable_resolver.py` (`resolve_variables`)
- Modify: `backend/app/services/postman_inspector.py`
- Modify: `backend/app/services/execution_job_service.py` (`create_job`)
- Test: `backend/tests/unit/test_postman_script_variables.py` (new), `backend/tests/unit/test_postman_variable_resolver.py`, `backend/tests/unit/test_execution_job_service_create.py`

**Why:** A correlation collection's whole point is that a login request's test script stores a token (`pm.environment.set("token", ...)`) and later requests use `{{token}}`. Today that `{{token}}` is reported `unresolved` and `create_job` rejects the job with 422 — so the product rejects exactly the collections it exists for. Spec §3 step 11: "Preserve mutations within each individual run so producer scripts can populate downstream variables."

**Interfaces:**
- Produces: `VariableSource.SCRIPT = "script"`; `script_set_variable_names(collection_data: dict) -> set[str]`; `resolve_variables(references, *, collection_variables, environment_values, supplied=None, script_set=None)` — a name with no supplied/environment/collection value that appears in `script_set` gets source `SCRIPT` (not `UNRESOLVED`), so `unresolved_names` excludes it.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_postman_script_variables.py`:

```python
from app.services.postman_script_variables import script_set_variable_names
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def _with_test_script(request: dict, *lines: str) -> dict:
    request["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": list(lines)}}]
    return request


def test_finds_names_set_by_every_supported_setter():
    login = _with_test_script(
        pm_request("Login", "POST", "https://api.example.com/login"),
        'pm.environment.set("token", pm.response.json().token);',
        "pm.collectionVariables.set('session_id', 'x');",
        "pm.globals.set(`tenant`, 'y');",
        'pm.variables.set("local_value", 1);',
        'postman.setEnvironmentVariable("legacy_env", "z");',
        'postman.setGlobalVariable("legacy_global", "z");',
    )
    names = script_set_variable_names(pm_collection("C", [login]))
    assert names == {"token", "session_id", "tenant", "local_value", "legacy_env", "legacy_global"}


def test_ignores_getters_and_unset():
    req = _with_test_script(
        pm_request("R", "GET", "https://api.example.com/"),
        'pm.environment.get("read_only");',
        'pm.environment.unset("gone");',
    )
    assert script_set_variable_names(pm_collection("C", [req])) == set()


def test_walks_nested_folders_and_collection_level_events():
    inner = _with_test_script(pm_request("Inner", "GET", "https://api.example.com/"), 'pm.environment.set("deep", 1);')
    collection = pm_collection("C", [pm_folder("Outer", [pm_folder("Mid", [inner])])])
    collection["event"] = [{"listen": "prerequest", "script": {"exec": ['pm.variables.set("root_level", 1);']}}]
    assert script_set_variable_names(collection) == {"deep", "root_level"}


def test_accepts_exec_as_a_single_string():
    req = pm_request("R", "GET", "https://api.example.com/")
    req["event"] = [{"listen": "test", "script": {"exec": 'pm.environment.set("single", 1);'}}]
    assert script_set_variable_names(pm_collection("C", [req])) == {"single"}
```

Add to `backend/tests/unit/test_postman_variable_resolver.py`:

```python
def test_script_set_names_resolve_as_script_not_unresolved():
    from app.domain.enums import VariableSource
    from app.services.postman_variable_extractor import VariableReference
    from app.services.postman_variable_resolver import resolve_variables, unresolved_names

    refs = [VariableReference(name="token", location="Profile.header.Authorization"),
            VariableReference(name="missing", location="Profile.url")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={}, script_set={"token"})
    by_name = {v.name: v for v in variables}
    assert by_name["token"].source == VariableSource.SCRIPT
    assert unresolved_names(variables) == ["missing"]


def test_static_values_take_precedence_over_script_set():
    from app.domain.enums import VariableSource
    from app.services.postman_variable_extractor import VariableReference
    from app.services.postman_variable_resolver import resolve_variables

    refs = [VariableReference(name="token", location="x")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={"token": "seed"},
                                  script_set={"token"})
    assert variables[0].source == VariableSource.ENVIRONMENT
```

(If `VariableReference` has different constructor fields, read `app/services/postman_variable_extractor.py` and use its real fields — keep the assertions.)

Add to `backend/tests/unit/test_execution_job_service_create.py`:

```python
def test_variable_set_by_a_producer_script_does_not_block_creation():
    login = pm_request("Login", "POST", "https://93.184.216.34/login")
    login["event"] = [{"listen": "test", "script": {"exec": ['pm.environment.set("token", pm.response.json().t);']}}]
    profile = pm_request("Profile", "GET", "https://93.184.216.34/me",
                         headers=[{"key": "Authorization", "value": "Bearer {{token}}"}])
    raw = json.dumps(pm_collection("Flow", [login, profile])).encode()
    job, created = create_job(
        collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={}, owner_key="127.0.0.1", idempotency_key=None,
        store=_store(), settings=Settings(),
    )
    assert created is True
    assert job.state == ExecutionJobState.QUEUED
```

(Check `pm_request`'s `headers` parameter shape in `tests/fixtures/postman_builders.py` and match it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_script_variables.py tests/unit/test_postman_variable_resolver.py tests/unit/test_execution_job_service_create.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.postman_script_variables'`, `TypeError ... unexpected keyword argument 'script_set'`, and the create test fails with a 422 `ProblemException` naming `token`.

- [ ] **Step 3: Implement**

In `backend/app/domain/enums.py`, add to `VariableSource` after `DYNAMIC`:

```python
    SCRIPT = "script"            # set at runtime by a pre-request/test script (pm.environment.set, ...)
```

Create `backend/app/services/postman_script_variables.py`:

```python
"""Find variable names that collection scripts assign at runtime.

A producer request's test script typically stores a value for later
requests (`pm.environment.set("token", ...)`). Those names have no static
value before execution but are not missing either: Newman fills them during
the run (spec §3 step 11). This is a static, best-effort scan of script
source; it never executes anything.
"""

from __future__ import annotations

import re

_SETTER = re.compile(
    r"""(?:pm\.(?:environment|collectionVariables|globals|variables)\.set"""
    r"""|postman\.set(?:Environment|Global)Variable)"""
    r"""\s*\(\s*(['"`])([^'"`\n]+)\1"""
)


def _script_text(event: object) -> str:
    if not isinstance(event, dict):
        return ""
    script = event.get("script")
    if not isinstance(script, dict):
        return ""
    exec_lines = script.get("exec")
    if isinstance(exec_lines, list):
        return "\n".join(line for line in exec_lines if isinstance(line, str))
    return exec_lines if isinstance(exec_lines, str) else ""


def _names_in_events(node: dict) -> set[str]:
    names: set[str] = set()
    for event in node.get("event") or []:
        names.update(m.group(2).strip() for m in _SETTER.finditer(_script_text(event)))
    return names


def _walk(items: object) -> set[str]:
    names: set[str] = set()
    if not isinstance(items, list):
        return names
    for item in items:
        if not isinstance(item, dict):
            continue
        names |= _names_in_events(item)
        names |= _walk(item.get("item"))
    return names


def script_set_variable_names(collection_data: dict) -> set[str]:
    return _names_in_events(collection_data) | _walk(collection_data.get("item"))
```

In `backend/app/services/postman_variable_resolver.py`, change `resolve_variables`:

```python
def resolve_variables(
    references: list[VariableReference],
    *,
    collection_variables: dict[str, str],
    environment_values: dict[str, str],
    supplied: dict[str, str] | None = None,
    script_set: set[str] | None = None,
) -> list[PostmanVariable]:
    supplied = supplied or {}
    script_set = script_set or set()
```

and in the source chain insert, after the `collection_variables` branch and before `else`:

```python
        elif name in script_set:
            source = VariableSource.SCRIPT
```

Also add one line to the module docstring: "A name with no static value that a collection script sets at runtime is reported as `script`, not `unresolved`."

In `backend/app/services/postman_inspector.py`: import `from .postman_script_variables import script_set_variable_names` and pass `script_set=script_set_variable_names(collection_data)` to `resolve_variables`.

In `backend/app/services/execution_job_service.py` `create_job`: import the same function and pass `script_set=script_set_variable_names(collection_data)` to its `resolve_variables` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_script_variables.py tests/unit/test_postman_variable_resolver.py tests/unit/test_execution_job_service_create.py tests/unit/test_postman_inspector.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/enums.py backend/app/services/postman_script_variables.py backend/app/services/postman_variable_resolver.py backend/app/services/postman_inspector.py backend/app/services/execution_job_service.py backend/tests/unit/test_postman_script_variables.py backend/tests/unit/test_postman_variable_resolver.py backend/tests/unit/test_execution_job_service_create.py
git commit -m "feat(postman): treat script-set variables as runtime-provided, not unresolved"
```

---

### Task 2: Carry the validated DNS answers forward as host pins

**Files:**
- Modify: `backend/app/services/postman_domain_extractor.py` (`DomainReport`, `_cached_resolver`, `extract_target_domains`)
- Modify: `backend/app/domain/execution_job.py` (`RunInput`)
- Modify: `backend/app/services/execution_job_service.py` (`_drive_job` → `_fresh_run_input`)
- Test: `backend/tests/unit/test_postman_domain_extractor.py`, `backend/tests/unit/test_execution_job_service_run.py`

**Why:** Validation resolves each hostname and checks every address. If the container then resolved the name again, DNS could answer differently (rebinding). Pinning the exact validated addresses into the container (`--add-host`) and disabling its DNS (Task 6) makes the validated answer the only answer (spec §5.1.3: "Revalidates DNS results to reduce DNS rebinding risk").

**Interfaces:**
- Produces: `DomainReport.pinned_addresses: dict[str, list[str]]` — for every validated *hostname* (IP-literal hosts excluded), the sorted IP-literal addresses the resolver returned; `RunInput.host_pins: dict[str, list[str]] = field(default_factory=dict)` (last field, defaulted); `_drive_job` fills it from `domain_report.pinned_addresses` (deep-copied per run).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_postman_domain_extractor.py` (reuse that file's existing imports/helpers; a fake resolver is a plain function):

```python
def test_pinned_addresses_record_validated_hostnames_only():
    from app.core.config import Settings
    from app.services.postman_domain_extractor import extract_target_domains
    from tests.fixtures.postman_builders import pm_collection, pm_request

    answers = {"api.example.com": ["93.184.216.34", "93.184.216.35"], "internal.example.com": ["10.0.0.5"]}
    collection = pm_collection("C", [
        pm_request("A", "GET", "https://api.example.com/a"),
        pm_request("B", "GET", "https://api.example.com/b"),
        pm_request("C", "GET", "https://93.184.216.36/c"),
        pm_request("D", "GET", "https://internal.example.com/d"),
    ])
    report = extract_target_domains(collection, environment_values={}, settings=Settings(),
                                    resolver=lambda host: answers[host])
    assert report.pinned_addresses == {"api.example.com": ["93.184.216.34", "93.184.216.35"]}
```

Add to `backend/tests/unit/test_execution_job_service_run.py` (it already has `_queued_job`, `_newman_report`, and `_run(job, runner, store, *, collection_data=..., resolver=...)`):

```python
def test_runner_receives_the_validated_host_pins():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    _run(job, runner, store, collection_data=collection, resolver=lambda host: ["93.184.216.34"])
    assert store.get(job.id).state == ExecutionJobState.READY
    assert runner.calls[0].host_pins == {"api.example.com": ["93.184.216.34"]}
    assert runner.calls[1].host_pins == {"api.example.com": ["93.184.216.34"]}
    assert runner.calls[0].host_pins is not runner.calls[1].host_pins
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_domain_extractor.py tests/unit/test_execution_job_service_run.py -v`
Expected: FAIL — `AttributeError: 'DomainReport' object has no attribute 'pinned_addresses'` and `AttributeError: 'RunInput' object has no attribute 'host_pins'`.

- [ ] **Step 3: Implement**

In `postman_domain_extractor.py`:

```python
@dataclass
class DomainReport:
    resolved_domains: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Validated hostname -> the exact addresses the policy checked. The Docker
    # runner pins these into the container so it can never re-resolve a name
    # to a different (unvalidated) address.
    pinned_addresses: dict[str, list[str]] = field(default_factory=dict)
```

Make `_cached_resolver` take the outcome cache from its caller: change its signature to `_cached_resolver(resolver, *, max_hosts, outcomes: dict[str, list[str] | ProblemException])` and delete its local `outcomes = {}` line. In `extract_target_domains`, create `outcomes: dict[str, list[str] | ProblemException] = {}` before building `resolve` and pass `outcomes=outcomes`. Replace the final `return` with:

```python
    pinned = {
        host: sorted(a for a in answer if destination_policy.is_ip_literal(a))
        for host in resolved
        if isinstance(answer := outcomes.get(host), list)
    }
    return DomainReport(
        resolved_domains=sorted(resolved), warnings=warnings,
        pinned_addresses={host: addrs for host, addrs in pinned.items() if addrs},
    )
```

In `app/domain/execution_job.py`, add as the LAST field of `RunInput`:

```python
    # Validated hostname -> addresses (from destination validation). The Docker
    # runner pins exactly these into the container's /etc/hosts.
    host_pins: dict[str, list[str]] = field(default_factory=dict)
```

and extend the `RunInput` docstring with one sentence about `host_pins`.

In `execution_job_service.py` `_drive_job`, add to the `RunInput(...)` built in `_fresh_run_input`:

```python
            host_pins=copy.deepcopy(domain_report.pinned_addresses),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_domain_extractor.py tests/unit/test_execution_job_service_run.py tests/unit/test_postman_inspector.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_domain_extractor.py backend/app/domain/execution_job.py backend/app/services/execution_job_service.py backend/tests/unit/test_postman_domain_extractor.py backend/tests/unit/test_execution_job_service_run.py
git commit -m "feat(execution-job): pin validated DNS answers into each run's input"
```

---

### Task 3: Pinned Newman image and runner settings

**Files:**
- Create: `docker/newman/Dockerfile` (repo root, next to `backend/`)
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/test_config_newman_runner.py` (new)

**Interfaces:**
- Produces: `Settings.newman_runner: Literal["disabled", "fake", "docker"] = "disabled"`; `newman_docker_image = "baseline11/newman:6.2.2"`; `newman_version = "6.2.2"`; `newman_container_memory = "512m"`; `newman_container_cpus = "1.0"`; `newman_container_pids_limit = 256`; `newman_request_timeout_ms = 30_000`; `newman_start_grace_seconds = 30`; `docker_binary = "docker"`; `max_concurrent_executions = 4`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_config_newman_runner.py
from app.core.config import Settings


def test_newman_runner_defaults():
    s = Settings()
    assert s.newman_runner == "disabled"
    assert s.newman_docker_image == "baseline11/newman:6.2.2"
    assert s.newman_version == "6.2.2"
    assert s.newman_container_memory == "512m"
    assert s.newman_container_cpus == "1.0"
    assert s.newman_container_pids_limit == 256
    assert s.newman_request_timeout_ms == 30_000
    assert s.newman_start_grace_seconds == 30
    assert s.docker_binary == "docker"
    assert s.max_concurrent_executions == 4


def test_docker_is_an_accepted_runner_mode():
    assert Settings(newman_runner="docker").newman_runner == "docker"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_config_newman_runner.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'newman_docker_image'` (and a validation error for `"docker"`).

- [ ] **Step 3: Implement**

In `backend/app/core/config.py`, change the existing `newman_runner` line to `newman_runner: Literal["disabled", "fake", "docker"] = "disabled"` and add directly below it:

```python
    # --- Docker Newman runner (Phase 3) ---
    # Built from docker/newman/Dockerfile: node:22-alpine + newman@6.2.2, non-root.
    newman_docker_image: str = "baseline11/newman:6.2.2"
    newman_version: str = "6.2.2"
    newman_container_memory: str = "512m"
    newman_container_cpus: str = "1.0"
    newman_container_pids_limit: int = 256
    newman_request_timeout_ms: int = 30_000
    # Added to job_run_timeout_seconds for container start-up before the run is killed.
    newman_start_grace_seconds: int = 30
    docker_binary: str = "docker"
    # Global cap on simultaneously executing jobs (all owners), see job_dispatcher.
    max_concurrent_executions: int = 4
```

Create `docker/newman/Dockerfile`:

```dockerfile
# Pinned, non-root Newman runtime for Baseline11 execution jobs
# (spec §5.1 item 5 "pinned Newman 6.2.2 runtime", §8 "non-root container").
# Build: docker build -t baseline11/newman:6.2.2 docker/newman
FROM node:22-alpine
RUN npm install --global --no-audit --no-fund newman@6.2.2 \
    && npm cache clean --force
USER node
WORKDIR /home/node
ENTRYPOINT ["newman"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_config_newman_runner.py tests/unit/test_config_execution_job.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docker/newman/Dockerfile backend/app/core/config.py backend/tests/unit/test_config_newman_runner.py
git commit -m "feat(newman): add pinned Newman 6.2.2 image and Docker runner settings"
```

---

### Task 4: Private per-run workspace

**Files:**
- Create: `backend/app/services/newman_workspace.py`
- Test: `backend/tests/unit/test_newman_workspace.py`

**Interfaces:**
- Consumes: `RunInput` (Phase 2, + `host_pins` from Task 2).
- Produces: `CONTAINER_WORKDIR = "/job"`; `NewmanWorkspace(root, collection_path, environment_path, report_path, files_dir)` (frozen dataclass of `Path`s); `build_environment(environment_data: dict | None, supplied_values: dict[str, str]) -> dict`; `newman_workspace(run_input: RunInput, *, base_dir: Path | None = None)` — a context manager yielding a `NewmanWorkspace` and always deleting the directory on exit.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_newman_workspace.py
import json
import stat
import sys

import pytest

from app.domain.execution_job import RunInput
from app.services.newman_workspace import build_environment, newman_workspace


def _run_input(**overrides) -> RunInput:
    base = dict(collection_data={"info": {"name": "C"}, "item": []}, environment_data=None,
                supplied_values={}, folder_id=None, timeout_seconds=300)
    base.update(overrides)
    return RunInput(**base)


def test_supplied_values_override_and_enable_environment_entries():
    env = {"name": "dev", "values": [
        {"key": "host", "value": "old.example.com", "enabled": True},
        {"key": "token", "value": "", "enabled": False},
        {"key": "keep", "value": "k", "enabled": True},
    ]}
    merged = build_environment(env, {"token": "s3cret", "extra": "e"})
    by_key = {v["key"]: v for v in merged["values"]}
    assert merged["name"] == "dev"
    assert by_key["token"] == {"key": "token", "value": "s3cret", "enabled": True}
    assert by_key["keep"]["value"] == "k"
    assert by_key["host"]["value"] == "old.example.com"
    assert by_key["extra"] == {"key": "extra", "value": "e", "enabled": True, "type": "default"}


def test_build_environment_without_uploaded_environment():
    merged = build_environment(None, {"host": "h"})
    assert merged == {"name": "Baseline11 run", "values": [
        {"key": "host", "value": "h", "enabled": True, "type": "default"},
    ]}


def test_build_environment_does_not_mutate_its_input():
    env = {"name": "dev", "values": [{"key": "token", "value": "", "enabled": False}]}
    build_environment(env, {"token": "x"})
    assert env["values"][0] == {"key": "token", "value": "", "enabled": False}


def test_workspace_writes_inputs_and_is_removed_afterwards(tmp_path):
    run_input = _run_input(supplied_values={"token": "s3cret"})
    with newman_workspace(run_input, base_dir=tmp_path) as ws:
        assert json.loads(ws.collection_path.read_text("utf-8")) == run_input.collection_data
        env = json.loads(ws.environment_path.read_text("utf-8"))
        assert env["values"][0]["value"] == "s3cret"
        assert ws.report_path.parent.is_dir()
        assert ws.files_dir.is_dir()
        assert "s3cret" not in str(ws.root)  # never in a filename
        root = ws.root
    assert not root.exists()


def test_workspace_is_removed_even_when_the_body_raises(tmp_path):
    with pytest.raises(RuntimeError), newman_workspace(_run_input(), base_dir=tmp_path) as ws:
        root = ws.root
        raise RuntimeError("boom")
    assert not root.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_workspace_permissions_are_private(tmp_path):
    with newman_workspace(_run_input(), base_dir=tmp_path) as ws:
        assert stat.S_IMODE(ws.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(ws.environment_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(ws.collection_path.stat().st_mode) == 0o600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_workspace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.newman_workspace'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/newman_workspace.py
"""A private, per-run working directory for one Newman execution.

Supplied variable values reach Newman only through `environment.json` in
this directory - never through process arguments (spec §8). The directory is
created 0700, its input files 0600, and it is always deleted when the run
ends, however it ends.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..domain.execution_job import RunInput

CONTAINER_WORKDIR = "/job"
_DEFAULT_ENV_NAME = "Baseline11 run"


@dataclass(frozen=True)
class NewmanWorkspace:
    root: Path
    collection_path: Path
    environment_path: Path
    report_path: Path
    files_dir: Path


def build_environment(environment_data: dict | None, supplied_values: dict[str, str]) -> dict:
    """The uploaded environment with every supplied value applied on top
    (supplied wins, and a disabled entry is re-enabled when supplied)."""
    source = environment_data or {}
    values = [copy.deepcopy(v) for v in (source.get("values") or []) if isinstance(v, dict)]
    by_key = {v.get("key"): v for v in values}
    for key, value in supplied_values.items():
        existing = by_key.get(key)
        if existing is not None:
            existing["value"] = value
            existing["enabled"] = True
        else:
            values.append({"key": key, "value": value, "enabled": True, "type": "default"})
    return {"name": source.get("name") or _DEFAULT_ENV_NAME, "values": values}


def _write_private(path: Path, document: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(document, fh)


@contextmanager
def newman_workspace(run_input: RunInput, *, base_dir: Path | None = None) -> Iterator[NewmanWorkspace]:
    root = Path(tempfile.mkdtemp(prefix="b11-newman-", dir=base_dir))
    try:
        os.chmod(root, 0o700)
        out_dir = root / "out"
        files_dir = root / "files"
        out_dir.mkdir(mode=0o700)
        files_dir.mkdir(mode=0o700)
        workspace = NewmanWorkspace(
            root=root,
            collection_path=root / "collection.json",
            environment_path=root / "environment.json",
            report_path=out_dir / "report.json",
            files_dir=files_dir,
        )
        _write_private(workspace.collection_path, run_input.collection_data)
        _write_private(
            workspace.environment_path,
            build_environment(run_input.environment_data, run_input.supplied_values),
        )
        yield workspace
    finally:
        shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_workspace.py -v`
Expected: PASS (the POSIX-permission test is skipped on Windows).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/newman_workspace.py backend/tests/unit/test_newman_workspace.py
git commit -m "feat(newman): add private per-run workspace with supplied values in the environment file"
```

---

### Task 5: Folder selection by Newman's name-based `--folder`

**Files:**
- Modify: `backend/app/services/postman_folder_extractor.py`
- Modify: `backend/app/services/execution_job_service.py` (`create_job`)
- Test: `backend/tests/unit/test_postman_folder_extractor.py`, `backend/tests/unit/test_execution_job_service_create.py`

**Why:** Newman's `--folder` selects folders by *name*, while the product identifies folders by a unique slug id. If two folders share a name, Newman would run the wrong one (or both) — an inaccurate run. Reject that at creation with an actionable 422 (spec §4: accurate result or explicit failure).

**Interfaces:**
- Produces: `class AmbiguousFolderError(ValueError)`; `folder_run_name(collection_data: dict, folder_id: str) -> str` — returns the folder's `name`; raises `KeyError` for an unknown id and `AmbiguousFolderError` if any other folder in the collection has the same name. `create_job` raises `validation_error` (422) for an ambiguous folder.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_postman_folder_extractor.py`:

```python
import pytest

from app.services.postman_folder_extractor import AmbiguousFolderError, extract_folders, folder_run_name
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def test_folder_run_name_returns_the_folder_name():
    collection = pm_collection("C", [pm_folder("Auth", [pm_request("Login", "POST", "https://a.example.com/")])])
    folder_id = extract_folders(collection)[0].id
    assert folder_run_name(collection, folder_id) == "Auth"


def test_folder_run_name_rejects_duplicate_names_anywhere_in_the_tree():
    collection = pm_collection("C", [
        pm_folder("Auth", [pm_request("A", "GET", "https://a.example.com/")]),
        pm_folder("Other", [pm_folder("Auth", [pm_request("B", "GET", "https://a.example.com/")])]),
    ])
    folder_id = extract_folders(collection)[0].id
    with pytest.raises(AmbiguousFolderError):
        folder_run_name(collection, folder_id)


def test_folder_run_name_unknown_id():
    with pytest.raises(KeyError):
        folder_run_name(pm_collection("C", []), "nope")
```

Add to `backend/tests/unit/test_execution_job_service_create.py`:

```python
def test_ambiguously_named_folder_is_rejected_at_creation():
    collection = pm_collection("C", [
        pm_folder("Auth", [pm_request("A", "GET", "https://93.184.216.34/a")]),
        pm_folder("Other", [pm_folder("Auth", [pm_request("B", "GET", "https://93.184.216.34/b")])]),
    ])
    from app.services.postman_folder_extractor import extract_folders
    folder_id = extract_folders(collection)[0].id
    with pytest.raises(ProblemException) as exc:
        create_job(
            collection_raw=json.dumps(collection).encode(), collection_filename="c.json",
            environment_raw=None, environment_filename=None, folder_id=folder_id, supplied_values={},
            owner_key="127.0.0.1", idempotency_key=None, store=_store(), settings=Settings(),
        )
    assert exc.value.status == 422
    assert "Auth" in exc.value.detail
```

(Import `pm_folder` at the top of that test file if it is not already imported.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_folder_extractor.py tests/unit/test_execution_job_service_create.py -v`
Expected: FAIL — `ImportError: cannot import name 'AmbiguousFolderError'`.

- [ ] **Step 3: Implement**

Append to `backend/app/services/postman_folder_extractor.py`:

```python
class AmbiguousFolderError(ValueError):
    """Newman selects folders by name; this folder's name is not unique."""


def folder_run_name(collection_data: dict, folder_id: str) -> str:
    folders = extract_folders(collection_data)
    by_id = {f.id: f for f in folders}
    folder = by_id[folder_id]  # KeyError for an unknown id
    if sum(1 for f in folders if f.name == folder.name) > 1:
        raise AmbiguousFolderError(folder.name)
    return folder.name
```

In `create_job`, right after the existing unknown-`folder_id` check (A4), add:

```python
    if folder_id is not None:
        try:
            folder_run_name(collection_data, folder_id)
        except AmbiguousFolderError as exc:
            detail = (
                f"Folder '{exc}' is not uniquely named; Newman selects folders by name. "
                "Rename one of the folders to run it on its own."
            )
            raise validation_error(detail, errors=[{"path": "$.folder_id", "detail": detail}]) from exc
```

(import `AmbiguousFolderError, folder_run_name` alongside the existing `extract_folders` import).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_postman_folder_extractor.py tests/unit/test_execution_job_service_create.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/postman_folder_extractor.py backend/app/services/execution_job_service.py backend/tests/unit/test_postman_folder_extractor.py backend/tests/unit/test_execution_job_service_create.py
git commit -m "feat(execution-job): resolve folder ids to Newman folder names and reject ambiguous names"
```

---

### Task 6: Hardened `docker run` argument builder

**Files:**
- Create: `backend/app/services/newman_command.py`
- Test: `backend/tests/unit/test_newman_command.py`

**Interfaces:**
- Consumes: `CONTAINER_WORKDIR` (Task 4); Task 3 settings.
- Produces: `build_docker_argv(*, settings: Settings, container_name: str, workspace_root: Path, folder_name: str | None, host_pins: dict[str, list[str]], container_user: str | None, timeout_seconds: int) -> list[str]`. Raises `ValueError` for an invalid container name, pin hostname, or pin address (defence in depth — argv elements are never shell-parsed, but a malformed value must never become a Docker option).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_newman_command.py
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.newman_command import build_docker_argv


def _argv(**overrides) -> list[str]:
    base = dict(settings=Settings(), container_name="b11-newman-abc123", workspace_root=Path("/tmp/b11-newman-x"),
                folder_name=None, host_pins={}, container_user=None, timeout_seconds=300)
    base.update(overrides)
    return build_docker_argv(**base)


def _pairs(argv: list[str]) -> list[tuple[str, str]]:
    return list(zip(argv, argv[1:], strict=False))


def test_isolation_and_limit_flags():
    argv = _argv()
    s = Settings()
    assert argv[:3] == ["docker", "run", "--rm"]
    for flag in ("--read-only", "--rm"):
        assert flag in argv
    pairs = _pairs(argv)
    assert ("--cap-drop", "ALL") in pairs
    assert ("--security-opt", "no-new-privileges") in pairs
    assert ("--pids-limit", str(s.newman_container_pids_limit)) in pairs
    assert ("--memory", s.newman_container_memory) in pairs
    assert ("--cpus", s.newman_container_cpus) in pairs
    assert ("--dns", "127.0.0.1") in pairs
    assert ("--network", "bridge") in pairs
    assert ("--name", "b11-newman-abc123") in pairs
    assert any(a == "--tmpfs" and b.startswith("/tmp:") for a, b in pairs)
    assert any(a == "--ulimit" and b.startswith("fsize=") for a, b in pairs)
    joined = " ".join(argv)
    for forbidden in ("--privileged", "docker.sock", "--network=host", "host-network", "--pid", "--ipc"):
        assert forbidden not in joined


def test_workspace_is_the_only_mount():
    argv = _argv(workspace_root=Path("/tmp/b11-newman-x"))
    mounts = [b for a, b in _pairs(argv) if a in ("--mount", "-v", "--volume")]
    assert mounts == [f"type=bind,src={Path('/tmp/b11-newman-x')},dst=/job"]


def test_newman_arguments():
    argv = _argv(timeout_seconds=300)
    image_index = argv.index(Settings().newman_docker_image)
    newman = argv[image_index + 1:]
    assert newman[:2] == ["run", "/job/collection.json"]
    pairs = _pairs(newman)
    assert ("--environment", "/job/environment.json") in pairs
    assert ("--reporters", "json") in pairs
    assert ("--reporter-json-export", "/job/out/report.json") in pairs
    assert ("--timeout", "300000") in pairs
    assert ("--timeout-request", str(Settings().newman_request_timeout_ms)) in pairs
    assert ("--working-dir", "/job/files") in pairs
    for flag in ("--ignore-redirects", "--no-insecure-file-read", "--disable-unicode"):
        assert flag in newman
    assert "--insecure" not in newman
    assert not any(a.startswith("--folder") for a in newman)


def test_folder_is_passed_as_a_single_equals_argument():
    argv = _argv(folder_name="-rf Auth")
    assert "--folder=-rf Auth" in argv


def test_host_pins_become_sorted_add_host_flags():
    argv = _argv(host_pins={"b.example.com": ["93.184.216.35"], "a.example.com": ["93.184.216.34", "2001:db8::1"]})
    adds = [b for a, b in _pairs(argv) if a == "--add-host"]
    assert adds == ["a.example.com:2001:db8::1", "a.example.com:93.184.216.34", "b.example.com:93.184.216.35"]


@pytest.mark.parametrize("pins", [
    {"--privileged": ["93.184.216.34"]},
    {"a.example.com": ["not-an-ip"]},
    {"a b.example.com": ["93.184.216.34"]},
])
def test_invalid_pins_are_rejected(pins):
    with pytest.raises(ValueError):
        _argv(host_pins=pins)


def test_invalid_container_name_is_rejected():
    with pytest.raises(ValueError):
        _argv(container_name="--rm")


def test_container_user_is_applied_when_given():
    assert ("--user", "1000:1000") in _pairs(_argv(container_user="1000:1000"))
    assert "--user" not in _argv(container_user=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_command.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.newman_command'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/newman_command.py
"""Build the `docker run` argument array for one isolated Newman run.

Always an argument list for `subprocess.Popen(..., shell=False)` - never a
command string (spec §5.1 item 5). Nothing here carries a variable value:
supplied values live in the workspace's environment file.

Isolation (spec §8): read-only root filesystem with a small /tmp tmpfs, all
capabilities dropped, no-new-privileges, CPU/memory/pid/file-size limits,
the per-run workspace as the only mount, no Docker socket, no host network.
DNS is pointed at 127.0.0.1 (nothing answers), so the container can reach
only hostnames pinned with --add-host to the addresses destination
validation checked - a name can never re-resolve somewhere else.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

from ..core.config import MIB, Settings
from .newman_workspace import CONTAINER_WORKDIR

_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


def _add_host_flags(host_pins: dict[str, list[str]]) -> list[str]:
    flags: list[str] = []
    for host in sorted(host_pins):
        if not _HOSTNAME.match(host):
            raise ValueError("Invalid pinned hostname.")
        for address in sorted(host_pins[host]):
            ipaddress.ip_address(address)  # ValueError if not an IP literal
            flags += ["--add-host", f"{host}:{address}"]
    return flags


def build_docker_argv(
    *,
    settings: Settings,
    container_name: str,
    workspace_root: Path,
    folder_name: str | None,
    host_pins: dict[str, list[str]],
    container_user: str | None,
    timeout_seconds: int,
) -> list[str]:
    if not _CONTAINER_NAME.match(container_name):
        raise ValueError("Invalid container name.")
    argv = [
        settings.docker_binary, "run", "--rm",
        "--name", container_name,
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(settings.newman_container_pids_limit),
        "--memory", settings.newman_container_memory,
        "--cpus", settings.newman_container_cpus,
        "--ulimit", f"fsize={settings.max_report_bytes + 8 * MIB}",
        "--network", "bridge",
        "--dns", "127.0.0.1",
        "--env", "HOME=/tmp",
        "--mount", f"type=bind,src={workspace_root},dst={CONTAINER_WORKDIR}",
        "--workdir", CONTAINER_WORKDIR,
    ]
    if container_user:
        argv += ["--user", container_user]
    argv += _add_host_flags(host_pins)
    argv += [
        settings.newman_docker_image,
        "run", f"{CONTAINER_WORKDIR}/collection.json",
        "--environment", f"{CONTAINER_WORKDIR}/environment.json",
        "--reporters", "json",
        "--reporter-json-export", f"{CONTAINER_WORKDIR}/out/report.json",
        "--timeout", str(timeout_seconds * 1000),
        "--timeout-request", str(settings.newman_request_timeout_ms),
        "--working-dir", f"{CONTAINER_WORKDIR}/files",
        "--no-insecure-file-read",
        "--ignore-redirects",
        "--disable-unicode",
        "--color", "off",
    ]
    if folder_name is not None:
        # One `--folder=<name>` element: a name starting with '-' can never be
        # read as a separate Newman option.
        argv.append(f"--folder={folder_name}")
    return argv
```

(If the `--add-host` expected order in the test differs from `sorted()` of the address strings, the test is authoritative only about: hosts sorted, every address present, one flag per address — adjust the expected list to the `sorted()` order rather than changing the implementation.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_command.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/newman_command.py backend/tests/unit/test_newman_command.py
git commit -m "feat(newman): build hardened docker run argument arrays with pinned hosts"
```

---

### Task 7: Generated-report adapter

**Files:**
- Create: `backend/app/services/newman_report_adapter.py`
- Test: `backend/tests/unit/test_newman_report_adapter.py`

**Interfaces:**
- Produces: `read_generated_report(path: Path, *, max_bytes: int) -> RunOutcome` — success with the exact file bytes, or a failed outcome with one of the stable codes `missing_report`, `report_too_large`, `malformed_report` and a fixed user-safe detail. The size is checked with `stat` BEFORE reading.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_newman_report_adapter.py
import json

from app.services.newman_report_adapter import read_generated_report

_VALID = json.dumps({"collection": {"info": {"name": "C"}}, "run": {"executions": []}}).encode()


def test_valid_report_returns_its_exact_bytes(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(_VALID)
    outcome = read_generated_report(path, max_bytes=1024)
    assert outcome.success is True
    assert outcome.report_bytes == _VALID


def test_missing_report(tmp_path):
    outcome = read_generated_report(tmp_path / "report.json", max_bytes=1024)
    assert (outcome.success, outcome.error_code) == (False, "missing_report")


def test_empty_report_is_missing(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"")
    assert read_generated_report(path, max_bytes=1024).error_code == "missing_report"


def test_oversized_report_is_rejected_without_reading(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_bytes(b"x" * 2048)
    from pathlib import Path

    def _no_read(self):
        raise AssertionError("must not read an oversized report")

    monkeypatch.setattr(Path, "read_bytes", _no_read)
    outcome = read_generated_report(path, max_bytes=1024)
    assert (outcome.success, outcome.error_code) == (False, "report_too_large")


def test_invalid_json_is_malformed(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"{not json")
    assert read_generated_report(path, max_bytes=1024).error_code == "malformed_report"


def test_report_without_run_executions_is_malformed(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(json.dumps({"run": {}}).encode())
    assert read_generated_report(path, max_bytes=1024).error_code == "malformed_report"


def test_error_details_are_fixed_messages(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b'{"secret-looking": "abc123"')
    outcome = read_generated_report(path, max_bytes=1024)
    assert "abc123" not in (outcome.error_detail or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_report_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.newman_report_adapter'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/newman_report_adapter.py
"""Turn a Newman JSON reporter file into a RunOutcome.

Only structural checks happen here (present, within the size limit, JSON,
has `run.executions`). Whether the run *succeeded as a business flow* is the
existing run-health analysis's job - an assertion failure is still a valid
report (spec §4). Error details are fixed messages; report content is never
echoed.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.execution_job import RunOutcome


def _failed(code: str, detail: str) -> RunOutcome:
    return RunOutcome(success=False, error_code=code, error_detail=detail)


def read_generated_report(path: Path, *, max_bytes: int) -> RunOutcome:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return _failed("missing_report", "Newman did not produce a JSON report.")
    if size == 0:
        return _failed("missing_report", "Newman did not produce a JSON report.")
    if size > max_bytes:
        return _failed("report_too_large", "The Newman report exceeded the maximum allowed size.")
    data = path.read_bytes()
    try:
        document = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return _failed("malformed_report", "Newman produced a report that is not valid JSON.")
    run = document.get("run") if isinstance(document, dict) else None
    if not isinstance(run, dict) or not isinstance(run.get("executions"), list):
        return _failed("malformed_report", "Newman produced a report without run executions.")
    return RunOutcome(success=True, report_bytes=data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_report_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/newman_report_adapter.py backend/tests/unit/test_newman_report_adapter.py
git commit -m "feat(newman): add generated-report adapter with size and structure checks"
```

---

### Task 8: `DockerNewmanRunner`

**Files:**
- Create: `backend/app/services/docker_newman_runner.py`
- Test: `backend/tests/unit/test_docker_newman_runner.py`

**Interfaces:**
- Consumes: `NewmanRunner`, `RunInput`, `RunOutcome` (domain); `newman_workspace` (Task 4); `folder_run_name`, `AmbiguousFolderError` (Task 5); `build_docker_argv` (Task 6); `read_generated_report` (Task 7); settings (Task 3).
- Produces: `DockerNewmanRunner(settings, *, popen=subprocess.Popen, kill_container=None, workspace_base=None, poll_interval=0.5, clock=time.monotonic, sleep=time.sleep, container_user=PROCESS_USER)` (`PROCESS_USER` → this process's uid:gid; `None` → no `--user` flag) implementing `run(run_input, *, should_cancel) -> RunOutcome`; `default_container_user() -> str | None` (`"uid:gid"` on POSIX, `None` on Windows). Failure codes: `invalid_folder`, `runner_unavailable`, `timeout`, `cancelled`, `resource_limit`, `process_failed`, plus the adapter's codes.

Exit-code mapping (after the process exits on its own): `0` or `1` → read the report (1 = assertions failed, still a complete run); `125` → `runner_unavailable`; `137` → `resource_limit`; anything else → `process_failed`. Process stdout/stderr go to `DEVNULL` — they may echo request data or secrets and are never needed.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_docker_newman_runner.py
import json
import subprocess
from pathlib import Path

from app.core.config import Settings
from app.domain.execution_job import RunInput
from app.services.docker_newman_runner import DockerNewmanRunner
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request

_REPORT = json.dumps({"collection": {"info": {"name": "C"}}, "run": {"executions": []}}).encode()


def _mount_source(argv: list[str]) -> Path:
    spec = argv[argv.index("--mount") + 1]
    fields = dict(part.split("=", 1) for part in spec.split(","))
    return Path(fields["src"])


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Proc:
    def __init__(self, docker: "_FakeDocker"):
        self._docker = docker
        self._polls = 0
        self.returncode = None

    def poll(self):
        if self._docker.never_exits and not self._docker.killed:
            return None
        if self._docker.killed:
            self.returncode = 137
            return 137
        self._polls += 1
        if self._polls > self._docker.polls_before_exit:
            self.returncode = self._docker.exit_code
            return self.returncode
        return None

    def wait(self, timeout=None):
        return self.poll() if self.poll() is not None else 137

    def kill(self):
        self._docker.killed.append("proc")


class _FakeDocker:
    def __init__(self, *, exit_code=0, report=_REPORT, polls_before_exit=0, never_exits=False, popen_error=None):
        self.exit_code, self.report = exit_code, report
        self.polls_before_exit, self.never_exits = polls_before_exit, never_exits
        self.popen_error = popen_error
        self.argv: list[str] | None = None
        self.kwargs: dict = {}
        self.killed: list[str] = []
        self.workspace: Path | None = None

    def popen(self, argv, **kwargs):
        if self.popen_error:
            raise self.popen_error
        self.argv, self.kwargs = argv, kwargs
        self.workspace = _mount_source(argv)
        if self.report is not None:
            (self.workspace / "out" / "report.json").write_bytes(self.report)
        return _Proc(self)

    def kill_container(self, name: str) -> None:
        self.killed.append(name)


def _runner(docker: _FakeDocker, tmp_path: Path, clock: _Clock | None = None) -> DockerNewmanRunner:
    clock = clock or _Clock()
    return DockerNewmanRunner(
        Settings(), popen=docker.popen, kill_container=docker.kill_container, workspace_base=tmp_path,
        poll_interval=0.5, clock=clock, sleep=clock.sleep, container_user=None,
    )


def _input(**overrides) -> RunInput:
    base = dict(collection_data=pm_collection("C", [pm_request("R", "GET", "https://93.184.216.34/")]),
                environment_data=None, supplied_values={"token": "s3cret-value"}, folder_id=None,
                timeout_seconds=300, host_pins={"api.example.com": ["93.184.216.34"]})
    base.update(overrides)
    return RunInput(**base)


def _never() -> bool:
    return False


def test_successful_run_returns_the_report_and_cleans_up(tmp_path):
    docker = _FakeDocker(exit_code=0)
    outcome = _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert outcome.success is True
    assert outcome.report_bytes == _REPORT
    assert docker.workspace is not None and not docker.workspace.exists()


def test_assertion_failures_exit_1_still_yield_the_report(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=1), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.success is True


def test_exit_1_without_a_report_is_missing_report(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=1, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "missing_report")


def test_process_is_started_without_a_shell_and_without_output_capture(tmp_path):
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert isinstance(docker.argv, list)
    assert docker.kwargs.get("shell", False) is False
    assert docker.kwargs["stdout"] is subprocess.DEVNULL
    assert docker.kwargs["stderr"] is subprocess.DEVNULL
    assert docker.kwargs["stdin"] is subprocess.DEVNULL


def test_supplied_values_never_appear_in_argv(tmp_path):
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert "s3cret-value" not in " ".join(docker.argv or [])


def test_host_pins_reach_the_container(tmp_path):
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    argv = docker.argv or []
    assert argv[argv.index("--add-host") + 1] == "api.example.com:93.184.216.34"


def test_folder_id_is_translated_to_a_folder_name(tmp_path):
    from app.services.postman_folder_extractor import extract_folders
    collection = pm_collection("C", [pm_folder("Auth", [pm_request("L", "POST", "https://93.184.216.34/")])])
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(
        _input(collection_data=collection, folder_id=extract_folders(collection)[0].id), should_cancel=_never,
    )
    assert "--folder=Auth" in (docker.argv or [])


def test_unknown_folder_fails_without_starting_docker(tmp_path):
    docker = _FakeDocker()
    outcome = _runner(docker, tmp_path).run(_input(folder_id="nope"), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "invalid_folder")
    assert docker.argv is None


def test_docker_missing_is_runner_unavailable(tmp_path):
    docker = _FakeDocker(popen_error=FileNotFoundError("docker"))
    outcome = _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "runner_unavailable")


def test_docker_exit_125_is_runner_unavailable(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=125, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.error_code == "runner_unavailable"


def test_exit_137_is_resource_limit(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=137, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.error_code == "resource_limit"


def test_other_exit_codes_are_process_failed(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=3, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.error_code == "process_failed"


def test_wall_clock_timeout_kills_the_container(tmp_path):
    docker = _FakeDocker(never_exits=True, report=None)
    outcome = _runner(docker, tmp_path).run(_input(timeout_seconds=5), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "timeout")
    assert any(name.startswith("b11-newman-") for name in docker.killed)
    assert docker.workspace is not None and not docker.workspace.exists()


def test_cancellation_kills_the_container(tmp_path):
    docker = _FakeDocker(never_exits=True, report=None)
    calls = {"n": 0}

    def cancel_on_third_check() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    outcome = _runner(docker, tmp_path).run(_input(), should_cancel=cancel_on_third_check)
    assert (outcome.success, outcome.error_code) == (False, "cancelled")
    assert any(name.startswith("b11-newman-") for name in docker.killed)


def test_each_run_uses_a_fresh_container_name_and_workspace(tmp_path):
    docker = _FakeDocker()
    runner = _runner(docker, tmp_path)
    runner.run(_input(), should_cancel=_never)
    first = (docker.argv[docker.argv.index("--name") + 1], docker.workspace)
    runner.run(_input(), should_cancel=_never)
    second = (docker.argv[docker.argv.index("--name") + 1], docker.workspace)
    assert first[0] != second[0]
    assert first[1] != second[1]


def test_error_details_are_fixed_and_contain_no_values(tmp_path):
    for exit_code in (3, 125, 137):
        outcome = _runner(_FakeDocker(exit_code=exit_code, report=None), tmp_path).run(_input(), should_cancel=_never)
        assert outcome.error_detail
        assert "s3cret-value" not in outcome.error_detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_docker_newman_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.docker_newman_runner'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/docker_newman_runner.py
"""Run one Newman execution in an isolated, short-lived Docker container.

Implements the `NewmanRunner` Protocol. Each call gets its own container
name and its own private workspace (deleted afterwards), so run B can never
see run A's files or mutations (spec §3 steps 10-11). The process runs
without a shell and with stdout/stderr discarded; the runner enforces a
wall-clock deadline and polls `should_cancel`, killing the container on
either (Phase 2 contract). Every failure maps to a stable error code with a
fixed, user-safe message.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..core.config import Settings
from ..core.logging import get_logger
from ..domain.execution_job import NewmanRunner, RunInput, RunOutcome
from .newman_command import build_docker_argv
from .newman_report_adapter import read_generated_report
from .newman_workspace import newman_workspace
from .postman_folder_extractor import folder_run_name

log = get_logger("docker_newman_runner")

_KILL_TIMEOUT_SECONDS = 30
# Default for `container_user`: run as this process's own uid:gid (POSIX).
# Passing None explicitly means "no --user flag" (the image's `node` user).
PROCESS_USER = "__process_user__"
_DETAILS = {
    "invalid_folder": "The selected folder cannot be run.",
    "runner_unavailable": "The Newman execution environment is unavailable.",
    "timeout": "The Newman run exceeded its time limit and was stopped.",
    "cancelled": "The run was cancelled.",
    "resource_limit": "The Newman run exceeded its memory or process limits.",
    "process_failed": "Newman exited unexpectedly.",
}


def _failed(code: str) -> RunOutcome:
    return RunOutcome(success=False, error_code=code, error_detail=_DETAILS[code])


def default_container_user() -> str | None:
    """Run as the API process's own uid:gid on POSIX so the 0600 workspace
    files are readable in the container; None (the image's non-root `node`
    user) where uids don't apply (Docker Desktop on Windows)."""
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return f"{os.getuid()}:{os.getgid()}"
    return None


class DockerNewmanRunner(NewmanRunner):
    def __init__(
        self,
        settings: Settings,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        kill_container: Callable[[str], None] | None = None,
        workspace_base: Path | None = None,
        poll_interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        container_user: str | None = PROCESS_USER,
    ) -> None:
        self._settings = settings
        self._popen = popen
        self._kill_container = kill_container or self._docker_kill
        self._workspace_base = workspace_base
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep
        self._container_user = default_container_user() if container_user == PROCESS_USER else container_user

    def run(self, run_input: RunInput, *, should_cancel: Callable[[], bool]) -> RunOutcome:
        folder_name: str | None = None
        if run_input.folder_id is not None:
            try:
                folder_name = folder_run_name(run_input.collection_data, run_input.folder_id)
            except (KeyError, ValueError):
                return _failed("invalid_folder")

        container_name = f"b11-newman-{secrets.token_hex(8)}"
        with newman_workspace(run_input, base_dir=self._workspace_base) as workspace:
            argv = build_docker_argv(
                settings=self._settings,
                container_name=container_name,
                workspace_root=workspace.root,
                folder_name=folder_name,
                host_pins=run_input.host_pins,
                container_user=self._container_user,
                timeout_seconds=run_input.timeout_seconds,
            )
            try:
                process = self._popen(
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError:
                log.warning("newman run failed: runner_unavailable (docker could not be started)",
                            extra={"stage": "newman_run"})
                return _failed("runner_unavailable")

            failure = self._supervise(process, container_name, run_input.timeout_seconds, should_cancel)
            if failure is not None:
                log.warning(f"newman run failed: {failure.error_code}", extra={"stage": "newman_run"})
                return failure
            return read_generated_report(workspace.report_path, max_bytes=self._settings.max_report_bytes)

    def _supervise(
        self, process: subprocess.Popen, container_name: str, timeout_seconds: int,
        should_cancel: Callable[[], bool],
    ) -> RunOutcome | None:
        deadline = self._clock() + timeout_seconds + self._settings.newman_start_grace_seconds
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                return self._map_exit_code(exit_code)
            if should_cancel():
                self._stop(process, container_name)
                return _failed("cancelled")
            if self._clock() >= deadline:
                self._stop(process, container_name)
                return _failed("timeout")
            self._sleep(self._poll_interval)

    @staticmethod
    def _map_exit_code(exit_code: int) -> RunOutcome | None:
        if exit_code in (0, 1):
            return None  # 1 = assertions failed; the report is still complete
        if exit_code == 125:
            return _failed("runner_unavailable")
        if exit_code == 137:
            return _failed("resource_limit")
        return _failed("process_failed")

    def _stop(self, process: subprocess.Popen, container_name: str) -> None:
        self._kill_container(container_name)
        try:
            process.wait(timeout=_KILL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()

    def _docker_kill(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self._settings.docker_binary, "kill", container_name],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_KILL_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            log.warning("docker kill failed", extra={"stage": "newman_run"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_docker_newman_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/docker_newman_runner.py backend/tests/unit/test_docker_newman_runner.py
git commit -m "feat(newman): add isolated DockerNewmanRunner with timeout and cancellation"
```

---

### Task 9: Wire the Docker runner and document it

**Files:**
- Modify: `backend/app/api/deps.py` (`get_newman_runner`)
- Modify: `README.md` (repo root)
- Test: `backend/tests/unit/test_newman_runner_dependency.py` (new)

**Interfaces:**
- Produces: `get_newman_runner` returns a `DockerNewmanRunner(settings)` when `settings.newman_runner == "docker"` and `shutil.which(settings.docker_binary)` finds the Docker CLI; `None` (→ the endpoint's existing 503 `runner_unavailable`) when it does not.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_newman_runner_dependency.py
from app.api import deps
from app.core.config import Settings
from app.services.docker_newman_runner import DockerNewmanRunner
from app.services.fake_newman_runner import FakeNewmanRunner


def test_docker_mode_returns_a_docker_runner_when_the_cli_exists(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda name: "/usr/bin/docker")
    assert isinstance(deps.get_newman_runner(Settings(newman_runner="docker")), DockerNewmanRunner)


def test_docker_mode_without_the_cli_is_unavailable(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    assert deps.get_newman_runner(Settings(newman_runner="docker")) is None


def test_fake_and_disabled_modes_are_unchanged():
    assert isinstance(deps.get_newman_runner(Settings(newman_runner="fake")), FakeNewmanRunner)
    assert deps.get_newman_runner(Settings(newman_runner="disabled")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_runner_dependency.py -v`
Expected: FAIL — `AttributeError: module 'app.api.deps' has no attribute 'shutil'` (or a docker-mode assertion failure).

- [ ] **Step 3: Implement**

In `backend/app/api/deps.py` add `import shutil` and `from ..services.docker_newman_runner import DockerNewmanRunner`, and change `get_newman_runner`:

```python
def get_newman_runner(settings: Settings = Depends(get_settings)) -> NewmanRunner | None:
    """The runner that executes a newly created job, or None when execution is
    unavailable (the default `disabled`, or `docker` without a Docker CLI) -
    the endpoint then answers 503 without creating a job (spec §4). `fake`
    yields a fresh canned FakeNewmanRunner per request.
    """
    if settings.newman_runner == "fake":
        return canned_fake_runner()
    if settings.newman_runner == "docker" and shutil.which(settings.docker_binary):
        return DockerNewmanRunner(settings)
    return None
```

Add a section to the repo-root `README.md` (place it after the existing backend run instructions):

```markdown
### Running Postman collections (Docker Newman runner)

Collection execution is disabled by default (`POST /api/v1/execution-jobs` answers 503).
To enable it locally:

1. Start Docker Desktop (or the Docker daemon).
2. Build the pinned Newman 6.2.2 image once: `docker build -t baseline11/newman:6.2.2 docker/newman`
3. Start the backend with `B11_NEWMAN_RUNNER=docker`.

Each job runs the collection twice (baseline, comparison) in fresh, short-lived containers:
read-only root filesystem, all Linux capabilities dropped, CPU/memory/process/file-size limits,
a 5-minute wall-clock limit per run, and only the per-run temporary workspace mounted.
Supplied variable values are written to a private environment file, never passed as
command-line arguments. The container's DNS is disabled; it can reach only the hostnames that
passed destination validation, pinned to the exact addresses that were checked.

Known limitations of the local worker: redirects are not followed; a script that builds a URL
from a raw IP address is not blocked at the network layer (production egress rules handle that);
a request whose host comes only from a script-set variable cannot be validated in advance and
fails the job.

Real-Docker tests are opt-in: `B11_RUN_DOCKER_TESTS=1 ./.venv/Scripts/python.exe -m pytest -m docker`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_newman_runner_dependency.py tests/integration/test_execution_jobs_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/deps.py backend/tests/unit/test_newman_runner_dependency.py README.md
git commit -m "feat(api): select the Docker Newman runner via B11_NEWMAN_RUNNER=docker"
```

---

### Task 10: Bounded job dispatcher instead of `BackgroundTasks`

**Files:**
- Create: `backend/app/services/job_dispatcher.py`
- Modify: `backend/app/api/deps.py` (`get_job_dispatcher`)
- Modify: `backend/app/api/v1/execution_jobs.py` (`create_execution_job`)
- Modify: `backend/tests/integration/test_execution_jobs_lifecycle.py` (override the dispatcher wherever the app's runner is overridden)
- Test: `backend/tests/unit/test_job_dispatcher.py` (new), `backend/tests/integration/test_execution_jobs_lifecycle.py`

**Why:** A real run takes up to ~10 minutes (two runs). `BackgroundTasks` runs sync callables on Starlette's shared thread pool, so enough jobs would starve `/inspect`, `/analyses`, and job creation itself (Phase 2 final review). A dedicated, bounded executor caps simultaneous executions globally (`max_concurrent_executions`); extra jobs simply wait `QUEUED`. Also, preparing run material *before* creating the job closes the Phase 2 gap where a job could be persisted and then never scheduled.

**Interfaces:**
- Produces: `JobDispatcher` Protocol with `submit(fn: Callable[..., None], /, *args, **kwargs) -> None`; `ThreadPoolJobDispatcher(max_workers: int)` with `submit` and `shutdown(wait: bool = False)`; `InlineJobDispatcher` (runs synchronously — for tests); `get_job_dispatcher() -> JobDispatcher` (lru_cache singleton using `Settings.max_concurrent_executions`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_job_dispatcher.py
import threading

from app.services.job_dispatcher import InlineJobDispatcher, ThreadPoolJobDispatcher


def test_inline_dispatcher_runs_immediately_on_the_calling_thread():
    seen = []
    InlineJobDispatcher().submit(lambda x, *, y: seen.append((x, y, threading.current_thread())), 1, y=2)
    assert seen == [(1, 2, threading.current_thread())]


def test_thread_pool_dispatcher_runs_off_the_calling_thread():
    done = threading.Event()
    seen = []

    def work():
        seen.append(threading.current_thread())
        done.set()

    dispatcher = ThreadPoolJobDispatcher(max_workers=1)
    try:
        dispatcher.submit(work)
        assert done.wait(timeout=5)
        assert seen[0] is not threading.current_thread()
    finally:
        dispatcher.shutdown(wait=True)


def test_thread_pool_dispatcher_never_exceeds_max_workers():
    release = threading.Event()
    running = []
    lock = threading.Lock()
    peak = {"value": 0}
    started = threading.Semaphore(0)

    def work():
        with lock:
            running.append(1)
            peak["value"] = max(peak["value"], len(running))
        started.release()
        release.wait(timeout=5)
        with lock:
            running.pop()

    dispatcher = ThreadPoolJobDispatcher(max_workers=2)
    try:
        for _ in range(5):
            dispatcher.submit(work)
        assert started.acquire(timeout=5) and started.acquire(timeout=5)
        assert not started.acquire(timeout=0.2)  # a third job is queued, not running
        release.set()
    finally:
        dispatcher.shutdown(wait=True)
    assert peak["value"] == 2
```

In `backend/tests/integration/test_execution_jobs_lifecycle.py`: everywhere the file sets `app.dependency_overrides[get_newman_runner] = ...`, also set `app.dependency_overrides[get_job_dispatcher] = InlineJobDispatcher` (import `get_job_dispatcher` from `app.api.deps` and `InlineJobDispatcher` from `app.services.job_dispatcher`). Then add:

```python
class _RecordingDispatcher:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, /, *args, **kwargs):
        self.submitted.append((fn, args, kwargs))


def test_create_returns_queued_and_hands_the_run_to_the_dispatcher():
    from app.api.deps import get_job_dispatcher, get_newman_runner
    from app.main import create_app
    from app.services.fake_newman_runner import canned_fake_runner

    app = create_app()
    recorder = _RecordingDispatcher()
    app.dependency_overrides[get_newman_runner] = canned_fake_runner
    app.dependency_overrides[get_job_dispatcher] = lambda: recorder
    client = TestClient(app)
    headers = {"Idempotency-Key": "dispatch-once"}
    first = client.post("/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
                        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
                        headers=headers)
    second = client.post("/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
                         files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
                         headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["state"] == "queued"
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(recorder.submitted) == 1  # the idempotent repeat schedules nothing
    assert recorder.submitted[0][1][0] == first.json()["job_id"]
```

(Use the file's existing `_collection()` helper and imports; if the file's other tests build the app through a fixture, follow the same pattern.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_job_dispatcher.py tests/integration/test_execution_jobs_lifecycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.job_dispatcher'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/job_dispatcher.py
"""Where execution jobs actually run.

A dedicated, bounded thread pool - not the web server's shared thread pool -
so long Newman runs can never starve request handling, and at most
`Settings.max_concurrent_executions` jobs execute at once (extra jobs wait,
still QUEUED). `run_job` never raises, so nothing is lost in the futures.
Phase 5 swaps this for a queue + ECS/Fargate worker behind the same seam.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol


class JobDispatcher(Protocol):
    def submit(self, fn: Callable[..., None], /, *args: Any, **kwargs: Any) -> None: ...


class ThreadPoolJobDispatcher:
    def __init__(self, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="b11-job")

    def submit(self, fn: Callable[..., None], /, *args: Any, **kwargs: Any) -> None:
        self._executor.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


class InlineJobDispatcher:
    """Runs the job synchronously on the calling thread. Tests only."""

    def submit(self, fn: Callable[..., None], /, *args: Any, **kwargs: Any) -> None:
        fn(*args, **kwargs)
```

In `backend/app/api/deps.py` add:

```python
@lru_cache
def get_job_dispatcher() -> JobDispatcher:
    return ThreadPoolJobDispatcher(max_workers=get_settings().max_concurrent_executions)
```

(with `from ..services.job_dispatcher import JobDispatcher, ThreadPoolJobDispatcher`).

In `backend/app/api/v1/execution_jobs.py` `create_execution_job`:
- remove the `background_tasks: BackgroundTasks` parameter (and `BackgroundTasks` from the `fastapi` import); add `dispatcher: JobDispatcher = Depends(get_job_dispatcher)`;
- move the `prepare_run_material` call (still via `run_in_threadpool`) to BEFORE `create_job`, unconditionally, so a parse problem can never leave a persisted job behind;
- build the response DTO right after `create_job` returns, BEFORE dispatching (an inline dispatcher would otherwise run the job to completion first and the response would no longer show the state the job was created in);
- replace `background_tasks.add_task(...)` with `dispatcher.submit(...)` using exactly the same positional/keyword arguments, still only `if created:`.

The resulting body after the supplied-values coercion reads:

```python
    collection_raw = await collection.read()
    environment_raw = await environment.read() if environment is not None else None
    environment_filename = environment.filename if environment is not None else None
    collection_filename = collection.filename or "collection.json"

    # Parse the run material first: a problem here must never leave a job
    # persisted that nothing will ever run.
    collection_data, environment_data, variable_values = await run_in_threadpool(
        execution_job_service.prepare_run_material,
        collection_raw=collection_raw,
        collection_filename=collection_filename,
        environment_raw=environment_raw,
        environment_filename=environment_filename,
        supplied_values=supplied_values,
        settings=settings,
    )

    job, created = await run_in_threadpool(
        execution_job_service.create_job,
        collection_raw=collection_raw,
        collection_filename=collection_filename,
        environment_raw=environment_raw,
        environment_filename=environment_filename,
        folder_id=folder_id,
        supplied_values=supplied_values,
        owner_key=owner_key,
        idempotency_key=idempotency_key,
        store=job_store,
        settings=settings,
    )
    dto = presenters.execution_job_dto(job)
    dto["status_url"] = f"{settings.api_prefix}/execution-jobs/{job.id}"

    # `created` is False on an idempotency-key hit that returns an existing
    # job - dispatching then would start a second run of the same job.
    if created:
        dispatcher.submit(
            execution_job_service.run_job,
            job.id,
            collection_data=collection_data,
            environment_data=environment_data,
            variable_values=variable_values,
            supplied_values=supplied_values,
            folder_id=folder_id,
            runner=runner,
            store=job_store,
            analysis_store=analysis_store,
            settings=settings,
        )

    log.info(
        "execution job created",
        extra={"stage": "create_job", "job_id": job.id, "state": dto["state"], "was_created": created},
    )
    return dto
```

If `backend/app/main.py` defines a lifespan/shutdown hook, add `get_job_dispatcher().shutdown(wait=False)` to it; if it has none, do not add one in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_job_dispatcher.py tests/integration/test_execution_jobs_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_dispatcher.py backend/app/api/deps.py backend/app/api/v1/execution_jobs.py backend/tests/unit/test_job_dispatcher.py backend/tests/integration/test_execution_jobs_lifecycle.py
git commit -m "feat(execution-job): run jobs on a bounded dispatcher instead of BackgroundTasks"
```

---

### Task 11: Real-Docker contract and end-to-end tests (opt-in)

**Files:**
- Modify: `backend/pyproject.toml` (register the `docker` marker)
- Create: `backend/tests/fixtures/newman/echo_collection.json`
- Create: `backend/tests/integration/test_docker_newman_runner.py`

**Why:** Spec §11 "Runner contract tests using a fixed Newman fixture collection" and "End-to-end demonstration ... two successful runs, correlation". These need a running Docker daemon, the built image, and internet access to `postman-echo.com`, so they are skipped unless explicitly enabled.

**Interfaces:**
- Consumes: everything above; `get_newman_runner`, `get_job_dispatcher` overrides; `InlineJobDispatcher`.

- [ ] **Step 1: Register the marker**

In `backend/pyproject.toml`, add to the `markers` list:

```toml
    "docker: tests that start real Newman containers (opt-in: B11_RUN_DOCKER_TESTS=1)",
```

- [ ] **Step 2: Add the fixture collection**

`backend/tests/fixtures/newman/echo_collection.json` — a producer/consumer flow against the public Postman Echo service. Request 1 returns a per-run random value; its test script stores it; request 2 sends it back in a header — the correlation the analysis must find. Request 3 proves DNS pinning: its pre-request script tries to reach a hostname that was never validated, and records whether that lookup failed.

```json
{
  "info": {
    "name": "Baseline11 Docker contract",
    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
  },
  "item": [
    {
      "name": "Create session",
      "event": [
        {
          "listen": "test",
          "script": {
            "type": "text/javascript",
            "exec": [
              "pm.test('status is 200', function () { pm.response.to.have.status(200); });",
              "pm.environment.set('session_token', pm.response.json().args.session);"
            ]
          }
        }
      ],
      "request": {
        "method": "GET",
        "url": "https://postman-echo.com/get?session={{$guid}}"
      }
    },
    {
      "name": "Use session",
      "event": [
        {
          "listen": "test",
          "script": {
            "type": "text/javascript",
            "exec": ["pm.test('status is 200', function () { pm.response.to.have.status(200); });"]
          }
        }
      ],
      "request": {
        "method": "GET",
        "header": [{ "key": "X-Session", "value": "{{session_token}}" }],
        "url": "https://postman-echo.com/headers"
      }
    },
    {
      "name": "DNS probe",
      "event": [
        {
          "listen": "prerequest",
          "script": {
            "type": "text/javascript",
            "exec": [
              "pm.sendRequest('https://example.org/', function (err) {",
              "  pm.environment.set('dns_probe', err ? 'blocked' : 'reachable');",
              "});"
            ]
          }
        }
      ],
      "request": { "method": "GET", "url": "https://postman-echo.com/get?probe=1" }
    }
  ]
}
```

- [ ] **Step 3: Write the opt-in tests**

```python
# backend/tests/integration/test_docker_newman_runner.py
"""Real-Docker Newman tests. Opt-in: B11_RUN_DOCKER_TESTS=1, a running Docker
daemon, the image built (docker build -t baseline11/newman:6.2.2 docker/newman),
and internet access to postman-echo.com."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.execution_job import RunInput
from app.services.docker_newman_runner import DockerNewmanRunner
from app.services.postman_domain_extractor import extract_target_domains

pytestmark = pytest.mark.docker

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "newman" / "echo_collection.json"


def _docker_ready() -> bool:
    if os.environ.get("B11_RUN_DOCKER_TESTS") != "1":
        return False
    settings = Settings()
    try:
        info = subprocess.run([settings.docker_binary, "info"], capture_output=True, timeout=30)
        image = subprocess.run([settings.docker_binary, "image", "inspect", settings.newman_docker_image],
                               capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return info.returncode == 0 and image.returncode == 0


if not _docker_ready():
    pytest.skip("Docker Newman tests are opt-in (B11_RUN_DOCKER_TESTS=1, daemon + image required)",
                allow_module_level=True)


def _collection() -> dict:
    return json.loads(_FIXTURE.read_text("utf-8"))


def _never() -> bool:
    return False


def test_image_runs_the_pinned_newman_version():
    settings = Settings()
    result = subprocess.run([settings.docker_binary, "run", "--rm", settings.newman_docker_image, "--version"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    assert result.stdout.strip() == settings.newman_version


def _run_once() -> dict:
    settings = Settings()
    collection = _collection()
    pins = extract_target_domains(collection, environment_values={}, settings=settings).pinned_addresses
    assert "postman-echo.com" in pins
    outcome = DockerNewmanRunner(settings).run(
        RunInput(collection_data=collection, environment_data=None, supplied_values={}, folder_id=None,
                 timeout_seconds=settings.job_run_timeout_seconds, host_pins=pins),
        should_cancel=_never,
    )
    assert outcome.success is True, outcome.error_code
    return json.loads(outcome.report_bytes or b"{}")


def test_runner_produces_a_parseable_report_with_every_request():
    report = _run_once()
    assert len(report["run"]["executions"]) == 3


def test_unvalidated_hostnames_cannot_be_resolved_inside_the_container():
    report = _run_once()
    values = {v["key"]: v.get("value") for v in (report.get("environment") or {}).get("values", [])}
    assert values.get("dns_probe") == "blocked"


def test_full_job_reaches_ready_with_a_correlation_candidate():
    from app.api.deps import get_job_dispatcher, get_newman_runner
    from app.main import create_app
    from app.services.job_dispatcher import InlineJobDispatcher

    app = create_app()
    app.dependency_overrides[get_newman_runner] = lambda: DockerNewmanRunner(Settings())
    app.dependency_overrides[get_job_dispatcher] = InlineJobDispatcher
    client = TestClient(app)
    created = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("echo.json", _FIXTURE.read_bytes(), "application/json")},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    status = created.json()
    deadline = time.monotonic() + 15 * 60
    while status["state"] not in ("ready", "failed", "cancelled") and time.monotonic() < deadline:
        time.sleep(1)
        status = client.get(f"/api/v1/execution-jobs/{job_id}").json()
    assert status["state"] == "ready", status
    assert status["stage_history"] == ["validating", "running_baseline", "running_comparison", "analyzing", "ready"]

    analysis = client.get(f"/api/v1/analyses/{status['analysis_id']}").json()
    assert analysis["mode"] == "two_run"
    assert analysis["candidate_count"] >= 1
```

- [ ] **Step 4: Verify the default suite skips them and everything else passes**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, with `test_docker_newman_runner.py` reported as skipped.

Run: `./.venv/Scripts/python.exe -m ruff check app tests` and `./.venv/Scripts/python.exe -m mypy app`
Expected: both clean.

If Docker is available on this machine: build the image (`docker build -t baseline11/newman:6.2.2 docker/newman` from the repo root), then run `B11_RUN_DOCKER_TESTS=1 ./.venv/Scripts/python.exe -m pytest -m docker -v` and record the result. If Docker is not available, record in the report that the opt-in tests were not executed and why — never claim they passed.

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/tests/fixtures/newman/echo_collection.json backend/tests/integration/test_docker_newman_runner.py
git commit -m "test(newman): opt-in real-Docker contract and end-to-end tests"
```

---

## Definition of Done for Phase 3

- With `B11_NEWMAN_RUNNER=docker` and the pinned image built, `POST /api/v1/execution-jobs` runs the collection twice in fresh isolated containers and lands on the existing analysis (`GET /api/v1/analyses/{id}`) with real baseline/comparison reports.
- A collection whose later requests use a value stored by an earlier request's script is accepted and correlated, not rejected as unresolved.
- No supplied value ever appears in process arguments, logs, filenames, job status, or error details; per-run workspaces are private and always deleted.
- Containers can reach only destination-validated hostnames at the validated addresses; redirects are not followed.
- Every runner failure (Docker missing, timeout, cancellation, resource limits, missing/oversized/malformed report, bad folder) ends the job `failed`/`cancelled` with a stable code and a fixed message.
- Jobs execute on a bounded dispatcher; web request handling never waits on a Newman run.
- The default test suite passes without Docker; the opt-in Docker tests pass when Docker, the image, and internet access are available (or are reported as not run).
