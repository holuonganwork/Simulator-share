"""Derive L3 `session_summary_input` — input S3 F3Patterns (sau ca).

trips + inferred_activities (L2i) + payout_breakdown (TÁCH gross/payout; net=null
khi chưa known costs) + day_state_end. Nhãn source MOCK.
"""

from __future__ import annotations

from gsm_core.policy import PolicyBundle
from gsm_core.features._common import date as _date, points_on_date
from gsm_core.features.infer_activity import infer_activities


def derive_session_summary_input(driver_id: str, session_date: str, l1: dict,
                                 policy: PolicyBundle) -> dict:
    trips = [t for t in l1.get("trip_record", [])
             if t["driver_id"] == driver_id and _date(t["t_request"]) == session_date]
    events = [e for e in l1.get("app_event", [])
              if e["driver_id"] == driver_id and _date(e["t"]) == session_date]
    ledger = [p for p in l1.get("payout_ledger", [])
              if p["driver_id"] == driver_id and _date(p["t"]) == session_date]

    inferred = infer_activities(driver_id, session_date, l1)

    # payout_breakdown: gross Σ từ trip; driver_payout Σ từ ledger (trip_payout+bonus)
    gross = sum(int(t["gross_vnd"]) for t in trips)
    payout = sum(int(p["amount_vnd"]) for p in ledger
                 if p["kind"] in ("trip_payout", "day_bonus", "week_bonus"))

    # day_state_end: điểm + tỷ lệ cuối ca (observable)
    points = points_on_date(trips, driver_id, session_date, policy)
    acc = sum(1 for e in events if e["kind"] == "accept")
    dec = sum(1 for e in events if e["kind"] == "decline")
    comp = sum(1 for e in events if e["kind"] == "complete")
    acceptance = round(acc / (acc + dec), 4) if (acc + dec) else 1.0
    completion = round(comp / acc, 4) if acc else 1.0

    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "session_date": session_date,
        "trips": trips, "inferred_activities": inferred,
        "payout_breakdown": {
            "gross_vnd": gross, "driver_payout_vnd": payout,
            "estimated_net_vnd": None,       # chưa đủ known costs (thuê/điện)
            "net_definition_version": None,
        },
        "day_state_end": {"points": points, "acceptance_rate": acceptance,
                          "completion_rate": completion},
        "view_version": "1.0.0", "source": "MOCK",
    }
