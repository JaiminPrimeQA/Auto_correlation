"""Job metrics as CloudWatch Embedded Metric Format (EMF) log lines.

Each metric is one JSON line on stdout; in ECS the awslogs driver ships it
to CloudWatch Logs, which extracts the metric - no SDK or extra permission
needed. Locally the lines are simply logged. Dimension values are restricted
to plain identifiers so a metric can never carry a variable value or other
free text; anything else is recorded as "other".
"""

from __future__ import annotations

import json
import re
import sys
import time

NAMESPACE = "Baseline11/Execution"
_UNITS = {"Count", "Seconds", "Milliseconds", "Bytes"}
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _write(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def emit(name: str, value: float, *, unit: str, **dimensions: str | None) -> None:
    if unit not in _UNITS:
        raise ValueError(f"Unsupported metric unit {unit!r}.")
    dims = {k: (v if isinstance(v, str) and _IDENTIFIER.match(v) else "other") for k, v in dimensions.items()}
    document = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [list(dims)] if dims else [[]],
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        **dims,
        name: value,
    }
    try:
        _write(json.dumps(document, separators=(",", ":")))
    except Exception:  # noqa: BLE001 - metrics must never break a job
        pass
