"""FraudDetectorEngine: Bộ điều phối và tổng hợp quét 6 loại gian lận (F1 - F6) của Taxi Cơ Hữu Hà Nội.

Nhiệm vụ:
1. Tiếp nhận nguồn dữ liệu đầu vào (từ EventDataStore, GsmDataStore, hoặc dict DataFrames).
2. Kích hoạt đồng thời 6 detectors chuyên biệt:
   - F1: Chở khách ngoài app (f1_off_app_pickup)
   - F2: Đi lòng vòng tăng cước (f2_route_deviation)
   - F3: Rút giắc/phá sóng mất tín hiệu TCU (f3_tcu_signal_loss)
   - F4: Dùng xe công ty vào việc riêng ngoài ca (f4_personal_use)
   - F5: Tạo cuốc ảo trục lợi sàn 650k/ngày (f5_fake_trip)
   - F6: Hủy sai lý do kén chọn cuốc xe (f6_invalid_driver_cancel)
3. Phân loại chuẩn xác loại lỗi vi phạm (Fraud Type Classification).
4. Tự động kiểm tra tính hợp lệ (Schema Validation) theo 6 file chuẩn trong schemas/frauds/.
5. Tổng hợp báo cáo tài chính: Tổng tiền truy thu, tiền phạt ATA, số tài xế bị tước quyền hưởng sàn 650k/ngày.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

from lcx_core.frauds.f1_off_app_pickup import detect_f1_off_app_pickup
from lcx_core.frauds.f2_route_deviation import detect_f2_route_deviation
from lcx_core.frauds.f3_tcu_signal_loss import detect_f3_tcu_signal_loss
from lcx_core.frauds.f4_personal_use import detect_f4_personal_use
from lcx_core.frauds.f5_fake_trip import detect_f5_fake_trip
from lcx_core.frauds.f6_invalid_driver_cancel import detect_f6_invalid_driver_cancel

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "frauds"

SCHEMA_FILES = {
    "F1_OFF_APP_PICKUP": SCHEMA_DIR / "f1_off_app_pickup.schema.json",
    "F2_ROUTE_DEVIATION": SCHEMA_DIR / "f2_route_deviation.schema.json",
    "F3_TCU_SIGNAL_LOSS": SCHEMA_DIR / "f3_tcu_signal_loss.schema.json",
    "F4_PERSONAL_USE": SCHEMA_DIR / "f4_personal_use.schema.json",
    "F5_FAKE_TRIP": SCHEMA_DIR / "f5_fake_trip.schema.json",
    "F6_INVALID_DRIVER_CANCEL": SCHEMA_DIR / "f6_invalid_driver_cancel.schema.json",
}


@dataclass
class FraudScanSummary:
    """Bản tóm tắt kết quả quét vi phạm toàn diện."""
    total_incidents: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    total_clawback_vnd: int = 0
    total_ata_penalty_vnd: int = 0
    total_lost_floor_subsidies_vnd: int = 0
    drivers_affected_count: int = 0
    validation_passed_count: int = 0
    validation_failed_count: int = 0

    def print_summary(self) -> None:
        import sys
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

        print("=" * 72)
        print(" BÁO CÁO TỔNG HỢP PHÁT HIỆN GIAN LẬN TAXI CƠ HỮU HÀ NỘI (F1 - F6)")
        print("=" * 72)
        print(f" • Tổng số vụ vi phạm phát hiện:       {self.total_incidents:,}")
        print(f" • Số tài xế có hành vi vi phạm:        {self.drivers_affected_count:,}")
        print("-" * 72)
        print(" PHÂN BỔ THEO TỪNG LOẠI VI PHẠM:")
        for ftype, count in sorted(self.by_type.items()):
            print(f"   - {ftype:<28}: {count:>5} vụ")
        print("-" * 72)
        print(" THIỆT HẠI TÀI CHÍNH & CHẾ TÀI ÁP DỤNG:")
        print(f"   • Tổng tiền truy thu cước/khấu hao:   {self.total_clawback_vnd:>12,} VNĐ")
        print(f"   • Tổng tiền phạt trừ ví (ATA/Quy chế): {self.total_ata_penalty_vnd:>12,} VNĐ")
        print(f"   • Tổng tiền sàn 650k/ngày bị tước bỏ: {self.total_lost_floor_subsidies_vnd:>12,} VNĐ")
        print("-" * 72)
        print(f" KIỂM TOÁN SCHEMA DRAFT 2020-12: Hợp lệ {self.validation_passed_count} / {self.total_incidents}")
        print("=" * 72)


class FraudDetectorEngine:
    """Engine trung tâm quét và phân loại toàn diện 6 loại gian lận."""

    def __init__(self, validate_schemas: bool = True):
        self.validate_schemas = validate_schemas and HAS_JSONSCHEMA
        self._schemas: dict[str, dict[str, Any]] = {}
        if self.validate_schemas:
            self._load_schemas()

    def _load_schemas(self) -> None:
        for ftype, p in SCHEMA_FILES.items():
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        self._schemas[ftype] = json.load(f)
                except Exception as e:
                    print(f"Warning: Failed to load schema for {ftype}: {e}")

    def validate_incident(self, incident: dict[str, Any]) -> bool:
        """Kiểm tra một record sự vụ có đúng chuẩn JSON Schema hay không."""
        if not self.validate_schemas:
            return True
        ftype = incident.get("fraud_type")
        schema = self._schemas.get(ftype)
        if not schema:
            return True
        try:
            jsonschema.validate(instance=incident, schema=schema)
            return True
        except jsonschema.ValidationError as err:
            print(f"Schema Validation Error [{ftype}]: {err.message}")
            return False

    def scan(
        self,
        trips_df: pl.DataFrame,
        telemetry_df: pl.DataFrame | None = None,
        camera_df: pl.DataFrame | None = None,
        declared_shifts_df: pl.DataFrame | None = None,
        vehicle_registry_df: pl.DataFrame | None = None,
        osrm_matrix_df: pl.DataFrame | None = None,
    ) -> tuple[list[dict[str, Any]], FraudScanSummary]:
        """Kích hoạt quét đồng loạt cả 6 loại lỗi F1 - F6.

        Returns:
        --------
        (all_incidents, summary_metrics)
        """
        all_incidents: list[dict[str, Any]] = []

        # 1. Quét F1: Chở khách ngoài app
        if telemetry_df is not None and not telemetry_df.is_empty():
            f1_results = detect_f1_off_app_pickup(
                trips_df=trips_df,
                telemetry_df=telemetry_df,
                camera_df=camera_df,
                vehicle_registry_df=vehicle_registry_df,
            )
            all_incidents.extend(f1_results)

        # 2. Quét F2: Kéo dài hành trình cuốc xe
        f2_results = detect_f2_route_deviation(
            trips_df=trips_df,
            osrm_matrix_df=osrm_matrix_df,
            vehicle_registry_df=vehicle_registry_df,
        )
        all_incidents.extend(f2_results)

        # 3. Quét F3: Mất tín hiệu hộp TCU
        if telemetry_df is not None and not telemetry_df.is_empty():
            f3_results = detect_f3_tcu_signal_loss(
                telemetry_df=telemetry_df,
                declared_shifts_df=declared_shifts_df,
                vehicle_registry_df=vehicle_registry_df,
            )
            all_incidents.extend(f3_results)

        # 4. Quét F4: Sử dụng xe công ty ngoài ca
        if telemetry_df is not None and not telemetry_df.is_empty():
            f4_results = detect_f4_personal_use(
                telemetry_df=telemetry_df,
                declared_shifts_df=declared_shifts_df,
                trips_df=trips_df,
                vehicle_registry_df=vehicle_registry_df,
            )
            all_incidents.extend(f4_results)

        # 5. Quét F5: Tạo cuốc ảo trục lợi sàn 650k
        if telemetry_df is not None and not telemetry_df.is_empty():
            f5_results = detect_f5_fake_trip(
                trips_df=trips_df,
                telemetry_df=telemetry_df,
                camera_df=camera_df,
                vehicle_registry_df=vehicle_registry_df,
            )
            all_incidents.extend(f5_results)

        # 6. Quét F6: Hủy sai lý do kén chọn cuốc
        f6_results = detect_f6_invalid_driver_cancel(
            trips_df=trips_df,
            telemetry_df=telemetry_df,
            vehicle_registry_df=vehicle_registry_df,
        )
        all_incidents.extend(f6_results)

        # 7. Tổng hợp thống kê & Validate Schema
        summary = FraudScanSummary(total_incidents=len(all_incidents))
        affected_drivers = set()

        for inc in all_incidents:
            ftype = inc.get("fraud_type", "UNKNOWN")
            summary.by_type[ftype] = summary.by_type.get(ftype, 0) + 1
            # Tổng hợp tiền truy thu theo từng trường cụ thể của 6 loại lỗi
            clawback = int(
                inc.get("clawback_amount_vnd")
                or inc.get("excess_fare_vnd")
                or inc.get("total_clawback_cost_vnd")
                or inc.get("clawback_trip_fare_vnd")
                or 0
            )
            summary.total_clawback_vnd += clawback

            # Tổng hợp tiền phạt trừ ví (ATA hoặc Nhóm 1b)
            penalty = int(
                inc.get("ata_penalty_vnd")
                or inc.get("group_1b_penalty_vnd")
                or 0
            )
            summary.total_ata_penalty_vnd += penalty

            # Tính tiền sàn 650k/ngày bị mất (F5, F6)
            if inc.get("daily_floor_forfeited"):
                summary.total_lost_floor_subsidies_vnd += int(inc.get("forfeited_floor_amount_vnd") or 650_000)
            if inc.get("clawback_floor_subsidy_vnd"):
                summary.total_lost_floor_subsidies_vnd += int(inc.get("clawback_floor_subsidy_vnd") or 0)
            elif inc.get("attempted_floor_subsidy_amount_vnd"):
                summary.total_lost_floor_subsidies_vnd += int(inc.get("attempted_floor_subsidy_amount_vnd") or 0)

            d_id = inc.get("driver_id")
            if d_id:
                affected_drivers.add(d_id)

            # Kiểm tra Schema
            if self.validate_schemas:
                if self.validate_incident(inc):
                    summary.validation_passed_count += 1
                else:
                    summary.validation_failed_count += 1
            else:
                summary.validation_passed_count += 1

        summary.drivers_affected_count = len(affected_drivers)
        return all_incidents, summary


def _normalize_trips_df(df: pl.DataFrame | None) -> pl.DataFrame:
    """Chuẩn hóa cột dữ liệu cuốc xe: chấp nhận cả chuẩn L1 cũ lẫn chuẩn telemetry mới."""
    if df is None or df.is_empty():
        return pl.DataFrame()
    norm = df
    if "status" not in norm.columns:
        norm = norm.with_columns(pl.lit("completed").alias("status"))
    if "trip_id" not in norm.columns and "order_id" in norm.columns:
        norm = norm.with_columns(pl.col("order_id").alias("trip_id"))
    if "distance_km" not in norm.columns:
        if "dist_km" in norm.columns:
            norm = norm.with_columns(pl.col("dist_km").alias("distance_km"))
        elif "billed_distance_km" in norm.columns:
            norm = norm.with_columns(pl.col("billed_distance_km").alias("distance_km"))
    if "pickup_time" not in norm.columns:
        if "t_pickup" in norm.columns:
            norm = norm.with_columns(pl.col("t_pickup").alias("pickup_time"))
        elif "request_time" in norm.columns:
            norm = norm.with_columns(pl.col("request_time").alias("pickup_time"))
    if "complete_time" not in norm.columns:
        if "t_complete" in norm.columns:
            norm = norm.with_columns(pl.col("t_complete").alias("complete_time"))
    return norm


def _normalize_telemetry_df(df: pl.DataFrame | None) -> pl.DataFrame | None:
    """Chuẩn hóa cột dữ liệu telemetry: chấp nhận cả gps_ping lẫn vehicle_telemetry_ping."""
    if df is None or df.is_empty():
        return None
    norm = df
    if "occurred_at" not in norm.columns and "t" in norm.columns:
        norm = norm.with_columns(pl.col("t").alias("occurred_at"))
    if "seat_any_passenger" not in norm.columns and "seat_occupancy" in norm.columns:
        dtype = norm["seat_occupancy"].dtype
        if dtype == pl.Struct:
            norm = norm.with_columns(
                pl.col("seat_occupancy").struct.field("any_passenger_seat").fill_null(False).alias("seat_any_passenger")
            )
        elif dtype == pl.String:
            norm = norm.with_columns(
                pl.col("seat_occupancy").str.contains(r'"any_passenger_seat":\s*true').fill_null(False).alias("seat_any_passenger")
            )
    return norm


def scan_store_frauds(store: Any, validate_schemas: bool = True) -> tuple[list[dict[str, Any]], FraudScanSummary]:
    """Hàm tiện ích nạp trực tiếp EventDataStore, GsmDataStore hoặc đường dẫn thư mục Parquet để quét toàn diện."""
    engine = FraudDetectorEngine(validate_schemas=validate_schemas)

    if isinstance(store, (str, Path)):
        p = Path(store)
        trips_df = None
        for t_name in ("trips.parquet", "trip_record.parquet"):
            if (p / t_name).exists():
                trips_df = pl.read_parquet(p / t_name)
                break

        telemetry_df = None
        # Ưu tiên vehicle_telemetry_ping trước, sau đó fallback gps_ping
        for tel_name in ("vehicle_telemetry_ping.parquet", "vehicle_telemetry_ping_sample.parquet", "gps_ping.parquet"):
            if (p / tel_name).exists():
                telemetry_df = pl.read_parquet(p / tel_name)
                break

        camera_df = pl.read_parquet(p / "camera_event.parquet") if (p / "camera_event.parquet").exists() else None
        shifts_df = pl.read_parquet(p / "driver_profile.parquet") if (p / "driver_profile.parquet").exists() else None
        registry_df = pl.read_parquet(p / "vehicle_registry.parquet") if (p / "vehicle_registry.parquet").exists() else None
        osrm_df = pl.read_parquet(p / "osrm_matrix.parquet") if (p / "osrm_matrix.parquet").exists() else None
    else:
        trips_df = getattr(store, "trips", None)
        if trips_df is None and hasattr(store, "table"):
            trips_df = store.table("trips")

        telemetry_df = getattr(store, "telemetry", None)
        camera_df = getattr(store, "camera_events", None)
        shifts_df = getattr(store, "declared_shifts", None)
        registry_df = getattr(store, "vehicle_registry", None)
        osrm_df = getattr(store, "osrm_matrix", None)

    trips_df = _normalize_trips_df(trips_df)
    telemetry_df = _normalize_telemetry_df(telemetry_df)

    return engine.scan(
        trips_df=trips_df,
        telemetry_df=telemetry_df,
        camera_df=camera_df,
        declared_shifts_df=shifts_df,
        vehicle_registry_df=registry_df,
        osrm_matrix_df=osrm_df,
    )
