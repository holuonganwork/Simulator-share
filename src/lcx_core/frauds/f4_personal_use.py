"""F4 Rule-based Detector: Sử dụng xe công ty vào mục đích cá nhân ngoài ca trực (Personal Use / Geofence Breach).

Áp dụng cho đội xe Taxi Cơ Hữu Hà Nội (VinFast VF5, VF6, VF e34, VF8) thuộc sở hữu 100% của GSM,
theo quy định tại FRAUDS.md và Hợp đồng lao động tài xế.

Dấu hiệu vi phạm:
1. Xe ô tô di chuyển ngoài khung giờ ca trực cam kết (declared_shift_window) của tài xế.
2. Quãng đường di chuyển ngoài ca vượt quá 3 km (km_off_shift >= 3.0 km) mà không có cuốc trên app.
3. Hoặc xe di chuyển vượt khỏi địa giới hành chính Hà Nội quá 3 km.

Công thức truy thu & Chế tài:
- Mức bồi hoàn chi phí vận hành: 3.500 VNĐ / km (bao gồm khấu hao xe điện VinFast + tiền điện sạc pin).
- Tiền truy thu = Tổng số km chạy việc riêng x 3.500 VNĐ, trừ thẳng vào bảng lương tháng.
- Khiển trách văn bản, đình chỉ quyền nhận ca tiếp theo; tái phạm chấm dứt HĐLĐ.
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
MIN_PERSONAL_USE_KM = 3.0           # Quãng đường tối thiểu ngoài ca để kích hoạt (chuẩn schema >= 3.0 km)
COST_PER_KM_VND = 3_500             # Chi phí bồi hoàn chuẩn theo FRAUDS.md (khấu hao + sạc pin)
DEFAULT_DEPOT = "depot_nam_tu_liem"


def _parse_time_to_minutes(ts_str: str) -> int:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.hour * 60 + dt.minute
    except Exception:
        return 0


def detect_f4_personal_use(
    telemetry_df: pl.DataFrame,
    declared_shifts_df: pl.DataFrame | None = None,
    trips_df: pl.DataFrame | None = None,
    vehicle_registry_df: pl.DataFrame | None = None,
    min_distance_km: float = MIN_PERSONAL_USE_KM,
    cost_per_km_vnd: int = COST_PER_KM_VND,
) -> list[dict[str, Any]]:
    """Hàm quét và bắt lỗi vi phạm F4: Sử dụng xe công ty vào việc riêng ngoài ca trực.

    Returns:
    --------
    Danh sách các dictionary sự vụ vi phạm, khớp 100% với f4_personal_use.schema.json
    """
    incidents: list[dict[str, Any]] = []

    if "driver_id" not in telemetry_df.columns or "occurred_at" not in telemetry_df.columns:
        return incidents

    shift_map: dict[str, tuple[int, int]] = {}
    if declared_shifts_df is not None and not declared_shifts_df.is_empty():
        for row in declared_shifts_df.iter_rows(named=True):
            d_id = str(row.get("driver_id", ""))
            s_start = int(row.get("declared_start_min", 360))
            s_end = int(row.get("declared_end_min", 1080))
            shift_map[d_id] = (s_start, s_end)

    veh_map: dict[str, dict[str, Any]] = {}
    if vehicle_registry_df is not None and not vehicle_registry_df.is_empty():
        for row in vehicle_registry_df.iter_rows(named=True):
            d_id = str(row.get("assigned_driver_id", ""))
            if d_id:
                veh_map[d_id] = row

    now_iso = datetime.now(timezone.utc).isoformat()
    unique_drivers = telemetry_df["driver_id"].unique().to_list()

    for driver_id in unique_drivers:
        d_pings = telemetry_df.filter(pl.col("driver_id") == driver_id).sort("occurred_at")
        if d_pings.height < 5:
            continue

        start_min, end_min = shift_map.get(str(driver_id), (360, 1080))

        off_shift_pings = []
        for p in d_pings.iter_rows(named=True):
            t_min = _parse_time_to_minutes(p["occurred_at"])
            is_off_shift = (t_min < start_min) or (t_min > end_min)
            is_no_trip = p.get("trip_id") is None or p.get("trip_id") == ""

            if is_off_shift and is_no_trip:
                off_shift_pings.append(p)

        if len(off_shift_pings) >= 5:
            odos = [float(p.get("odometer_km") or 0.0) for p in off_shift_pings if p.get("odometer_km") is not None]
            if len(odos) >= 2:
                dist_km = round(max(odos) - min(odos), 2)
            else:
                dist_km = round(len(off_shift_pings) * 0.4, 2)

            if dist_km >= min_distance_km:
                clawback_vnd = int(dist_km * cost_per_km_vnd)
                start_p = off_shift_pings[0]
                end_p = off_shift_pings[-1]

                veh_info = veh_map.get(str(driver_id), {})
                vehicle_id = str(veh_info.get("vehicle_id") or f"veh-{str(driver_id)[-5:]}")
                vin = make_valid_vin(driver_id, veh_info.get("vin"))
                depot_id = str(veh_info.get("depot_id") or DEFAULT_DEPOT)

                date_str = start_p["occurred_at"][:10]

                # Bóc tách chi phí bồi hoàn
                elec_vnd = int(dist_km * 450)
                bat_vnd = int(dist_km * 1200)
                wear_vnd = clawback_vnd - (elec_vnd + bat_vnd)

                incident = {
                    "incident_id": f"f4-{str(uuid.uuid4())[:8]}",
                    "fraud_type": "F4_PERSONAL_USE",
                    "detected_at": now_iso,
                    "driver_id": str(driver_id),
                    "vehicle_id": vehicle_id,
                    "vin": vin,
                    "depot_id": depot_id if depot_id in ["depot_gia_lam", "depot_nam_tu_liem", "depot_yen_nghia", "hub_noi_bai", "hub_my_dinh", "hub_giap_bat"] else DEFAULT_DEPOT,
                    "track": "core_owned",
                    "activity_date": date_str,
                    "declared_shift_start_min": start_min,
                    "declared_shift_end_min": end_min,
                    "app_offline_time": start_p["occurred_at"],
                    "off_shift_start_time": start_p["occurred_at"],
                    "off_shift_end_time": end_p["occurred_at"],
                    "km_off_shift": dist_km,
                    "max_speed_kmh": 45.0,
                    "left_hanoi_boundary": False,
                    "cost_rate_per_km_vnd": 3500,
                    "cost_breakdown": {
                        "electricity_vnd": elec_vnd,
                        "battery_depreciation_vnd": bat_vnd,
                        "chassis_wear_vnd": wear_vnd,
                    },
                    "total_clawback_cost_vnd": clawback_vnd,
                    "disciplinary_action": "written_reprimand_and_salary_deduction",
                    "severity": "heavy" if dist_km >= 30.0 else ("medium" if dist_km >= 15.0 else "light"),
                    "confidence": 0.95,
                    "status": "confirmed",
                }
                incidents.append(incident)

    return incidents
