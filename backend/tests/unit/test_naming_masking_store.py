import time

from app.core.config import Settings
from app.domain.enums import AnalysisMode
from app.repositories.analysis_store import Analysis, InMemorySessionStore
from app.services.normalizer import normalize_run
from app.utils.masking import (
    is_sensitive_key,
    looks_like_secret_value,
    mask_value,
    redact_for_log,
)
from app.utils.naming import (
    dedupe_variable_name,
    is_valid_variable_name,
    sanitize_variable_name,
)
from tests.fixtures.scenarios import scenario_login_token


def test_sanitize_and_validate_names():
    assert sanitize_variable_name("order-id!") == "order_id"
    assert sanitize_variable_name("123abc").startswith("var_")
    assert is_valid_variable_name("token_1")
    assert not is_valid_variable_name("1token")


def test_dedupe_names():
    taken: set[str] = set()
    assert dedupe_variable_name("token", taken) == "token"
    assert dedupe_variable_name("token", taken) == "token_2"
    assert dedupe_variable_name("token", taken) == "token_3"


def test_masking_and_redaction():
    assert mask_value("supersecretvalue").count("*") > 0
    assert is_sensitive_key("Authorization")
    assert looks_like_secret_value("aaaaaaaaaa.bbbbbbbbbb.ccccc")  # JWT-like
    assert "[REDACTED]" in redact_for_log("Authorization: Bearer abc.def.ghi")


def test_store_ttl_cleanup():
    store = InMemorySessionStore(ttl_seconds=1)
    run = normalize_run(scenario_login_token("baseline"), filename="b.json", settings=Settings())
    now = time.time()
    a = Analysis(id="abc", mode=AnalysisMode.SINGLE_RUN, created_at=now, expires_at=now - 1, baseline_run=run)
    store.create(a)
    assert store.get("abc") is None  # already expired
    assert store.cleanup() >= 0
