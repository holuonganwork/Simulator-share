"""Unit tests for Taxi Co Huu Rule-based Fraud Detection (F1-F6) & Telemetry Generator.

Validates against:
- FRAUDS.md & GSM Code of Conduct (11/09/2026)
- JSON Schema Draft 2020-12 contracts in schemas/frauds/
- Exoneration principle for cruising / lunch break / recharge
"""

from datetime import datetime, timezone
import polars as pl
import pytest

from lcx_core.frauds import (
    FraudDetectorEngine,
    detect_f1_off_app_pickup,
    detect_f2_route_deviation,
    detect_f3_tcu_signal_loss,
    detect_f4_personal_use,
    detect_f5_fake_trip,
    detect_f6_invalid_driver_cancel,
)


@pytest.fixture
def engine():
    return FraudDetectorEngine(validate_schemas=True)


def test_f1_exoneration_empty_seat_cruising():
    """Tài xế chạy rỗng tìm khách hoặc đi ăn trưa (ghế phụ/sau trống) -> THOÁI TỘI 100%."""
    trips = pl.DataFrame({
        "trip_id": ["t-cancel-1"],
        "driver_id": ["d-honest"],
        "status": ["cancelled"],
        "arrived_at_pickup_time": ["2026-07-01T12:00:00+07:00"],
    })
    telemetry = pl.DataFrame([
        {
            "driver_id": "d-honest",
            "trip_id": None,
            "occurred_at": f"2026-07-01T12:0{i}:00+07:00",
            "speed_kmh": 35.0,
            "odometer_km": 100.0 + i * 0.5,
            "seat_occupancy": {"driver": True, "any_passenger_seat": False},  # Ghế phụ trống!
        }
        for i in range(10)
    ])
    incidents = detect_f1_off_app_pickup(trips, telemetry)
    assert len(incidents) == 0, "Lỗi: Tài xế bị bắt oan dù ghế trống khi nghỉ trưa/tìm khách!"


def test_f1_detected_when_seat_occupied(engine):
    """Tài xế hủy cuốc nhưng xe chạy có khách ngồi -> Bắt lỗi F1 chính xác và schema hợp lệ."""
    trips = pl.DataFrame({
        "trip_id": ["t-f1"],
        "driver_id": ["d-f1"],
        "status": ["cancelled"],
        "arrived_at_pickup_time": ["2026-07-01T14:00:00+07:00"],
        "cancelled_by": ["driver"],
    })
    telemetry = pl.DataFrame([
        {
            "driver_id": "d-f1",
            "trip_id": None,
            "occurred_at": f"2026-07-01T14:0{i}:00+07:00",
            "speed_kmh": 30.0,
            "odometer_km": 100.0 + i * 0.8,
            "seat_occupancy": {"driver": True, "any_passenger_seat": True},  # Có khách ngồi!
        }
        for i in range(6)
    ])
    incidents = detect_f1_off_app_pickup(trips, telemetry)
    assert len(incidents) == 1
    assert incidents[0]["fraud_type"] == "F1_OFF_APP_PICKUP"
    assert incidents[0]["clawback_amount_vnd"] > 0
    assert incidents[0]["ata_penalty_vnd"] == 1_000_000
    assert engine.validate_incident(incidents[0])


def test_f2_route_deviation(engine):
    """Đi lòng vòng tăng cước (>25% quãng đường) -> Bắt lỗi F2 và tính cước hoàn trả."""
    trips = pl.DataFrame({
        "trip_id": ["t-f2"],
        "driver_id": ["d-f2"],
        "status": ["completed"],
        "actual_distance_km": [15.0],
        "optimal_road_km": [10.0],
        "pickup_h3": ["892e00000000000"],
        "drop_h3": ["892e00000000001"],
        "road_tier": ["EXPRESSWAY"],
    })
    incidents = detect_f2_route_deviation(trips)
    assert len(incidents) == 1
    assert incidents[0]["fraud_type"] == "F2_ROUTE_DEVIATION"
    assert incidents[0]["road_tier"] == "EXPRESSWAY"
    assert incidents[0]["excess_fare_vnd"] == (15 - 10) * 13_000
    assert engine.validate_incident(incidents[0])


def test_f2_highway_exoneration(engine):
    """Tài xế chọn lộ trình Cao tốc hợp lệ (highway_alternative_taken) -> Thoái tội hợp lệ."""
    trips = pl.DataFrame({
        "trip_id": ["t-f2-hwy"],
        "driver_id": ["d-f2-hwy"],
        "status": ["completed"],
        "actual_distance_km": [16.0],
        "optimal_road_km": [10.0],
        "pickup_h3": ["892e00000000000"],
        "drop_h3": ["892e00000000001"],
        "road_tier": ["EXPRESSWAY"],
        "highway_alternative_taken": [True],
    })
    incidents = detect_f2_route_deviation(trips)
    assert len(incidents) == 0


def test_f3_tcu_signal_loss(engine):
    """Rút giắc TCU (>15 phút, ODO tăng >= 3km) -> Bắt lỗi F3."""
    telemetry = pl.DataFrame([
        {"driver_id": "d-f3", "occurred_at": "2026-07-01T08:00:00+07:00", "odometer_km": 100.0, "battery_level_pct": 85.0},
        {"driver_id": "d-f3", "occurred_at": "2026-07-01T08:35:00+07:00", "odometer_km": 108.5, "battery_level_pct": 80.0},
    ])
    incidents = detect_f3_tcu_signal_loss(telemetry)
    assert len(incidents) == 1
    assert incidents[0]["fraud_type"] == "F3_TCU_SIGNAL_LOSS"
    assert incidents[0]["gap_minutes"] == 35
    assert incidents[0]["odo_jump_km"] == 8.5
    assert engine.validate_incident(incidents[0])


def test_f4_personal_use(engine):
    """Chạy xe ngoài ca trực >= 3km -> Truy thu bồi hoàn 3.500đ/km."""
    telemetry = pl.DataFrame([
        {"driver_id": "d-f4", "trip_id": None, "occurred_at": f"2026-07-01T02:0{i}:00+07:00", "odometer_km": 200.0 + i * 1.5}
        for i in range(6)
    ])
    shifts = pl.DataFrame({"driver_id": ["d-f4"], "declared_start_min": [360], "declared_end_min": [1080]})
    incidents = detect_f4_personal_use(telemetry, declared_shifts_df=shifts)
    assert len(incidents) == 1
    assert incidents[0]["fraud_type"] == "F4_PERSONAL_USE"
    assert incidents[0]["total_clawback_cost_vnd"] == int(7.5 * 3500)
    assert engine.validate_incident(incidents[0])


def test_f5_fake_trip(engine):
    """Cuốc ảo xe đứng yên -> Phạt Nhóm 1b 10tr và tước sàn 650k."""
    trips = pl.DataFrame({
        "trip_id": ["t-f5"],
        "driver_id": ["d-f5"],
        "status": ["completed"],
        "distance_km": [8.0],
        "pickup_time": ["2026-07-01T15:00:00+07:00"],
        "complete_time": ["2026-07-01T15:25:00+07:00"],
    })
    telemetry = pl.DataFrame([
        {"driver_id": "d-f5", "trip_id": "t-f5", "occurred_at": f"2026-07-01T15:1{i}:00+07:00",
         "speed_kmh": 0.5, "odometer_km": 300.0, "seat_occupancy": {"driver": True, "any_passenger_seat": False}}
        for i in range(5)
    ])
    incidents = detect_f5_fake_trip(trips, telemetry)
    assert len(incidents) == 1
    assert incidents[0]["fraud_type"] == "F5_FAKE_TRIP"
    assert incidents[0]["group_1b_penalty_vnd"] == 10_000_000
    assert incidents[0]["clawback_floor_subsidy_vnd"] == 650_000
    assert engine.validate_incident(incidents[0])


def test_f6_invalid_driver_cancel(engine):
    """Hủy cuốc báo khách không đến nhưng xe cách xa >150m -> Bắt lỗi F6 và tước sàn 650k."""
    trips = pl.DataFrame({
        "trip_id": ["t-f6"],
        "driver_id": ["d-f6"],
        "status": ["cancelled"],
        "cancelled_by": ["driver"],
        "cancel_reason": ["khach_khong_den"],
        "request_time": ["2026-07-01T16:00:00+07:00"],
        "arrived_at_pickup_time": [None],
        "pickup_lat": [21.0285],
        "pickup_lng": [105.8542],
    })
    telemetry = pl.DataFrame([
        {"driver_id": "d-f6", "occurred_at": "2026-07-01T16:05:00+07:00", "lat": 21.0450, "lng": 105.8700}
    ])
    incidents = detect_f6_invalid_driver_cancel(trips, telemetry)
    assert len(incidents) == 1
    assert incidents[0]["fraud_type"] == "F6_INVALID_DRIVER_CANCEL"
    assert incidents[0]["forfeited_floor_amount_vnd"] == 650_000
    assert engine.validate_incident(incidents[0])
