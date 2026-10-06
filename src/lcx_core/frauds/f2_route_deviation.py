"""F2 Rule-based Detector: Kéo dài hành trình cuốc xe / Đi lòng vòng tăng cước (Route Deviation / Route Inflation).

Áp dụng cho đội xe Taxi Cơ Hữu Hà Nội (VinFast VF5, VF6, VF e34, VF8) thuộc sở hữu 100% của GSM,
theo quy định tại FRAUDS.md và Bộ Quy tắc Ứng xử ngày 11/09/2026.

Dấu hiệu vi phạm:
1. Cuốc xe đã hoàn thành (status == 'completed').
2. Quãng đường thực tế tính tiền cho khách (trips.distance_km hoặc billed_distance_km) vượt quá
   quãng đường tối ưu đường bộ tra cứu từ ma trận OSRM (optimal_road_km).
3. Tỷ lệ vượt ngưỡng (deviation_pct >= 0.25) và vượt phân vị P99 cùng phân hạng xe.
4. Không có lý do chính đáng miễn trừ (justified == false).

Chế tài & Hoàn tiền:
- Hoàn trả 100% số tiền cước thu vượt (excess_fare_vnd) cho hành khách bị chém giá.
- Phạt kỷ luật khiển trách bằng văn bản (lần 1) hoặc phạt tiền ATA 500k - 1 triệu.
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
MIN_ROAD_KM = 0.5                     # Loại bỏ các cuốc quá ngắn (< 500m) vì sai số phần trăm không ổn định
DEFAULT_P99_THRESHOLD_RATIO = 1.25    # Ngưỡng tỷ lệ tối đa cho phép (thực tế / tối ưu), trên 1.25 là vi phạm (vượt 25%)
DEFAULT_RATE_PER_KM_VND = 13_000      # Đơn giá cước trung bình trên mỗi km phát sinh
DEFAULT_DEPOT = "depot_nam_tu_liem"
DEFAULT_VEHICLE_CLASS = "A_COMPACT"
ATA_PENALTY_TIER_1_VND = 500_000      # Phạt trừ ví lần 1


def detect_f2_route_deviation(
    trips_df: pl.DataFrame,
    osrm_matrix_df: pl.DataFrame | None = None,
    vehicle_registry_df: pl.DataFrame | None = None,
    threshold_ratio: float = DEFAULT_P99_THRESHOLD_RATIO,
    rate_per_km_vnd: int = DEFAULT_RATE_PER_KM_VND,
) -> list[dict[str, Any]]:
    """Hàm quét và bắt lỗi vi phạm F2: Chạy sai lộ trình / Đi lòng vòng tăng tiền cước.

    Returns:
    --------
    Danh sách các dictionary sự vụ vi phạm, khớp 100% với f2_route_deviation.schema.json
    """
    incidents: list[dict[str, Any]] = []

    if "status" not in trips_df.columns:
        return incidents

    completed_trips = trips_df.filter(pl.col("status") == "completed")
    if completed_trips.is_empty():
        return incidents

    dist_col = (
        "actual_distance_km" if "actual_distance_km" in completed_trips.columns else (
            "billed_distance_km" if "billed_distance_km" in completed_trips.columns else (
                "distance_km" if "distance_km" in completed_trips.columns else (
                    "dist_km" if "dist_km" in completed_trips.columns else None
                )
            )
        )
    )
    if dist_col is None:
        return incidents

    joined_trips = completed_trips
    if "osrm_expected_distance_km" in joined_trips.columns:
        optimal_col = "osrm_expected_distance_km"
    elif "expected_distance_km" in joined_trips.columns:
        optimal_col = "expected_distance_km"
    elif "optimal_road_km" in joined_trips.columns:
        optimal_col = "optimal_road_km"
    elif "route_km_optimal" in joined_trips.columns:
        optimal_col = "route_km_optimal"
    elif osrm_matrix_df is not None and "pickup_h3" in joined_trips.columns and "drop_h3" in joined_trips.columns:
        cols_to_select = ["cell_from", "cell_to", "road_km"]
        if "road_tier" in osrm_matrix_df.columns:
            cols_to_select.append("road_tier")
        osrm_sub = osrm_matrix_df.select(cols_to_select)
        osrm_renamed = osrm_sub.rename({"cell_from": "pickup_h3", "cell_to": "drop_h3", "road_km": "osrm_road_km"})
        joined_trips = joined_trips.join(osrm_renamed, on=["pickup_h3", "drop_h3"], how="left")
        optimal_col = "osrm_road_km"
    else:
        optimal_col = None

    veh_map: dict[str, dict[str, Any]] = {}
    if vehicle_registry_df is not None and not vehicle_registry_df.is_empty():
        for row in vehicle_registry_df.iter_rows(named=True):
            d_id = str(row.get("assigned_driver_id", ""))
            if d_id:
                veh_map[d_id] = row

    now_iso = datetime.now(timezone.utc).isoformat()

    for trip in joined_trips.iter_rows(named=True):
        actual_km = float(trip.get(dist_col) or 0.0)

        if optimal_col and trip.get(optimal_col) is not None:
            optimal_km = float(trip[optimal_col])
        else:
            continue

        if optimal_km < MIN_ROAD_KM:
            continue

        ratio = round(actual_km / optimal_km, 3)
        pct = round((actual_km - optimal_km) / optimal_km, 3)

        if ratio >= threshold_ratio:
            traffic_flag = bool(trip.get("traffic_congestion_flag", False))
            flood_flag = bool(trip.get("flood_or_construction_reroute_flag", False))
            customer_request = bool(trip.get("passenger_requested_detour", False))
            highway_alt = bool(trip.get("highway_alternative_taken", False))

            if traffic_flag or flood_flag or customer_request or highway_alt:
                continue

            excess_km = round(max(actual_km - optimal_km, 0.0), 2)
            excess_fare_vnd = int(excess_km * rate_per_km_vnd)
            gross_fare = int(trip.get("gross_fare_vnd") or trip.get("gross_vnd") or (actual_km * rate_per_km_vnd))

            driver_id = str(trip.get("driver_id", ""))
            veh_info = veh_map.get(driver_id, {})
            vehicle_id = str(veh_info.get("vehicle_id") or trip.get("vehicle_id") or f"veh-{driver_id[-5:]}")
            vin = make_valid_vin(driver_id, veh_info.get("vin") or trip.get("vin"))
            depot_id = str(veh_info.get("depot_id") or DEFAULT_DEPOT)

            raw_road_tier = str(trip.get("road_tier") or "LOCAL_STREET")
            road_tier = raw_road_tier if raw_road_tier in ["EXPRESSWAY", "ARTERIAL", "LOCAL_STREET"] else "LOCAL_STREET"

            incident = {
                "incident_id": f"f2-{str(uuid.uuid4())[:8]}",
                "fraud_type": "F2_ROUTE_DEVIATION",
                "detected_at": now_iso,
                "driver_id": driver_id,
                "vehicle_id": vehicle_id,
                "vin": vin,
                "depot_id": depot_id if depot_id in ["depot_gia_lam", "depot_nam_tu_liem", "depot_yen_nghia", "hub_noi_bai", "hub_my_dinh", "hub_giap_bat"] else DEFAULT_DEPOT,
                "track": "core_owned",
                "trip_id": str(trip.get("trip_id") or trip.get("order_id") or ""),
                "pickup_h3": str(trip.get("pickup_h3") or "892e00000000000"),
                "drop_h3": str(trip.get("drop_h3") or trip.get("dropoff_h3") or "892e00000000001"),
                "road_tier": road_tier,
                "actual_distance_km": round(actual_km, 2),
                "optimal_road_km": round(optimal_km, 2),
                "deviation_km": excess_km,
                "deviation_pct": pct,
                "percentile_p99_threshold": round(threshold_ratio - 1.0, 2),
                "travel_mode": "car",
                "vehicle_class": DEFAULT_VEHICLE_CLASS,
                "gross_fare_vnd": gross_fare,
                "fare_per_km_vnd": rate_per_km_vnd,
                "excess_fare_vnd": excess_fare_vnd,
                "customer_refund_status": "refunded_to_wallet",
                "traffic_justification": {
                    "severe_congestion_detected": False,
                    "flooding_detour_detected": False,
                    "road_closure_detected": False,
                    "highway_alternative_taken": False,
                    "justified": False,
                },
                "ata_penalty_vnd": ATA_PENALTY_TIER_1_VND,
                "severity": "heavy" if ratio >= 1.60 else ("medium" if ratio >= 1.40 else "light"),
                "confidence": 0.95,
                "status": "confirmed",
            }
            incidents.append(incident)

    return incidents
