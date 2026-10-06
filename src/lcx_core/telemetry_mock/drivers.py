"""May trang thai tai xe ao cho Buoc 3 - logic THUAN (khong goi mang), test
duoc doc lap voi service. Moi `tick()` tra ve (list ping, list event) theo
DUNG 2 schema Buoc 2 - day la "nguon phat" telemetry, khong phai chi random so.

Di chuyen dung h3.grid_path_cells giua 2 o hex that (lay tu universe cell cua
chinh ma tran OSRM trong GSM_SIMULATOR neu co, fallback ve mot cum hex quanh
Dong Da) - dung tinh than "tai su dung toa do H3 va ma tran OSRM da co san" cua
muc 12.3, thay vi random lat/lng khong lien quan gi toi dia ly that.
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import h3

# ---- Tham so mo phong (nguon: configs/telemetry_mock.yaml + docs/telemetry-research.md) ----
OFFER_COUNTDOWN_SECONDS = 15
OFFER_DECLINE_PROB = 0.10
XANHNOW_PROB = 0.15          # ty le cuoc khop qua XanhNow thay vi dieu phoi trung tam
GEOFENCE_PASS_PROB = 0.95
NO_SHOW_AFTER_ARRIVED_PROB = 0.05
CANCEL_MID_TRIP_PROB = 0.02
IDLE_WAIT_TICKS_RANGE = (2, 6)
ARRIVED_LOAD_TICKS = 1
APP_TOGGLE_PROB = 0.05  # hang muc 4 "Chien luoc lam giau du lieu": app_foreground/background

CAR_SPEED_KMH_RANGE = (35, 75)
IDLE_SPEED_KMH = 0.0

BATTERY_LOW_THRESHOLD_PCT = 20.0
BATTERY_DRAIN_PCT_PER_KM = 0.6
SWAP_DURATION_TICKS_RANGE = (30, 75)   # ~120-300s o interval 4s, xem configs/telemetry_mock.yaml
SWAP_TARGET_PCT_RANGE = (95, 100)

CAR_MODELS = ["VF 5", "VF 6", "VF e34", "VF 8"]

STAGE_ONLINE_IDLE = "ONLINE_IDLE"
STAGE_OFFER_PENDING = "OFFER_PENDING"
STAGE_ENROUTE_PICKUP = "ENROUTE_PICKUP"
STAGE_ARRIVED = "ARRIVED"
STAGE_TRIP_ACTIVE = "TRIP_ACTIVE"
STAGE_SWAPPING = "SWAPPING"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hex_bearing_deg(from_hex: str, to_hex: str) -> float:
    if from_hex == to_hex:
        return 0.0
    lat1, lng1 = h3.cell_to_latlng(from_hex)
    lat2, lng2 = h3.cell_to_latlng(to_hex)
    lat1, lng1, lat2, lng2 = map(math.radians, (lat1, lng1, lat2, lng2))
    dlng = lng2 - lng1
    y = math.sin(dlng) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _hex_distance_km(from_hex: str, to_hex: str) -> float:
    if from_hex == to_hex:
        return 0.0
    lat1, lng1 = h3.cell_to_latlng(from_hex)
    lat2, lng2 = h3.cell_to_latlng(to_hex)
    return h3.great_circle_distance((lat1, lng1), (lat2, lng2), unit="km")


@dataclass
class VirtualDriver:
    driver_id: str
    source_device: str          # "vehicle_tcu_car" (100% Taxi Co Huu)
    vehicle_model: str
    current_hex: str
    stage: str = STAGE_ONLINE_IDLE
    hex_path: list[str] = field(default_factory=list)
    path_idx: int = 0
    ticks_in_state: int = 0
    trip_id: str | None = None
    customer_id: str | None = None
    via_xanhnow: bool = False
    odometer_km: float = 0.0
    battery_pct: float = field(default_factory=lambda: random.uniform(40, 100))
    swap_ticks_remaining: int = 0
    has_gone_online: bool = False
    app_in_background: bool = False

    def is_car(self) -> bool:
        return self.source_device == "vehicle_tcu_car"


def make_driver(driver_id: str, source_device: str, start_hex: str) -> VirtualDriver:
    model = random.choice(CAR_MODELS)
    return VirtualDriver(
        driver_id=driver_id, source_device=source_device,
        vehicle_model=model, current_hex=start_hex,
    )


def _pick_destination(driver: VirtualDriver, universe: list[str], rng: random.Random) -> None:
    dest = rng.choice([h for h in universe if h != driver.current_hex] or universe)
    path = h3.grid_path_cells(driver.current_hex, dest)
    driver.hex_path = path if len(path) > 1 else [driver.current_hex, dest]
    driver.path_idx = 0


def _advance_along_path(driver: VirtualDriver) -> tuple[str, float, float]:
    """Di 1 buoc doc hex_path. Tra ve (prev_hex, distance_km, bearing_deg)."""
    prev_hex = driver.current_hex
    if driver.path_idx < len(driver.hex_path) - 1:
        driver.path_idx += 1
        driver.current_hex = driver.hex_path[driver.path_idx]
    dist = _hex_distance_km(prev_hex, driver.current_hex)
    bearing = _hex_bearing_deg(prev_hex, driver.current_hex)
    return prev_hex, dist, bearing


def _reached_end_of_path(driver: VirtualDriver) -> bool:
    return driver.path_idx >= len(driver.hex_path) - 1


def _make_ping(driver: VirtualDriver, speed_kmh: float, bearing: float | None) -> dict:
    lat, lng = h3.cell_to_latlng(driver.current_hex)
    ping: dict = {
        "ping_id": f"ping-{uuid.uuid4().hex[:12]}",
        "driver_id": driver.driver_id,
        "trip_id": driver.trip_id,
        "source_device": driver.source_device,
        "occurred_at": _now_iso(),
        "lat": lat,
        "lng": lng,
        "current_hex_h3_res9": driver.current_hex,
        "speed_kmh": round(speed_kmh, 1),
        "heading_deg": round(bearing, 1) if bearing is not None else None,
        "vin": f"VF{driver.driver_id[-6:].upper().zfill(6)}KR000001" if driver.is_car() else None,
        "gear": None,
        "odometer_km": None,
        "battery_level_pct": round(driver.battery_pct, 1),
        "remaining_range_km": None,
        "charging_status": "CHARGING" if driver.stage == STAGE_SWAPPING else "DISCHARGING" if speed_kmh > 0 else "IDLE",
        "seat_occupancy": None,
        "door_lock_status": None,
        "trunk_status": None,
        "phone_connectivity_lost": False,
    }
    if driver.is_car():
        moving = driver.stage in (STAGE_ENROUTE_PICKUP, STAGE_TRIP_ACTIVE)
        ping["gear"] = "D" if moving else "P"
        ping["odometer_km"] = round(driver.odometer_km, 2)
        ping["seat_occupancy"] = {
            "driver": True,
            "any_passenger_seat": driver.stage == STAGE_TRIP_ACTIVE,
        }
        ping["door_lock_status"] = "UNLOCKED" if driver.stage in (STAGE_ARRIVED,) else "LOCKED"
        ping["trunk_status"] = "CLOSED"
    return ping


def _make_event(driver: VirtualDriver, event_type: str, session_level: bool = False, **extra) -> dict:
    """session_level=True cho cac su kien cap phien/tai khoan (went_online,
    app_foreground/background, driver_break_*, manual_location_toggle) - KHONG
    gan voi mot cuoc cu the nao, nen trip_id=null va KHONG duoc kich hoat side
    effect tu dong sinh trip_id moi (chi danh cho cac event thuoc vong doi MOT
    cuoc cu the)."""
    if session_level:
        trip_id = None
    else:
        trip_id = driver.trip_id or f"trip-{uuid.uuid4().hex[:10]}"

    event = {
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "trip_id": trip_id,
        "driver_id": driver.driver_id,
        "customer_id": driver.customer_id,
        "event_type": event_type,
        "occurred_at": _now_iso(),
        "offer_countdown_seconds": None,
        "arrival_geofence_check_passed": None,
        "arrival_geofence_radius_m": None,
        "cancel_reason": None,
        "cancel_actor": None,
        "via_xanhnow": driver.via_xanhnow,
        "xanhnow_otp_code_masked": "XXXX" if driver.via_xanhnow else None,
        "payment_method": None,
        "location_enabled": None,
    }
    event.update(extra)
    if not session_level and driver.trip_id is None:
        driver.trip_id = trip_id
    return event


def tick(driver: VirtualDriver, universe: list[str], station_hex: str, rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Tien driver 1 buoc thoi gian. Tra ve (pings, events) can gui len service."""
    pings: list[dict] = []
    events: list[dict] = []
    driver.ticks_in_state += 1

    # Hang muc 4 "Chien luoc lam giau du lieu": went_online da co khung trong
    # trip_lifecycle_event.schema.json tu Buoc 2 nhung producer Buoc 3 chua bao
    # gio mo phong - tai xe ao gio phat dung 1 lan o tick dau tien.
    if not driver.has_gone_online:
        driver.has_gone_online = True
        events.append(_make_event(driver, "went_online", session_level=True))

    # --- uu tien: dang doi pin tai tram (chi khi ranh, khong o giua chuyen) ---
    if driver.stage == STAGE_SWAPPING:
        driver.swap_ticks_remaining -= 1
        if driver.swap_ticks_remaining <= 0:
            driver.battery_pct = float(rng.randint(*SWAP_TARGET_PCT_RANGE))
            driver.stage = STAGE_ONLINE_IDLE
            driver.ticks_in_state = 0
        pings.append(_make_ping(driver, IDLE_SPEED_KMH, None))
        return pings, events

    if driver.current_hex == station_hex and driver.battery_pct < BATTERY_LOW_THRESHOLD_PCT and driver.stage == STAGE_ONLINE_IDLE:
        driver.stage = STAGE_SWAPPING
        driver.swap_ticks_remaining = rng.randint(*SWAP_DURATION_TICKS_RANGE)
        pings.append(_make_ping(driver, IDLE_SPEED_KMH, None))
        return pings, events

    if driver.stage == STAGE_ONLINE_IDLE:
        pings.append(_make_ping(driver, IDLE_SPEED_KMH, None))
        if rng.random() < APP_TOGGLE_PROB:
            driver.app_in_background = not driver.app_in_background
            event_type = "app_background" if driver.app_in_background else "app_foreground"
            events.append(_make_event(driver, event_type, session_level=True))
        if driver.ticks_in_state >= rng.randint(*IDLE_WAIT_TICKS_RANGE):
            driver.ticks_in_state = 0
            driver.customer_id = f"cust-{uuid.uuid4().hex[:10]}"
            if rng.random() < XANHNOW_PROB:
                driver.via_xanhnow = True
                events.append(_make_event(driver, "xanhnow_otp_matched"))
                events.append(_make_event(
                    driver, "arrived_at_pickup",
                    arrival_geofence_check_passed=True,  # da o cung vi tri voi khach
                ))
                driver.stage = STAGE_ARRIVED
            else:
                events.append(_make_event(
                    driver, "offer_received", offer_countdown_seconds=OFFER_COUNTDOWN_SECONDS,
                ))
                driver.stage = STAGE_OFFER_PENDING
        return pings, events

    if driver.stage == STAGE_OFFER_PENDING:
        pings.append(_make_ping(driver, IDLE_SPEED_KMH, None))
        if rng.random() < OFFER_DECLINE_PROB:
            events.append(_make_event(driver, "offer_declined"))
            driver.trip_id = None
            driver.customer_id = None
            driver.stage = STAGE_ONLINE_IDLE
            driver.ticks_in_state = 0
            return pings, events
        events.append(_make_event(driver, "offer_accepted"))
        _pick_destination(driver, universe, rng)
        events.append(_make_event(driver, "enroute_to_pickup"))
        driver.stage = STAGE_ENROUTE_PICKUP
        return pings, events

    if driver.stage == STAGE_ENROUTE_PICKUP:
        speed = rng.uniform(*CAR_SPEED_KMH_RANGE)
        _prev, dist_km, bearing = _advance_along_path(driver)
        driver.odometer_km += dist_km
        driver.battery_pct = max(0.0, driver.battery_pct - dist_km * BATTERY_DRAIN_PCT_PER_KM)
        pings.append(_make_ping(driver, speed, bearing))
        if _reached_end_of_path(driver):
            passed = rng.random() < GEOFENCE_PASS_PROB
            events.append(_make_event(driver, "arrived_at_pickup", arrival_geofence_check_passed=passed))
            driver.stage = STAGE_ARRIVED
            driver.ticks_in_state = 0
        return pings, events

    if driver.stage == STAGE_ARRIVED:
        pings.append(_make_ping(driver, IDLE_SPEED_KMH, None))
        if driver.ticks_in_state >= ARRIVED_LOAD_TICKS:
            if rng.random() < NO_SHOW_AFTER_ARRIVED_PROB and not driver.via_xanhnow:
                events.append(_make_event(
                    driver, "no_show_reported", cancel_reason="khach khong xuat hien", cancel_actor="driver",
                ))
                driver.trip_id = None
                driver.customer_id = None
                driver.via_xanhnow = False
                driver.stage = STAGE_ONLINE_IDLE
                driver.ticks_in_state = 0
                return pings, events
            _pick_destination(driver, universe, rng)
            events.append(_make_event(driver, "trip_started"))
            driver.stage = STAGE_TRIP_ACTIVE
        return pings, events

    if driver.stage == STAGE_TRIP_ACTIVE:
        speed = rng.uniform(*CAR_SPEED_KMH_RANGE)
        _prev, dist_km, bearing = _advance_along_path(driver)
        driver.odometer_km += dist_km
        driver.battery_pct = max(0.0, driver.battery_pct - dist_km * BATTERY_DRAIN_PCT_PER_KM)
        pings.append(_make_ping(driver, speed, bearing))

        if rng.random() < CANCEL_MID_TRIP_PROB:
            events.append(_make_event(
                driver, "trip_cancelled", cancel_reason="huy giua chuyen (mo phong)", cancel_actor="customer",
            ))
            driver.trip_id = None
            driver.customer_id = None
            driver.via_xanhnow = False
            driver.stage = STAGE_ONLINE_IDLE
            driver.ticks_in_state = 0
            return pings, events

        if _reached_end_of_path(driver):
            events.append(_make_event(
                driver, "trip_completed", payment_method=rng.choice(["cash", "wallet", "card"]),
            ))
            driver.trip_id = None
            driver.customer_id = None
            driver.via_xanhnow = False
            driver.stage = STAGE_ONLINE_IDLE
            driver.ticks_in_state = 0
        return pings, events

    raise AssertionError(f"trang thai khong hop le: {driver.stage}")
