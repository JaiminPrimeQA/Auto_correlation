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
        name = str(it.get("name", "unnamed"))
        here = f"{path} > {name}" if path else name
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
    # Per-unique-host cache: None means "allowed", a string is the warning
    # detail to reuse for every subsequent occurrence of that host. This
    # avoids redundant (blocking) DNS lookups when a collection references
    # the same host many times (spec: Important #1).
    host_outcomes: dict[str, str | None] = {}
    cap_warning_added = False
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
        if host not in host_outcomes:
            if len(host_outcomes) >= settings.max_target_hosts_to_validate:
                if not cap_warning_added:
                    warnings.append(
                        f"Target-domain validation stopped after {settings.max_target_hosts_to_validate} "
                        "distinct hosts; remaining targets were not checked."
                    )
                    cap_warning_added = True
                continue
            try:
                destination_policy.validate_destination(substituted, settings=settings, resolver=resolver)
            except ProblemException as exc:
                host_outcomes[host] = exc.detail
            else:
                host_outcomes[host] = None
        detail = host_outcomes[host]
        if detail is None:
            resolved.add(host)
        else:
            warnings.append(f"'{location}': {detail}")
    return DomainReport(resolved_domains=sorted(resolved), warnings=warnings)
