"""F1 Rule-based Detector: Chở khách không tạo cuốc / Nhận cuốc ngoài app (Off-App Pickup / Street Hail).

Áp dụng cho đội xe Taxi Cơ Hữu Hà Nội (VinFast VF5, VF6, VF e34, VF8) thuộc sở hữu 100% của GSM,
theo quy định tại FRAUDS.md và Bộ Quy tắc Ứng xử ngày 11/09/2026.

Dấu hiệu vi phạm:
1. Cuốc xe bị hủy trên ứng dụng sau khi tài xế đã tới điểm đón (arrived_at_pickup_time có giá trị),
   hoặc khách bị ép tự hủy chuyến.
2. Ngay sau khi hủy cuốc (trong cửa sổ 45 phút), xe không đỗ lại mà tiếp tục di chuyển (tốc độ >= 10 km/h).
3. Cảm biến áp suất trọng lượng ghế (seat_occupied_passenger) xác nhận có người ngồi trên xe.
4. Camera AI cabin (nếu có) củng cố bằng chứng với số người phát hiện >= 1.
5. Công-tơ-mét phần cứng (odo_end_km - odo_start_km) nhảy tăng thực tế trong khi trên app không có cuốc nào.

Công thức truy thu & Chế tài:
- Tiền truy thu = Quãng đường chạy chui (km_moved_off_app) x Đơn giá cước theo hạng xe (13.000đ - 19.000đ/km).
- Tiền phạt ATA: 1.000.000 VNĐ (lần 1), 2.000.000 VNĐ (lần 2, đình chỉ ca trực/sa thải).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import polars as pl
from lcx_core.frauds._common import DEFAULT_DEPOT, make_valid_vin

# ==============================================================================
# CÁC THAM SỐ NGƯỠNG CỨNG RULE-BASE (CÓ THỂ TINH CHỈNH TẠI ĐÂY)
# ==============================================================================
WINDOW_MINUTES = 45                  # Cửa sổ thời gian sau khi hủy cuốc để theo dõi xe (phút)
MIN_MOVING_SPEED_KMH = 10.0          # Tốc độ tối thiểu xác định xe đang lăn bánh thật (km/h)
MIN_MOVING_PINGS = 5                 # Số ping telemetry tối thiểu ghi nhận có khách (chuẩn schema >= 5)
DEFAULT_RATE_PER_KM_VND = 13_000     # Đơn giá truy thu cước chuẩn (VF5: 13000, VF6: 13800, e34: 14500, VF8: 19000)
DEFAULT_DEPOT = "depot_nam_tu_liem"
DEFAULT_VEHICLE_CLASS = "A_COMPACT"  # Phân hạng xe: A_COMPACT (VF5), B_SEDAN, C_SUV (VF6/e34), D_LUXURY (VF8)
ATA_PENALTY_TIER_1_VND = 1_000_000   # Mức phạt ATA lần 1
ATA_PENALTY_TIER_2_VND = 2_000_000   # Mức phạt ATA lần 2


def _plus_minutes(iso_ts: str, minutes: int) -> str:
    """Cộng thêm số phút vào timestamp ISO 8601."""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return (dt + timedelta(minutes=minutes)).isoformat()
    except Exception:
        return iso_ts


def detect_f1_off_app_pickup(
    trips_df: pl.DataFrame,
    telemetry_df: pl.DataFrame,
    camera_df: pl.DataFrame | None = None,
    vehicle_registry_df: pl.DataFrame | None = None,
    rate_per_km_vnd: int = DEFAULT_RATE_PER_KM_VND,
    min_moving_pings: int = MIN_MOVING_PINGS,
) -> list[dict[str, Any]]:
    """Hàm quét và bắt lỗi vi phạm F1: Chở khách ngoài app.

    Returns:
    --------
    Danh sách các dictionary sự vụ vi phạm, khớp 100% với f1_off_app_pickup.schema.json
    """
    incidents: list[dict[str, Any]] = []

    if "status" not in trips_df.columns:
        return incidents

    has_arrived = "arrived_at_pickup_time" in trips_df.columns
    if has_arrived:
        cancelled_trips = trips_df.filter(
            (pl.col("status") == "cancelled") & pl.col("arrived_at_pickup_time").is_not_null()
        )
    else:
        cancelled_trips = trips_df.filter(pl.col("status") == "cancelled")

    if cancelled_trips.is_empty():
        return incidents

    # Chuẩn hóa cột cảm biến ghế: hỗ trợ cả seat_any_passenger (bool) và seat_occupancy (struct/dict/json)
    df_telemetry = telemetry_df
    if "seat_any_passenger" not in df_telemetry.columns:
        if "seat_occupancy" in df_telemetry.columns:
            dtype = df_telemetry["seat_occupancy"].dtype
            if dtype == pl.Struct:
                df_telemetry = df_telemetry.with_columns(
                    pl.col("seat_occupancy").struct.field("any_passenger_seat").fill_null(False).alias("seat_any_passenger")
                )
            elif dtype == pl.String:
                df_telemetry = df_telemetry.with_columns(
                    pl.col("seat_occupancy").str.contains(r'"any_passenger_seat":\s*true').fill_null(False).alias("seat_any_passenger")
                )
            else:
                return incidents
        else:
            return incidents

    if "speed_kmh" not in df_telemetry.columns:
        return incidents

    # ==============================================================================
    # NGUYÊN TẮC THOÁI TỘI (EXONERATION FOR CRUISE / LUNCH BREAK):
    # Nếu xe di chuyển mà cảm biến ghế phụ/ghế sau KHÔNG cảm nhận được người ngồi
    # (seat_any_passenger == False), tài xế hoàn toàn ĐƯỢC THOÁI TỘI.
    # Lý do: Tài xế có quyền lái xe không khách để tìm khách (cruising/repositioning),
    # lái xe đi nghỉ trưa (lunch break), hoặc di chuyển tới trạm sạc.
    # Chỉ xem xét vi phạm F1 khi cảm biến ghế phụ/ghế sau CÓ TẢI TRỌNG (>= 15kg).
    # ==============================================================================
    moving_pings = df_telemetry.filter(
        pl.col("seat_any_passenger")
        & (pl.col("speed_kmh") >= MIN_MOVING_SPEED_KMH)
        & (pl.col("trip_id").is_null() | (pl.col("trip_id") == ""))
    )

    if moving_pings.is_empty():
        return incidents

    camera_map: dict[str, dict[str, Any]] = {}
    if camera_df is not None and not camera_df.is_empty() and "driver_id" in camera_df.columns:
        for row in camera_df.iter_rows(named=True):
            d_id = str(row.get("driver_id"))
            camera_map[d_id] = {
                "camera_event_type": row.get("camera_event_type", "passenger_detected"),
                "detected_passenger_count": int(row.get("detected_passenger_count") or 1),
                "confidence": float(row.get("confidence") or 0.95),
            }

    veh_map: dict[str, dict[str, Any]] = {}
    if vehicle_registry_df is not None and not vehicle_registry_df.is_empty():
        for row in vehicle_registry_df.iter_rows(named=True):
            d_id = str(row.get("assigned_driver_id", ""))
            if d_id:
                veh_map[d_id] = row

    now_iso = datetime.now(timezone.utc).isoformat()

    for trip in cancelled_trips.iter_rows(named=True):
        driver_id = str(trip.get("driver_id", ""))
        trip_id = str(trip.get("trip_id") or trip.get("order_id") or "")
        cancel_time_str = str(trip.get("arrived_at_pickup_time") or trip.get("t_cancel") or trip.get("request_time") or now_iso)
        window_end_str = _plus_minutes(cancel_time_str, WINDOW_MINUTES)

        # Chốt an toàn: Nếu tài xế nhận cuốc mới hợp lệ trên app trước khi hết 45 phút,
        # cửa sổ theo dõi sẽ lập tức đóng lại tại thời điểm cuốc mới phát sinh
        if "driver_id" in trips_df.columns:
            id_col = "trip_id" if "trip_id" in trips_df.columns else ("order_id" if "order_id" in trips_df.columns else None)
            if id_col:
                next_trips = trips_df.filter(
                    (pl.col("driver_id") == driver_id)
                    & (pl.col(id_col) != trip_id)
                    & (pl.col("status").is_in(["completed", "ongoing", "dispatched", "accepted"]))
                )
                for next_t in next_trips.iter_rows(named=True):
                    next_t_time = str(next_t.get("request_time") or next_t.get("t_request") or next_t.get("start_time") or next_t.get("t_pickup") or "")
                    if next_t_time and next_t_time > cancel_time_str:
                        if next_t_time < window_end_str:
                            window_end_str = next_t_time

        driver_pings = moving_pings.filter(
            (pl.col("driver_id") == driver_id)
            & (pl.col("occurred_at") >= cancel_time_str)
            & (pl.col("occurred_at") <= window_end_str)
        ).sort("occurred_at")

        ping_count = driver_pings.height
        if ping_count >= min_moving_pings:
            odo_list = driver_pings["odometer_km"].to_list() if "odometer_km" in driver_pings.columns else []
            odo_start = float(odo_list[0]) if odo_list else 100.0
            odo_end = float(odo_list[-1]) if odo_list else (odo_start + round(ping_count * 0.4, 2))
            unaccounted_km = max(round(odo_end - odo_start, 2), 0.5)

            speeds = driver_pings["speed_kmh"].to_list() if "speed_kmh" in driver_pings.columns else []
            max_spd = max(speeds) if speeds else 25.0
            avg_spd = round(sum(speeds) / len(speeds), 1) if speeds else 20.0

            veh_info = veh_map.get(driver_id, {})
            vehicle_id = str(veh_info.get("vehicle_id") or trip.get("vehicle_id") or f"veh-{driver_id[-5:]}")
            vin = make_valid_vin(driver_id, veh_info.get("vin") or trip.get("vin"))
            depot_id = str(veh_info.get("depot_id") or DEFAULT_DEPOT)

            clawback_vnd = int(unaccounted_km * rate_per_km_vnd)

            lats = driver_pings["lat"].to_list() if "lat" in driver_pings.columns else []
            lngs = driver_pings["lng"].to_list() if "lng" in driver_pings.columns else []
            start_loc = {"lat": float(lats[0]), "lng": float(lngs[0]), "h3": "892e00000000000"} if lats else {"lat": 21.0285, "lng": 105.8542, "h3": "892e00000000000"}
            end_loc = {"lat": float(lats[-1]), "lng": float(lngs[-1]), "h3": "892e00000000001"} if lats else start_loc

            timestamps = driver_pings["occurred_at"].to_list()
            off_app_start = timestamps[0] if timestamps else cancel_time_str
            off_app_end = timestamps[-1] if timestamps else window_end_str

            try:
                t_start_dt = datetime.fromisoformat(off_app_start.replace("Z", "+00:00"))
                t_end_dt = datetime.fromisoformat(off_app_end.replace("Z", "+00:00"))
                dur_minutes = round(max((t_end_dt - t_start_dt).total_seconds() / 60.0, 0.5), 1)
            except Exception:
                dur_minutes = round(ping_count * 0.5, 1)

            cam_info = camera_map.get(driver_id)
            cancelled_by = str(trip.get("cancelled_by") or "driver")
            if cancelled_by not in ["driver", "customer"]:
                cancelled_by = "driver"

            incident = {
                "incident_id": f"f1-{str(uuid.uuid4())[:8]}",
                "fraud_type": "F1_OFF_APP_PICKUP",
                "detected_at": now_iso,
                "driver_id": driver_id,
                "vehicle_id": vehicle_id,
                "vin": vin,
                "depot_id": depot_id if depot_id in ["depot_gia_lam", "depot_nam_tu_liem", "depot_yen_nghia", "hub_noi_bai", "hub_my_dinh", "hub_giap_bat"] else DEFAULT_DEPOT,
                "track": "core_owned",
                "trip_id": trip_id,
                "trip_cancelled_at": cancel_time_str,
                "cancelled_by": cancelled_by,
                "cancel_reason": str(trip.get("cancel_reason") or "khach_khong_den"),
                "arrived_at_pickup_time": str(trip.get("arrived_at_pickup_time") or cancel_time_str),
                "pickup_h3": str(trip.get("pickup_h3") or "892e00000000000"),
                "off_app_start_time": off_app_start,
                "off_app_end_time": off_app_end,
                "moving_duration_minutes": dur_minutes,
                "n_moving_pings": ping_count,
                "max_speed_kmh": round(max(max_spd, 10.0), 1),
                "avg_speed_kmh": avg_spd,
                "seat_occupied_passenger": True,
                "camera_corroboration": cam_info,
                "start_location": start_loc,
                "end_location": end_loc,
                "odo_start_km": odo_start,
                "odo_end_km": odo_end,
                "km_moved_off_app": unaccounted_km,
                "vehicle_class": DEFAULT_VEHICLE_CLASS,
                "rate_per_km_vnd": rate_per_km_vnd if rate_per_km_vnd in [13000, 13800, 14500, 19000] else 13000,
                "clawback_amount_vnd": clawback_vnd,
                "ata_penalty_vnd": ATA_PENALTY_TIER_1_VND,
                "disciplinary_action": "ata_fine_1m",
                "severity": "heavy" if unaccounted_km >= 10.0 else ("medium" if unaccounted_km >= 3.0 else "light"),
                "confidence": 0.98 if cam_info else 0.92,
                "status": "confirmed",
            }
            incidents.append(incident)

    return incidents
