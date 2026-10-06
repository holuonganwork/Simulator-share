"""Tier A - luat nguong cung, danh cho 9 mau hinh gian lan trong docs/strategy.md.

REGISTRY liet ke ca 9 module (5 co logic day du + 3 proxy "THO" co ghi chu ro
+ 2 stub bao loi ro rang vi thieu du lieu) - dung boi scripts/run_tier_a_scan.py
va agent/orchestrator.py de chay dong loat va bat MissingDataError mot cach co
kiem soat (khong crash toan bo pipeline vi mot mau hinh chua lam duoc).
"""

from __future__ import annotations

from lcx_core.tier_a import (
    abnormal_cancel,
    fake_no_show,
    fake_ride_for_kpi,
    gps_distance_inflation,
    location_dropout,
    off_app_cash_pressure,
    ride_preview_selection,
    route_deviation,
    split_long_trip,
    wait_fee_manipulation,
)

# driver_id -> module co ham detect(store, ...) -> RuleResult
REGISTRY = {
    abnormal_cancel.PATTERN: abnormal_cancel,
    route_deviation.PATTERN: route_deviation,
    gps_distance_inflation.PATTERN: gps_distance_inflation,
    split_long_trip.PATTERN: split_long_trip,
    location_dropout.PATTERN: location_dropout,
    wait_fee_manipulation.PATTERN: wait_fee_manipulation,
    fake_ride_for_kpi.PATTERN: fake_ride_for_kpi,
    ride_preview_selection.PATTERN: ride_preview_selection,
    fake_no_show.PATTERN: fake_no_show,           # raise MissingDataError khi goi
    off_app_cash_pressure.PATTERN: off_app_cash_pressure,  # raise MissingDataError khi goi
}

IMPLEMENTED_PATTERNS = [
    abnormal_cancel.PATTERN,
    route_deviation.PATTERN,
    gps_distance_inflation.PATTERN,
    split_long_trip.PATTERN,
    location_dropout.PATTERN,
    wait_fee_manipulation.PATTERN,
    fake_ride_for_kpi.PATTERN,
    ride_preview_selection.PATTERN,
]

PENDING_DATA_PATTERNS = [
    fake_no_show.PATTERN,
    off_app_cash_pressure.PATTERN,
]

__all__ = ["REGISTRY", "IMPLEMENTED_PATTERNS", "PENDING_DATA_PATTERNS"]
