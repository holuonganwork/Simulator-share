"""Test 4 detector moi cua Khoi 1 "Chien luoc lam giau du lieu" (hang muc
2/4/6-log) tren du lieu SINH RA boi lcx_core.event_sim (khong co du lieu that
de doi chieu o mien nay - xem docstring event_sim/simulator.py).

Muc tieu giong tests/fraud_injector/test_injector.py: (1) so nhan doc lap dung
mong doi, (2) o muc do 'heavy', detector phai bat duoc PHAN LON don vi da
tiem - day la phep kiem tra hoi quy chinh: neu injector hoac detector bi sua
sai khien tin hieu khong con phat hien duoc, test nay do vo truoc khi anh
huong bao cao.
"""

from __future__ import annotations

import polars as pl
import pytest

from lcx_core.event_sim import PATTERNS, SEVERITIES, inject_all
from lcx_core.fraud_injector import flag_matches_label
from lcx_core.telemetry_tier import REGISTRY

N_PER_SEVERITY = 8


@pytest.fixture(scope="module")
def sim():
    return inject_all(n_drivers=120, n_days=14, seed=11, n_per_severity=N_PER_SEVERITY)


def test_ground_truth_has_expected_shape(sim):
    _store, ground_truth = sim
    assert ground_truth.height == len(PATTERNS) * len(SEVERITIES) * N_PER_SEVERITY
    assert set(ground_truth["pattern"].unique().to_list()) == set(PATTERNS)


@pytest.mark.parametrize("pattern", PATTERNS)
def test_heavy_severity_is_mostly_detected(sim, pattern):
    store, ground_truth = sim
    result = REGISTRY[pattern].detect(store)

    gt_rows = ground_truth.filter(
        (pl.col("pattern") == pattern) & (pl.col("severity") == "heavy")
    ).to_dicts()
    assert gt_rows, f"khong co nhan gt cho {pattern}/heavy"

    tp = sum(
        1
        for r in gt_rows
        if any(
            flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"])
            for f in result.flags
        )
    )
    recall = tp / len(gt_rows)
    assert recall >= 0.6, f"{pattern}: recall={recall:.0%} qua thap o muc heavy ({tp}/{len(gt_rows)})"


def test_ghost_vehicle_vin_never_in_registry(sim):
    """Kiem tra rieng: don vi da tiem cua ghost_vehicle phai co VIN THAT SU
    khong ton tai trong vehicle_registry - neu khong day la loi injector, khong
    phai loi detector."""
    store, ground_truth = sim
    known_vins = set(store.vehicle_registry["vin"].drop_nulls().to_list())
    gt_sessions = ground_truth.filter(pl.col("pattern") == "ghost_vehicle")["match_value"].to_list()
    injected_vins = (
        store.charging_sessions.filter(pl.col("session_id").is_in(gt_sessions))["vehicle_vin"].to_list()
    )
    assert injected_vins
    assert all(vin not in known_vins for vin in injected_vins)
