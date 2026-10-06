"""Phat hien xe "ma" (ghost_vehicle) - hang muc 6 "Chien luoc lam giau du lieu"
(phan LOG/dang ky xe, khong bao gom mo rong ban do tram). Doi chieu
vehicle_vin cua tung phien sac voi vehicle_registry - VIN KHONG khop bat ky
dong nao la dau hieu ro rang nhat (khong can percentile, day la kiem tra toan
ven du lieu (data-integrity), khong phai bat thuong thong ke).
"""

from __future__ import annotations

import polars as pl

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "ghost_vehicle"


def detect(store: EventDataStore) -> RuleResult:
    known_vins = set(store.vehicle_registry["vin"].drop_nulls().to_list())
    df = store.charging_sessions.filter(pl.col("vehicle_vin").is_not_null())

    flags: list[RuleFlag] = []
    for row in df.iter_rows(named=True):
        if row["vehicle_vin"] not in known_vins:
            flags.append(
                RuleFlag(
                    pattern=PATTERN,
                    driver_id=row["driver_id"],
                    signal_value=1.0,
                    threshold=0.0,
                    evidence={
                        "session_id": row["session_id"],
                        "vehicle_vin": row["vehicle_vin"],
                        "station_id": row["station_id"],
                    },
                )
            )

    return RuleResult(
        pattern=PATTERN,
        method="vin_not_in_vehicle_registry",
        threshold=0.0,
        n_evaluated=df.height,
        flags=flags,
    )
