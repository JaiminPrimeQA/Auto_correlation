"""Application configuration.

All limits and thresholds are configurable via environment variables so that
deployments can tune complexity/security bounds and correlation gating without
code changes. Defaults are conservative and match the product specification.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class Settings(BaseSettings):
    """Runtime settings loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_prefix="B11_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Service ---
    app_name: str = "Baseline11 Auto-Correlate"
    environment: str = "development"  # development | production
    api_prefix: str = "/api/v1"

    # --- CORS ---
    # Comma-separated list. No wildcard is permitted when environment == production.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- Upload / parsing limits ---
    max_file_bytes: int = 25 * MIB
    max_files: int = 2
    max_json_depth: int = 64
    max_array_length: int = 200_000
    max_buffer_length: int = 25 * MIB
    max_scalar_length: int = 200_000
    max_total_values: int = 2_000_000

    # --- Postman collection intake limits (Phase 1: inspection only) ---
    max_collection_bytes: int = 10 * MIB
    max_environment_bytes: int = 2 * MIB
    # Distinct hosts validated against the destination policy per inspection.
    # Protects the (blocking, per-host) DNS resolution path from a collection
    # that references an unbounded number of distinct hosts.
    max_target_hosts_to_validate: int = 50

    # --- Public-HTTPS destination policy ---
    # Never true in production regardless of this flag; see `https_only`.
    allow_insecure_http_destinations: bool = False
    # Comma-separated hostnames that are always blocked in addition to the
    # localhost/private/metadata-IP checks in `destination_policy`.
    blocked_hostnames: str = "169.254.169.254,metadata.google.internal,metadata.goog,metadata.azure.com"

    # --- Execution jobs (Phase 2: state model + fake runner) ---
    max_concurrent_jobs_per_owner: int = 2
    job_ttl_seconds: int = 30 * 60
    job_run_timeout_seconds: int = 5 * 60
    max_report_bytes: int = 25 * MIB
    # A repeat POST with the same Idempotency-Key header, from the same owner,
    # within this window returns the existing job instead of starting a new one.
    idempotency_window_seconds: int = 10 * 60
    # Which NewmanRunner executes jobs. `disabled` (the default, spec §4) makes
    # POST /execution-jobs answer 503 `runner_unavailable` without creating a
    # job; `fake` selects the deterministic canned FakeNewmanRunner (dev/tests
    # only - it never contacts the collection's targets).
    newman_runner: Literal["disabled", "fake", "docker"] = "disabled"
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

    # --- Session store ---
    session_ttl_seconds: int = 30 * 60
    session_store: str = "memory"  # memory | redis
    redis_url: str | None = None

    # --- Rate limiting (token bucket per client) ---
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # --- Correlation / health thresholds (all configurable) ---
    # Block high-confidence automatic analysis when the comparison run exceeds
    # this fraction of 401/403 responses.
    auth_failure_block_ratio: float = 0.30
    # Minimum sequence alignment coverage required for auto analysis.
    min_alignment_coverage: float = 0.80
    # Server-error ratio above which auto analysis is flagged.
    server_error_warn_ratio: float = 0.30
    # Values shorter than this are excluded as too common/short by default.
    min_dynamic_value_length: int = 4
    # Minimum Shannon entropy (bits/char) hint for token-likeness scoring.
    token_entropy_hint: float = 2.5

    # --- JMeter validation ---
    # Path to a JMeter home (contains bin/jmeter[.bat]). Defaults to the bundled
    # Apache JMeter 5.6.3 under the repo's .jmeter directory when present.
    jmeter_home: str | None = None
    jmeter_mode: str = "local"  # local | docker | disabled
    jmeter_docker_image: str = "justb4/jmeter:5.6.3"
    jmeter_timeout_seconds: int = 180
    # A plan is VALIDATED only when it runs AND the sampler error ratio is within
    # this bound (default 0: every sampler must succeed). Raise for flaky nets.
    jmeter_max_error_ratio: float = 0.0

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if self.is_production and "*" in origins:
            raise ValueError("Wildcard CORS origin is not allowed in production.")
        return origins

    @property
    def blocked_hostname_list(self) -> list[str]:
        return [h.strip().lower() for h in self.blocked_hostnames.split(",") if h.strip()]

    @property
    def https_only(self) -> bool:
        return self.is_production or not self.allow_insecure_http_destinations


@lru_cache
def get_settings() -> Settings:
    return Settings()
