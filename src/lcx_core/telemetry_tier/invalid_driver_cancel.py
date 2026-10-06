"""F6 - Huy sai ly do / ken cuoc (invalid_driver_cancel), FRAUDS.md muc F6.

Dau hieu: tai xe bam huy voi ly do "khach khong den" trong khi CHUA TOI diem don
(arrived_at_pickup_time rong) - khong the biet khach co den hay khong. Huy "khach
khong den" SAU KHI da toi va cho la hop le. Flag theo tai xe-tuan khi so lan >= MIN_PER_WEEK
(FRAUDS.md: huy 1-5 cuoc/tuan bi phat 1 trieu, > 5 cuoc/tuan phat 2 trieu va mat san 650k).

Doi chieu GPS doc lap (2026-09-29): co tu bao "chua toi diem don" chi la 1 truong
trong bang `trips`, ban than tai xe co the KHONG bao gio bam "da toi" du thuc te
da o ngay canh diem don - day la cach lach tinh vi hon "huy that su truoc khi
gap khach". De bat duoc dieu do, doi chieu vi tri THAT cua xe (`telemetry.lat/lng`
trong khoang thoi gian cuoc dang mo) voi `trips.pickup_lat/pickup_lng` (toa do
diem don doc lap, ghi lai luc sinh du lieu - xem vehicle_scenarios.py). Neu xe
tung o trong ban kinh GPS_CONFIRM_RADIUS_KM quanh diem don, do la bang chung GPS
doc lap manh hon co tu bao - dua vao evidence, KHONG dung de gate flag (tranh
giam recall khi thieu ping telemetry).
"""

from __future__ import annotations

import math
from datetime import datetime

import polars as pl

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "invalid_driver_cancel"
INVALID_REASON = "khach_khong_den"
MIN_PER_WEEK = 2
GPS_CONFIRM_RADIUS_KM = 0.15  # 150m - trong ban kinh nay coi la "da o diem don"

# FRAUDS.md: huy 1-5 cuoc/tuan phat 1 trieu; >5 cuoc/tuan phat 2 trieu VA mat
# san dam bao thu nhap 650k/ngay.
PENALTY_LOW_VND = 1_000_000
PENALTY_HIGH_VND = 2_000_000
PENALTY_HIGH_THRESHOLD = 5


def _week_key(iso_ts: str) -> str:
    iso = datetime.fromisoformat(iso_ts[:10]).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _gps_confirmed_trip_ids(store: EventDataStore, invalid: pl.DataFrame) -> set[str]:
    """Tra ve tap trip_id ma telemetry cho thay xe TUNG o gan pickup_lat/lng
    (doc lap voi co tu bao arrived_at_pickup_time)."""
    pickup_by_trip = {
        row["trip_id"]: (row["pickup_lat"], row["pickup_lng"])
        for row in invalid.select(["trip_id", "pickup_lat", "pickup_lng"]).iter_rows(named=True)
        if row["pickup_lat"] is not None and row["pickup_lng"] is not None
    }
    if not pickup_by_trip:
        return set()
    tel = store.telemetry.filter(pl.col("trip_id").is_in(list(pickup_by_trip)))
    confirmed: set[str] = set()
    for row in tel.select(["trip_id", "lat", "lng"]).iter_rows(named=True):
        if row["trip_id"] in confirmed:
            continue
        plat, plng = pickup_by_trip[row["trip_id"]]
        if _haversine_km(row["lat"], row["lng"], plat, plng) <= GPS_CONFIRM_RADIUS_KM:
            confirmed.add(row["trip_id"])
    return confirmed


def detect(store: EventDataStore) -> RuleResult:
    trips = store.trips.with_columns(
        pl.col("request_time").map_elements(_week_key, return_dtype=pl.Utf8).alias("week_key")
    )
    invalid = trips.filter(
        (pl.col("status") == "cancelled")
        & (pl.col("cancelled_by") == "driver")
        & (pl.col("cancel_reason") == INVALID_REASON)
        & pl.col("arrived_at_pickup_time").is_null()
    )
    gps_confirmed = _gps_confirmed_trip_ids(store, invalid)
    invalid = invalid.with_columns(pl.col("trip_id").is_in(list(gps_confirmed)).alias("gps_confirmed_near_pickup"))

    weekly = invalid.group_by(["driver_id", "week_key"]).agg(
        pl.len().alias("n_invalid_cancels"),
        pl.col("gps_confirmed_near_pickup").sum().alias("n_gps_confirmed"),
    )
    flagged = weekly.filter(pl.col("n_invalid_cancels") >= MIN_PER_WEEK)

    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=float(row["n_invalid_cancels"]),
            threshold=float(MIN_PER_WEEK),
            evidence={
                "week_key": row["week_key"],
                "n_invalid_cancels": row["n_invalid_cancels"],
                # Bang chung GPS doc lap: bao nhieu trong so cac lan huy that su
                # co telemetry xac nhan xe da o gan diem don (150m) - manh hon
                # nhieu so voi chi tin co tu bao "chua toi".
                "n_gps_confirmed_near_pickup": row["n_gps_confirmed"],
                "est_penalty_vnd": (
                    PENALTY_HIGH_VND if row["n_invalid_cancels"] > PENALTY_HIGH_THRESHOLD else PENALTY_LOW_VND
                ),
                "mat_san_thu_nhap_650k": row["n_invalid_cancels"] > PENALTY_HIGH_THRESHOLD,
            },
        )
        for row in flagged.iter_rows(named=True)
    ]
    return RuleResult(
        pattern=PATTERN,
        method=f"driver_cancel_no_show_before_arrival>={MIN_PER_WEEK}_per_week",
        threshold=float(MIN_PER_WEEK),
        n_evaluated=trips.select(["driver_id", "week_key"]).unique().height,
        flags=flags,
    )
