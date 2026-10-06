"""T-038 C0 — schema registry tests (TDD: viết trước, đỏ → xanh).

Mọi entity trong spec core-data-schema §1.2–1.5 + advisor I/O §2–3 phải có JSON Schema
parse được, validate đúng record hợp lệ, và bắt đúng record hỏng.
"""

from pathlib import Path

import pytest

from gsm_core.schema_registry import SchemaRegistry, ALL_ENTITIES

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def reg():
    return SchemaRegistry(ROOT / "schemas")


# Danh sách entity BẮT BUỘC theo spec (23 schema)
EXPECTED = {
    # L0
    "policy_bundle", "driver_profile", "station_registry", "zone_map", "service_catalog",
    # L1
    "app_event", "trip_record", "gps_ping", "swap_transaction", "payout_ledger",
    "policy_change_event",
    # L2
    "supply_field", "demand_field", "station_state", "driver_day_state",
    # L2i
    "inferred_activity",
    # L3 (+ PI-4b: view cho S5 khoán tuần, S6 mission knapsack)
    "shift_plan_input", "bonus_gap_input", "session_summary_input", "allocation_input",
    "weekly_khoan_input", "mission_select_input", "idle_reduction_input",
    "penalty_explain_input", "anomaly_alert_input",
    "market_state_view",   # Cycle V: vá lỗ view T-045a emit version mà không registry nào biết
    # advisor
    "advice_request", "solver_report", "composed_advice",
    # Cycle W (ĐA-05): event log lifecycle append-only
    "advice_lifecycle_event",
    # AdviceCheckpoint shadow presentation lifecycle — không overload decision_id legacy
    "advice_artifact", "advice_checkpoint", "advice_checkpoint_event",
    "composite_episode",
    "agent_presentation_input", "agent_presentation_output",
    "agent_explanation_input", "agent_explanation_output",
}

# L1-real (re-ground về bảng thật gsm-data-prod — UPDATE-033/034, PI-1)
EXPECTED_L1R = {
    "driver_statistic_daily", "driver_online_hours_sap_id", "driver_orders_rush_hours",
    "driver_bike_stoppoints", "kpi_driver_platform_calculator_gbq", "driver_income_daily",
    "trips", "public_driver_hex_tracking", "public_mission", "public_mission_earn_history",
    "public_user_mission_progress", "driver_penalization_ATA", "public_frauds",
}


def test_all_entities_registered():
    assert set(ALL_ENTITIES) == EXPECTED | EXPECTED_L1R


# Cycle V (2026-07-28, gỡ B-02): PIN VERSION TƯỜNG MINH thay cho "mọi thứ == 1.0.0".
# Bump có chủ ý ⇒ sửa map này + CHANGELOG + snapshot @old + upcaster (quy trình schemas/README).
# `shift_plan_input` 1.1.0 là bump THẬT đầu tiên (Cycle R thêm 2 trường rest mà const chưa đổi
# — đúng anti-pattern B-02; nay trả nợ).
LATEST_VERSIONS = {
    "composite_episode": "1.2.0",
    "shift_plan_input": "1.1.0",
    # D-ADV-04: thêm nhãn CÁCH SUY MẪU SỐ của historical_points_per_hour (measured vs xấp xỉ).
    "bonus_gap_input": "1.1.0",
    # Cycle 8 (UPDATE-198): candidates[].tier_gap_points + tier_weight — additive-optional.
    # S4 biết KHOẢNG CÁCH TỚI MỐC thưởng để ưu tiên ô tốt cho người sát mốc.
    "allocation_input": "1.1.0",
    "policy_bundle": "1.1.0",
    # 2026-09: cuốc hủy (status=cancelled) + lý do hủy — additive-optional, phục vụ F1/F6 Taxi Cơ Hữu.
    "trips": "1.1.0",
    "advice_checkpoint": "1.3.0",
    "advice_checkpoint_event": "1.1.0",
    "advice_artifact": "1.1.0",
    "agent_presentation_input": "1.1.0",
}   # entity vắng mặt = "1.0.0"


def test_all_schemas_load_and_have_version(reg):
    for e in ALL_ENTITIES:
        s = reg.schema(e)
        assert s["$id"], e
        assert reg.schema_version(e) == LATEST_VERSIONS.get(e, "1.0.0"), e


VALID = {
    "app_event": {
        "schema_version": "1.0.0", "event_id": "ev-1", "driver_id": "d-1",
        "t": "2026-07-23T08:00:00+07:00", "kind": "go_online", "source": "MOCK",
    },
    "trip_record": {
        "schema_version": "1.0.0", "order_id": "o-1", "driver_id": "d-1",
        "service_type": "car", "t_request": "2026-07-23T08:00:00+07:00",
        "t_assign": "2026-07-23T08:01:00+07:00", "t_pickup": "2026-07-23T08:05:00+07:00",
        "t_complete": "2026-07-23T08:20:00+07:00",
        "pickup": {"lat": 21.01, "lon": 105.82, "h3": "892ab13a677ffff"},
        "drop": {"lat": 21.02, "lon": 105.83, "h3": "892ab13a67bffff"},
        "dist_km": 3.2, "gross_vnd": 22000, "source": "MOCK",
    },
    "payout_ledger": {
        "schema_version": "1.0.0", "entry_id": "p-1", "driver_id": "d-1",
        "t": "2026-07-23T08:20:00+07:00", "kind": "trip_payout", "amount_vnd": 16500,
        "basis": {"trip_id": "o-1", "policy_bundle_version": "sim-policy-v0"},
        "gross_vnd": 22000, "source": "MOCK",
    },
    "solver_report": {
        "schema_version": "1.0.0", "solver": "bonus_feasibility",
        "problem_digest": "Gap 40 điểm tới mốc 160; quỹ 3h",
        "inputs_used": [{"view_id": "bonus_gap_input:d-1", "version": "1.0.0",
                         "freshness": "2026-07-23T08:00:00+07:00"}],
        "solution": {"trips_needed": 8, "hours_needed": 2.5, "feasible": True},
        "numbers": [{"value": 115000, "unit": "vnd", "source": "policy_v:sim-policy-v0"}],
        "sensitivity": [], "confidence": 0.9, "caveats": [], "infeasible_reason": None,
    },
}


@pytest.mark.parametrize("entity", sorted(VALID))
def test_valid_records_pass(reg, entity):
    assert reg.validate(entity, VALID[entity]) == []


def test_invalid_kind_rejected(reg):
    bad = dict(VALID["app_event"], kind="teleport_home")  # ngoài enum action-space
    errs = reg.validate("app_event", bad)
    assert errs, "kind ngoài enum phải fail"


def test_negative_money_rejected(reg):
    bad = dict(VALID["trip_record"], gross_vnd=-5000)
    assert reg.validate("trip_record", bad), "tiền âm phải fail"


def test_missing_source_label_rejected(reg):
    bad = {k: v for k, v in VALID["trip_record"].items() if k != "source"}
    assert reg.validate("trip_record", bad), "thiếu nhãn source (MOCK/REAL) phải fail"


def test_latent_fields_absent_from_l1():
    """Taxonomy §3.5: latent (meals_taken, fatigue, belief, patience) KHÔNG được có trong L1."""
    import json
    for name in ("app_event", "trip_record", "gps_ping", "swap_transaction", "payout_ledger"):
        raw = json.dumps(json.load(open(ROOT / "schemas" / "l1" / f"{name}.schema.json", encoding="utf-8")))
        for latent in ("meals_taken", "fatigue", "belief", "patience", "demand_prior"):
            assert latent not in raw, f"latent '{latent}' lọt vào L1 {name}"


def test_inferred_activity_requires_rule_version(reg):
    rec = {"schema_version": "1.0.0", "driver_id": "d-1",
           "t_start": "2026-07-23T12:00:00+07:00", "t_end": "2026-07-23T12:40:00+07:00",
           "label": "rest_likely", "confidence": 0.7, "source": "MOCK"}
    errs = reg.validate("inferred_activity", rec)
    assert errs, "thiếu inference_rule_version phải fail (INFERRED bắt buộc có rule version)"
    rec["inference_rule_version"] = "infer-v1"
    assert reg.validate("inferred_activity", rec) == []
