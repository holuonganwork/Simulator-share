#!/usr/bin/env python3
"""So sanh do chinh xac cua route_deviation (2-diem-mut, hien dung) voi mot
phien ban dung CA LO TRINH (trip_waypoints, hang muc 3 "Chien luoc lam giau
du lieu"). KHONG thay the route_deviation.py hien co (tai lieu chien luoc goc
danh gia hang muc nay la "cai thien do chinh xac cho mau DA co luat, khong mo
mau moi") - script nay chi DO xem 2 cach tinh lech nhau bao nhieu tren cung
mot mau du lieu that, dung lam can cu quyet dinh co nen thay doi
route_deviation.py trong tuong lai hay khong.

Dung:
    ./.venv/bin/python3 scripts/run_waypoint_accuracy_check.py --sample-n 3000
"""

from __future__ import annotations

import argparse

import polars as pl

from lcx_core.data import load_data_store
from lcx_core.derived_data.trip_waypoints import compute_trip_waypoints, path_length_km
from lcx_core.tier_a.base import percentile_threshold


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample-n", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    store = load_data_store()
    trips = store.table("trips").filter(pl.col("status") == "completed")
    osrm = store.osrm_matrix.rename({"cell_from": "pickup_h3", "cell_to": "drop_h3"})

    sample = trips.sample(n=min(args.sample_n, trips.height), seed=args.seed)
    two_point = sample.join(osrm, on=["pickup_h3", "drop_h3"], how="inner").filter(pl.col("road_km") >= 0.3)
    two_point = two_point.with_columns(
        ((pl.col("distance_km") - pl.col("road_km")) / pl.col("road_km")).alias("deviation_pct_2point")
    )

    waypoints = compute_trip_waypoints(store, sample_n=None, seed=args.seed)
    waypoints = waypoints.filter(pl.col("trip_id").is_in(two_point["trip_id"].to_list()))
    lengths = path_length_km(store, waypoints)

    joined = two_point.join(lengths, on="trip_id", how="inner").filter(pl.col("path_length_km") >= 0.3)
    joined = joined.with_columns(
        ((pl.col("distance_km") - pl.col("path_length_km")) / pl.col("path_length_km")).alias(
            "deviation_pct_path"
        )
    )

    for mode in sorted(joined["travel_mode"].drop_nulls().unique().to_list()):
        subset = joined.filter(pl.col("travel_mode") == mode)
        thr_2point = percentile_threshold(subset["deviation_pct_2point"].to_list(), 0.99)
        thr_path = percentile_threshold(subset["deviation_pct_path"].to_list(), 0.99)

        flagged_2point = set(subset.filter(pl.col("deviation_pct_2point") > thr_2point)["trip_id"].to_list())
        flagged_path = set(subset.filter(pl.col("deviation_pct_path") > thr_path)["trip_id"].to_list())

        only_2point = flagged_2point - flagged_path
        only_path = flagged_path - flagged_2point
        both = flagged_2point & flagged_path

        print(f"=== travel_mode={mode} (n={subset.height}) ===")
        print(f"  nguong 2-diem-mut: {thr_2point:.4f}  ({len(flagged_2point)} flag)")
        print(f"  nguong ca-lo-trinh: {thr_path:.4f}  ({len(flagged_path)} flag)")
        print(f"  trung nhau: {len(both)}  |  chi 2-diem-mut bat: {len(only_2point)}  |  chi ca-lo-trinh bat: {len(only_path)}")
        print()


if __name__ == "__main__":
    main()
