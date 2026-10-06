"""Test driver_profile_enriched (hang muc 1, PHAN HO SO) - kiem tra phan THAT
(violation_count_total gop dung tu driver_penalization_ATA.parquet) tach biet
ro voi phan SYNTHETIC (join_date, contract_type)."""

from __future__ import annotations

import polars as pl

from lcx_core.derived_data.driver_profile import compute_driver_profile_enriched


def test_violation_counts_match_real_penalization_table(store):
    profile = compute_driver_profile_enriched(store, seed=1)
    penal = store.table("driver_penalization_ATA")

    real_total = penal.height
    profile_total = profile["violation_count_total"].sum()
    assert profile_total == real_total


def test_synthetic_fields_are_labeled(store):
    profile = compute_driver_profile_enriched(store, seed=1)
    assert (profile["synthetic_fields"] == "join_date,contract_type").all()
    assert profile["contract_type"].is_in(["platform_partner", "rto_lease", "employee"]).all()


def test_one_row_per_driver(store):
    profile = compute_driver_profile_enriched(store, seed=1)
    stat_daily = store.table("driver_statistic_daily")
    assert profile.height == stat_daily["driver_id"].n_unique()
    assert profile["driver_id"].n_unique() == profile.height
