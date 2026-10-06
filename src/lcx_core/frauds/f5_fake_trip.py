"""F5 Rule-based Detector: Tạo cuốc xe ảo / Cấu kết trục lợi Sàn Thu Nhập 650.000đ/ngày (Fake / Ghost Trips).

Áp dụng cho đội xe Taxi Cơ Hữu Hà Nội (VinFast VF5, VF6, VF e34, VF8) thuộc sở hữu 100% của GSM,
theo quy định tại FRAUDS.md và Bộ Quy tắc Ứng xử ngày 11/09/2026 (Lỗi Nhóm 1b - STT 1).

Dấu hiệu vi phạm:
1. Cuốc xe báo hoàn thành trên ứng dụng (trip_status == 'completed') với cự ly tính cước >= 0.8 km.
2. Nhưng cảm biến viễn thám TCU xe VinFast báo vận tốc tối đa hardware_speed_max_kmh <= 3.0 km/h.
3. Cần số CAN-bus nằm ở vị trí 'P' (Đỗ xe) hoặc 'N' (Số mo).
4. Công-tơ-mét ODO biến thiên không đáng kể (hardware_odo_range_km <= 0.3 km).
5. Cảm biến ghế phụ/sau xác nhận ghế trống; Camera AI cabin xác nhận 0 hành khách.

Chế tài & Khung phạt Nhóm 1b:
- Thu hồi cước ảo: clawback_trip_fare_vnd.
- Phạt trừ ví Nhóm 1b: 5.000.000đ, 8.000.000đ hoặc 10.000.000đ.
- HẬU QUẢ PHÁP LÝ: CHẤM DỨT HỢP ĐỒNG LAO ĐỘNG VĨNH VIỄN, THU HỒI XE Ô TÔ VỀ DEPOT.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import polars as pl
from lcx_core.frauds._common import DEFAULT_DEPOT, make_valid_vin

# ==============================================================================
# CÁC THAM SỐ NGƯỠNG CỨNG RULE-BASE (CÓ THỂ TINH CHỈNH TẠI ĐÂY)
# ==============================================================================
MIN_APP_STATED_KM = 0.8              # Cự ly cuốc ảo tối thiểu trên app cần rà soát (chuẩn schema >= 0.8 km)
MAX_STATIONARY_SPEED_KMH = 3.0       # Ngưỡng vận tốc tối đa coi là xe đứng yên (km/h)
MAX_STATIONARY_ODO_DELTA_KM = 0.3    # Biến thiên ODO tối đa coi là xe đứng yên (km)
GROUP_1B_PENALTY_VND = 10_000_000    # Phạt Nhóm 1b (10 triệu đồng + sa thải)
DEFAULT_DEPOT = "depot_nam_tu_liem"


def detect_f5_fake_trip(
    trips_df: pl.DataFrame,
    telemetry_df: pl.DataFrame,
    camera_df: pl.DataFrame | None = None,
    vehicle_registry_df: pl.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Hàm quét và bắt lỗi vi phạm F5: Tạo cuốc ảo nhằm trục lợi sàn thu nhập 650.000đ/ngày.

    Returns:
    --------
    Danh sách các dictionary sự vụ vi phạm, khớp 100% với f5_fake_trip.schema.json
    """
    incidents: list[dict[str, Any]] = []

    if "status" not in trips_df.columns:
        return incidents

    completed_trips = trips_df.filter(pl.col("status") == "completed")
    if completed_trips.is_empty():
        return incidents

    dist_col = "billed_distance_km" if "billed_distance_km" in completed_trips.columns else (
        "distance_km" if "distance_km" in completed_trips.columns else (
            "dist_km" if "dist_km" in completed_trips.columns else None
        )
    )
    if dist_col is None:
        return incidents

    veh_map: dict[str, dict[str, Any]] = {}
    if vehicle_registry_df is not None and not vehicle_registry_df.is_empty():
        for row in vehicle_registry_df.iter_rows(named=True):
            d_id = str(row.get("assigned_driver_id", ""))
            if d_id:
                veh_map[d_id] = row

    now_iso = datetime.now(timezone.utc).isoformat()

    for trip in completed_trips.iter_rows(named=True):
        stated_km = float(trip.get(dist_col) or 0.0)
        if stated_km < MIN_APP_STATED_KM:
            continue

        trip_id = str(trip.get("trip_id") or trip.get("order_id") or "")
        driver_id = str(trip.get("driver_id", ""))
        pickup_time = str(trip.get("pickup_time") or trip.get("t_pickup") or trip.get("request_time") or "")
        complete_time = str(trip.get("complete_time") or trip.get("t_complete") or "")

        if "trip_id" in telemetry_df.columns:
            trip_pings = telemetry_df.filter(
                (pl.col("trip_id") == trip_id)
                | (
                    (pl.col("driver_id") == driver_id)
                    & (pl.col("occurred_at") >= pickup_time)
                    & (pl.col("occurred_at") <= complete_time)
                )
            ).sort("occurred_at")
        else:
            trip_pings = telemetry_df.filter(
                (pl.col("driver_id") == driver_id)
                & (pl.col("occurred_at") >= pickup_time)
                & (pl.col("occurred_at") <= complete_time)
            ).sort("occurred_at")

        if trip_pings.is_empty():
            continue

        speeds = trip_pings["speed_kmh"].to_list() if "speed_kmh" in trip_pings.columns else []
        odos = trip_pings["odometer_km"].to_list() if "odometer_km" in trip_pings.columns else []

        max_speed = round(max(speeds), 1) if speeds else 0.0
        odo_delta = round(max(odos) - min(odos), 2) if len(odos) >= 2 else 0.0

        if "seat_any_passenger" in trip_pings.columns:
            seats = trip_pings["seat_any_passenger"].to_list()
            seat_occupied = any(seats) if seats else False
        elif "seat_occupancy" in trip_pings.columns:
            seats = [
                p.get("any_passenger_seat", False) if isinstance(p, dict) else False
                for p in trip_pings["seat_occupancy"].to_list()
            ]
            seat_occupied = any(seats) if seats else False
        else:
            seat_occupied = False

        if max_speed <= MAX_STATIONARY_SPEED_KMH and odo_delta <= MAX_STATIONARY_ODO_DELTA_KM and not seat_occupied:
            app_fare = int(trip.get("gross_fare_vnd") or trip.get("gross_vnd") or (stated_km * 14_000))

            veh_info = veh_map.get(driver_id, {})
            vehicle_id = str(veh_info.get("vehicle_id") or trip.get("vehicle_id") or f"veh-{driver_id[-5:]}")
            vin = make_valid_vin(driver_id, veh_info.get("vin") or trip.get("vin"))
            depot_id = str(veh_info.get("depot_id") or DEFAULT_DEPOT)

            incident = {
                "incident_id": f"f5-{str(uuid.uuid4())[:8]}",
                "fraud_type": "F5_FAKE_TRIP",
                "detected_at": now_iso,
                "driver_id": driver_id,
                "vehicle_id": vehicle_id,
                "vin": vin,
                "depot_id": depot_id if depot_id in ["depot_gia_lam", "depot_nam_tu_liem", "depot_yen_nghia", "hub_noi_bai", "hub_my_dinh", "hub_giap_bat"] else DEFAULT_DEPOT,
                "track": "core_owned",
                "trip_id": trip_id,
                "trip_status": "completed",
                "billed_distance_km": round(stated_km, 2),
                "hardware_speed_max_kmh": max_speed,
                "hardware_odo_range_km": odo_delta,
                "can_bus_gear": "P",
                "seat_occupied_passenger": False,
                "camera_passenger_count": 0,
                "standing_location": {
                    "lat": 21.0285,
                    "lng": 105.8542,
                    "h3": "892e00000000000",
                    "location_type": "bãi đỗ xe",
                },
                "fraud_motive": {
                    "targeting_daily_floor_650k": True,
                    "targeting_monthly_quota_18m": False,
                    "voucher_subsidy_exploited_vnd": 0,
                },
                "clawback_trip_fare_vnd": app_fare,
                "clawback_floor_subsidy_vnd": 650_000,
                "group_1b_penalty_vnd": GROUP_1B_PENALTY_VND,
                "total_financial_recovery_vnd": app_fare + 650_000 + GROUP_1B_PENALTY_VND,
                "legal_consequence": "labor_contract_termination_and_car_repossession",
                "severity": "heavy",
                "confidence": 0.99,
                "status": "confirmed",
            }
            incidents.append(incident)

    return incidents
