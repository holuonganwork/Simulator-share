"""L2i inference — suy hoạt động (nghỉ/sạc/idle) từ observable gaps.

Taxonomy §3.5: inferred TÁCH RIÊNG tầng L2i, nhãn INFERRED + rule_version + confidence.
KHÔNG BAO GIỜ trình bày như đo được. Nuôi S3 F3Patterns.
"""

from __future__ import annotations

from gsm_core.features._common import date as _date, min_of_day

RULE_VERSION = "infer-v1"
DEFAULT = {
    "charge_window_min": 15,   # ± quanh swap = charging_likely
    "rest_min_gap": 20,        # gap ≥ này (không swap) = rest_likely
    "idle_min_gap": 5,         # gap [idle_min, rest_min) = idle_wait
    "max_gap_min": 180,        # gap quá dài coi như offline, bỏ
}


def _iso(date: str, minute_of_day: int) -> str:
    h, m = divmod(minute_of_day, 60)
    return f"{date}T{h:02d}:{m:02d}:00+07:00"


def _rec(driver, date, s_min, e_min, label, conf):
    return {"schema_version": "1.0.0", "driver_id": driver,
            "t_start": _iso(date, s_min), "t_end": _iso(date, e_min),
            "label": label, "confidence": round(conf, 3),
            "inference_rule_version": RULE_VERSION, "source": "INFERRED"}


def infer_activities(driver_id: str, session_date: str, l1: dict,
                     params: dict | None = None) -> list[dict]:
    p = {**DEFAULT, **(params or {})}
    trips = sorted([t for t in l1.get("trip_record", [])
                    if t["driver_id"] == driver_id and _date(t["t_request"]) == session_date],
                   key=lambda t: t["t_pickup"])
    swaps = [s for s in l1.get("swap_transaction", [])
             if s["driver_id"] == driver_id and _date(s["t"]) == session_date]

    acts: list[dict] = []
    swap_mins = [min_of_day(s["t"]) for s in swaps]

    # charging_likely quanh mỗi swap (confidence cao — có giao dịch thật)
    for sm in swap_mins:
        acts.append(_rec(driver_id, session_date, max(0, sm - p["charge_window_min"]),
                         sm + p["charge_window_min"], "charging_likely", 0.9))

    # gaps giữa các trip liên tiếp → rest_likely / idle_wait
    for a, b in zip(trips, trips[1:]):
        end = min_of_day(a["t_complete"])
        start = min_of_day(b["t_pickup"])
        gap = start - end
        if gap <= 0 or gap > p["max_gap_min"]:
            continue
        # bỏ nếu gap trùng swap (đã là charging)
        if any(end - p["charge_window_min"] <= sm <= start + p["charge_window_min"]
               for sm in swap_mins):
            continue
        if gap >= p["rest_min_gap"]:
            acts.append(_rec(driver_id, session_date, end, start, "rest_likely", 0.6))
        elif gap >= p["idle_min_gap"]:
            acts.append(_rec(driver_id, session_date, end, start, "idle_wait", 0.5))

    acts.sort(key=lambda a: a["t_start"])
    return acts
