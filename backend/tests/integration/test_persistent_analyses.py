"""Analyses survive a restart and stay consistent across API replicas when
kept in shared storage (session_store=file here; s3 uses the same code over
S3 conditional writes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_store
from app.core.config import Settings, get_settings
from app.main import create_app
from app.repositories.persistent_analysis_store import (
    AnalysisConflict,
    FileBlobBackend,
    PersistentSessionStore,
)
from tests.fixtures.har_builders import har, login_flow

KEY = "test-signing-key-0123456789"


def make_store(root: Path) -> PersistentSessionStore:
    return PersistentSessionStore(FileBlobBackend(root), ttl_seconds=1800, signing_key=KEY)


def replica(store: PersistentSessionStore) -> TestClient:
    """One API process: its own app instance and its own store object (own cache)."""
    settings = Settings(rate_limit_enabled=False, trusted_proxy_hops=0)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def new_analysis(client: TestClient) -> str:
    raw = har(login_flow())
    inspected = client.post("/api/v1/analyses/har/inspect", files={"files": ("flow.har", raw, "application/json")})
    keep = [e["index"] for e in inspected.json()["files"][0]["entries"] if e["decision"] == "keep"]
    created = client.post(
        "/api/v1/analyses/har", files={"files": ("flow.har", raw, "application/json")},
        data={"selected": json.dumps(keep)},
    )
    assert created.status_code == 201, created.text
    return created.json()["analysis_id"]


def open_candidates(client: TestClient, analysis_id: str) -> list[dict]:
    r = client.get(f"/api/v1/analyses/{analysis_id}/candidates", params={"state": "suggested"})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_accepted_candidate_survives_an_api_restart(tmp_path):
    first = replica(make_store(tmp_path))
    analysis_id = new_analysis(first)
    candidates = open_candidates(first, analysis_id)
    assert candidates, "the login flow should produce at least one candidate"
    accepted = first.post(f"/api/v1/analyses/{analysis_id}/candidates/{candidates[0]['id']}/accept")
    assert accepted.status_code == 200, accepted.text

    # A brand-new process (fresh store object, empty cache) over the same storage.
    restarted = replica(make_store(tmp_path))
    remaining = open_candidates(restarted, analysis_id)
    assert candidates[0]["id"] not in {c["id"] for c in remaining}
    summary = restarted.get(f"/api/v1/analyses/{analysis_id}")
    assert summary.status_code == 200
    reloaded = make_store(tmp_path).get(analysis_id)
    assert reloaded is not None
    assert accepted.json()["variable_name"] in {r.variable_name for r in reloaded.rules.values()}


def test_replicas_see_each_others_changes(tmp_path):
    a = replica(make_store(tmp_path))
    b = replica(make_store(tmp_path))
    analysis_id = new_analysis(a)
    first = open_candidates(b, analysis_id)  # b caches the snapshot
    assert a.post(f"/api/v1/analyses/{analysis_id}/candidates/{first[0]['id']}/reject").status_code == 200
    # b notices the newer version and reloads instead of serving its cache.
    assert first[0]["id"] not in {c["id"] for c in open_candidates(b, analysis_id)}


def test_deleted_analysis_is_not_saved_back(tmp_path):
    client = replica(make_store(tmp_path))
    analysis_id = new_analysis(client)
    assert client.delete(f"/api/v1/analyses/{analysis_id}").status_code == 204
    assert client.get(f"/api/v1/analyses/{analysis_id}").status_code == 404
    assert not list((tmp_path / "analyses").glob("*.bin"))


def test_concurrent_writes_from_two_replicas_conflict_instead_of_overwriting(tmp_path):
    client = replica(make_store(tmp_path))
    analysis_id = new_analysis(client)
    one, two = make_store(tmp_path), make_store(tmp_path)
    left, right = one.get(analysis_id), two.get(analysis_id)
    assert left is not None and right is not None
    left.warnings.append("edited on replica one")
    one.save(left)
    right.warnings.append("edited on replica two")
    with pytest.raises(AnalysisConflict):
        two.save(right)
    # After the conflict replica two reloads the winning version.
    reloaded = two.get(analysis_id)
    assert reloaded is not None and "edited on replica one" in reloaded.warnings


def test_a_tampered_snapshot_is_refused_not_unpickled(tmp_path):
    client = replica(make_store(tmp_path))
    analysis_id = new_analysis(client)
    blob = tmp_path / "analyses" / f"{analysis_id}.bin"
    data = bytearray(blob.read_bytes())
    data[-1] ^= 0xFF
    blob.write_bytes(bytes(data))
    (blob.with_suffix(".bin.tag")).write_text("changed", encoding="ascii")
    assert make_store(tmp_path).get(analysis_id) is None


def test_a_different_signing_key_cannot_read_snapshots(tmp_path):
    client = replica(make_store(tmp_path))
    analysis_id = new_analysis(client)
    other = PersistentSessionStore(FileBlobBackend(tmp_path), ttl_seconds=1800, signing_key="another-key-9876543210")
    assert other.get(analysis_id) is None


def test_a_failed_request_does_not_leave_half_applied_changes(tmp_path):
    store = make_store(tmp_path)
    client = replica(store)
    analysis_id = new_analysis(client)
    loaded = store.get(analysis_id)
    assert loaded is not None
    loaded.warnings.append("dirty, never saved")
    store.discard(analysis_id)  # what the middleware does after an error response
    fresh = store.get(analysis_id)
    assert fresh is not None and "dirty, never saved" not in fresh.warnings


def test_signing_key_is_required():
    with pytest.raises(ValueError):
        PersistentSessionStore(FileBlobBackend("."), ttl_seconds=60, signing_key="short")
