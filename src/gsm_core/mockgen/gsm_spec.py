"""SPEC metadata bảng thật gsm-data-prod (Cường cung cấp 2026-07-24) — NGUỒN SỰ THẬT.

Dùng chung bởi `scripts/audit_schema_vs_spec.py` và test gate
`tests/test_schema_matches_gsm_spec.py` để mock KHÔNG trôi khỏi schema thật.
Sửa ở ĐÂY khi GSM cập nhật cột/bảng.
"""

from __future__ import annotations

META_COLS = {"schema_version", "source"}  # ta thêm có chủ ý (§5 nhãn nguồn)

# full_path GSM -> (entity file của ta, [cột theo spec]); None = "chưa có cột" (ta ENGINEER)
SPEC: dict[str, tuple[str, list[str] | None]] = {
    "gsm-data-prod.M_DRIVER_KPI_REWARD.driver_statistic_daily": ("driver_statistic_daily", [
        "local_date", "driver_id", "completed_count", "accepted_count", "cancelled_count",
        "total_request_calculate_complete", "total_request_calculate_cancel",
        "total_request_calculate_accept", "count_cancel_not_relate_driver", "total_rating",
        "total_order_rating", "count_rating_5_star", "acceptance_rate", "fulfillment_rate",
        "cancellation_rate"]),
    "gsm-data-prod.M_DRIVER_KPI_REWARD.driver_online_hours_sap_id": ("driver_online_hours_sap_id", [
        "local_date", "schedule_date", "driver_id", "full_name", "sap_profile_id", "hub_id",
        "depot_id", "phone_number", "driver_type", "online_time"]),
    "gsm-data-prod.M_DRIVER_KPI_REWARD.driver_orders_rush_hours": ("driver_orders_rush_hours", [
        "driver_id", "local_date", "total_order", "commission", "total_fee",
        "revenue_not_relate_driver", "total_order_normal_hour", "commission_normal_hour",
        "total_fee_normal_hour", "revenue_not_relate_driver_normal_hour", "total_order_rush_hour",
        "commission_rush_hour", "total_fee_rush_hour", "revenue_not_relate_driver_rush_hour"]),
    "gsm-data-prod.M_DRIVER_KPI_REWARD.driver_bike_stoppoints": ("driver_bike_stoppoints", [
        "driver_id", "local_date", "total_stoppoints", "total_stoppoints_rush_hour"]),
    "gsm-data-prod.M_DRIVER_KPI_REWARD.kpi_driver_platform_calculator_gbq": (
        "kpi_driver_platform_calculator_gbq", [
            "id", "driver_id", "driver_name", "sap_id", "status", "week_key", "week_start",
            "week_end", "kpi_month", "kpi_year", "email", "tel", "engname", "depot_code",
            "depot_name", "vehicle_vin_number", "vehicle_license_plate", "vehicle_model",
            "country", "type", "last_updated_date"]),
    "gsm-data-prod.M_DRIVER_KPI_REWARD.driver_income_daily": ("driver_income_daily", [
        "driver_id", "order_date", "commission", "total_order", "total_fee",
        "revenue_not_relate_driver", "avg_daily_revenue", "total_core_order"]),
    "gsm-data-prod.GSM_ORDER_DISPATCH_SERVICE_APPEND.trips": ("trips", None),
    "gsm-data-prod.GSM_MISSION_SERVICE_APPEND.public_driver_hex_tracking": (
        "public_driver_hex_tracking", [
            "id", "driver_id", "campaign_id", "log_id", "init_hex", "current_hex", "last_hex",
            "target_hex", "last_seen_at", "entered_current_hex_at", "stay_duration_seconds",
            "reached_target", "reached_target_at", "hex_history", "created_at", "updated_at",
            "schedule_job_id", "datastream_metadata", "tracking_status"]),
    "gsm-data-prod.M_DRIVER_KPI_REWARD.driver_penalization_ATA": ("driver_penalization_ATA", None),
    "gsm-data-prod.M_BROADCASTING_SERVICE_APPEND.public_frauds": ("public_frauds", None),
    "gsm-data-prod.GSM_MISSION_SERVICE_APPEND.public_user_mission_progress": (
        "public_user_mission_progress", None),
    "gsm-data-prod.GSM_MISSION_SERVICE_APPEND.public_mission": ("public_mission", [
        "id", "created_at", "updated_at", "deleted_at", "created_by", "updated_by", "mission_type",
        "parent_id", "name", "state", "audience", "description", "start_time", "end_time",
        "point_id", "rewards", "mission_claim", "mission_code", "time_claim_reward", "rule_code",
        "meta_data", "contract_type", "qualify_execute_code", "status", "datastream_metadata",
        "business_code", "show_only", "is_ddi_mission"]),
    "gsm-data-prod.GSM_MISSION_SERVICE_APPEND.public_mission_earn_history": (
        "public_mission_earn_history", [
            "id", "created_at", "updated_at", "deleted_at", "mission_id", "order_id",
            "order_status", "driver_id", "customer_id", "service_type", "order_time",
            "complete_time", "travel_mode", "sap_contract_type", "type", "count_order",
            "count_stoppoint", "earn", "description", "datastream_metadata", "reward_level"]),
}
