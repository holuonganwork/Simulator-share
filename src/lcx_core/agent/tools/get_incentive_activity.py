"""Tool get_incentive_activity - so cuoc/thuong trong khung gio so voi phan phoi chuan.

Dung public_mission_earn_history + kpi_driver_platform_calculator_gbq. Tai su
dung z-score tren tien thuong tuan tu fake_ride_for_kpi.py nhung cho MOT driver
cu the, kem chi tiet tung dong mission_earn de agent trich dan.
"""

from __future__ import annotations

import polars as pl

from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a import fake_ride_for_kpi

TOOL_NAME = "get_incentive_activity"


def run(store: GsmDataStore, driver_id: str) -> ToolResult:
    earn = store.table("public_mission_earn_history").filter(pl.col("driver_id") == driver_id)
    kpi = store.table("kpi_driver_platform_calculator_gbq").filter(pl.col("driver_id") == driver_id)

    result = fake_ride_for_kpi.detect(store)
    driver_flags = [f.to_dict() for f in result.flags if f.driver_id == driver_id]

    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={
            "driver_id": driver_id,
            "n_mission_earn_records": earn.height,
            "total_earn_vnd": int(earn["earn"].sum()) if earn.height else 0,
            "kpi_weeks_tracked": kpi.height,
            "weekly_earn_anomalies": driver_flags,
            "n_weekly_earn_anomalies": len(driver_flags),
        },
    )
