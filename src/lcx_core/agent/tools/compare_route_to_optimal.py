"""Tool compare_route_to_optimal - % lech quang duong/thoi gian cho MOT chuyen cu the.

Tra cuu truc tiep 1 trip_id trong trips.parquet, doi chieu voi ma tran OSRM (dung
logic giong route_deviation.py nhung cho 1 dong thay vi ca bang) - dung khi agent
da co trip_id cu the tu mot case, khong can chay lai toan bo Tier A.
"""

from __future__ import annotations

import polars as pl

from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore

TOOL_NAME = "compare_route_to_optimal"


def run(store: GsmDataStore, trip_id: str) -> ToolResult:
    trips = store.table("trips").filter(pl.col("trip_id") == trip_id)
    if trips.height == 0:
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="trip_not_found",
            message=f"Khong tim thay trip_id={trip_id}.",
        )
    trip = trips.row(0, named=True)

    osrm = store.osrm_matrix.filter(
        (pl.col("cell_from") == trip["pickup_h3"]) & (pl.col("cell_to") == trip["drop_h3"])
    )
    if osrm.height == 0:
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="no_osrm_route",
            message="Khong co du lieu OSRM cho cap hex nay.",
            data={"trip_id": trip_id, "pickup_h3": trip["pickup_h3"], "drop_h3": trip["drop_h3"]},
        )
    route = osrm.row(0, named=True)
    road_km = route["road_km"]
    deviation_pct = (trip["distance_km"] - road_km) / road_km if road_km else None

    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={
            "trip_id": trip_id,
            "driver_id": trip["driver_id"],
            "travel_mode": trip["travel_mode"],
            "recorded_distance_km": trip["distance_km"],
            "recorded_duration_seconds": trip["duration_seconds"],
            "optimal_road_km": road_km,
            "optimal_duration_seconds": route["duration_s"],
            "deviation_pct": round(deviation_pct, 4) if deviation_pct is not None else None,
            "caveat": (
                "deviation_pct mang do lech he thong am (~-32% trung binh tren toan "
                "bo du lieu) vi distance_km co ve la khoang cach duong chim, khong "
                "phai khoang cach theo mang luoi duong. Doc kem 2 so tho, khong chi %."
            ),
        },
    )
