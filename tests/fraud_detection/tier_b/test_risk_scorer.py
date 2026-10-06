from __future__ import annotations

from lcx_core.tier_b import build_feature_matrix, score_all_drivers


def test_feature_matrix_covers_full_driver_population(store):
    all_drivers = set(store.table("driver_statistic_daily")["driver_id"].unique().to_list())
    features = build_feature_matrix(store)
    assert set(features["driver_id"].to_list()) >= all_drivers


def test_score_all_drivers_ranks_descending_and_bounded(store):
    scores = score_all_drivers(store)
    assert len(scores) > 0
    values = [s.risk_score for s in scores]
    assert values == sorted(values, reverse=True)
    assert all(0.0 <= v <= 1.0 for v in values)
