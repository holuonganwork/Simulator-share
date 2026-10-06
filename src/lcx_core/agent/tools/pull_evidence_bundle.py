"""Tool pull_evidence_bundle - gop bang chung cho MOT case.

Chien luoc goc mo ta bundle gom "GPS trace, anh, log cam bien, lich su chat".
13 bang hien co CHI cung cap duoc doan trace hex (khong anh, khong log cam
bien, khong lich su chat that - chua co he thong nhan tin that). Bundle o day
ghi ro TUNG PHAN nao co du lieu that, TUNG PHAN nao khong co thay vi gia vo day du.
"""

from __future__ import annotations

import polars as pl

from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.labels import get_case

TOOL_NAME = "pull_evidence_bundle"


def run(store: GsmDataStore, case_id: str) -> ToolResult:
    try:
        case = get_case(case_id)
    except KeyError:
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="case_not_found",
            message=f"Khong tim thay case_id={case_id}.",
        )

    hex_trace = (
        store.table("public_driver_hex_tracking")
        .filter(pl.col("driver_id") == case.driver_id)
        .sort("entered_current_hex_at")
        .tail(50)  # 50 ban ghi gan nhat quanh thoi diem case, du de xem boi canh
    )

    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={
            "case_id": case_id,
            "driver_id": case.driver_id,
            "pattern": case.pattern,
            "case_evidence": case.evidence,
            "hex_trace_available": True,
            "hex_trace_sample": hex_trace.tail(10).to_dicts(),
            "n_hex_trace_records": hex_trace.height,
            "photos_available": False,
            "sensor_logs_available": False,
            "chat_history_available": False,
            "bundle_gap_note": (
                "Chua co anh/log cam bien/lich su chat that trong 13 bang hien co - "
                "xem docs/appendix-pending-data.md. Bundle chi gom evidence cua case "
                "va doan trace hex gan nhat."
            ),
        },
    )
