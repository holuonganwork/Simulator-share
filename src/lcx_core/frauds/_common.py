"""Các tiện ích dùng chung cho toàn bộ 6 bộ luật gian lận Taxi Cơ Hữu trong lcx_core.frauds."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

DEFAULT_DEPOT = "depot_nam_tu_liem"
DEFAULT_VEHICLE_CLASS = "A_COMPACT"
VALID_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def make_valid_vin(driver_id: str, raw_vin: Any = None) -> str:
    """Tạo hoặc làm sạch số khung VIN đúng 17 ký tự chuẩn ISO (không chứa I, O, Q hoặc dấu gạch nối)."""
    if raw_vin:
        clean = "".join(c for c in str(raw_vin).upper() if c in VALID_VIN_CHARS)
        if len(clean) == 17:
            return clean
        if len(clean) > 17:
            return clean[:17]

    # Fallback sinh VIN hợp lệ từ driver_id: tiền tố VF8HN (5 ký tự) + 12 chữ số = 17 ký tự
    seed_num = abs(hash(str(driver_id))) % 1_000_000_000_000
    res = f"VF8HN{seed_num:012d}"
    return res[:17]


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Tính khoảng cách đường chim bay giữa 2 điểm tọa độ GPS theo mét."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def parse_iso(iso_str: Any) -> datetime | None:
    """Parse chuỗi ISO 8601 sang datetime an toàn."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except Exception:
        return None


def now_utc_iso() -> str:
    """Lấy thời điểm hiện tại dạng chuỗi ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()
