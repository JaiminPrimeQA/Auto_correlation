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
