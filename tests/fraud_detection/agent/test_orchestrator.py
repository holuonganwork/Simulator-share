from __future__ import annotations

from lcx_core.agent import orchestrator


def test_investigate_runs_fixed_pipeline_for_any_driver(store):
    driver_id = store.table("driver_statistic_daily")["driver_id"][0]
    bundle = orchestrator.investigate(store, driver_id)

    assert set(bundle.steps) == {
        "risk_profile",
        "location_integrity",
        "incentive_activity",
        "device_account_graph",
    }
    # device_account_graph LUON bi chan (Tier C chua co du lieu) - pipeline van
    # chay het cac buoc khac, khong crash vi mot buoc bi chan.
    assert bundle.step("device_account_graph").blocked_by == "missing_data_tier_c"
    assert bundle.step("risk_profile").ok


def test_manager_summary_mentions_real_sanction_text(store):
    driver_id = store.table("driver_statistic_daily")["driver_id"][0]
    bundle = orchestrator.investigate(store, driver_id)
    summary = orchestrator.draft_manager_summary(bundle)
    assert "tru 100% thuong tuan" in summary
    assert "tam khoa tai khoan 7 ngay" in summary


def test_driver_draft_never_leaks_threshold_keywords(store):
    driver_id = store.table("driver_statistic_daily")["driver_id"][0]
    bundle = orchestrator.investigate(store, driver_id)
    draft = orchestrator.draft_driver_explanation_request(bundle, "route_deviation")
    lowered = draft.lower()
    for banned in ["percentile", "threshold", "nguong", "z-score"]:
        assert banned not in lowered
