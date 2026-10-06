"""derived_tier - detector doc bang du lieu DAN XUAT (fare_breakdown,...) tu
du lieu that cua GSM_SIMULATOR, sinh boi lcx_core.derived_data. Tach khoi
tier_a vi input la pl.DataFrame don le (khong phai GsmDataStore day du)."""

from __future__ import annotations

from lcx_core.derived_tier import artificial_surge_collusion

REGISTRY = {
    artificial_surge_collusion.PATTERN: artificial_surge_collusion,
}

PATTERNS = list(REGISTRY)

__all__ = ["REGISTRY", "PATTERNS"]
