"""Tool check_location_integrity - co mock-location, buoc nhay toa do bat thuong.

Loc ket qua gps_distance_inflation (Tier A) + location_dropout (Tier A) theo
dung driver_id, tra ve danh sach lan vi pham thay vi chi so tong hop - de agent
co the trich dan bang chung cu the khi hoi tai xe.
"""

from __future__ import annotations

from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a import gps_distance_inflation, location_dropout

TOOL_NAME = "check_location_integrity"


def run(store: GsmDataStore, driver_id: str) -> ToolResult:
    speed_result = gps_distance_inflation.detect(store)
    dropout_result = location_dropout.detect(store)

    speed_flags = [f.to_dict() for f in speed_result.flags if f.driver_id == driver_id]
    dropout_flags = [f.to_dict() for f in dropout_result.flags if f.driver_id == driver_id]

    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={
            "driver_id": driver_id,
            "implausible_speed_events": speed_flags,
            "n_implausible_speed_events": len(speed_flags),
            "location_dropout_events": dropout_flags,
            "n_location_dropout_events": len(dropout_flags),
        },
    )
