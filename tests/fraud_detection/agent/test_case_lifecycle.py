"""Test vong doi case + fail-closed cua apply_action/notify_driver.

Day la phep kiem tra QUAN TRONG NHAT ve mat an toan cua he thong: dam bao
KHONG co duong nao apply_action chay ma thieu approver_id hoac tren mot case
chua duoc confirmed (docs/strategy.md muc 08 - "100% hanh dong trung phat di
qua approver_id").
"""

from __future__ import annotations

from lcx_core.agent.tools import apply_action, flag_case, notify_driver, resolve_case


def _make_case():
    result = flag_case.run("d-test", "route_deviation", "medium", 0.7, {"x": 1})
    assert result.ok
    return result.data["case_id"]


def test_apply_action_rejects_missing_approver():
    case_id = _make_case()
    result = apply_action.run(case_id, "suspend_7_days", None, "reason")
    assert not result.ok
    assert result.blocked_by == "missing_approver_id"


def test_apply_action_rejects_unconfirmed_case():
    case_id = _make_case()
    result = apply_action.run(case_id, "suspend_7_days", "mgr-1", "reason")
    assert not result.ok
    assert result.blocked_by == "case_not_confirmed"


def test_apply_action_rejects_invalid_sanction_tier():
    case_id = _make_case()
    resolve_case.run(case_id, "confirmed", "mgr-1", "note")
    result = apply_action.run(case_id, "fire_immediately", "mgr-1", "reason")
    assert not result.ok
    assert result.blocked_by == "invalid_sanction_tier"


def test_apply_action_succeeds_after_confirmed_with_approver():
    case_id = _make_case()
    resolve_case.run(case_id, "confirmed", "mgr-1", "note")
    result = apply_action.run(case_id, "suspend_7_days", "mgr-1", "reason")
    assert result.ok
    assert result.data["outcome"] == "applied"


def test_resolve_case_requires_reviewer_id():
    case_id = _make_case()
    result = resolve_case.run(case_id, "confirmed", "", "note")
    assert not result.ok
    assert result.blocked_by == "missing_reviewer_id"


def test_notify_driver_blocks_threshold_leak():
    result = notify_driver.run("d-test", "Ban vuot nguong percentile 99", source_texts=[])
    assert not result.ok
    assert result.blocked_by == "guardrail_threshold_leak"


def test_notify_driver_blocks_unverifiable_numbers():
    result = notify_driver.run(
        "d-test", "Chuyen lech 999% so voi lo trinh.", source_texts=["khong lien quan"]
    )
    assert not result.ok
    assert result.blocked_by == "unverified_numbers"


def test_notify_driver_allows_numbers_present_in_source():
    result = notify_driver.run(
        "d-test", "Chuyen cua anh/chi ghi nhan lech 0.4 so voi toi uu.",
        source_texts=["deviation_pct: 0.4"],
    )
    assert result.ok
