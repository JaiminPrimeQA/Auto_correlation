"""Execute a generated plan with Apache JMeter 5.6.3 and parse the results.

A plan becomes 'Validated' only after JMeter actually runs it here. External
secrets are passed as JMeter properties (-Jname=value) and never written into
the plan. Correlation variables are proven extracted via ``sample_variables``,
which makes JMeter write each variable's per-sample value into the JTL.

Runs locally against the bundled JMeter, or in a Docker container for isolation.
The report is faithful: if the target host is unreachable from the runner, the
report says the samplers failed rather than pretending success.
"""

from __future__ import annotations

import csv
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Settings
from ..core.logging import get_logger
from ..domain.enums import JmxStatus
from ..domain.models import JMeterValidationReport, SamplerResult
from ..utils.naming import sanitize_variable_name

log = get_logger("jmeter")

_DEFAULT_SENTINEL = "__NOT_FOUND__"


@dataclass
class RunOutcome:
    executed: bool
    return_code: int | None
    timed_out: bool
    jtl_path: Path | None
    log_path: Path | None
    stdout: str
    stderr: str
    error: str | None = None


# --------------------------------------------------------------------------- #
# JMeter discovery
# --------------------------------------------------------------------------- #

def _repo_root() -> Path:
    # app/services/jmeter_runner.py -> repo root is three parents up from app/.
    return Path(__file__).resolve().parents[3]


def resolve_jmeter_launcher(settings: Settings) -> list[str] | None:
    """Return the command prefix that launches JMeter, or None if unavailable."""
    if settings.jmeter_mode == "disabled":
        return None
    if settings.jmeter_mode == "docker":
        if shutil.which("docker"):
            return ["docker"]
        return None

    # local mode
    homes: list[Path] = []
    if settings.jmeter_home:
        homes.append(Path(settings.jmeter_home))
    env_home = os.environ.get("JMETER_HOME")
    if env_home:
        homes.append(Path(env_home))
    homes.append(_repo_root() / ".jmeter" / "apache-jmeter-5.6.3")

    is_windows = platform.system() == "Windows"
    launcher_name = "jmeter.bat" if is_windows else "jmeter"
    for home in homes:
        candidate = home / "bin" / launcher_name
        if candidate.exists():
            # .bat must be run via cmd.exe on Windows.
            return ["cmd", "/c", str(candidate)] if is_windows else [str(candidate)]

    on_path = shutil.which("jmeter")
    if on_path:
        return [on_path]
    return None


def jmeter_available(settings: Settings) -> bool:
    return resolve_jmeter_launcher(settings) is not None


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

def run_plan(
    jmx_xml: str,
    *,
    correlation_variables: list[str],
    required_properties: list[str],
    property_values: dict[str, str],
    settings: Settings,
) -> JMeterValidationReport:
    launcher = resolve_jmeter_launcher(settings)
    report = JMeterValidationReport(
        correlation_variables=list(correlation_variables),
        required_properties=list(required_properties),
        properties_supplied=sorted(property_values.keys()),
    )
    if launcher is None:
        report.status = JmxStatus.VALIDATION_FAILED.value
        report.reasons.append(
            "JMeter is not available on this host (set B11_JMETER_HOME or B11_JMETER_MODE=docker)."
        )
        return report

    workdir = Path(tempfile.mkdtemp(prefix="b11_jmeter_"))
    try:
        outcome = _execute(launcher, jmx_xml, correlation_variables, property_values, workdir, settings)
        return _build_report(outcome, report, correlation_variables, settings)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _execute(
    launcher: list[str],
    jmx_xml: str,
    sample_variables: list[str],
    property_values: dict[str, str],
    workdir: Path,
    settings: Settings,
) -> RunOutcome:
    jmx_path = workdir / "plan.jmx"
    jtl_path = workdir / "result.jtl"
    log_path = workdir / "jmeter.log"
    jmx_path.write_text(jmx_xml, encoding="utf-8")

    props = {sanitize_variable_name(k): v for k, v in property_values.items()}
    docker = settings.jmeter_mode == "docker"

    if docker:
        cmd = [
            *launcher, "run", "--rm", "-v", f"{workdir}:/work", settings.jmeter_docker_image,
            "-n", "-t", "/work/plan.jmx", "-l", "/work/result.jtl", "-j", "/work/jmeter.log",
        ]
    else:
        cmd = [
            *launcher, "-n", "-t", str(jmx_path), "-l", str(jtl_path), "-j", str(log_path),
        ]
    if sample_variables:
        cmd.append("-Jsample_variables=" + ",".join(sanitize_variable_name(v) for v in sample_variables))
    for k, v in props.items():
        cmd.append(f"-J{k}={v}")

    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=settings.jmeter_timeout_seconds, cwd=str(workdir)
        )
    except subprocess.TimeoutExpired as exc:
        log.warning("jmeter timed out", extra={"stage": "validate"})
        return RunOutcome(
            executed=True, return_code=None, timed_out=True,
            jtl_path=jtl_path if jtl_path.exists() else None,
            log_path=log_path if log_path.exists() else None,
            stdout=exc.stdout or "", stderr=exc.stderr or "", error="timed out",
        )
    except (OSError, ValueError) as exc:
        return RunOutcome(
            executed=False, return_code=None, timed_out=False,
            jtl_path=None, log_path=None, stdout="", stderr="", error=str(exc),
        )

    return RunOutcome(
        executed=True, return_code=completed.returncode, timed_out=False,
        jtl_path=jtl_path if jtl_path.exists() else None,
        log_path=log_path if log_path.exists() else None,
        stdout=completed.stdout or "", stderr=completed.stderr or "",
    )


# --------------------------------------------------------------------------- #
# Parsing / report
# --------------------------------------------------------------------------- #

def _build_report(
    outcome: RunOutcome,
    report: JMeterValidationReport,
    correlation_variables: list[str],
    settings: Settings,
) -> JMeterValidationReport:
    report.executed = outcome.executed
    report.return_code = outcome.return_code
    report.timed_out = outcome.timed_out

    if not outcome.executed:
        report.status = JmxStatus.VALIDATION_FAILED.value
        report.reasons.append(f"JMeter could not be launched: {outcome.error}.")
        return report
    if outcome.timed_out:
        report.status = JmxStatus.VALIDATION_FAILED.value
        report.reasons.append(f"JMeter run exceeded {settings.jmeter_timeout_seconds}s and was stopped.")

    rows = _parse_jtl(outcome.jtl_path) if outcome.jtl_path else []
    var_seen: set[str] = set()
    for row in rows:
        success = str(row.get("success", "")).strip().lower() == "true"
        failure = (row.get("failureMessage") or "").strip()
        result = SamplerResult(
            label=row.get("label", ""),
            code=row.get("responseCode", ""),
            success=success,
            message=(row.get("responseMessage") or "").strip(),
            assertion_failure=failure or None,
        )
        report.sampler_results.append(result)
        if failure:
            report.assertion_failures += 1
        for var in correlation_variables:
            val = (row.get(var) or "").strip()
            if val and val != _DEFAULT_SENTINEL:
                var_seen.add(var)

    report.samplers_total = len(rows)
    report.samplers_success = sum(1 for r in report.sampler_results if r.success)
    report.samplers_failed = report.samplers_total - report.samplers_success
    report.error_ratio = round(report.samplers_failed / report.samplers_total, 4) if report.samplers_total else 0.0
    report.variables_extracted = [v for v in correlation_variables if v in var_seen]
    report.variables_missing = [v for v in correlation_variables if v not in var_seen]
    report.log_errors = _scan_log(outcome.log_path) if outcome.log_path else []

    _decide_status(report, outcome, settings)
    return report


def _decide_status(report: JMeterValidationReport, outcome: RunOutcome, settings: Settings) -> None:
    reasons = report.reasons
    ok = True

    # Most common failure: requests rejected for missing/invalid auth. Explain it
    # up front and name the secret to supply, so the fix is obvious.
    auth_failed = sum(1 for r in report.sampler_results if r.code in ("401", "403"))
    if auth_failed and report.samplers_total and auth_failed / report.samplers_total >= 0.5:
        unsupplied = [p for p in report.required_properties if p not in report.properties_supplied]
        hint = f"{auth_failed}/{report.samplers_total} requests were rejected as 401/403 (unauthorized)."
        if unsupplied:
            hint += (
                " You did not supply the required secret(s): "
                + ", ".join(unsupplied)
                + ". Enter the real value in the runtime-secret box and validate again."
            )
        else:
            hint += " The supplied credential may be invalid or expired - capture a fresh one and retry."
        reasons.append(hint)

    connection_failed = bool(report.sampler_results) and all(
        not r.success and any(
            marker in f"{r.code} {r.message}"
            for marker in ("HttpHostConnectException", "ConnectException", "UnknownHostException")
        )
        for r in report.sampler_results
    )
    if connection_failed:
        reasons.append(
            "The target API was unreachable from JMeter. Start the API and verify BASE_PROTOCOL, "
            "BASE_HOST, and the sampler port. Correlation variables were not evaluated because "
            "their producer responses were never received."
        )

    if outcome.timed_out:
        ok = False
    if outcome.return_code not in (0, None):
        ok = False
        reasons.append(f"JMeter exited with code {outcome.return_code} (the plan failed to run).")
    if report.samplers_total == 0:
        ok = False
        reasons.append("No samples were recorded - the plan did not execute any requests.")
    if report.error_ratio > settings.jmeter_max_error_ratio:
        ok = False
        reasons.append(
            f"{report.samplers_failed}/{report.samplers_total} samplers failed "
            f"(error ratio {report.error_ratio:.0%} > allowed {settings.jmeter_max_error_ratio:.0%})."
        )
    if report.assertion_failures:
        ok = False
        reasons.append(f"{report.assertion_failures} assertion(s) failed during the run.")
    if report.variables_missing and not connection_failed:
        ok = False
        reasons.append(
            "Correlation variable(s) were never extracted at runtime: "
            + ", ".join(report.variables_missing)
        )
    if ok:
        reasons.append("JMeter executed the plan successfully; all samplers passed.")
    report.status = JmxStatus.VALIDATED.value if ok else JmxStatus.VALIDATION_FAILED.value


def _parse_jtl(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []
    reader = csv.DictReader(text.splitlines())
    return [row for row in reader]


_LOG_ERROR_MARKERS = ("ERROR", "Variable ", "not found", "Could not", "Exception")


def _scan_log(path: Path) -> list[str]:
    out: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(m in line for m in _LOG_ERROR_MARKERS) and "StatusConsoleListener" not in line:
                out.append(line.strip()[:300])
            if len(out) >= 50:
                break
    except OSError:
        pass
    return out
