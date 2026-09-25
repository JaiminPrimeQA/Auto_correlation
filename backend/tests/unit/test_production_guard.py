import pytest

from app.core.config import Settings
from app.core.production_guard import UnsafeConfiguration, check_production_settings

_AWS = dict(
    job_backend="aws", aws_region="eu-west-1", jobs_table="t", material_bucket="b", job_queue_url="https://q",
    kms_key_id="alias/k",
)
_SAFE = dict(
    environment="production", auth_mode="oidc", oidc_issuer="https://idp.example.com/", oidc_audience="api",
    cors_origins="https://b11.example.com", newman_runner="disabled", **_AWS,
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
