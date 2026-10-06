"""Package lcx_core.frauds: Bộ điều phối và 6 detectors bắt gian lận Rule-based dành cho Taxi Cơ Hữu Hà Nội.

Các loại vi phạm tương ứng 1-1 với schemas/frauds/:
- F1: f1_off_app_pickup (Chở khách ngoài app)
- F2: f2_route_deviation (Kéo dài hành trình cuốc xe)
- F3: f3_tcu_signal_loss (Mất tín hiệu hộp viễn thám TCU)
- F4: f4_personal_use (Sử dụng xe công ty ngoài ca trực)
- F5: f5_fake_trip (Tạo cuốc ảo trục lợi Sàn 650k/ngày)
- F6: f6_invalid_driver_cancel (Hủy sai lý do kén chọn cuốc)

Bộ điều phối trung tâm:
- FraudDetectorEngine: Lớp điều phối quét và phân loại toàn diện
- scan_store_frauds: Hàm tiện ích quét trực tiếp từ DataStore
"""

from __future__ import annotations

from lcx_core.frauds.detector_engine import (
    FraudDetectorEngine,
    FraudScanSummary,
    scan_store_frauds,
)
from lcx_core.frauds.f1_off_app_pickup import detect_f1_off_app_pickup
from lcx_core.frauds.f2_route_deviation import detect_f2_route_deviation
from lcx_core.frauds.f3_tcu_signal_loss import detect_f3_tcu_signal_loss
from lcx_core.frauds.f4_personal_use import detect_f4_personal_use
from lcx_core.frauds.f5_fake_trip import detect_f5_fake_trip
from lcx_core.frauds.f6_invalid_driver_cancel import detect_f6_invalid_driver_cancel

__all__ = [
    "FraudDetectorEngine",
    "FraudScanSummary",
    "scan_store_frauds",
    "detect_f1_off_app_pickup",
    "detect_f2_route_deviation",
    "detect_f3_tcu_signal_loss",
    "detect_f4_personal_use",
    "detect_f5_fake_trip",
    "detect_f6_invalid_driver_cancel",
]
