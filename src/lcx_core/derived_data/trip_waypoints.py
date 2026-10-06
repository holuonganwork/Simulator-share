"""trip_waypoints - hang muc 3 "Chien luoc lam giau du lieu" (data hanh trinh
vi tri). KHONG mo mau hinh moi (dung nhu danh gia trong tai lieu chien luoc:
"cai thien do chinh xac cho mau DA co luat, khong mo mau moi") - cai thien do
chinh xac cua route_deviation bang cach so CA LO TRINH thay vi chi 2 diem mut.

public_driver_hex_tracking (nguon hien co) ghi theo DRIVER, khong gan chac voi
MOT trip_id cu the (xem docs/appendix-pending-data.md, Phan A) - bang nay vá
dung khoang trong do: moi dong la MOT DIEM tren duong di CUA MOT CHUYEN CU THE.

Vi khong co GPS tho, waypoint duoc NOI SUY tu chinh ma tran OSRM da co
(research/simulation/data/osrm_matrix_dd.parquet) qua h3.grid_path_cells giua
pickup_h3 va drop_h3 - CHINH LA ky thuat da dung trong
telemetry_mock/drivers.py (_pick_destination) - khong bia duong di moi, chi
noi suy giua 2 diem THAT da co trong trips.parquet."""

from __future__ import annotations

import h3
import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore


def compute_trip_waypoints(store: GsmDataStore, sample_n: int | None = None, seed: int = 42) -> pl.DataFrame:
    """Tra ve (trip_id, seq, hex) - moi trip 1 chuoi diem tu pickup_h3 den
    drop_h3 qua h3.grid_path_cells. `sample_n` gioi han so trip xu ly (toan bo
    174k trip co the cham - mac dinh None = xu ly het)."""
    trips = store.table("trips").filter(pl.col("status") == "completed")
    trips = trips.select(["trip_id", "pickup_h3", "drop_h3"])
    if sample_n is not None:
        trips = trips.sample(n=min(sample_n, trips.height), seed=seed)

    rows: list[dict] = []
    for row in trips.iter_rows(named=True):
        try:
            path = h3.grid_path_cells(row["pickup_h3"], row["drop_h3"])
        except Exception:
            path = [row["pickup_h3"], row["drop_h3"]]
        for seq, hexid in enumerate(path):
            rows.append({"trip_id": row["trip_id"], "seq": seq, "hex": hexid})
    return pl.DataFrame(rows, schema={"trip_id": pl.Utf8, "seq": pl.Int64, "hex": pl.Utf8})


def path_length_km(store: GsmDataStore, waypoints: pl.DataFrame) -> pl.DataFrame:
    """Tinh tong do dai CA LO TRINH (tong road_km giua tung cap diem lien tiep
    trong waypoints, tra OSRM) - de so sanh voi road_km TOI UU 2-diem-mut hien
    dung trong route_deviation.py. Tra ve (trip_id, path_length_km)."""
    osrm = store.osrm_matrix.rename({"cell_from": "hex", "cell_to": "hex_next", "road_km": "leg_km"})
    pairs = waypoints.sort(["trip_id", "seq"]).with_columns(
        pl.col("hex").shift(-1).over("trip_id").alias("hex_next")
    )
    pairs = pairs.filter(pl.col("hex_next").is_not_null())
    joined = pairs.join(osrm.select(["hex", "hex_next", "leg_km"]), on=["hex", "hex_next"], how="left")
    joined = joined.with_columns(pl.col("leg_km").fill_null(0.0))
    return joined.group_by("trip_id").agg(pl.col("leg_km").sum().alias("path_length_km"))
