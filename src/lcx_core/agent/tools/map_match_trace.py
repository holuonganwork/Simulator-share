"""Tool map_match_trace - phat hien "day o hex" (teleport) khong lien tuc.

Khong co GPS tho de chay HMM map-matching that (docs/strategy.md muc 06 mo ta
day la ky thuat cho toa do chinh xac). Proxy o day KHAC voi
gps_distance_inflation (van toc): dung h3.grid_distance de dem SO O HEX BI BO
QUA giua hai lan ghi nhan lien tiep cung driver - mot buoc nhay day nhieu vong o
(grid_distance lon) trong MOT LAN CAP NHAT la dau hieu mat lien tuc du lieu vi
tri (spoofing hoac mat song GPS), khac voi van toc cao lien tuc.
"""

from __future__ import annotations

import h3
import polars as pl

from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore

TOOL_NAME = "map_match_trace"
MIN_RING_GAP_TO_FLAG = 3  # bo qua di chuyen 1-2 o lang gieng (binh thuong)


def run(store: GsmDataStore, driver_id: str, local_date: str | None = None) -> ToolResult:
    hex_t = store.table("public_driver_hex_tracking").filter(
        (pl.col("driver_id") == driver_id) & pl.col("last_hex").is_not_null()
    )
    if local_date:
        hex_t = hex_t.filter(pl.col("entered_current_hex_at").str.starts_with(local_date))

    if hex_t.height == 0:
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="no_tracking_data",
            message=f"Khong co ban ghi public_driver_hex_tracking cho driver_id={driver_id}"
            + (f" ngay {local_date}" if local_date else "") + ".",
        )

    residuals = []
    for row in hex_t.iter_rows(named=True):
        if row["last_hex"] == row["current_hex"]:
            continue
        ring_gap = h3.grid_distance(row["last_hex"], row["current_hex"])
        if ring_gap >= MIN_RING_GAP_TO_FLAG:
            residuals.append(
                {
                    "from_hex": row["last_hex"],
                    "to_hex": row["current_hex"],
                    "grid_distance": ring_gap,
                    "at": row["entered_current_hex_at"],
                }
            )

    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={
            "driver_id": driver_id,
            "local_date": local_date,
            "n_records_checked": hex_t.height,
            "teleport_events": residuals,
            "n_teleport_events": len(residuals),
            "caveat": (
                "Proxy tren luoi H3 res-9 theo phut, KHONG phai map-matching HMM "
                "tren GPS lien tuc that. Xem docs/data-matrix.md."
            ),
        },
    )
