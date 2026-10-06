"""Xac nhan Tier C nem loi RO RANG thay vi gia vo co ket qua."""

from __future__ import annotations

import pytest

from lcx_core.tier_c.device_account_graph import MissingDataError, query_cluster_for_driver


def test_query_cluster_raises_missing_data_error(store):
    driver_id = store.table("driver_statistic_daily")["driver_id"][0]
    with pytest.raises(MissingDataError):
        query_cluster_for_driver(store, driver_id)
