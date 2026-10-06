"""F4 - Su dung xe muc dich ca nhan ngoai ca (personal_use), FRAUDS.md muc F4.

Dau hieu: xe CHAY (toc do >= MOVING_KMH) NGOAI ca cam ket (declared_shifts, co dung sai
truoc/sau ca) va ODO tang. Ve depot ngay sau ca (vai km, trong dung sai) la hop le.
Flag neu km ngoai ca >= MIN_KM, hoac >= MIN_KM_OUT_OF_HANOI khi xe ra ngoai dia gioi Ha Noi.

Truy thu: km ngoai ca x 3.500 d/km.
"""

from __future__ import annotations

import polars as pl

from lcx_core.data.gsm_loader import DataSourceError
from lcx_core.event_sim import hanoi_geo
from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "personal_use"
MOVING_KMH = 10.0
GRACE_BEFORE_MIN = 15
GRACE_AFTER_MIN = 30
MIN_KM = 10.0
MIN_KM_OUT_OF_HANOI = 3.0
RATE_VND_PER_KM = 3_500
# Fallback CU - chi dung khi thieu du lieu ban do that (xem hanoi_geo.py). Duong
# THUONG: lay tu 925 o H3 Res 7 THAT duoc OSRM khao sat (toan Ha Noi sau sap
# nhap hanh chinh 2025), khong con doan bounding-box tay nhu truoc.
_HANOI_LAT_FALLBACK = (20.85, 21.25)
_HANOI_LNG_FALLBACK = (105.65, 106.05)


def _hanoi_bbox() -> tuple[tuple[float, float], tuple[float, float]]:
    try:
        return hanoi_geo.lat_range(), hanoi_geo.lng_range()
    except DataSourceError:
        return _HANOI_LAT_FALLBACK, _HANOI_LNG_FALLBACK


def detect(store: EventDataStore) -> RuleResult:
    tel = (
        store.telemetry.with_columns(
            pl.col("occurred_at").str.slice(0, 10).alias("local_date"),
            (
                pl.col("occurred_at").str.slice(11, 2).cast(pl.Int32) * 60
                + pl.col("occurred_at").str.slice(14, 2).cast(pl.Int32)
            ).alias("minute"),
        )
        .sort(["driver_id", "occurred_at"])
        .with_columns(pl.col("odometer_km").diff().over(["driver_id", "local_date"]).fill_null(0.0).alias("d_odo"))
        .join(store.declared_shifts, on="driver_id", how="inner")
    )
    off_shift = (
        (pl.col("speed_kmh") >= MOVING_KMH)
        & (
            (pl.col("minute") < pl.col("declared_start_min") - GRACE_BEFORE_MIN)
            | (pl.col("minute") > pl.col("declared_end_min") + GRACE_AFTER_MIN)
        )
    )
    hanoi_lat, hanoi_lng = _hanoi_bbox()
    outside_hanoi = ~pl.col("lat").is_between(*hanoi_lat) | ~pl.col("lng").is_between(*hanoi_lng)

    daily = tel.group_by(["driver_id", "local_date"]).agg(
        pl.col("d_odo").filter(off_shift).sum().alias("km_off_shift"),
        (off_shift & outside_hanoi).any().alias("left_hanoi"),
    )
    flagged = daily.filter(
        (pl.col("km_off_shift") >= MIN_KM) | (pl.col("left_hanoi") & (pl.col("km_off_shift") >= MIN_KM_OUT_OF_HANOI))
    )

    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=float(row["km_off_shift"]),
            threshold=MIN_KM,
            evidence={
                "local_date": row["local_date"],
                "km_off_shift": round(row["km_off_shift"], 2),
                "left_hanoi": row["left_hanoi"],
                "est_clawback_vnd": int(row["km_off_shift"] * RATE_VND_PER_KM),
            },
        )
        for row in flagged.iter_rows(named=True)
    ]
    return RuleResult(
        pattern=PATTERN,
        method=f"km_moving_outside_declared_shift>={MIN_KM}",
        threshold=MIN_KM,
        n_evaluated=daily.height,
        flags=flags,
    )
