"""Test F1/F3/F4/F5/F6 cua Taxi Co Huu (event_sim/vehicle_scenarios + telemetry_tier moi).

Y nghia: (1) quan the 100% o to, (2) quan the BINH THUONG khong bi flag oan boi cac luat
xac dinh (F1/F3/F4/F5 - nguong cung, nhieu nen hop le nhu huy sau khi cho khach, mat
song ngan, ve depot sau ca), (3) o moi muc do tiem, don vi gian lan bi bat.
"""

from __future__ import annotations

import polars as pl
import pytest

from lcx_core.event_sim import generate_baseline, inject_all
from lcx_core.fraud_injector import flag_matches_label
from lcx_core.telemetry_tier import REGISTRY

VEHICLE_PATTERNS = ["off_app_pickup", "tcu_signal_loss", "personal_use", "fake_trip", "invalid_driver_cancel"]
DETERMINISTIC = ["off_app_pickup", "tcu_signal_loss", "personal_use", "fake_trip"]


@pytest.fixture(scope="module")
def baseline():
    return generate_baseline(n_drivers=60, n_days=10, seed=5)


@pytest.fixture(scope="module")
def sim():
    return inject_all(n_drivers=120, n_days=14, seed=21, n_per_severity=8, patterns=VEHICLE_PATTERNS)


def test_fleet_is_all_cars(baseline):
    assert set(baseline.vehicle_registry["vehicle_type"].unique().to_list()) == {"car"}
    assert baseline.vehicle_registry["vin"].null_count() == 0


def test_operations_tables_are_populated(baseline):
    assert baseline.trips.height > 0 and baseline.telemetry.height > 0 and baseline.declared_shifts.height == 60
    statuses = set(baseline.trips["status"].unique().to_list())
    assert statuses == {"completed", "cancelled"}


@pytest.mark.parametrize("pattern", DETERMINISTIC)
def test_baseline_is_not_flagged_by_hard_rules(baseline, pattern):
    assert REGISTRY[pattern].detect(baseline).n_flagged == 0


@pytest.mark.parametrize("pattern", VEHICLE_PATTERNS)
def test_every_severity_is_detected(sim, pattern):
    store, ground_truth = sim
    result = REGISTRY[pattern].detect(store)
    rows = ground_truth.filter(pl.col("pattern") == pattern).to_dicts()
    assert len(rows) == 3 * 8
    tp = sum(
        1
        for r in rows
        if any(flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"]) for f in result.flags)
    )
    assert tp == len(rows)


@pytest.mark.parametrize("pattern,min_precision", [(p, 0.95) for p in DETERMINISTIC] + [("invalid_driver_cancel", 0.7)])
def test_precision_among_flags(sim, pattern, min_precision):
    store, ground_truth = sim
    result = REGISTRY[pattern].detect(store)
    rows = ground_truth.filter(pl.col("pattern") == pattern).to_dicts()
    tp = sum(
        1
        for f in result.flags
        if any(flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"]) for r in rows)
    )
    assert tp / result.n_flagged >= min_precision


def test_legit_cancel_after_arrival_is_not_off_app(baseline):
    """Cuoc huy 'khach khong den' sau khi cho (xe dung yen, ghe trong) la hop le."""
    legit = baseline.trips.filter(
        (pl.col("status") == "cancelled") & pl.col("arrived_at_pickup_time").is_not_null()
    )
    assert legit.height > 0
    assert REGISTRY["off_app_pickup"].detect(baseline).n_flagged == 0
