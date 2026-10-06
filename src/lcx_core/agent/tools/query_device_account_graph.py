"""Tool query_device_account_graph - boc lop ToolResult quanh Tier C (con la stub).

Bat MissingDataError va tra ve ToolResult(ok=False, blocked_by="missing_data_tier_c")
thay vi de exception lam sap orchestrator/chat - dung nguyen tac "moi lenh bi
chan phai tra ve ten tang da chan" (xem agent/tools/_common.py).
"""

from __future__ import annotations

from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_c.device_account_graph import MissingDataError, query_cluster_for_driver

TOOL_NAME = "query_device_account_graph"


def run(store: GsmDataStore, driver_id: str) -> ToolResult:
    try:
        clusters = query_cluster_for_driver(store, driver_id)
    except MissingDataError as exc:
        return ToolResult(
            ok=False,
            tool=TOOL_NAME,
            blocked_by="missing_data_tier_c",
            message=str(exc),
            data={"driver_id": driver_id},
        )
    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={"driver_id": driver_id, "clusters": [c.__dict__ for c in clusters]},
    )
