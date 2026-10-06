"""Tool get_driver_risk_profile - lich su gan co, ty le huy, tham nien.

Du lieu that dung: driver_statistic_daily (ty le huy/hoan thanh), var/cases (lich
su case tu label_store). "Tham nien" o day la SO NGAY XUAT HIEN TRONG CUA SO
QUAN SAT cua bo du lieu mock (90 ngay tu 2026-04-03), KHONG phai ngay gia nhap
that cua tai xe - 13 bang khong co truong ngay gia nhap/hop dong.
"""

from __future__ import annotations

import polars as pl

from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.labels import list_cases

TOOL_NAME = "get_driver_risk_profile"


def run(store: GsmDataStore, driver_id: str) -> ToolResult:
    stats = store.table("driver_statistic_daily").filter(pl.col("driver_id") == driver_id)
    if stats.height == 0:
        return ToolResult(
            ok=False,
            tool=TOOL_NAME,
            blocked_by="driver_not_found",
            message=f"Khong tim thay driver_id={driver_id} trong driver_statistic_daily.",
        )

    total_accepted = stats["accepted_count"].sum()
    total_cancelled = stats["cancelled_count"].sum()
    total_completed = stats["completed_count"].sum()
    avg_cancellation_rate = stats["cancellation_rate"].mean()
    days_observed = stats.height

    cases = list_cases(driver_id=driver_id)

    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={
            "driver_id": driver_id,
            "days_observed_in_window": days_observed,
            "days_observed_caveat": (
                "So ngay xuat hien trong cua so du lieu mock (90 ngay tu 2026-04-03), "
                "KHONG phai tham nien hop tac that."
            ),
            "total_accepted": total_accepted,
            "total_cancelled": total_cancelled,
            "total_completed": total_completed,
            "avg_cancellation_rate": round(avg_cancellation_rate, 4),
            "case_history": [
                {
                    "case_id": c.case_id,
                    "pattern": c.pattern,
                    "status": c.status,
                    "severity": c.severity,
                    "created_at": c.created_at,
                }
                for c in cases
            ],
            "n_prior_cases": len(cases),
            "n_confirmed_cases": len([c for c in cases if c.status == "confirmed"]),
        },
    )
