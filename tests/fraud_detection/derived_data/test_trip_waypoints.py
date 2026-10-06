"""Test trip_waypoints (hang muc 3) - waypoint noi suy tu OSRM phai bat dau/
ket thuc DUNG pickup_h3/drop_h3 that cua tung chuyen."""

from __future__ import annotations

import polars as pl

from lcx_core.derived_data.trip_waypoints import compute_trip_waypoints, path_length_km


def test_waypoints_start_and_end_match_trip_endpoints(store):
    waypoints = compute_trip_waypoints(store, sample_n=50, seed=1)
    trips = store.table("trips").filter(pl.col("status") == "completed").select(["trip_id", "pickup_h3", "drop_h3"])

    sampled_trip_ids = waypoints["trip_id"].unique().to_list()
    trips_map = {
        row["trip_id"]: (row["pickup_h3"], row["drop_h3"])
        for row in trips.filter(pl.col("trip_id").is_in(sampled_trip_ids)).iter_rows(named=True)
    }

    for trip_id, group in waypoints.sort("seq").group_by("trip_id"):
        trip_id = trip_id[0] if isinstance(trip_id, tuple) else trip_id
        hexes = group.sort("seq")["hex"].to_list()
        pickup_h3, drop_h3 = trips_map[trip_id]
        assert hexes[0] == pickup_h3
        assert hexes[-1] == drop_h3


def test_path_length_km_non_negative(store):
    waypoints = compute_trip_waypoints(store, sample_n=50, seed=1)
    lengths = path_length_km(store, waypoints)
    assert lengths.height > 0
    assert lengths["path_length_km"].min() >= 0.0
