"""Data loader & multi-layer schema dictionary for preview_schema.

Hỗ trợ nạp toàn bộ 51 bảng chính thức (657 trường) qua 7 tầng dữ liệu:
- L0: Reference / Master Data (5 bảng)
- L1: Simulation Event Logs (6 bảng)
- L1R: Raw GSM Data (13 bảng có 13 file Parquet thực tế, 218 trường)
- L2 / L2I: Aggregated State & Inferred Views (5 bảng)
- L3: Feature Views cho Solver / AI (10 bảng)
- Advisor: AI Decisioning & Agent Payloads (12 bảng)
Kèm 12 bản snapshot lịch sử phiên bản (@version).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEMAS_BASE_DIR = ROOT_DIR / "schemas"
PARQUET_DATA_DIR = ROOT_DIR / "data" / "mock" / "realdata-v1"

# 7 Tầng kiến trúc dữ liệu chuẩn Medallion / Event-Sourcing
LAYERS = {
    "all": {
        "title": "Toàn Bộ Hệ Thống (51 Bảng)",
        "subtitle": "Xem mạng lưới liên kết tổng thể toàn CSDL",
        "color": "#475569",
        "icon": "🌐",
    },
    "l0": {
        "title": "L0 - Reference Master Data",
        "subtitle": "Bảng danh mục gốc: Hồ sơ tài xế, trạm đổi pin, chính sách, bản đồ vùng",
        "color": "#6366F1",  # Tím Indigo
        "icon": "🏛️",
    },
    "l1": {
        "title": "L1 - Event Logs Core",
        "subtitle": "Nhật ký sự kiện mô phỏng bất biến: GPS ping, cuốc xe, đổi pin, bảng lương",
        "color": "#0D9488",  # Xanh Teal
        "icon": "⚡",
    },
    "l1r": {
        "title": "L1R - Raw GSM Data (Parquet)",
        "subtitle": "13 bảng dữ liệu thô GSM thật (218 trường) có file Parquet đối chiếu",
        "color": "#2563EB",  # Xanh Dương Royal
        "icon": "📦",
    },
    "l2": {
        "title": "L2 / L2I - Aggregated State",
        "subtitle": "Trạng thái tổng hợp cung-cầu H3, trạng thái tài xế theo ngày, trạm pin, suy luận",
        "color": "#D97706",  # Vàng Cam Amber
        "icon": "📊",
    },
    "l3": {
        "title": "L3 - AI Feature Views",
        "subtitle": "Bảng đặc trưng đầu vào cho mô hình tối ưu phân bổ xe, ca làm việc, nhiệm vụ",
        "color": "#9333EA",  # Tím Violet
        "icon": "🧠",
    },
    "advisor": {
        "title": "Advisor - AI Decision Engine",
        "subtitle": "Hợp đồng giao tiếp, checkpoint và giải trình quyết định của AI Agent",
        "color": "#E11D48",  # Đỏ Hồng Rose
        "icon": "🤖",
    },
}

# Ánh xạ Primary Key chuẩn cho 51 bảng
PRIMARY_KEYS = {
    # L0
    "driver_profile": ["driver_id"],
    "policy_bundle": ["bundle_id"],
    "service_catalog": ["service_type"],
    "station_registry": ["station_id"],
    "zone_map": ["zone_id"],
    # L1
    "app_event": ["event_id"],
    "gps_ping": ["driver_id", "t"],
    "payout_ledger": ["entry_id"],
    "policy_change_event": ["change_id"],
    "swap_transaction": ["tx_id"],
    "trip_record": ["order_id"],
    # L1R
    "driver_bike_stoppoints": ["driver_id", "start_time"],
    "driver_income_daily": ["driver_id", "date"],
    "driver_online_hours_sap_id": ["driver_id", "working_date"],
    "driver_orders_rush_hours": ["driver_id", "date"],
    "driver_penalization_ATA": ["penalization_id"],
    "driver_statistic_daily": ["driver_id", "report_date"],
    "kpi_driver_platform_calculator_gbq": ["id"],
    "public_driver_hex_tracking": ["id"],
    "public_frauds": ["fraud_id"],
    "public_mission": ["id"],
    "public_mission_earn_history": ["id"],
    "public_user_mission_progress": ["id"],
    "trips": ["trip_id"],
    # L2 & L2I
    "demand_field": ["h3", "bucket"],
    "driver_day_state": ["driver_id", "date"],
    "station_state": ["station_id", "bucket"],
    "supply_field": ["h3", "bucket"],
    "inferred_activity": ["driver_id", "window_start"],
    # L3
    "allocation_input": ["t_now", "view_version"],
    "anomaly_alert_input": ["driver_id", "alert_id"],
    "bonus_gap_input": ["driver_id", "period"],
    "idle_reduction_input": ["driver_id", "t_now"],
    "market_state_view": ["t_now", "bucket_min"],
    "mission_select_input": ["driver_id", "t_now"],
    "penalty_explain_input": ["driver_id", "penalization_id"],
    "session_summary_input": ["driver_id", "session_id"],
    "shift_plan_input": ["driver_id", "plan_date"],
    "weekly_khoan_input": ["driver_id", "week_key"],
    # Advisor
    "advice_artifact": ["artifact_id"],
    "advice_checkpoint": ["checkpoint_id"],
    "advice_checkpoint_event": ["event_id"],
    "advice_lifecycle_event": ["event_id"],
    "advice_request": ["request_id"],
    "agent_explanation_input": ["checkpoint_id", "display_id"],
    "agent_explanation_output": ["checkpoint_id", "display_id"],
    "agent_presentation_input": ["checkpoint_id"],
    "agent_presentation_output": ["checkpoint_id"],
    "composed_advice": ["advice_id"],
    "composite_episode": ["episode_id"],
    "solver_report": ["solver", "problem_digest"],
}

# Danh sách quan hệ khóa ngoại (Foreign Keys & Visual Wires)
# Gồm cả quan hệ nội bộ tầng và quan hệ xuyên tầng
ALL_RELATIONSHIPS = [
    # --- 1. TRỤC DRIVER_ID (L0 Master -> Các tầng khác) ---
    {"source": "driver_profile", "source_col": "driver_id", "target": "trip_record", "target_col": "driver_id", "label": "L0 -> L1 (Thực hiện cuốc xe)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "gps_ping", "target_col": "driver_id", "label": "L0 -> L1 (Định vị GPS)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "payout_ledger", "target_col": "driver_id", "label": "L0 -> L1 (Chi trả thu nhập)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "swap_transaction", "target_col": "driver_id", "label": "L0 -> L1 (Đổi pin)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "app_event", "target_col": "driver_id", "label": "L0 -> L1 (Sự kiện app)", "type": "inter-layer"},

    {"source": "driver_profile", "source_col": "driver_id", "target": "trips", "target_col": "driver_id", "label": "L0 -> L1R (Cuốc xe thật)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "kpi_driver_platform_calculator_gbq", "target_col": "driver_id", "label": "L0 -> L1R (KPI tài xế)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "driver_income_daily", "target_col": "driver_id", "label": "L0 -> L1R (Thu nhập ngày)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "driver_statistic_daily", "target_col": "driver_id", "label": "L0 -> L1R (Thống kê ngày)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "driver_online_hours_sap_id", "target_col": "driver_id", "label": "L0 -> L1R (Giờ online SAP)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "driver_orders_rush_hours", "target_col": "driver_id", "label": "L0 -> L1R (Đơn cao điểm)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "driver_bike_stoppoints", "target_col": "driver_id", "label": "L0 -> L1R (Điểm dừng đỗ)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "public_driver_hex_tracking", "target_col": "driver_id", "label": "L0 -> L1R (Vết di chuyển H3)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "driver_penalization_ATA", "target_col": "driver_id", "label": "L0 -> L1R (Phạt vi phạm)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "public_frauds", "target_col": "driver_id", "label": "L0 -> L1R (Cảnh báo gian lận)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "public_user_mission_progress", "target_col": "driver_id", "label": "L0 -> L1R (Tiến độ nhiệm vụ)", "type": "inter-layer"},

    {"source": "driver_profile", "source_col": "driver_id", "target": "driver_day_state", "target_col": "driver_id", "label": "L0 -> L2 (Trạng thái ngày)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "inferred_activity", "target_col": "driver_id", "label": "L0 -> L2I (Hoạt động suy luận)", "type": "inter-layer"},

    {"source": "driver_profile", "source_col": "driver_id", "target": "shift_plan_input", "target_col": "driver_id", "label": "L0 -> L3 (Kế hoạch ca)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "weekly_khoan_input", "target_col": "driver_id", "label": "L0 -> L3 (Chỉ tiêu khoán tuần)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "bonus_gap_input", "target_col": "driver_id", "label": "L0 -> L3 (Khoảng cách thưởng)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "idle_reduction_input", "target_col": "driver_id", "label": "L0 -> L3 (Giảm giờ rỗi)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "penalty_explain_input", "target_col": "driver_id", "label": "L0 -> L3 (Giải trình phạt)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "mission_select_input", "target_col": "driver_id", "label": "L0 -> L3 (Gợi ý nhiệm vụ)", "type": "inter-layer"},

    {"source": "driver_profile", "source_col": "driver_id", "target": "advice_request", "target_col": "driver_id", "label": "L0 -> Advisor (Yêu cầu tư vấn)", "type": "inter-layer"},
    {"source": "driver_profile", "source_col": "driver_id", "target": "advice_checkpoint", "target_col": "driver_id", "label": "L0 -> Advisor (Điểm kiểm soát)", "type": "inter-layer"},

    # --- 2. TRỤC TRẠM PIN (STATION_ID) ---
    {"source": "station_registry", "source_col": "station_id", "target": "swap_transaction", "target_col": "station_id", "label": "L0 -> L1 (Giao dịch đổi pin)", "type": "inter-layer"},
    {"source": "station_registry", "source_col": "station_id", "target": "station_state", "target_col": "station_id", "label": "L0 -> L2 (Trạng thái pin trạm)", "type": "inter-layer"},

    # --- 3. TRỤC CHÍNH SÁCH (BUNDLE_ID) ---
    {"source": "policy_bundle", "source_col": "bundle_id", "target": "policy_change_event", "target_col": "bundle_id", "label": "L0 -> L1 (Nhật ký đổi chính sách)", "type": "inter-layer"},

    # --- 4. TRỤC KHÔNG GIAN VÙNG (ZONE_ID & H3) ---
    {"source": "zone_map", "source_col": "zone_id", "target": "demand_field", "target_col": "h3", "label": "L0 -> L2 (Mật độ nhu cầu theo ô)", "type": "inter-layer"},
    {"source": "zone_map", "source_col": "zone_id", "target": "supply_field", "target_col": "h3", "label": "L0 -> L2 (Mật độ cung tài xế)", "type": "inter-layer"},
    {"source": "zone_map", "source_col": "zone_id", "target": "public_driver_hex_tracking", "target_col": "h3_res9", "label": "L0 -> L1R (Tọa độ ô H3)", "type": "inter-layer"},

    # --- 5. NỘI BỘ TẦNG L1R (RAW GSM DATA) ---
    {"source": "public_mission", "source_col": "id", "target": "public_user_mission_progress", "target_col": "mission_id", "label": "L1R (1:N Nhiệm vụ)", "type": "intra-layer"},
    {"source": "public_mission", "source_col": "id", "target": "public_mission_earn_history", "target_col": "mission_id", "label": "L1R (1:N Trả thưởng)", "type": "intra-layer"},
    {"source": "public_mission", "source_col": "id", "target": "mission_select_input", "target_col": "mission_id", "label": "L1R -> L3 (Chọn nhiệm vụ)", "type": "inter-layer"},
    {"source": "trips", "source_col": "trip_id", "target": "public_mission_earn_history", "target_col": "order_id", "label": "L1R (Cuốc đạt thưởng)", "type": "intra-layer"},
    {"source": "trips", "source_col": "trip_id", "target": "driver_penalization_ATA", "target_col": "order_id", "label": "L1R (Vi phạm cuốc)", "type": "intra-layer"},

    # --- 6. NỘI BỘ TẦNG L1 (CORE EVENT LOGS) ---
    {"source": "trip_record", "source_col": "order_id", "target": "payout_ledger", "target_col": "basis", "label": "L1 (Cơ sở chi trả)", "type": "intra-layer"},

    # --- 7. TẦNG ADVISOR (AI DECISIONING) ---
    {"source": "advice_request", "source_col": "request_id", "target": "composed_advice", "target_col": "request_id", "label": "Advisor (Tổng hợp tư vấn)", "type": "intra-layer"},
    {"source": "advice_request", "source_col": "request_id", "target": "advice_artifact", "target_col": "request_id", "label": "Advisor (Tạo artifact)", "type": "intra-layer"},
    {"source": "advice_checkpoint", "source_col": "checkpoint_id", "target": "advice_checkpoint_event", "target_col": "checkpoint_id", "label": "Advisor (Sự kiện checkpoint)", "type": "intra-layer"},
    {"source": "advice_checkpoint", "source_col": "checkpoint_id", "target": "agent_explanation_input", "target_col": "checkpoint_id", "label": "Advisor (Input giải trình)", "type": "intra-layer"},
    {"source": "advice_checkpoint", "source_col": "checkpoint_id", "target": "agent_presentation_input", "target_col": "checkpoint_id", "label": "Advisor (Input trình diễn)", "type": "intra-layer"},
    {"source": "agent_explanation_input", "source_col": "checkpoint_id", "target": "agent_explanation_output", "target_col": "checkpoint_id", "label": "Advisor (Output giải trình)", "type": "intra-layer"},
    {"source": "agent_presentation_input", "source_col": "checkpoint_id", "target": "agent_presentation_output", "target_col": "checkpoint_id", "label": "Advisor (Output giao diện)", "type": "intra-layer"},
]


class SchemaCatalog:
    """Kho lưu trữ và phân tích tập trung toàn bộ các schema của hệ thống."""

    def __init__(self):
        self.tables: dict[str, dict[str, Any]] = {}
        self.versions_by_table: dict[str, list[str]] = {}
        self.tables_by_layer: dict[str, list[str]] = {k: [] for k in LAYERS if k != "all"}
        self._load_all_schemas()

    def _load_all_schemas(self):
        all_files = sorted(SCHEMAS_BASE_DIR.glob("**/*.schema.json"))

        # 1. Thu thập các snapshot version
        for f in all_files:
            fname = f.name
            if "@" in fname:
                base_name = fname.split("@")[0]
                ver = fname.split("@")[1].replace(".schema.json", "")
                self.versions_by_table.setdefault(base_name, []).append(ver)

        # 2. Thu thập và phân tích các bảng chính thức (latest)
        for f in all_files:
            fname = f.name
            if "@" in fname:
                continue

            tbl_name = fname.replace(".schema.json", "")
            layer = f.parent.name.lower()
            if layer == "l2i":
                layer = "l2"  # gộp l2i vào l2 để tiện lọc

            try:
                with open(f, encoding="utf-8") as fp:
                    raw_data = json.load(fp)
            except Exception as e:
                print(f"Lỗi đọc schema {f}: {e}")
                continue

            properties = raw_data.get("properties", {})
            required_fields = set(raw_data.get("required", []))
            pk_list = PRIMARY_KEYS.get(tbl_name, [])

            # Xác định các cột khóa ngoại dựa trên quan hệ
            incoming_fks = [
                r for r in ALL_RELATIONSHIPS if r["target"] == tbl_name
            ]
            outgoing_fks = [
                r for r in ALL_RELATIONSHIPS if r["source"] == tbl_name
            ]
            fk_cols = {r["target_col"] for r in incoming_fks}

            # Kiểm tra file Parquet tương ứng
            parquet_path = PARQUET_DATA_DIR / f"{tbl_name}.parquet"
            has_parquet = parquet_path.exists()
            parquet_size = parquet_path.stat().st_size if has_parquet else 0

            cols_info = []
            for col_name, col_meta in properties.items():
                is_pk = col_name in pk_list
                is_fk = col_name in fk_cols or (col_name.endswith("_id") and not is_pk)
                is_h3 = "h3" in col_name.lower() or "hex" in col_name.lower()
                is_pii = (
                    col_meta.get("x-sensitivity") == "PII"
                    or col_name in ("driver_id", "customer_id", "phone", "email")
                )

                # Xác định kiểu dữ liệu hiển thị
                col_type = col_meta.get("type", "unknown")
                if isinstance(col_type, list):
                    col_type = "/".join(col_type)

                cols_info.append({
                    "name": col_name,
                    "type": col_type,
                    "required": col_name in required_fields,
                    "is_pk": is_pk,
                    "is_fk": is_fk,
                    "is_h3": is_h3,
                    "is_pii": is_pii,
                    "description": col_meta.get("description", ""),
                    "enum": col_meta.get("enum", None),
                    "pattern": col_meta.get("pattern", None),
                    "default": col_meta.get("default", None),
                })

            self.tables[tbl_name] = {
                "name": tbl_name,
                "layer": layer,
                "title": raw_data.get("title", tbl_name),
                "description": raw_data.get("description", ""),
                "schema_version": raw_data.get("properties", {}).get("schema_version", {}).get("const", "1.0.0"),
                "total_columns": len(cols_info),
                "columns": cols_info,
                "required_columns": list(required_fields),
                "primary_keys": pk_list,
                "has_parquet": has_parquet,
                "parquet_path": str(parquet_path) if has_parquet else None,
                "parquet_size_bytes": parquet_size,
                "historical_versions": sorted(self.versions_by_table.get(tbl_name, [])),
                "raw_schema": raw_data,
                "incoming_fks": incoming_fks,
                "outgoing_fks": outgoing_fks,
            }

            if layer in self.tables_by_layer:
                self.tables_by_layer[layer].append(tbl_name)

    def get_layer_tables(self, layer: str) -> list[str]:
        """Lấy danh sách tên bảng thuộc tầng được chỉ định (hoặc tất cả)."""
        if layer == "all":
            return sorted(list(self.tables.keys()))
        return sorted(self.tables_by_layer.get(layer, []))

    def get_table(self, table_name: str) -> dict[str, Any] | None:
        """Lấy thông tin chi tiết của một bảng."""
        return self.tables.get(table_name)

    def get_relationships_for_selection(self, selected_tables: list[str]) -> list[dict[str, Any]]:
        """Lấy danh sách các dây nối giữa tập hợp các bảng được chọn."""
        tbl_set = set(selected_tables)
        return [
            r for r in ALL_RELATIONSHIPS
            if r["source"] in tbl_set and r["target"] in tbl_set
        ]

    def get_all_columns_flattened(self) -> list[dict[str, Any]]:
        """Lấy danh sách toàn bộ cột của tất cả các bảng để phục vụ tra cứu."""
        flat = []
        for tbl_name, t_data in sorted(self.tables.items()):
            layer_info = LAYERS.get(t_data["layer"], {})
            for c in t_data["columns"]:
                flat.append({
                    "table": tbl_name,
                    "layer": t_data["layer"].upper(),
                    "layer_title": layer_info.get("title", t_data["layer"]),
                    "column": c["name"],
                    "type": c["type"],
                    "required": "Có" if c["required"] else "Không",
                    "role": (
                        "[PK]" if c["is_pk"] else (
                            "[FK]" if c["is_fk"] else (
                                "[H3]" if c["is_h3"] else "-"
                            )
                        )
                    ),
                    "pii": "Có" if c["is_pii"] else "Không",
                    "description": c["description"],
                })
        return flat

    def generate_mock_record(self, table_name: str) -> dict[str, Any]:
        """Sinh một bản ghi mẫu dựa trên JSON schema của bảng."""
        tbl = self.get_table(table_name)
        if not tbl:
            return {}
        record = {}
        for c in tbl["columns"]:
            cname = c["name"]
            ctype = c["type"]
            if cname == "schema_version":
                record[cname] = tbl["schema_version"]
            elif cname == "source":
                record[cname] = "MOCK"
            elif cname.endswith("_id") or cname == "id":
                record[cname] = f"{cname.replace('_id', '')}_001"
            elif "time" in cname or cname in ("created_at", "updated_at", "t", "t_now"):
                record[cname] = "2026-09-21T08:00:00Z"
            elif "date" in cname:
                record[cname] = "2026-09-21"
            elif "h3" in cname or "hex" in cname:
                record[cname] = "892f5a024b7ffff"
            elif "vnd" in cname or "amount" in cname or "fare" in cname:
                record[cname] = 50000
            elif c["enum"]:
                record[cname] = c["enum"][0]
            elif "integer" in ctype:
                record[cname] = 10
            elif "number" in ctype:
                record[cname] = 12.5
            elif "boolean" in ctype:
                record[cname] = True
            elif "array" in ctype:
                record[cname] = []
            elif "object" in ctype:
                record[cname] = {}
            else:
                record[cname] = f"sample_{cname}"
        return record


# Biến toàn cục nạp sẵn dữ liệu catalog
CATALOG = SchemaCatalog()
