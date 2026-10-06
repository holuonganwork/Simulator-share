"""telemetry_tier - luat nguong cung cho 4 mau hinh moi mo khoa boi Khoi 1 cua
"Chien luoc lam giau du lieu" (hang muc 2 camera, 4 action/state, 6-log
charging/vehicle). Cung dang RuleFlag/RuleResult voi tier_a (tai su dung
tier_a/base.py) nhung doc tu EventDataStore (lcx_core.event_sim) thay vi
GsmDataStore - hai tang du lieu doc lap, KHONG tron REGISTRY voi tier_a de
tranh scripts/run_tier_a_scan.py goi nham store sai kieu.
"""

from __future__ import annotations

from lcx_core.telemetry_tier import (
    battery_swap_validation,
    camera_intervention,
    fake_trip,
    ghost_vehicle,
    invalid_driver_cancel,
    off_app_pickup,
    personal_use,
    tcu_signal_loss,
    virtual_online_status,
)

REGISTRY = {
    virtual_online_status.PATTERN: virtual_online_status,
    camera_intervention.PATTERN: camera_intervention,
    battery_swap_validation.PATTERN: battery_swap_validation,
    ghost_vehicle.PATTERN: ghost_vehicle,
    # F1/F3/F4/F5/F6 cua Taxi Co Huu (FRAUDS.md); F2 dung tier_a/route_deviation
    off_app_pickup.PATTERN: off_app_pickup,
    tcu_signal_loss.PATTERN: tcu_signal_loss,
    personal_use.PATTERN: personal_use,
    fake_trip.PATTERN: fake_trip,
    invalid_driver_cancel.PATTERN: invalid_driver_cancel,
}

PATTERNS = list(REGISTRY)

__all__ = ["REGISTRY", "PATTERNS"]
