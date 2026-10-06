"""Test may trang thai tai xe ao (drivers.py) - logic thuan, khong can service.

Quan trong nhat: test_all_emitted_payloads_match_schema. Day la loai test lam
lo ra loi 'const:15' phat hien khi chay thu Buoc 3 lan dau (17/24 event bi tu
choi oan vi offer_countdown_seconds=null bi coi la sai const) - neu co test
nay tu truoc, loi da bi bat ngay khi viet code, khong can chay thu bang tay.
"""

from __future__ import annotations

import random

import pytest

from lcx_core.telemetry_mock.drivers import (
    STAGE_SWAPPING,
    BATTERY_LOW_THRESHOLD_PCT,
    make_driver,
    tick,
)
from lcx_core.telemetry_mock.schemas import validate_event, validate_ping

UNIVERSE = [
    "89415cb4883ffff", "89415cb48c7ffff", "89415cb4977ffff", "89415cb492bffff",
    "89415cb4c13ffff", "89415cb488fffff", "89415cb4c2bffff", "89415cb4c23ffff",
]
STATION_HEX = UNIVERSE[0]


def test_all_emitted_payloads_match_schema():
    rng = random.Random(7)
    driver = make_driver("d-test", "vehicle_tcu_car", rng.choice(UNIVERSE))

    n_pings = n_events = 0
    for _ in range(500):  # du dai de di qua het cac nhanh cua may trang thai
        pings, events = tick(driver, UNIVERSE, STATION_HEX, rng)
        for p in pings:
            validate_ping(p)  # raise neu sai hop dong
            n_pings += 1
        for e in events:
            validate_event(e)
            n_events += 1

    assert n_pings > 0
    assert n_events > 0


def test_car_ping_has_car_only_fields_while_active():
    rng = random.Random(1)
    driver = make_driver("d-car", "vehicle_tcu_car", UNIVERSE[0])
    # dua ve trang thai co chuyen dang chay de kiem tra gear='D'
    from lcx_core.telemetry_mock.drivers import STAGE_TRIP_ACTIVE

    driver.stage = STAGE_TRIP_ACTIVE
    driver.hex_path = UNIVERSE
    driver.path_idx = 0
    pings, _ = tick(driver, UNIVERSE, STATION_HEX, rng)
    assert pings[0]["gear"] == "D"
    assert pings[0]["seat_occupancy"]["any_passenger_seat"] is True
    assert pings[0]["vin"] is not None


def test_battery_swap_triggers_and_completes_at_station():
    rng = random.Random(3)
    driver = make_driver("d-lowbatt", "vehicle_tcu_car", STATION_HEX)
    driver.battery_pct = BATTERY_LOW_THRESHOLD_PCT - 1  # duoi nguong

    pings, _events = tick(driver, UNIVERSE, STATION_HEX, rng)
    assert driver.stage == STAGE_SWAPPING
    assert pings[0]["speed_kmh"] == 0.0

    for _ in range(200):  # du dai de swap_ticks_remaining ve 0
        tick(driver, UNIVERSE, STATION_HEX, rng)
        if driver.stage != STAGE_SWAPPING:
            break

    assert driver.stage != STAGE_SWAPPING
    assert driver.battery_pct >= 95.0  # dung SWAP_TARGET_PCT_RANGE
