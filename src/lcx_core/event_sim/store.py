"""EventDataStore - noi luu 7 bang tong hop cua Khoi 1 (hang muc 2/4/5/6-log
"Chien luoc lam giau du lieu"): events (trip_lifecycle_event mo rong),
camera_events, charging_sessions, vehicle_registry, va 3 bang phuc vu F1/F3/F4/F5/F6
cua Taxi Co Huu: trips (co cuoc huy + ly do), telemetry (TCU: ODO/toc do/ghe/pin),
declared_shifts (ca cam ket).

Khac voi GsmDataStore (doc file that tu GSM_SIMULATOR), du lieu o day KHONG co
"ban goc that" de lam giau - toan bo duoc SINH RA boi
`lcx_core.event_sim.simulator` (quan the binh thuong + don vi gian lan tiem co
tham so, cung triet ly voi `lcx_core.fraud_injector`). Cac detector trong
`lcx_core.telemetry_tier` chi doc qua EventDataStore, khong bao gio doc thang
tu simulator - giu dung nguyen tac "detector khong biet gi ve injector".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

EVENTS_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.Utf8,
    "driver_id": pl.Utf8,
    "event_type": pl.Utf8,
    "occurred_at": pl.Utf8,
    "location_enabled": pl.Boolean,
    "source": pl.Utf8,
}

CAMERA_EVENTS_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.Utf8,
    "driver_id": pl.Utf8,
    "trip_id": pl.Utf8,
    "occurred_at": pl.Utf8,
    "camera_event_type": pl.Utf8,
    "detected_passenger_count": pl.Int64,
    "seat_sensor_passenger_count": pl.Int64,
    "confidence": pl.Float64,
    "source": pl.Utf8,
}

CHARGING_SESSIONS_SCHEMA: dict[str, pl.DataType] = {
    "session_id": pl.Utf8,
    "driver_id": pl.Utf8,
    "vehicle_vin": pl.Utf8,
    "station_id": pl.Utf8,
    "started_at": pl.Utf8,
    "ended_at": pl.Utf8,
    "soc_before_pct": pl.Float64,
    "soc_after_pct": pl.Float64,
    "duration_seconds": pl.Int64,
    "cost_vnd": pl.Int64,
    "status": pl.Utf8,
    "source": pl.Utf8,
}

VEHICLE_REGISTRY_SCHEMA: dict[str, pl.DataType] = {
    "vehicle_id": pl.Utf8,
    "vin": pl.Utf8,
    "vehicle_type": pl.Utf8,
    "model": pl.Utf8,
    "battery_capacity_kwh": pl.Float64,
    "assigned_driver_id": pl.Utf8,
    "registered_at": pl.Utf8,
    "status": pl.Utf8,
}

TRIPS_SCHEMA: dict[str, pl.DataType] = {
    "trip_id": pl.Utf8,
    "driver_id": pl.Utf8,
    "vin": pl.Utf8,
    "status": pl.Utf8,  # completed | cancelled
    "request_time": pl.Utf8,
    "assign_time": pl.Utf8,
    "arrived_at_pickup_time": pl.Utf8,  # null neu tai xe chua toi diem don
    "pickup_time": pl.Utf8,  # khach len xe; null neu cuoc bi huy
    "complete_time": pl.Utf8,
    "cancel_reason": pl.Utf8,
    "cancelled_by": pl.Utf8,  # customer | driver
    "billed_distance_km": pl.Float64,
    "route_km_optimal": pl.Float64,
    # Vi tri THAT diem don (toa do xe luc ket thuc doan "di toi don" - xem
    # vehicle_scenarios.py::_Recorder.trip) - doc lap voi co tu bao
    # arrived_at_pickup_time, dung de doi chieu GPS that cho F6.
    "pickup_lat": pl.Float64,
    "pickup_lng": pl.Float64,
    "source": pl.Utf8,
}

TELEMETRY_SCHEMA: dict[str, pl.DataType] = {
    "ping_id": pl.Utf8,
    "driver_id": pl.Utf8,
    "vin": pl.Utf8,
    "trip_id": pl.Utf8,
    "occurred_at": pl.Utf8,
    "lat": pl.Float64,
    "lng": pl.Float64,
    "speed_kmh": pl.Float64,
    "odometer_km": pl.Float64,
    "battery_pct": pl.Float64,
    "seat_any_passenger": pl.Boolean,
    "app_status": pl.Utf8,  # online | offline
    "source": pl.Utf8,
}

SHIFTS_SCHEMA: dict[str, pl.DataType] = {
    "driver_id": pl.Utf8,
    "declared_start_min": pl.Int64,  # phut trong ngay (0-1439)
    "declared_end_min": pl.Int64,
}

# Fallback CU (3 tram gia dinh) - chi dung khi thieu du lieu ban do that
# (`research/simulation/data/batt_hanoi.json`, vd moi truong test/CI offline).
# Duong THUONG: `simulator.py` uu tien `hanoi_geo.station_ids()` (399 tram
# sac/doi pin THAT tu OSM), chi roi ve day khi `DataSourceError`.
STATION_IDS = ["station-dd-01", "station-dd-02", "station-dd-03"]


def _empty(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


@dataclass(frozen=True)
class EventDataStore:
    """7 bang tong hop, san sang cho `lcx_core.telemetry_tier.REGISTRY[...].detect()`."""

    events: pl.DataFrame
    camera_events: pl.DataFrame
    charging_sessions: pl.DataFrame
    vehicle_registry: pl.DataFrame
    trips: pl.DataFrame = field(default_factory=lambda: _empty(TRIPS_SCHEMA))
    telemetry: pl.DataFrame = field(default_factory=lambda: _empty(TELEMETRY_SCHEMA))
    declared_shifts: pl.DataFrame = field(default_factory=lambda: _empty(SHIFTS_SCHEMA))

    def tables(self) -> dict[str, pl.DataFrame]:
        return {
            "events": self.events,
            "camera_events": self.camera_events,
            "charging_sessions": self.charging_sessions,
            "vehicle_registry": self.vehicle_registry,
            "trips": self.trips,
            "telemetry": self.telemetry,
            "declared_shifts": self.declared_shifts,
        }

    def describe(self) -> str:
        return "\n".join(f"  - {name}: {df.height} dong" for name, df in self.tables().items())

    def save(self, out_dir: Path) -> dict[str, int]:
        """Ghi 7 bang ra parquet (vd data/derived/). Tra ve {ten_bang: so_dong}."""
        out_dir.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for name, df in self.tables().items():
            df.write_parquet(out_dir / f"{name}.parquet")
            counts[name] = df.height
        return counts
