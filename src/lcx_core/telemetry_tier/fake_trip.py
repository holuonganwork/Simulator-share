"""F5 - Tao cuoc xe ao (fake_trip), FRAUDS.md muc F5.

Dau hieu (bang chung "chi mang" cua FRAUDS.md): app bao cuoc HOAN THANH co cuoc phi
(billed_distance_km >= MIN_BILLED_KM) nhung TCU trong khoang [pickup, complete] cho thay
xe dung yen (toc do toi da < STILL_KMH, ODO gan nhu khong doi) va ghe sau khong co
nguoi. Flag theo tai xe-ngay khi co >= MIN_FAKE_TRIPS cuoc nhu vay - muc tieu la sàn thu
nhap 650k/ngay (can >= 6 cuoc) va cay KPI; mot cuoc dung yen le te co the la loi thiet bi.

Truy thu: tong cuoc phi cac cuoc ao (+ tien bu san bi truc loi, tinh o tang chinh sach).

Bo sung camera cabin (2026-09-29): noi them `camera_events` theo (driver_id +
cua so [pickup_time, complete_time]) - khong theo trip_id (camera_events chua
gan trip_id). Dung LAM GIAU evidence (dem so cuoc ao co camera xac nhan them),
KHONG gate flag - camera_events rat thua nen bat buoc se lam sap recall.
"""

from __future__ import annotations

import polars as pl

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "fake_trip"
MIN_BILLED_KM = 0.8
STILL_KMH = 3.0
MAX_ODO_RANGE_KM = 0.3
MIN_PINGS_IN_TRIP = 2
MIN_FAKE_TRIPS = 2
FARE_PER_KM_VND = 13_000


def detect(store: EventDataStore) -> RuleResult:
    completed = store.trips.filter(
        (pl.col("status") == "completed") & pl.col("pickup_time").is_not_null() & pl.col("complete_time").is_not_null()
    )
    per_trip = (
        completed.select(["trip_id", "driver_id", "request_time", "pickup_time", "complete_time", "billed_distance_km"])
        .join(
            store.telemetry.select(["trip_id", "occurred_at", "speed_kmh", "odometer_km", "seat_any_passenger"]),
            on="trip_id",
            how="inner",
        )
        .filter((pl.col("occurred_at") >= pl.col("pickup_time")) & (pl.col("occurred_at") <= pl.col("complete_time")))
        .group_by(["trip_id", "driver_id", "request_time", "billed_distance_km"])
        .agg(
            pl.len().alias("n_pings"),
            pl.col("speed_kmh").max().alias("max_speed"),
            (pl.col("odometer_km").max() - pl.col("odometer_km").min()).alias("odo_range"),
            pl.col("seat_any_passenger").any().alias("any_passenger"),
        )
        .filter(
            (pl.col("n_pings") >= MIN_PINGS_IN_TRIP)
            & (pl.col("max_speed") < STILL_KMH)
            & (pl.col("odo_range") < MAX_ODO_RANGE_KM)
            & ~pl.col("any_passenger")
            & (pl.col("billed_distance_km") >= MIN_BILLED_KM)
        )
        .with_columns(pl.col("request_time").str.slice(0, 10).alias("local_date"))
    )

    # Camera cabin: noi theo driver_id + cua so [pickup_time, complete_time] cua
    # tung cuoc ao (can lay lai pickup_time/complete_time tu completed vi da bi
    # gop mat o per_trip).
    cam_confirmed_trip_ids: set[str] = set()
    if store.camera_events.height and per_trip.height:
        windows = per_trip.select(["trip_id", "driver_id"]).join(
            completed.select(["trip_id", "pickup_time", "complete_time"]), on="trip_id", how="left"
        )
        cam_joined = windows.join(
            store.camera_events.select(["driver_id", "occurred_at"]), on="driver_id", how="inner"
        ).filter((pl.col("occurred_at") >= pl.col("pickup_time")) & (pl.col("occurred_at") <= pl.col("complete_time")))
        cam_confirmed_trip_ids = set(cam_joined["trip_id"].unique().to_list())

    per_trip = per_trip.with_columns(pl.col("trip_id").is_in(list(cam_confirmed_trip_ids)).alias("camera_confirmed"))
    daily = (
        per_trip.group_by(["driver_id", "local_date"])
        .agg(
            pl.len().alias("n_fake_trips"),
            pl.col("billed_distance_km").sum().alias("billed_km"),
            pl.col("camera_confirmed").sum().alias("n_camera_confirmed"),
        )
        .filter(pl.col("n_fake_trips") >= MIN_FAKE_TRIPS)
    )

    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=float(row["n_fake_trips"]),
            threshold=float(MIN_FAKE_TRIPS),
            evidence={
                "local_date": row["local_date"],
                "n_fake_trips": row["n_fake_trips"],
                "est_fake_revenue_vnd": int(row["billed_km"] * FARE_PER_KM_VND),
                "n_camera_confirmed": row["n_camera_confirmed"],
            },
        )
        for row in daily.iter_rows(named=True)
    ]
    return RuleResult(
        pattern=PATTERN,
        method=f"completed_trips_with_vehicle_stationary>={MIN_FAKE_TRIPS}_per_day",
        threshold=float(MIN_FAKE_TRIPS),
        n_evaluated=completed.height,
        flags=flags,
    )
