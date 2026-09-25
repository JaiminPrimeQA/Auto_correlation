"""Refuse to start in production with an unsafe configuration (spec §8:
authenticate users before production deployment; §4: never execute a
collection in the API process). Every problem is reported at once, naming
the environment variable to fix."""

from __future__ import annotations

from typing import Literal

from .config import Settings


class UnsafeConfiguration(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Unsafe production configuration:\n- " + "\n- ".join(problems))


def _aws_problems(settings: Settings) -> list[str]:
    problems = []
    if settings.job_backend != "aws":
        problems.append("B11_JOB_BACKEND must be 'aws' in production (collections never run in the API process).")
        return problems
    for name in ("aws_region", "jobs_table", "material_bucket", "job_queue_url", "kms_key_id"):
        if not getattr(settings, name):
            problems.append(f"B11_{name.upper()} is required.")
    if settings.aws_endpoint_url:
        problems.append("B11_AWS_ENDPOINT_URL is for local testing only; unset it in production.")
    return problems


def check_production_settings(settings: Settings, *, role: Literal["api", "worker"] = "api") -> None:
    if not settings.is_production:
        return
    problems = _aws_problems(settings)
    if settings.newman_runner == "fake":
        problems.append("B11_NEWMAN_RUNNER=fake never contacts real targets; it is for tests only.")
    if settings.allow_insecure_http_destinations:
        problems.append("B11_ALLOW_INSECURE_HTTP_DESTINATIONS must be false: production allows only https targets.")
    if role == "worker":
        if settings.newman_runner != "ecs":
            problems.append("B11_NEWMAN_RUNNER must be 'ecs' for the production worker.")
    else:
        if settings.auth_mode != "oidc":
            problems.append("B11_AUTH_MODE must be 'oidc' in production.")
        if not settings.oidc_issuer:
            problems.append("B11_OIDC_ISSUER is required.")
        elif not settings.oidc_issuer.startswith("https://"):
            problems.append("B11_OIDC_ISSUER must be an https URL.")
        if not settings.oidc_audience:
            problems.append("B11_OIDC_AUDIENCE is required.")
        origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        if not origins or any(o == "*" or not o.startswith("https://") for o in origins):
            problems.append("B11_CORS_ORIGINS must list explicit https origins (no wildcard) in production.")
    if problems:
        raise UnsafeConfiguration(problems)
