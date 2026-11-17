import pytest

from app.core.config import Settings
from app.core.production_guard import UnsafeConfiguration, check_production_settings

_AWS = dict(
    job_backend="aws", aws_region="eu-west-1", jobs_table="t", material_bucket="b", job_queue_url="https://q",
    kms_key_id="alias/k",
)
_SAFE = dict(
    environment="production", auth_mode="oidc", oidc_issuer="https://idp.example.com/", oidc_audience="api",
    cors_origins="https://b11.example.com", newman_runner="disabled", billing_provider="disabled",
    database_url="postgresql://b11:secret@db.internal:5432/b11", history_backend="s3", history_bucket="h",
    session_store="s3", state_bucket="s", state_signing_key="x" * 32, **_AWS,
)


def test_safe_production_settings_pass():
    check_production_settings(Settings(**_SAFE))


def test_development_is_never_blocked():
    check_production_settings(Settings())


@pytest.mark.parametrize("override,needle", [
    ({"auth_mode": "disabled"}, "B11_AUTH_MODE"),
    ({"oidc_issuer": None}, "B11_OIDC_ISSUER"),
    ({"oidc_issuer": "http://idp.example.com/"}, "https"),
    ({"oidc_audience": None}, "B11_OIDC_AUDIENCE"),
    ({"newman_runner": "fake"}, "fake"),
    ({"job_backend": "local", "newman_runner": "docker"}, "B11_JOB_BACKEND"),
    ({"cors_origins": "*"}, "CORS"),
    ({"cors_origins": "http://b11.example.com"}, "CORS"),
    ({"material_bucket": None}, "B11_MATERIAL_BUCKET"),
    ({"kms_key_id": None}, "B11_KMS_KEY_ID"),
    ({"aws_endpoint_url": "http://localhost:5000"}, "B11_AWS_ENDPOINT_URL"),
    ({"allow_insecure_http_destinations": True}, "http"),
    ({"billing_provider": "demo"}, "B11_BILLING_PROVIDER"),
    ({"database_url": None}, "B11_DATABASE_URL"),
    ({"database_url": "sqlite:///data.db"}, "B11_DATABASE_URL"),
    ({"history_backend": "local"}, "B11_HISTORY_BACKEND"),
    ({"session_store": "memory"}, "B11_SESSION_STORE"),
    ({"state_bucket": None}, "B11_STATE_BUCKET"),
    ({"state_signing_key": "short"}, "B11_STATE_SIGNING_KEY"),
])
def test_each_unsafe_setting_is_named(override, needle):
    with pytest.raises(UnsafeConfiguration) as exc:
        check_production_settings(Settings(**{**_SAFE, **override}))
    assert needle.lower() in str(exc.value).lower()


def test_all_problems_are_reported_together():
    with pytest.raises(UnsafeConfiguration) as exc:
        check_production_settings(Settings(environment="production"))
    assert len(exc.value.problems) >= 3


def test_create_app_refuses_unsafe_production_settings(monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("B11_ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(UnsafeConfiguration):
            create_app()
    finally:
        monkeypatch.delenv("B11_ENVIRONMENT")
        get_settings.cache_clear()


def test_worker_role_needs_the_ecs_runner_but_not_oidc():
    worker = Settings(environment="production", newman_runner="ecs", **_AWS)
    check_production_settings(worker, role="worker")
    with pytest.raises(UnsafeConfiguration) as exc:
        check_production_settings(Settings(environment="production", newman_runner="docker", **_AWS), role="worker")
    assert "ecs" in str(exc.value)


def test_demo_billing_is_refused_in_production():
    with pytest.raises(UnsafeConfiguration) as exc:
        check_production_settings(Settings(**{**_SAFE, "billing_provider": "demo"}))
    assert any("B11_BILLING_PROVIDER" in p for p in exc.value.problems)


def test_database_url_can_be_assembled_from_rds_secret_parts():
    settings = Settings(**{**_SAFE, "database_url": None, "database_host": "db.internal",
                           "database_user": "b11", "database_password": "p@ss/word:1"})
    assert settings.account_database == "postgresql://b11:p%40ss%2Fword%3A1@db.internal:5432/baseline11?sslmode=require"
    check_production_settings(settings)


def test_local_account_database_is_refused_in_production():
    # A SQLite file (or :memory:) lives on one task's disk; replicas need PostgreSQL.
    for path in (":memory:", "data/baseline11.db"):
        with pytest.raises(UnsafeConfiguration) as exc:
            check_production_settings(Settings(**{**_SAFE, "database_url": None, "database_path": path}))
        assert any("B11_DATABASE_URL" in p for p in exc.value.problems)
