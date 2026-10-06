"""Sinh du lieu cho Khoi 1 cua "Chien luoc lam giau du lieu" (hang muc 2 camera,
4 action/state, 6-log charging_session/vehicle_registry). Khac voi
`lcx_core.fraud_injector` (LAM GIAU du lieu THAT co san cua GSM_SIMULATOR), o
day KHONG co du lieu that de lam giau - telemetry_mock (Buoc 1-3) la mot demo
ingest THOI GIAN THUC (vai chuc giay), khong sinh ra du lieu lich su nhieu
ngay. Module nay SINH MOI mot quan the driver-ngay tong hop (binh thuong +
mot phan gian lan co tham so), dung CHUNG mot may trang thai/logic hop dong voi
`telemetry_mock.drivers` (went_online/went_offline, app_foreground/background,
driver_break, camera_event, charging_session) nhung o dang BATCH (khong qua
HTTP/sleep that) de co the tao du du lieu danh gia Precision/Recall - cung
triet ly voi Pha A (fraud_injector), chi khac la "lam giau tu khong" thay vi
"lam giau tu du lieu that co san".

Nhan doc lap dung LAI dung `InjectedLabel`/`flag_matches_label` cua
`lcx_core.fraud_injector` (hai kieu du lieu nay hoan toan khong phu thuoc mien
"trips" - chi la (pattern, driver_id, severity, match_field, match_value)).
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

import polars as pl

from lcx_core.data.gsm_loader import DataSourceError
from lcx_core.event_sim import hanoi_geo
from lcx_core.event_sim.store import (
    CAMERA_EVENTS_SCHEMA,
    CHARGING_SESSIONS_SCHEMA,
    EVENTS_SCHEMA,
    STATION_IDS,
    VEHICLE_REGISTRY_SCHEMA,
    EventDataStore,
)
from lcx_core.event_sim.vehicle_scenarios import SCENARIO_PARAMS, generate_operations, inject_scenarios
from lcx_core.fraud_injector.injector import InjectedLabel
from lcx_core.telemetry_tier.battery_swap_validation import HARD_CAP_SWAP_RATE_PCT_PER_S


def _station_ids() -> list[str]:
    """399 tram sac/doi pin THAT (OSM), roi ve 3 tram gia dinh cu neu thieu du
    lieu ban do (vd moi truong test/CI offline)."""
    try:
        ids = list(hanoi_geo.station_ids())
        return ids or STATION_IDS
    except DataSourceError:
        return STATION_IDS

SIM_SOURCE = "SIM"
INJECTED_SOURCE = "INJECTED_FRAUD"
SEVERITIES = ("light", "medium", "heavy")
_VN_TZ = timezone(timedelta(hours=7))
_ANCHOR = datetime(2099, 1, 1, tzinfo=_VN_TZ)

DEFAULT_N_DRIVERS = 120
DEFAULT_N_DAYS = 14
DEFAULT_N_PER_SEVERITY = 15
CAR_MODELS = ["VF 5", "VF 6", "VF e34", "VF 8"]
ATTENDANCE_PROB = 0.75

# gioi han vat ly biet truoc (docs/telemetry-research.md, SWAP_DURATION_TICKS_RANGE
# 120-300s cho 15%->98%) - dung de sinh phien SAC BINH THUONG, luon nam DUOI
# HARD_CAP_SWAP_RATE_PCT_PER_S (dinh nghia o telemetry_tier/battery_swap_validation.py
# - detector la nguon su that DUY NHAT cho hang so vat ly nay).
NORMAL_SWAP_RATE_PCT_PER_S = (0.25, 0.65)

SEVERITY_PARAMS: dict[str, dict[str, float]] = {
    "virtual_online_status": {"light": 1.3, "medium": 2.5, "heavy": 5.0},  # x p99 gap that
    "camera_intervention": {"light": 3, "medium": 6, "heavy": 12},  # so event confidence cao them
    "battery_swap_validation": {"light": 1.2, "medium": 2.0, "heavy": 4.0},  # x HARD_CAP rate
    "ghost_vehicle": {"light": 1, "medium": 1, "heavy": 1},  # nhi phan - severity chi anh huong so luong
    **SCENARIO_PARAMS,  # F1/F3/F4/F5/F6 cua Taxi Co Huu - xem vehicle_scenarios.py
}
PATTERNS = list(SEVERITY_PARAMS)
VEHICLE_SCENARIO_PATTERNS = list(SCENARIO_PARAMS)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_driver_universe(n_drivers: int, rng: random.Random) -> tuple[list[str], list[str], list[dict]]:
    """Tra ve (driver_ids, car_driver_ids, vehicle_registry_rows). 100% Taxi Co Huu (o to)."""
    driver_ids = [f"sim-d-{i}" for i in range(n_drivers)]
    rng.shuffle(driver_ids)

    rows: list[dict] = []
    for did in driver_ids:
        vin = f"VF{did[-6:].upper().zfill(6)}KR{rng.randint(100000, 999999)}"
        rows.append(
            {
                "vehicle_id": f"veh-{did}",
                "vin": vin,
                "vehicle_type": "car",
                "model": rng.choice(CAR_MODELS),
                "battery_capacity_kwh": round(rng.uniform(37.0, 87.0), 2),
                "assigned_driver_id": did,
                "registered_at": _iso(_ANCHOR - timedelta(days=rng.randint(30, 900))),
                "status": "active",
            }
        )
    return driver_ids, driver_ids, rows


# --------------------------------------------------------------------------
# events: went_online .. (app_fg/bg, driver_break, manual_location_toggle) .. went_offline
# --------------------------------------------------------------------------
def _normal_shift_offsets(rng: random.Random) -> tuple[float, list[tuple[float, str, dict]]]:
    """Ca lam viec BINH THUONG: cac su kien "nhip tim" (heartbeat) cach nhau
    25-70 phut (jitter +-30%) - app_foreground/app_background xen ke, thinh
    thoang mot cap driver_break_started/ended thay cho mot nhip."""
    duration_hours = rng.uniform(3.0, 9.0)
    total_minutes = duration_hours * 60
    segment = rng.uniform(25, 70)

    offsets: list[tuple[float, str, dict]] = []
    t = segment
    is_background = True
    pending_break = False
    while t < total_minutes - segment * 0.5:
        if not pending_break and rng.random() < 0.12:
            offsets.append((t, "driver_break_started", {}))
            pending_break = True
        elif pending_break:
            offsets.append((t, "driver_break_ended", {}))
            pending_break = False
        else:
            event_type = "app_background" if is_background else "app_foreground"
            offsets.append((t, event_type, {}))
            is_background = not is_background
        t += segment * rng.uniform(0.7, 1.3)
    if pending_break:
        offsets.append((t, "driver_break_ended", {}))
    return duration_hours, offsets


def _injected_gap_shift(rng: random.Random, gap_seconds: float) -> tuple[float, list[tuple[float, str, dict]]]:
    """Ca lam viec CO tiem gian lan: tat dinh vi thu cong (manual_location_toggle
    location_enabled=False), im lang dung `gap_seconds`, roi bat lai - khong co
    went_offline nao dong phien trong luc do (dung la 'giu trang thai online
    ao'). Ca duoc keo dai du de chua tron khoang lang nay."""
    gap_hours = gap_seconds / 3600.0
    pre_hours = rng.uniform(0.5, 1.5)
    post_hours = rng.uniform(0.3, 1.0)
    duration_hours = pre_hours + gap_hours + post_hours
    offsets = [
        (pre_hours * 60, "manual_location_toggle", {"location_enabled": False}),
        ((pre_hours + gap_hours) * 60, "manual_location_toggle", {"location_enabled": True}),
    ]
    return duration_hours, offsets


def _shift_rows(
    driver_id: str,
    shift_start: datetime,
    duration_hours: float,
    offsets: list[tuple[float, str, dict]],
    source: str,
) -> list[dict]:
    rows = [
        {
            "event_id": f"evt-{uuid.uuid4().hex[:12]}",
            "driver_id": driver_id,
            "event_type": "went_online",
            "occurred_at": _iso(shift_start),
            "location_enabled": None,
            "source": source,
        }
    ]
    for offset_min, event_type, extra in offsets:
        row = {
            "event_id": f"evt-{uuid.uuid4().hex[:12]}",
            "driver_id": driver_id,
            "event_type": event_type,
            "occurred_at": _iso(shift_start + timedelta(minutes=offset_min)),
            "location_enabled": extra.get("location_enabled"),
            "source": source,
        }
        rows.append(row)
    rows.append(
        {
            "event_id": f"evt-{uuid.uuid4().hex[:12]}",
            "driver_id": driver_id,
            "event_type": "went_offline",
            "occurred_at": _iso(shift_start + timedelta(hours=duration_hours)),
            "location_enabled": None,
            "source": source,
        }
    )
    return rows


def _p99_heartbeat_gap(events: pl.DataFrame) -> float:
    """Tinh p99 khoang cach (giay) giua 2 su kien lien tiep TRONG CUNG mot phien
    (went_online..went_offline) tren QUAN THE BINH THUONG - dung lam moc hieu
    chuan cho muc do nghiem trong cua virtual_online_status, giong het cach
    lam voi location_dropout o Pha A (khong doan truoc so tuyet doi)."""
    ordered = events.sort(["driver_id", "occurred_at"])
    gaps: list[float] = []
    open_since: datetime | None = None
    prev_ts: datetime | None = None
    prev_driver: str | None = None
    for row in ordered.iter_rows(named=True):
        driver_id, etype, ts = row["driver_id"], row["event_type"], datetime.fromisoformat(row["occurred_at"])
        if driver_id != prev_driver:
            open_since = None
        if etype == "went_online":
            open_since = ts
        elif open_since is not None and prev_ts is not None and prev_driver == driver_id:
            gaps.append((ts - prev_ts).total_seconds())
        if etype == "went_offline":
            open_since = None
        prev_ts, prev_driver = ts, driver_id
    if not gaps:
        return 3600.0
    gaps.sort()
    idx = min(int(len(gaps) * 0.99), len(gaps) - 1)
    return gaps[idx]


def _generate_events(driver_ids: list[str], n_days: int, rng: random.Random) -> pl.DataFrame:
    rows: list[dict] = []
    for day in range(n_days):
        for driver_id in driver_ids:
            if rng.random() > ATTENDANCE_PROB:
                continue
            shift_start = _ANCHOR + timedelta(days=day, hours=rng.uniform(5, 20))
            duration_hours, offsets = _normal_shift_offsets(rng)
            rows.extend(_shift_rows(driver_id, shift_start, duration_hours, offsets, SIM_SOURCE))
    return pl.DataFrame(rows, schema=EVENTS_SCHEMA)


def _inject_virtual_online(
    driver_ids: list[str], baseline_events: pl.DataFrame, rng: random.Random, n: int
) -> tuple[pl.DataFrame, list[InjectedLabel]]:
    real_p99_gap = _p99_heartbeat_gap(baseline_events)
    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    for severity, mult in SEVERITY_PARAMS["virtual_online_status"].items():
        gap_seconds = real_p99_gap * mult
        chosen = rng.sample(driver_ids, min(n, len(driver_ids)))
        for i, driver_id in enumerate(chosen):
            day = hash((severity, driver_id, i)) % 1000  # ngay rieng, khong dung ngay that cua baseline
            shift_start = _ANCHOR + timedelta(days=10_000 + day, hours=rng.uniform(5, 12))
            duration_hours, offsets = _injected_gap_shift(rng, gap_seconds)
            rows = _shift_rows(driver_id, shift_start, duration_hours, offsets, INJECTED_SOURCE)
            new_rows.extend(rows)
            toggle_off_ts = rows[1]["occurred_at"]  # dong thu 2 = manual_location_toggle(false)
            labels.append(
                InjectedLabel(
                    "virtual_online_status", driver_id, severity, "toggle_off_event_id", rows[1]["event_id"]
                )
            )
            _ = toggle_off_ts
    new_df = pl.DataFrame(new_rows, schema=EVENTS_SCHEMA)
    return pl.concat([baseline_events, new_df], how="vertical"), labels


# --------------------------------------------------------------------------
# camera_events
# --------------------------------------------------------------------------
def _generate_camera_events(car_driver_ids: list[str], n_days: int, rng: random.Random) -> pl.DataFrame:
    """Nhieu nen binh thuong: xac suat rat thap moi ngay/tai xe co DUNG 1 su
    kien camera le te - do tin cay TRAI RONG (phan lon thap, mot so it cao,
    dai dien mot su co CA BIET that nhung khong lap lai) de detector co quan
    the that ma hieu chuan percentile, tranh nguong tu tham chieu vao chinh du
    lieu da tiem (da gap loi tuong tu o route_deviation Pha A - xem
    docs/injector-eval-results.md)."""
    rows: list[dict] = []
    for day in range(n_days):
        for driver_id in car_driver_ids:
            if rng.random() > 0.05:
                continue
            ts = _ANCHOR + timedelta(days=day, hours=rng.uniform(6, 22))
            rows.append(
                {
                    "event_id": f"cam-{uuid.uuid4().hex[:12]}",
                    "driver_id": driver_id,
                    "trip_id": None,
                    "occurred_at": _iso(ts),
                    "camera_event_type": rng.choice(
                        ["no_face_detected", "face_mismatch", "passenger_count_exceeds_seat_sensor"]
                    ),
                    "detected_passenger_count": rng.randint(1, 2),
                    "seat_sensor_passenger_count": 1,
                    "confidence": round(rng.uniform(0.30, 0.95), 2),
                    "source": SIM_SOURCE,
                }
            )
    return pl.DataFrame(rows, schema=CAMERA_EVENTS_SCHEMA)


def _inject_camera_intervention(
    car_driver_ids: list[str], rng: random.Random, n: int
) -> tuple[pl.DataFrame, list[InjectedLabel]]:
    """Them nhieu su kien do tin cay CAO trong CUNG 1 ngay cho 1 driver - dai
    dien mot ngay co hanh vi don khach ngoai app/gia mao tai khoan lap lai
    nhieu lan, khong phai nhieu AI ngau nhien."""
    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    for severity, count in SEVERITY_PARAMS["camera_intervention"].items():
        chosen = rng.sample(car_driver_ids, min(n, len(car_driver_ids)))
        for i, driver_id in enumerate(chosen):
            day = 20_000 + hash((severity, driver_id, i)) % 1000
            local_date = (_ANCHOR + timedelta(days=day)).date().isoformat()
            for j in range(int(count)):
                ts = _ANCHOR + timedelta(days=day, hours=8 + j * 0.5)
                new_rows.append(
                    {
                        "event_id": f"cam-{uuid.uuid4().hex[:12]}",
                        "driver_id": driver_id,
                        "trip_id": None,
                        "occurred_at": _iso(ts),
                        "camera_event_type": rng.choice(
                            ["face_mismatch", "passenger_count_exceeds_seat_sensor"]
                        ),
                        "detected_passenger_count": rng.randint(2, 4),
                        "seat_sensor_passenger_count": 1,
                        "confidence": round(rng.uniform(0.85, 0.99), 2),
                        "source": INJECTED_SOURCE,
                    }
                )
            labels.append(
                InjectedLabel("camera_intervention", driver_id, severity, "local_date", local_date)
            )
    new_df = pl.DataFrame(new_rows, schema=CAMERA_EVENTS_SCHEMA)
    return new_df, labels


# --------------------------------------------------------------------------
# charging_sessions
# --------------------------------------------------------------------------
def _generate_charging_sessions(
    driver_ids: list[str], vehicle_by_driver: dict[str, dict], n_days: int, rng: random.Random
) -> pl.DataFrame:
    stations = _station_ids()
    rows: list[dict] = []
    for day in range(n_days):
        for driver_id in driver_ids:
            if rng.random() > 0.35:  # sac khoang 1 lan/~3 ngay
                continue
            vin = vehicle_by_driver[driver_id]["vin"]
            duration = rng.randint(120, 300)
            soc_before = rng.uniform(8, 20)
            rate = rng.uniform(*NORMAL_SWAP_RATE_PCT_PER_S)
            soc_after = min(100.0, soc_before + rate * duration)
            started = _ANCHOR + timedelta(days=day, hours=rng.uniform(0, 23))
            rows.append(
                {
                    "session_id": f"chg-{uuid.uuid4().hex[:12]}",
                    "driver_id": driver_id,
                    "vehicle_vin": vin,
                    "station_id": rng.choice(stations),
                    "started_at": _iso(started),
                    "ended_at": _iso(started + timedelta(seconds=duration)),
                    "soc_before_pct": round(soc_before, 1),
                    "soc_after_pct": round(soc_after, 1),
                    "duration_seconds": duration,
                    "cost_vnd": rng.randint(15_000, 35_000),
                    "status": "completed",
                    "source": SIM_SOURCE,
                }
            )
    return pl.DataFrame(rows, schema=CHARGING_SESSIONS_SCHEMA)


def _inject_battery_swap_violations(
    driver_ids: list[str], vehicle_by_driver: dict[str, dict], rng: random.Random, n: int
) -> tuple[pl.DataFrame, list[InjectedLabel]]:
    """Bao cao thoi gian sac RAT NGAN nhung SoC tang RAT NHIEU - vuot xa gioi
    han vat ly that (HARD_CAP_SWAP_RATE_PCT_PER_S) - dai dien bao cao phien sac
    khong khop thuc te (gian lan phia tram/nhan vien, khong phai tai xe)."""
    stations = _station_ids()
    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    for severity, cap_mult in SEVERITY_PARAMS["battery_swap_validation"].items():
        chosen = rng.sample(driver_ids, min(n, len(driver_ids)))
        for i, driver_id in enumerate(chosen):
            vin = vehicle_by_driver[driver_id]["vin"]
            duration = rng.randint(15, 45)
            soc_before = rng.uniform(8, 20)
            target_rate = HARD_CAP_SWAP_RATE_PCT_PER_S * cap_mult
            soc_after = min(100.0, soc_before + target_rate * duration)
            day = 30_000 + hash((severity, driver_id, i)) % 1000
            started = _ANCHOR + timedelta(days=day, hours=rng.uniform(0, 23))
            session_id = f"chg-inject-{severity}-{uuid.uuid4().hex[:10]}"
            new_rows.append(
                {
                    "session_id": session_id,
                    "driver_id": driver_id,
                    "vehicle_vin": vin,
                    "station_id": rng.choice(stations),
                    "started_at": _iso(started),
                    "ended_at": _iso(started + timedelta(seconds=duration)),
                    "soc_before_pct": round(soc_before, 1),
                    "soc_after_pct": round(soc_after, 1),
                    "duration_seconds": duration,
                    "cost_vnd": rng.randint(15_000, 35_000),
                    "status": "completed",
                    "source": INJECTED_SOURCE,
                }
            )
            labels.append(
                InjectedLabel("battery_swap_validation", driver_id, severity, "session_id", session_id)
            )
    new_df = pl.DataFrame(new_rows, schema=CHARGING_SESSIONS_SCHEMA)
    return new_df, labels


def _inject_ghost_vehicles(
    driver_ids: list[str], rng: random.Random, n: int
) -> tuple[pl.DataFrame, list[InjectedLabel]]:
    """Phien sac voi VIN KHONG khop bat ky dong nao trong vehicle_registry -
    "xe ma"."""
    stations = _station_ids()
    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    for severity, _ in SEVERITY_PARAMS["ghost_vehicle"].items():
        chosen = rng.sample(driver_ids, min(n, len(driver_ids)))
        for i, driver_id in enumerate(chosen):
            fake_vin = f"GHOST{uuid.uuid4().hex[:11].upper()}"
            duration = rng.randint(120, 300)
            soc_before = rng.uniform(8, 20)
            rate = rng.uniform(*NORMAL_SWAP_RATE_PCT_PER_S)
            soc_after = min(100.0, soc_before + rate * duration)
            day = 40_000 + hash((severity, driver_id, i)) % 1000
            started = _ANCHOR + timedelta(days=day, hours=rng.uniform(0, 23))
            session_id = f"chg-ghost-{severity}-{uuid.uuid4().hex[:10]}"
            new_rows.append(
                {
                    "session_id": session_id,
                    "driver_id": driver_id,
                    "vehicle_vin": fake_vin,
                    "station_id": rng.choice(stations),
                    "started_at": _iso(started),
                    "ended_at": _iso(started + timedelta(seconds=duration)),
                    "soc_before_pct": round(soc_before, 1),
                    "soc_after_pct": round(soc_after, 1),
                    "duration_seconds": duration,
                    "cost_vnd": rng.randint(15_000, 35_000),
                    "status": "completed",
                    "source": INJECTED_SOURCE,
                }
            )
            labels.append(InjectedLabel("ghost_vehicle", driver_id, severity, "session_id", session_id))
    new_df = pl.DataFrame(new_rows, schema=CHARGING_SESSIONS_SCHEMA)
    return new_df, labels


# --------------------------------------------------------------------------
# API top-level
# --------------------------------------------------------------------------
def generate_baseline(
    n_drivers: int = DEFAULT_N_DRIVERS, n_days: int = DEFAULT_N_DAYS, seed: int = 42
) -> EventDataStore:
    """Chi quan the BINH THUONG (khong gian lan) - dung khi can baseline sach
    de hieu chuan nguong (vd _p99_heartbeat_gap) hoac lam demo."""
    rng = random.Random(seed)
    driver_ids, car_driver_ids, registry_rows = _make_driver_universe(n_drivers, rng)
    events = _generate_events(driver_ids, n_days, rng)
    camera_events = _generate_camera_events(car_driver_ids, n_days, rng)
    vehicle_by_driver = {r["assigned_driver_id"]: r for r in registry_rows}
    charging = _generate_charging_sessions(driver_ids, vehicle_by_driver, n_days, rng)
    registry = pl.DataFrame(registry_rows, schema=VEHICLE_REGISTRY_SCHEMA)
    trips, telemetry, shifts, _cars, _shift_by_driver = generate_operations(
        driver_ids, vehicle_by_driver, n_days, rng
    )
    return EventDataStore(events, camera_events, charging, registry, trips, telemetry, shifts)


def inject_all(
    n_drivers: int = DEFAULT_N_DRIVERS,
    n_days: int = DEFAULT_N_DAYS,
    seed: int = 42,
    n_per_severity: int = DEFAULT_N_PER_SEVERITY,
    patterns: list[str] | None = None,
) -> tuple[EventDataStore, pl.DataFrame]:
    """Sinh quan the binh thuong RIENG cho tung mau hinh muon tiem CO CHU DICH,
    dung nhat quan voi `fraud_injector.inject_all`: tra ve (store, ground_truth).
    """
    patterns = patterns or PATTERNS
    rng = random.Random(seed)
    driver_ids, car_driver_ids, registry_rows = _make_driver_universe(n_drivers, rng)
    vehicle_by_driver = {r["assigned_driver_id"]: r for r in registry_rows}
    registry = pl.DataFrame(registry_rows, schema=VEHICLE_REGISTRY_SCHEMA)

    events = _generate_events(driver_ids, n_days, rng)
    camera_events = _generate_camera_events(car_driver_ids, n_days, rng)
    charging = _generate_charging_sessions(driver_ids, vehicle_by_driver, n_days, rng)

    all_labels: list[InjectedLabel] = []

    trips = telemetry = shifts = None
    scenario_patterns = [p for p in patterns if p in VEHICLE_SCENARIO_PATTERNS]
    if scenario_patterns:
        trips, telemetry, shifts, cars, shift_by_driver = generate_operations(
            driver_ids, vehicle_by_driver, n_days, rng
        )
        inj_trips, inj_tel, labels = inject_scenarios(
            driver_ids, cars, shift_by_driver, rng, n_per_severity, scenario_patterns
        )
        trips = pl.concat([trips, inj_trips], how="vertical")
        telemetry = pl.concat([telemetry, inj_tel], how="vertical")
        all_labels.extend(labels)

    if "virtual_online_status" in patterns:
        events, labels = _inject_virtual_online(driver_ids, events, rng, n_per_severity)
        all_labels.extend(labels)

    if "camera_intervention" in patterns:
        injected_cam, labels = _inject_camera_intervention(car_driver_ids, rng, n_per_severity)
        camera_events = pl.concat([camera_events, injected_cam], how="vertical")
        all_labels.extend(labels)

    if "battery_swap_validation" in patterns:
        injected_chg, labels = _inject_battery_swap_violations(driver_ids, vehicle_by_driver, rng, n_per_severity)
        charging = pl.concat([charging, injected_chg], how="vertical")
        all_labels.extend(labels)

    if "ghost_vehicle" in patterns:
        injected_ghost, labels = _inject_ghost_vehicles(driver_ids, rng, n_per_severity)
        charging = pl.concat([charging, injected_ghost], how="vertical")
        all_labels.extend(labels)

    ground_truth = pl.DataFrame(
        [label.to_dict() for label in all_labels],
        schema={"pattern": pl.Utf8, "driver_id": pl.Utf8, "severity": pl.Utf8, "match_field": pl.Utf8, "match_value": pl.Utf8},
    )
    extra = {} if trips is None else {"trips": trips, "telemetry": telemetry, "declared_shifts": shifts}
    store = EventDataStore(events, camera_events, charging, registry, **extra)
    return store, ground_truth
