import json
import pytest
from pathlib import Path
from specs.data_contracts import DispatchRecommendation
from tools.dispatch_tools import (
    save_dispatch, approve_dispatch, reject_dispatch, get_pending_dispatches,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def log_path(tmp_path) -> Path:
    return tmp_path / "test_dispatch_log.json"


def make_rec(dispatch_id: str = "dr-001", priority: str = "HIGH",
             dispatch_type: str = "ttc_diversion", status: str = "AWAITING_APPROVAL") -> DispatchRecommendation:
    return DispatchRecommendation(
        dispatch_id=dispatch_id,
        dispatch_type=dispatch_type,
        target_department="TTC Operations",
        message=f"Test dispatch {dispatch_id}",
        priority=priority,
        status=status,
    )


# ---------------------------------------------------------------------------
# save_dispatch
# ---------------------------------------------------------------------------

def test_save_dispatch_creates_file(log_path):
    save_dispatch(make_rec(), path=log_path)
    assert log_path.exists()


def test_save_dispatch_writes_correct_data(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    records = json.loads(log_path.read_text())
    assert len(records) == 1
    assert records[0]["dispatch_id"] == "dr-001"
    assert records[0]["status"] == "AWAITING_APPROVAL"


def test_save_dispatch_appends_multiple(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    save_dispatch(make_rec("dr-002"), path=log_path)
    records = json.loads(log_path.read_text())
    assert len(records) == 2


def test_save_dispatch_upserts_on_duplicate_id(log_path):
    save_dispatch(make_rec("dr-001", priority="HIGH"), path=log_path)
    save_dispatch(make_rec("dr-001", priority="LOW"), path=log_path)
    records = json.loads(log_path.read_text())
    assert len(records) == 1
    assert records[0]["priority"] == "LOW"


def test_save_dispatch_no_file_yet(log_path):
    assert not log_path.exists()
    save_dispatch(make_rec(), path=log_path)
    assert log_path.exists()


# ---------------------------------------------------------------------------
# approve_dispatch
# ---------------------------------------------------------------------------

def test_approve_dispatch_returns_updated_record(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    result = approve_dispatch("dr-001", path=log_path)
    assert result is not None
    assert result.status == "APPROVED"
    assert result.dispatch_id == "dr-001"


def test_approve_dispatch_persists_to_file(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    approve_dispatch("dr-001", path=log_path)
    records = json.loads(log_path.read_text())
    assert records[0]["status"] == "APPROVED"


def test_approve_dispatch_only_updates_target(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    save_dispatch(make_rec("dr-002"), path=log_path)
    approve_dispatch("dr-001", path=log_path)
    records = json.loads(log_path.read_text())
    statuses = {r["dispatch_id"]: r["status"] for r in records}
    assert statuses["dr-001"] == "APPROVED"
    assert statuses["dr-002"] == "AWAITING_APPROVAL"


def test_approve_dispatch_not_found_returns_none(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    result = approve_dispatch("dr-999", path=log_path)
    assert result is None


def test_approve_dispatch_missing_file_returns_none(log_path):
    result = approve_dispatch("dr-001", path=log_path)
    assert result is None


# ---------------------------------------------------------------------------
# reject_dispatch
# ---------------------------------------------------------------------------

def test_reject_dispatch_returns_updated_record(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    result = reject_dispatch("dr-001", path=log_path)
    assert result is not None
    assert result.status == "REJECTED"


def test_reject_dispatch_persists_to_file(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    reject_dispatch("dr-001", path=log_path)
    records = json.loads(log_path.read_text())
    assert records[0]["status"] == "REJECTED"


def test_reject_dispatch_only_updates_target(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    save_dispatch(make_rec("dr-002"), path=log_path)
    reject_dispatch("dr-002", path=log_path)
    records = json.loads(log_path.read_text())
    statuses = {r["dispatch_id"]: r["status"] for r in records}
    assert statuses["dr-001"] == "AWAITING_APPROVAL"
    assert statuses["dr-002"] == "REJECTED"


def test_reject_dispatch_not_found_returns_none(log_path):
    result = reject_dispatch("dr-999", path=log_path)
    assert result is None


# ---------------------------------------------------------------------------
# get_pending_dispatches
# ---------------------------------------------------------------------------

def test_get_pending_returns_only_awaiting(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    save_dispatch(make_rec("dr-002"), path=log_path)
    approve_dispatch("dr-001", path=log_path)
    pending = get_pending_dispatches(path=log_path)
    assert len(pending) == 1
    assert pending[0].dispatch_id == "dr-002"


def test_get_pending_empty_when_all_resolved(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    approve_dispatch("dr-001", path=log_path)
    assert get_pending_dispatches(path=log_path) == []


def test_get_pending_empty_when_no_file(log_path):
    assert get_pending_dispatches(path=log_path) == []


def test_get_pending_returns_dispatch_recommendation_objects(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    pending = get_pending_dispatches(path=log_path)
    assert all(isinstance(p, DispatchRecommendation) for p in pending)


def test_get_pending_includes_all_awaiting(log_path):
    for i in range(4):
        save_dispatch(make_rec(f"dr-{i:03d}"), path=log_path)
    reject_dispatch("dr-001", path=log_path)
    pending = get_pending_dispatches(path=log_path)
    assert len(pending) == 3


# ---------------------------------------------------------------------------
# approve then reject (idempotency edge cases)
# ---------------------------------------------------------------------------

def test_approve_already_approved_updates_again(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    approve_dispatch("dr-001", path=log_path)
    result = approve_dispatch("dr-001", path=log_path)
    assert result.status == "APPROVED"


def test_reject_already_approved(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    approve_dispatch("dr-001", path=log_path)
    result = reject_dispatch("dr-001", path=log_path)
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# Timestamp fields (created_at / updated_at)
# ---------------------------------------------------------------------------

def test_save_dispatch_has_created_at(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    records = json.loads(log_path.read_text())
    assert "created_at" in records[0]
    assert records[0]["created_at"] is not None


def test_new_dispatch_has_no_updated_at(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    records = json.loads(log_path.read_text())
    assert records[0].get("updated_at") is None


def test_approve_sets_updated_at(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    result = approve_dispatch("dr-001", path=log_path)
    assert result.updated_at is not None
    records = json.loads(log_path.read_text())
    assert records[0]["updated_at"] is not None


def test_reject_sets_updated_at(log_path):
    save_dispatch(make_rec("dr-001"), path=log_path)
    result = reject_dispatch("dr-001", path=log_path)
    assert result.updated_at is not None
    records = json.loads(log_path.read_text())
    assert records[0]["updated_at"] is not None
