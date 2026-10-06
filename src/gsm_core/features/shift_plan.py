"""Derive L3 `shift_plan_input` từ L1 records — input cho solver S2 ShiftDP.

demand_forecast = historical hour-shape từ N ngày TRƯỚC t_now (NO future-leak:
không đọc trip realized của ngày đang giải). points_now suy từ trip+policy.
Bucket 30ph, timing-only (cell_cluster="ALL"). Nhãn source MOCK.
"""

from __future__ import annotations

import math
from collections import defaultdict

from gsm_core.policy import PolicyBundle
from gsm_core.features._common import (hour as _hour, date as _date, min_of_day,
                                        points_on_date)

BUCKET_MIN = 30


def _iso_bucket(date: str, minute_of_day: int) -> str:
    h, m = divmod(minute_of_day, 60)
    return f"{date}T{h:02d}:{m:02d}:00+07:00"


def derive_shift_plan_input(driver_id: str, t_now: str, l1: dict, policy: PolicyBundle,
                            shift_window: list[int], history_days: int = 7) -> dict:
    today = _date(t_now)
    trips = l1.get("trip_record", [])
    now_min = min_of_day(t_now)

    points_now = points_on_date(trips, driver_id, today, policy)
    buckets_remaining = max(0, math.ceil((shift_window[1] - now_min) / BUCKET_MIN))

    # soc: bản đầu chưa suy từ swap chi tiết → None (DP dùng default đầy). Hook sau.
    soc_pct = None

    # historical hour-shape: trung bình số cuốc/GIỜ theo giờ, từ ngày TRƯỚC today
    hist_days = sorted({_date(t["t_request"]) for t in trips
                        if t["driver_id"] == driver_id and _date(t["t_request"]) < today})
    hist_days = hist_days[-history_days:]
    by_hour: dict[int, list[int]] = defaultdict(list)
    for d in hist_days:
        cnt: dict[int, int] = defaultdict(int)
        for t in trips:
            if t["driver_id"] == driver_id and _date(t["t_request"]) == d:
                cnt[_hour(t["t_request"])] += 1
        for h in range(24):
            by_hour[h].append(cnt.get(h, 0))
    # mean cuốc/giờ → /2 cho bucket 30ph
    hour_mean = {h: (sum(v) / len(v) if v else 0.0) for h, v in by_hour.items()}

    # forecast cho các bucket còn lại của ca
    forecast = []
    for i in range(buckets_remaining):
        bmin = now_min + i * BUCKET_MIN
        if bmin >= shift_window[1]:
            break
        h = (bmin // 60) % 24
        forecast.append({
            "bucket": _iso_bucket(today, bmin % (24 * 60)),
            "cell_cluster": "ALL",
            "expected_orders": round(hour_mean.get(h, 0.0) / (60 / BUCKET_MIN), 3),
        })

    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "t_now": t_now,
        "buckets_remaining": buckets_remaining, "soc_pct": soc_pct,
        "points_now": points_now, "demand_forecast": forecast,
        "policy_bundle_version": policy.version, "view_version": "1.0.0", "source": "MOCK",
    }
