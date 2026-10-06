"""Test artificial_surge_collusion (hang muc 8) tren du lieu that cua
GSM_SIMULATOR + don vi gian lan tiem qua lcx_core.derived_data.fare_breakdown.
Dung fixture `store` (session-scoped) tu conftest.py chung cua repo.
"""

from __future__ import annotations

import polars as pl
import pytest

from lcx_core.derived_data.fare_breakdown import SEVERITY_PARAMS, compute_fare_breakdown, inject_artificial_surge
from lcx_core.derived_tier import artificial_surge_collusion
from lcx_core.fraud_injector import flag_matches_label

N_PER_SEVERITY = 10


@pytest.fixture(scope="module")
def fare_breakdown_with_injection(store):
    baseline = compute_fare_breakdown(store, seed=42)
    injected_rows, labels = inject_artificial_surge(store, baseline, seed=43, n_per_severity=N_PER_SEVERITY)
    fb = pl.concat([baseline, injected_rows], how="vertical")
    gt = pl.DataFrame(
        [label.to_dict() for label in labels],
        schema={"pattern": pl.Utf8, "driver_id": pl.Utf8, "severity": pl.Utf8, "match_field": pl.Utf8, "match_value": pl.Utf8},
    )
    return fb, gt


def test_compute_fare_breakdown_runs_on_all_real_trips(store):
    baseline = compute_fare_breakdown(store, seed=42)
    real_trips = store.table("trips").filter(pl.col("status") == "completed")
    assert baseline.height == real_trips.height
    assert baseline["surge_multiplier"].min() >= 1.0


def test_ground_truth_has_expected_shape(fare_breakdown_with_injection):
    _fb, gt = fare_breakdown_with_injection
    assert gt.height == len(SEVERITY_PARAMS) * N_PER_SEVERITY


def test_heavy_severity_is_mostly_detected(fare_breakdown_with_injection):
    fb, gt = fare_breakdown_with_injection
    result = artificial_surge_collusion.detect(fb)

    gt_rows = gt.filter(pl.col("severity") == "heavy").to_dicts()
    tp = sum(
        1
        for r in gt_rows
        if any(flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"]) for f in result.flags)
    )
    recall = tp / len(gt_rows)
    assert recall >= 0.6, f"recall={recall:.0%} qua thap o muc heavy ({tp}/{len(gt_rows)})"


def test_flags_carry_caveat(fare_breakdown_with_injection):
    fb, _gt = fare_breakdown_with_injection
    result = artificial_surge_collusion.detect(fb)
    assert all(f.caveat for f in result.flags)
