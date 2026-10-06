"""F1 - Cho khach khong tao cuoc (off_app_pickup), FRAUDS.md muc F1.

Dau hieu: cuoc bi huy SAU KHI tai xe da toi diem don (arrived_at_pickup_time co
gia tri), nhung telemetry TCU ngay sau do cho thay xe van chay deu (>= MOVING_KMH)
voi ghe sau CO nguoi. Cuoc huy hop le (khach khong den) thi xe dung yen, ghe trong.

Truy thu: km ODO xe chay co khach sau khi huy x don gia cuoc TB (13.000 d/km).
Nguong cung (khong percentile): so ping (1 phut) co khach + xe chay >= MIN_MOVING_PINGS.

Bo sung camera cabin (2026-09-29): noi them `camera_events` theo (driver_id +
cua so thoi gian) - KHONG theo trip_id, vi camera_events hien luon ghi
trip_id=null (chua noi voi trip nao ca) va ban than cac ping "chay co khach"
o day CO CHU DICH khong gan trip_id (di ngoai app). Camera_events rat thua
(hau het cuoc khong co dong nao) nen chi dung LAM GIAU evidence, KHONG dung de
gate flag - neu bat buoc phai co moi flag thi recall se tut manh.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "off_app_pickup"
WINDOW_MIN = 45
MOVING_KMH = 10.0
MIN_MOVING_PINGS = 5
RATE_VND_PER_KM = 13_000


def _plus_minutes(iso_ts: str, minutes: int) -> str:
    return (datetime.fromisoformat(iso_ts) + timedelta(minutes=minutes)).isoformat()


def detect(store: EventDataStore) -> RuleResult:
    cancelled = store.trips.filter(
        (pl.col("status") == "cancelled") & pl.col("arrived_at_pickup_time").is_not_null()
    ).with_columns(
        pl.col("arrived_at_pickup_time").map_elements(lambda s: _plus_minutes(s, WINDOW_MIN), return_dtype=pl.Utf8)
        .alias("window_end")
    )
    # chi tinh ping KHONG gan cuoc nao tren app (trip_id rong): khach di xe ngoai app.
    # Ping co trip_id la mot cuoc that khac (cuoc ke tiep) - khong phai gian lan.
    moving = store.telemetry.filter(
        pl.col("seat_any_passenger") & (pl.col("speed_kmh") >= MOVING_KMH) & pl.col("trip_id").is_null()
    )

    # Vet di chuyen (bo sung 2026-09-29): giu lai toa do dau/cuoi cua doan
    # "chay co khach ngoai app" de nguoi doi soat thay duoc tren ban do, khong
    # chi mot con so km tru tuong.
    joined = (
        cancelled.select(["trip_id", "driver_id", "arrived_at_pickup_time", "window_end"])
        .join(moving.select(["driver_id", "occurred_at", "odometer_km", "lat", "lng"]), on="driver_id", how="inner")
        .filter(
            (pl.col("occurred_at") >= pl.col("arrived_at_pickup_time")) & (pl.col("occurred_at") <= pl.col("window_end"))
        )
        .sort("occurred_at")
        .group_by(["trip_id", "driver_id"], maintain_order=True)
        .agg(
            pl.len().alias("n_moving_pings"),
            (pl.col("odometer_km").max() - pl.col("odometer_km").min()).alias("km_moved"),
            pl.col("lat").first().alias("lat_start"), pl.col("lng").first().alias("lng_start"),
            pl.col("lat").last().alias("lat_end"), pl.col("lng").last().alias("lng_end"),
        )
        .filter(pl.col("n_moving_pings") >= MIN_MOVING_PINGS)
    )

    # Camera cabin (bo sung, xem docstring): noi theo driver_id + cua so thoi
    # gian, khong theo trip_id (camera_events chua co trip_id). Rat thua nen
    # hau het se ra null - chi la bang chung BO SUNG khi co san.
    cam = store.camera_events.select(["driver_id", "occurred_at", "camera_event_type", "confidence"])
    cancelled_win = cancelled.select(["trip_id", "driver_id", "arrived_at_pickup_time", "window_end"])
    cam_by_trip: dict[str, list[str]] = {}
    if cam.height:
        cam_joined = cancelled_win.join(cam, on="driver_id", how="inner").filter(
            (pl.col("occurred_at") >= pl.col("arrived_at_pickup_time")) & (pl.col("occurred_at") <= pl.col("window_end"))
        )
        for row in cam_joined.iter_rows(named=True):
            cam_by_trip.setdefault(row["trip_id"], []).append(row["camera_event_type"])

    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=float(row["n_moving_pings"]),
            threshold=float(MIN_MOVING_PINGS),
            evidence={
                "trip_id": row["trip_id"],
                "moving_minutes_with_passenger": row["n_moving_pings"],
                "km_moved": round(row["km_moved"], 2),
                "est_clawback_vnd": int(row["km_moved"] * RATE_VND_PER_KM),
                "route_start": [row["lat_start"], row["lng_start"]],
                "route_end": [row["lat_end"], row["lng_end"]],
                "camera_corroboration": cam_by_trip.get(row["trip_id"]),
            },
        )
        for row in joined.iter_rows(named=True)
    ]
    return RuleResult(
        pattern=PATTERN,
        method=f"cancelled_after_arrival_and_moving_with_passenger>={MIN_MOVING_PINGS}pings",
        threshold=float(MIN_MOVING_PINGS),
        n_evaluated=cancelled.height,
        flags=flags,
    )
