"""Can thiep cam bien "Mat than" (camera_intervention) - hang muc 2 "Chien luoc
lam giau du lieu". Truoc day KHONG co (dang "Thieu han" trong tai lieu chien
luoc goc, muc 02) - camera_event.schema.json la nguon du lieu MOI HOAN TOAN.

Tin hieu: so su kien camera DO TIN CAY CAO (>= HIGH_CONFIDENCE, AI on-device
chac chan) trong MOT ngay cua MOT driver. Dung NGUONG CUNG (MIN_EVENTS_SUSPICIOUS)
thay vi percentile: mot su kien do tin cay cao DON LE la nhieu (mismatch/dem
sai tam thoi), nhung TU 2 su kien tro len trong CUNG 1 ngay la mot MAU HINH
lap lai, khong con la nhieu ngau nhien - cung tinh than "hard cap" voi
tier_a/gps_distance_inflation.py va telemetry_tier/battery_swap_validation.py.
Percentile KHONG phu hop o day vi quy mo quan the that (driver-ngay co xe hoi)
nho hon nhieu so voi Tier A (hang tram thay vi hang tram nghin dong), de bi
chinh du lieu tiem lam lech nguong (da gap loi nay khi thu nghiem - xem
docs/injector-eval-results.md muc route_deviation cho mot truong hop tuong tu).
"""

from __future__ import annotations

from datetime import datetime

import polars as pl

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "camera_intervention"
HIGH_CONFIDENCE = 0.80
MIN_EVENTS_SUSPICIOUS = 2  # >=2 su kien do tin cay cao / ngay -> flag


def _local_date(iso_ts: str) -> str:
    return datetime.fromisoformat(iso_ts).date().isoformat()


def detect(store: EventDataStore) -> RuleResult:
    car_ids = (
        store.vehicle_registry.filter(pl.col("vehicle_type") == "car")["assigned_driver_id"]
        .drop_nulls()
        .to_list()
    )
    workdays = (
        store.events.filter((pl.col("event_type") == "went_online") & pl.col("driver_id").is_in(car_ids))
        .with_columns(pl.col("occurred_at").map_elements(_local_date, return_dtype=pl.Utf8).alias("local_date"))
        .select(["driver_id", "local_date"])
        .unique()
    )

    high_conf = store.camera_events.filter(pl.col("confidence") >= HIGH_CONFIDENCE).with_columns(
        pl.col("occurred_at").map_elements(_local_date, return_dtype=pl.Utf8).alias("local_date")
    )
    counts = high_conf.group_by(["driver_id", "local_date"]).agg(pl.len().alias("n_high_confidence_events"))

    # Hop nhat voi CA ngay co su kien (ngay bi tiem gian lan co the roi vao
    # mot ngay khong co trong `workdays` that, vi day sinh du lieu doc lap) -
    # tranh mat don vi da tiem khoi quan the danh gia.
    population = pl.concat([workdays, counts.select(["driver_id", "local_date"])], how="vertical").unique()
    daily = population.join(counts, on=["driver_id", "local_date"], how="left").with_columns(
        pl.col("n_high_confidence_events").fill_null(0)
    )

    flagged = daily.filter(pl.col("n_high_confidence_events") >= MIN_EVENTS_SUSPICIOUS)
    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=row["n_high_confidence_events"],
            threshold=float(MIN_EVENTS_SUSPICIOUS),
            evidence={
                "local_date": row["local_date"],
                "n_high_confidence_events": row["n_high_confidence_events"],
            },
        )
        for row in flagged.iter_rows(named=True)
    ]

    return RuleResult(
        pattern=PATTERN,
        method=f"hard_count>={MIN_EVENTS_SUSPICIOUS}_daily_confidence>={HIGH_CONFIDENCE}",
        threshold=float(MIN_EVENTS_SUSPICIOUS),
        n_evaluated=daily.height,
        flags=flags,
    )
