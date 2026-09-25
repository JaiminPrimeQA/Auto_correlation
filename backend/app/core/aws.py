"""boto3 clients for the AWS backend. `aws_endpoint_url` is only for local
testing (moto server / LocalStack); in AWS the task role's credentials and
the regional endpoints are used."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from .config import Settings

_RETRIES = Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=5, read_timeout=30)


def aws_client(service: str, settings: Settings) -> Any:
    return boto3.client(
        service,
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        config=_RETRIES,
    )


def require(settings: Settings, *names: str) -> None:
    missing = [n for n in names if not getattr(settings, n)]
    if missing:
        env = ", ".join(f"B11_{n.upper()}" for n in missing)
        raise RuntimeError(f"job_backend=aws requires {env}.")
