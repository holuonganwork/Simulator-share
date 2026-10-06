"""Van hanh o to Taxi Co Huu + tiem gian lan F1/F3/F4/F5/F6 (FRAUDS.md).

Sinh 3 bang cho EventDataStore: trips (co cuoc huy + ly do huy), telemetry (TCU:
ODO, toc do, ghe sau, pin, app_status) va declared_shifts (ca cam ket). Cung triet ly
voi simulator.py: quan the BINH THUONG (co nhieu am tinh gia hop le) + don vi gian lan
tiem tren NGAY RIENG (offset ngay lon) de nhan khong lan voi quan the that.

Mau hinh <-> FRAUDS.md:
  off_app_pickup       F1  huy cuoc sau khi toi diem don nhung xe van chay co khach
  tcu_signal_loss      F3  mat tin hieu TCU giua ca, ODO tang vot
  personal_use         F4  chay xe ngoai ca cam ket (co the ra ngoai Ha Noi)
  fake_trip            F5  cuoc hoan thanh nhung xe dung yen, ghe sau trong
  invalid_driver_cancel F6 tai xe huy "khach khong den" khi chua toi diem don
(F2 dung tier_a/route_deviation tren du lieu trips l1r, khong tiem o day.)
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import polars as pl

from lcx_core.data.gsm_loader import DataSourceError
from lcx_core.event_sim import hanoi_geo
from lcx_core.event_sim.store import SHIFTS_SCHEMA, TELEMETRY_SCHEMA, TRIPS_SCHEMA
from lcx_core.fraud_injector.injector import InjectedLabel

SIM_SOURCE = "SIM"
INJECTED_SOURCE = "INJECTED_FRAUD"
_VN_TZ = timezone(timedelta(hours=7))
_ANCHOR = datetime(2099, 1, 1, tzinfo=_VN_TZ)

# Fallback CU (jitter quanh 1 tam gia dinh) - chi dung khi thieu du lieu ban do
# that (`research/simulation/data/`, vd may/CI khong checkout thu muc do) de
# module van chay duoc offline, khong vo test. Duong THUONG: `_sample_point()`
# lay diem THAT tren luoi Res 7 tu `hanoi_geo` (925 o OSRM da khao sat).
HANOI_CENTER = (21.0285, 105.8542)


def _sample_point(rng: random.Random) -> tuple[float, float]:
    try:
        return hanoi_geo.sample_point(rng)
    except DataSourceError:
        return HANOI_CENTER[0] + rng.uniform(-0.05, 0.05), HANOI_CENTER[1] + rng.uniform(-0.05, 0.05)


BASELINE_ATTENDANCE_PROB = 0.75
IDLE_STEP_MIN = 5
MOVE_STEP_MIN = 1
BATTERY_PCT_PER_KM = 0.3  # trong khoang 0,22-0,39%/km suy tu kwh_per_100km / battery_kwh cua cac hang xe
BATTERY_FLOOR_PCT = 5.0
OVERNIGHT_RECHARGE_BELOW_PCT = 50.0
HOME_RADIUS_KM = 12.0  # xe van hanh trong ban kinh nay quanh NHA RIENG cua no (khong con 1 tam chung)
COMMUTE_MAX_MIN = 10  # tai xe ve depot sau ca (binh thuong, <= vai km)

# muc do -> tham so; ten mau hinh dung lam key trong SEVERITY_PARAMS cua simulator.py
SCENARIO_PARAMS: dict[str, dict[str, float]] = {
    "off_app_pickup": {"light": 6, "medium": 15, "heavy": 30},  # phut xe chay co khach sau khi huy
    "tcu_signal_loss": {"light": 6, "medium": 20, "heavy": 60},  # km ODO tang trong luc mat tin hieu
    "personal_use": {"light": 15, "medium": 60, "heavy": 200},  # km chay ngoai ca
    "fake_trip": {"light": 2, "medium": 4, "heavy": 8},  # so cuoc ao / ngay
    "invalid_driver_cancel": {"light": 2, "medium": 5, "heavy": 9},  # so cuoc huy sai / tuan
}
_TCU_GAP_MINUTES = {"light": 20, "medium": 45, "heavy": 120}


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def week_key(local_date: str) -> str:
    iso = datetime.fromisoformat(local_date).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class _Car:
    """Trang thai vat ly cua 1 xe: vi tri, ODO, pin - chi tang dan theo thoi gian."""

    def __init__(self, driver_id: str, vin: str, rng: random.Random) -> None:
        self.driver_id = driver_id
        self.vin = vin
        self.odo = round(rng.uniform(5_000, 60_000), 1)
        self.battery = rng.uniform(50, 100)
        # Moi xe co 1 "nha" rieng, lay THAT tu luoi Res 7 toan Ha Noi (thay vi
        # tat ca 120 xe cung jitter quanh 1 tam gia dinh nhu truoc) - xe phan
        # bo trai deu tren toan thanh pho, dung nhu ban do that.
        self.home_lat, self.home_lng = _sample_point(rng)
        self.lat, self.lng = self.home_lat, self.home_lng
        self.heading = rng.uniform(0, 2 * math.pi)

    def advance(self, km: float, rng: random.Random, fixed_heading: float | None = None) -> None:
        """Di `km` theo huong hien tai. Neu xe xa NHA (`home_lat/lng`) qua HOME_RADIUS_KM
        (va khong bi ep `fixed_heading`) thi lai huong ve nha - xe van hanh quanh khu vuc
        cua minh, khong troi di xa."""
        if km <= 0:
            return
        if fixed_heading is not None:
            self.heading = fixed_heading
        else:
            self.heading += rng.uniform(-0.6, 0.6)
            dy = (self.home_lat - self.lat) * 111.0
            dx = (self.home_lng - self.lng) * 111.0 * math.cos(math.radians(self.lat))
            if math.hypot(dx, dy) > HOME_RADIUS_KM:
                self.heading = math.atan2(dx, dy) + rng.uniform(-0.5, 0.5)
        self.lat += km * math.cos(self.heading) / 111.0
        self.lng += km * math.sin(self.heading) / (111.0 * math.cos(math.radians(self.lat)))
        self.odo += km
        self.battery = max(BATTERY_FLOOR_PCT, self.battery - km * BATTERY_PCT_PER_KM)

    def advance_far(self, km: float, rng: random.Random, step_km: float = 3.0) -> None:
        """Quang duong dai (vd luc mat tin hieu) chia thanh cac buoc ngan de xe van quanh trung tam."""
        left = km
        while left > 1e-9:
            d = min(step_km, left)
            self.advance(d, rng)
            left -= d

    def recharge_if_low(self, rng: random.Random) -> None:
        """Sac qua dem: pin thap thi dau ngay lam viec xe da duoc sac day (phien sac nay chua ghi thanh bang)."""
        if self.battery < OVERNIGHT_RECHARGE_BELOW_PCT:
            self.battery = rng.uniform(85, 100)

    def teleport_home(self, rng: random.Random) -> None:
        """Dat xe ve gan NHA cua no (dau moi ngay tiem) - ODO va pin giu nguyen (khong lui)."""
        self.lat = self.home_lat + rng.uniform(-0.01, 0.01)
        self.lng = self.home_lng + rng.uniform(-0.01, 0.01)
        self.heading = rng.uniform(0, 2 * math.pi)
        self.recharge_if_low(rng)


class _Recorder:
    """Gom dong telemetry/trips cua mot lan sinh; dem ping_id/trip_id tuan tu."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tel: list[dict] = []
        self.trips: list[dict] = []
        self._n_ping = 0
        self._n_trip = 0

    def trip_id(self, tag: str) -> str:
        self._n_trip += 1
        return f"trip-{tag}-{self._n_trip}"

    def ping(self, car: _Car, t: datetime, speed: float, seat: bool, status: str, trip_id: str | None) -> None:
        self._n_ping += 1
        self.tel.append(
            {
                "ping_id": f"tel-{self.source[:3].lower()}-{self._n_ping}",
                "driver_id": car.driver_id, "vin": car.vin, "trip_id": trip_id,
                "occurred_at": _iso(t), "lat": round(car.lat, 5), "lng": round(car.lng, 5),
                "speed_kmh": round(speed, 1), "odometer_km": round(car.odo, 2),
                "battery_pct": round(car.battery, 1), "seat_any_passenger": seat,
                "app_status": status, "source": self.source,
            }
        )

    def segment(
        self, car: _Car, rng: random.Random, t0: datetime, minutes: float, step: int,
        speed: tuple[float, float], seat: bool = False, status: str = "online",
        trip_id: str | None = None, heading: float | None = None,
    ) -> datetime:
        """Sinh ping moi `step` phut trong `minutes` phut; ODO tang theo toc do. Tra ve thoi diem ket thuc."""
        t = t0
        n = max(1, int(minutes // step))
        for _ in range(n):
            t = t + timedelta(minutes=step)
            v = rng.uniform(*speed) if speed[1] > 0 else 0.0
            car.advance(v * step / 60.0, rng, heading)
            self.ping(car, t, v, seat, status, trip_id)
        return t

    def trip(self, car: _Car, trip_id: str, status: str, request: datetime, assign: datetime | None,
             arrived: datetime | None, pickup: datetime | None, complete: datetime | None,
             reason: str | None, by: str | None, billed: float, optimal: float, rng: random.Random,
             pickup_lat: float | None = None, pickup_lng: float | None = None) -> None:
        self.trips.append(
            {
                "trip_id": trip_id, "driver_id": car.driver_id, "vin": car.vin, "status": status,
                "request_time": _iso(request), "assign_time": _iso(assign) if assign else None,
                "arrived_at_pickup_time": _iso(arrived) if arrived else None,
                "pickup_time": _iso(pickup) if pickup else None,
                "complete_time": _iso(complete) if complete else None,
                "cancel_reason": reason, "cancelled_by": by,
                "billed_distance_km": round(billed, 2), "route_km_optimal": round(optimal, 2),
                # Vi tri THAT cua xe luc ket thuc doan "di toi don" (doc lap voi
                # co tu bao arrived_at_pickup_time) - null neu khong biet (chua
                # tung di doan nao ca, vd F5 xe dung yen tu dau).
                "pickup_lat": round(pickup_lat, 5) if pickup_lat is not None else None,
                "pickup_lng": round(pickup_lng, 5) if pickup_lng is not None else None,
                "source": self.source,
            }
        )


# --------------------------------------------------------------------------
# quan the: xe + ca cam ket
# --------------------------------------------------------------------------
def make_cars_and_shifts(
    driver_ids: list[str], vehicle_by_driver: dict[str, dict], rng: random.Random
) -> tuple[dict[str, _Car], pl.DataFrame]:
    cars: dict[str, _Car] = {}
    shift_rows: list[dict] = []
    for did in driver_ids:
        cars[did] = _Car(did, vehicle_by_driver[did]["vin"], rng)
        if rng.random() < 0.4:  # ca som: 5-8h, dai 8-9h => ket thuc <= 17h
            start = rng.choice([5, 6, 7, 8]) * 60
        else:  # ca chieu
            start = rng.choice([12, 13, 14]) * 60
        shift_rows.append(
            {"driver_id": did, "declared_start_min": start, "declared_end_min": start + rng.choice([8, 9]) * 60}
        )
    return cars, pl.DataFrame(shift_rows, schema=SHIFTS_SCHEMA)


# --------------------------------------------------------------------------
# quan the binh thuong
# --------------------------------------------------------------------------
def _normal_trip(rec: _Recorder, car: _Car, rng: random.Random, t: datetime) -> datetime:
    """Mot cuoc binh thuong (co ~5% huy hop le, ~0.4% huy sai ly do). Tra ve thoi diem ket thuc."""
    tid = rec.trip_id("n")
    assign = t + timedelta(seconds=rng.randint(10, 60))
    roll = rng.random()
    eta = rng.uniform(4, 10)

    if roll < 0.025:  # khach huy khi xe dang toi don
        t_end = rec.segment(car, rng, t, rng.uniform(1, eta - 1), MOVE_STEP_MIN, (20, 40), trip_id=tid)
        rec.trip(car, tid, "cancelled", t, assign, None, None, None, "khach_doi_y", "customer", 0.0, 0.0, rng,
                  pickup_lat=car.lat, pickup_lng=car.lng)
        return t_end
    if roll < 0.035:  # tai xe huy vi su co xe (hop le)
        t_end = rec.segment(car, rng, t, rng.uniform(1, eta - 1), MOVE_STEP_MIN, (20, 40), trip_id=tid)
        rec.trip(car, tid, "cancelled", t, assign, None, None, None, "su_co_xe", "driver", 0.0, 0.0, rng,
                  pickup_lat=car.lat, pickup_lng=car.lng)
        return t_end
    if roll < 0.045:  # toi noi, cho khach 5 phut, khach khong den (hop le - xe dung yen, ghe trong)
        arrived = rec.segment(car, rng, t, eta, MOVE_STEP_MIN, (20, 40), trip_id=tid)
        pickup_lat, pickup_lng = car.lat, car.lng
        cancel = rec.segment(car, rng, arrived, 5, MOVE_STEP_MIN, (0, 0), trip_id=tid)
        rec.trip(car, tid, "cancelled", t, assign, arrived, None, None, "khach_khong_den", "driver", 0.0, 0.0, rng,
                  pickup_lat=pickup_lat, pickup_lng=pickup_lng)
        return cancel
    if roll < 0.049:  # tai xe huy "khach khong den" khi chua toi (am tinh gia cho F6)
        t_end = rec.segment(car, rng, t, rng.uniform(1, eta - 1), MOVE_STEP_MIN, (20, 40), trip_id=tid)
        rec.trip(car, tid, "cancelled", t, assign, None, None, None, "khach_khong_den", "driver", 0.0, 0.0, rng,
                  pickup_lat=car.lat, pickup_lng=car.lng)
        return t_end

    arrived = rec.segment(car, rng, t, eta, MOVE_STEP_MIN, (20, 40), trip_id=tid)
    pickup_lat, pickup_lng = car.lat, car.lng
    pickup = arrived + timedelta(minutes=rng.uniform(1, 3))
    odo_start = car.odo
    complete = rec.segment(car, rng, pickup, rng.uniform(8, 35), MOVE_STEP_MIN, (20, 45), seat=True, trip_id=tid)
    billed = car.odo - odo_start
    rec.trip(car, tid, "completed", t, assign, arrived, pickup, complete, None, None,
             billed, billed * rng.uniform(0.85, 1.0), rng, pickup_lat=pickup_lat, pickup_lng=pickup_lng)
    return complete


def _normal_day(rec: _Recorder, car: _Car, shift: dict, rng: random.Random, day: int) -> None:
    day0 = _ANCHOR + timedelta(days=day)
    car.recharge_if_low(rng)
    start = day0 + timedelta(minutes=shift["declared_start_min"] + rng.uniform(-10, 10))
    end = day0 + timedelta(minutes=shift["declared_end_min"] + rng.uniform(-10, 10))
    t = start
    rec.ping(car, t, 0.0, False, "online", None)
    while t < end - timedelta(minutes=30):
        idle = rng.uniform(8, 35)
        if rng.random() < 0.03:  # nhieu tu nhien: mat song ngan 10-14 phut, xe dung yen (khong ODO)
            t = t + timedelta(minutes=rng.uniform(10, 14))
            rec.ping(car, t, 0.0, False, "online", None)
        else:
            t = rec.segment(car, rng, t, idle, IDLE_STEP_MIN, (0, 0))
        if t >= end - timedelta(minutes=30):
            break
        t = _normal_trip(rec, car, rng, t)
    # ve depot sau ca: vai km, van trong khoang cho phep (< COMMUTE_MAX_MIN phut)
    rec.segment(car, rng, t, rng.uniform(3, COMMUTE_MAX_MIN), IDLE_STEP_MIN, (25, 40), status="offline")


def generate_operations(
    driver_ids: list[str], vehicle_by_driver: dict[str, dict], n_days: int, rng: random.Random
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, _Car], dict[str, dict]]:
    """Quan the binh thuong: (trips, telemetry, declared_shifts, cars, shift_by_driver)."""
    cars, shifts = make_cars_and_shifts(driver_ids, vehicle_by_driver, rng)
    shift_by_driver = {r["driver_id"]: r for r in shifts.to_dicts()}
    rec = _Recorder(SIM_SOURCE)
    for day in range(n_days):
        for did in driver_ids:
            if rng.random() > BASELINE_ATTENDANCE_PROB:
                continue
            _normal_day(rec, cars[did], shift_by_driver[did], rng, day)
    return (
        pl.DataFrame(rec.trips, schema=TRIPS_SCHEMA),
        pl.DataFrame(rec.tel, schema=TELEMETRY_SCHEMA),
        shifts,
        cars,
        shift_by_driver,
    )


# --------------------------------------------------------------------------
# tiem gian lan
# --------------------------------------------------------------------------
class _DayAllocator:
    """Moi don vi tiem duoc mot ngay RIENG (offset lon) de khong lan voi quan the that."""

    def __init__(self, base: int) -> None:
        self._next = base

    def next(self) -> int:
        self._next += 1
        return self._next

    def next_monday(self) -> int:
        d = self.next()
        while (_ANCHOR + timedelta(days=d)).weekday() != 0:
            d = self.next()
        return d


def _shift_start(day: int, shift: dict) -> datetime:
    return _ANCHOR + timedelta(days=day, minutes=shift["declared_start_min"] + 15)


def inject_scenarios(
    driver_ids: list[str], cars: dict[str, _Car], shift_by_driver: dict[str, dict],
    rng: random.Random, n: int, patterns: list[str],
) -> tuple[pl.DataFrame, pl.DataFrame, list[InjectedLabel]]:
    rec = _Recorder(INJECTED_SOURCE)
    labels: list[InjectedLabel] = []

    if "off_app_pickup" in patterns:
        alloc = _DayAllocator(50_000)
        for severity, minutes in SCENARIO_PARAMS["off_app_pickup"].items():
            for did in rng.sample(driver_ids, min(n, len(driver_ids))):
                car, day = cars[did], alloc.next()
                car.teleport_home(rng)
                t = _shift_start(day, shift_by_driver[did])
                rec.ping(car, t, 0.0, False, "online", None)
                t = rec.segment(car, rng, t, 10, IDLE_STEP_MIN, (0, 0))
                tid = rec.trip_id("f1")
                assign = t + timedelta(seconds=30)
                arrived = rec.segment(car, rng, t, 6, MOVE_STEP_MIN, (25, 40), trip_id=tid)
                pickup_lat, pickup_lng = car.lat, car.lng
                cancelled_at = rec.segment(car, rng, arrived, 1, MOVE_STEP_MIN, (0, 0), trip_id=tid)
                by = rng.choice(["customer", "driver"])
                rec.trip(car, tid, "cancelled", t, assign, arrived, None, None,
                         "khach_huy" if by == "customer" else "tai_xe_huy", by, 0.0, 0.0, rng,
                         pickup_lat=pickup_lat, pickup_lng=pickup_lng)
                # sau khi huy: xe chay deu co khach (khong con trip_id tren app)
                rec.segment(car, rng, cancelled_at, minutes, MOVE_STEP_MIN, (30, 50), seat=True)
                labels.append(InjectedLabel("off_app_pickup", did, severity, "trip_id", tid))

    if "tcu_signal_loss" in patterns:
        alloc = _DayAllocator(60_000)
        for severity, km in SCENARIO_PARAMS["tcu_signal_loss"].items():
            for did in rng.sample(driver_ids, min(n, len(driver_ids))):
                car, day = cars[did], alloc.next()
                car.teleport_home(rng)
                t = _shift_start(day, shift_by_driver[did])
                rec.ping(car, t, 0.0, False, "online", None)
                t = rec.segment(car, rng, t, 40, IDLE_STEP_MIN, (0, 0))
                gap_min = _TCU_GAP_MINUTES[severity]
                # xe van chay trong luc mat tin hieu: ODO tang `km` nhung KHONG co ping nao
                car.advance_far(float(km), rng)
                t = t + timedelta(minutes=gap_min)
                rec.ping(car, t, 0.0, False, "online", None)
                rec.segment(car, rng, t, 30, IDLE_STEP_MIN, (0, 0))
                labels.append(InjectedLabel("tcu_signal_loss", did, severity, "local_date",
                                            (_ANCHOR + timedelta(days=day)).date().isoformat()))

    if "personal_use" in patterns:
        alloc = _DayAllocator(70_000)
        early = [d for d in driver_ids if shift_by_driver[d]["declared_end_min"] <= 17 * 60] or driver_ids
        for severity, km in SCENARIO_PARAMS["personal_use"].items():
            for did in rng.sample(early, min(n, len(early))):
                car, day = cars[did], alloc.next()
                car.teleport_home(rng)
                shift = shift_by_driver[did]
                t = _ANCHOR + timedelta(days=day, minutes=shift["declared_end_min"] + 60)
                speed = 60.0 if km >= 100 else 45.0
                out_of_hanoi = severity == "heavy"  # nang: chay ra ngoai dia gioi Ha Noi (huong bac)
                rec.segment(car, rng, t, km / speed * 60, IDLE_STEP_MIN, (speed, speed), status="offline",
                            heading=0.0 if out_of_hanoi else None)
                labels.append(InjectedLabel("personal_use", did, severity, "local_date",
                                            (_ANCHOR + timedelta(days=day)).date().isoformat()))

    if "fake_trip" in patterns:
        alloc = _DayAllocator(80_000)
        for severity, n_fake in SCENARIO_PARAMS["fake_trip"].items():
            for did in rng.sample(driver_ids, min(n, len(driver_ids))):
                car, day = cars[did], alloc.next()
                car.teleport_home(rng)
                t = _shift_start(day, shift_by_driver[did])
                rec.ping(car, t, 0.0, False, "online", None)
                for _ in range(int(n_fake)):
                    tid = rec.trip_id("f5")
                    t = rec.segment(car, rng, t, 10, IDLE_STEP_MIN, (0, 0))
                    request = t
                    arrived = t + timedelta(minutes=2)
                    pickup = t + timedelta(minutes=3)
                    pickup_lat, pickup_lng = car.lat, car.lng  # xe dung yen tu dau, chua tung di doan nao
                    # xe dung yen, ghe sau trong, nhung app bao cuoc ngan 1-2 km da hoan thanh
                    complete = rec.segment(car, rng, t, 8, MOVE_STEP_MIN, (0, 0), trip_id=tid)
                    billed = rng.uniform(1.0, 2.0)
                    rec.trip(car, tid, "completed", request, request + timedelta(seconds=20), arrived, pickup,
                             complete, None, None, billed, billed * rng.uniform(0.9, 1.0), rng,
                             pickup_lat=pickup_lat, pickup_lng=pickup_lng)
                    t = complete
                labels.append(InjectedLabel("fake_trip", did, severity, "local_date",
                                            (_ANCHOR + timedelta(days=day)).date().isoformat()))

    if "invalid_driver_cancel" in patterns:
        alloc = _DayAllocator(90_000)
        for severity, n_cancel in SCENARIO_PARAMS["invalid_driver_cancel"].items():
            for did in rng.sample(driver_ids, min(n, len(driver_ids))):
                car, monday = cars[did], alloc.next_monday()
                car.teleport_home(rng)
                # thu 2 -> thu 6 cung 1 tuan ISO; duyet ngay truoc, vong sau de ODO tang dung thu tu thoi gian
                schedule = [(d, r) for d in range(5) for r in range(2) if d + 5 * r < int(n_cancel)]
                for d, r in schedule:
                    day = monday + d
                    t = _shift_start(day, shift_by_driver[did]) + timedelta(minutes=20 * r)
                    tid = rec.trip_id("f6")
                    rec.ping(car, t, 0.0, False, "online", None)
                    t_end = rec.segment(car, rng, t, rng.uniform(2, 4), MOVE_STEP_MIN, (15, 30), trip_id=tid)
                    # Diem mau chot cua F6: xe THAT SU da lai gan/toi diem don (toa do
                    # ngay sau doan lai xe nay) nhung ung dung van bao "chua toi" - day
                    # la bang chung GPS doc lap de doi chieu voi co tu bao.
                    rec.trip(car, tid, "cancelled", t, t + timedelta(seconds=20), None, None, None,
                             "khach_khong_den", "driver", 0.0, 0.0, rng,
                             pickup_lat=car.lat, pickup_lng=car.lng)
                    _ = t_end
                labels.append(InjectedLabel("invalid_driver_cancel", did, severity, "week_key",
                                            week_key((_ANCHOR + timedelta(days=monday)).date().isoformat())))

    return (
        pl.DataFrame(rec.trips, schema=TRIPS_SCHEMA),
        pl.DataFrame(rec.tel, schema=TELEMETRY_SCHEMA),
        labels,
    )
