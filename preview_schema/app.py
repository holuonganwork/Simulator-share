"""GSM Schema & Multi-Layer ERD Explorer — Streamlit Interactive App.

Xem sơ đồ cơ sở dữ liệu có dây nối (ERD), cấu trúc 51 bảng (657 trường) qua 7 tầng:
L0, L1, L1R (13 file Parquet), L2, L2I, L3 và Advisor.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Thêm thư mục gốc dự án vào sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import polars as pl
import streamlit as st
import streamlit.components.v1 as components

from preview_schema.erd_generator import (
    generate_mermaid_erd,
    generate_sql_ddl,
    generate_vis_network_html,
)
from preview_schema.schema_loader import CATALOG, LAYERS

st.set_page_config(
    page_title="GSM Multi-Layer Schema & ERD Explorer",
    page_icon="🗄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS giao diện hiện đại & trực quan
st.markdown("""
<style>
  .metric-card {
    background: #1E293B;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 10px 14px;
    text-align: center;
    margin-bottom: 8px;
  }
  .metric-value {
    font-size: 22px;
    font-weight: 700;
    color: #38BDF8;
  }
  .metric-label {
    font-size: 11px;
    color: #94A3B8;
  }
  .layer-badge {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    color: white;
    margin-right: 6px;
  }
  .badge-pk {
    background-color: #EF4444;
    color: white;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
  .badge-fk {
    background-color: #3B82F6;
    color: white;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
  .badge-h3 {
    background-color: #10B981;
    color: white;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
</style>
""", unsafe_allow_html=True)

# Thống kê tổng quan
total_tables_count = len(CATALOG.tables)
total_columns_count = sum(t["total_columns"] for t in CATALOG.tables.values())
parquet_tables_count = sum(1 for t in CATALOG.tables.values() if t["has_parquet"])
total_versions_count = sum(len(t["historical_versions"]) for t in CATALOG.tables.values())

# --- SIDEBAR ---
st.sidebar.title("🗄️ GSM Schema Explorer")
st.sidebar.caption("Hệ thống 51 Bảng CSDL Đa Tầng & Sơ Đồ ERD Có Dây Nối")

# Khối thống kê số liệu
sc1, sc2 = st.sidebar.columns(2)
with sc1:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{total_tables_count}</div><div class="metric-label">Bảng CSDL</div></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="metric-card"><div class="metric-value">{parquet_tables_count}</div><div class="metric-label">File Parquet</div></div>', unsafe_allow_html=True)
with sc2:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{total_columns_count}</div><div class="metric-label">Trường Dữ Liệu</div></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="metric-card"><div class="metric-value">{total_versions_count}</div><div class="metric-label">Bản Ghi Lịch Sử</div></div>', unsafe_allow_html=True)

st.sidebar.markdown("---")

# 1. Bộ lọc tầng dữ liệu
layer_keys = list(LAYERS.keys())
layer_labels = {k: f"{v['icon']} {v['title']}" for k, v in LAYERS.items()}

selected_layer = st.sidebar.selectbox(
    "1. Lọc theo tầng kiến trúc:",
    layer_keys,
    format_func=lambda x: layer_labels[x],
    index=0,
)

# 2. Danh sách các bảng theo tầng đã chọn
layer_tables = CATALOG.get_layer_tables(selected_layer)

def table_display_name(tbl_name: str) -> str:
    tbl = CATALOG.get_table(tbl_name)
    if not tbl:
        return tbl_name
    icon = LAYERS.get(tbl["layer"], {}).get("icon", "📄")
    pq_tag = "📦 " if tbl["has_parquet"] else ""
    return f"{icon} [{tbl['layer'].upper()}] {tbl_name} ({tbl['total_columns']} cols) {pq_tag}"

selected_table = st.sidebar.selectbox(
    "2. Chọn bảng xem chi tiết:",
    layer_tables,
    format_func=table_display_name,
    index=0 if layer_tables else 0,
)

st.sidebar.markdown("---")
st.sidebar.markdown("**⚙️ Tùy chọn Sơ đồ ERD:**")
show_inter_layer_wires = st.sidebar.checkbox("Hiển thị dây nối xuyên tầng", value=True)
physics_enabled = st.sidebar.checkbox("Bật tự sắp xếp vị trí (Physics)", value=True)

st.sidebar.markdown("---")
st.sidebar.info(
    "💡 **Mẹo:** Trong tab **Sơ Đồ ERD Có Dây Nối**, hãy dùng chuột kéo thả từng bảng "
    "để quan sát các dây nối mũi tên thể hiện quan hệ khóa ngoại (Foreign Keys)."
)

# --- MAIN TABS ---
tab_erd, tab_sql, tab_parquet, tab_search, tab_arch = st.tabs([
    "🕸️ Sơ Đồ ERD Có Dây Nối",
    "📋 Cấu Trúc Bảng SQL",
    "📊 Dữ Liệu Parquet & JSON Mẫu",
    "🔍 Tra Cứu Cột Toàn CSDL",
    "🗺️ Kiến Trúc Dữ Liệu Đa Tầng",
])

# =============================================================================
# TAB 1: SƠ ĐỒ ERD CÓ DÂY NỐI
# =============================================================================
with tab_erd:
    curr_layer_info = LAYERS.get(selected_layer, {})
    
    top_col1, top_col2, top_col3 = st.columns([4, 2, 2])
    with top_col1:
        st.subheader(f"🕸️ Sơ Đồ ERD Có Dây Nối — {curr_layer_info.get('title', 'Tất Cả')}")
        st.caption(
            f"Hiển thị {len(layer_tables)} bảng. "
            "Bấm nút **⛶ Toàn Màn Hình** hoặc **🌐 Mở Tab Riêng** trên thanh công cụ sơ đồ để mở rộng 100% màn hình."
        )
    with top_col2:
        height_choice = st.selectbox(
            "📐 Chiều cao khung sơ đồ:",
            options=[750, 950, 1200, 1500],
            format_func=lambda h: {750: "750px (Tiêu chuẩn)", 950: "950px (Rộng rãi)", 1200: "1200px (Rất rộng)", 1500: "1500px (Toàn cảnh)"}[h],
            index=1,
        )
    with top_col3:
        st.write("")
        st.download_button(
            "💾 Tải File HTML Offline",
            data=generate_vis_network_html(
                current_layer=selected_layer,
                show_inter_layer_wires=show_inter_layer_wires,
                physics_enabled=physics_enabled,
            ),
            file_name=f"gsm_erd_{selected_layer}.html",
            mime="text/html",
            help="Tải file HTML độc lập để mở bằng Chrome/Edge hoặc trình chiếu toàn màn hình.",
        )

    # Hiển thị canvas Vis.js
    erd_html = generate_vis_network_html(
        current_layer=selected_layer,
        show_inter_layer_wires=show_inter_layer_wires,
        physics_enabled=physics_enabled,
    )
    if hasattr(st, "iframe"):
        st.iframe(erd_html, height=height_choice)
    else:
        components.html(erd_html, height=height_choice, scrolling=False)

    with st.expander("📝 Xem mã Mermaid ERD (Dành cho tài liệu Markdown / Notion / Obsidian)"):
        mermaid_code = generate_mermaid_erd(layer_tables)
        st.code(mermaid_code, language="mermaid")

# =============================================================================
# TAB 2: CẤU TRÚC BẢNG SQL (DESCRIBE & DDL)
# =============================================================================
with tab_sql:
    if selected_table:
        tbl = CATALOG.get_table(selected_table)
        if tbl:
            layer_info = LAYERS.get(tbl["layer"], {})
            badge_color = layer_info.get("color", "#475569")

            # Header bảng
            st.markdown(
                f"### <span class='layer-badge' style='background-color:{badge_color};'>"
                f"{layer_info.get('icon', '')} TẦNG {tbl['layer'].upper()}</span> "
                f"Bảng: `{selected_table}` ({tbl['total_columns']} trường)",
                unsafe_allow_html=True,
            )

            if tbl["description"]:
                st.info(f"**Mô tả:** {tbl['description']}")

            # Quan hệ khóa ngoại (Incoming / Outgoing)
            rc1, rc2 = st.columns(2)
            with rc1:
                st.markdown("**🔗 Bảng này liên kết đến (Outgoing FKs):**")
                if tbl["outgoing_fks"]:
                    for r in tbl["outgoing_fks"]:
                        st.markdown(f"- ➔ `{r['target']}` qua `{r['source_col']} = {r['target_col']}` ({r['label']})")
                else:
                    st.markdown("- *Không có liên kết đi ra ngoài.*")

            with rc2:
                st.markdown("**📥 Bảng này được tham chiếu bởi (Incoming FKs):**")
                if tbl["incoming_fks"]:
                    for r in tbl["incoming_fks"]:
                        st.markdown(f"- ⬅️ Từ `{r['source']}` qua `{r['source_col']} = {r['target_col']}` ({r['label']})")
                else:
                    st.markdown("- *Không có bảng nào tham chiếu vào bảng này.*")

            # Lịch sử phiên bản (nếu có)
            if tbl["historical_versions"]:
                st.markdown(
                    f"**📜 Phiên bản hiện tại:** `v{tbl['schema_version']}` | "
                    f"**Các bản snapshot lịch sử:** {', '.join([f'`v{v}`' for v in tbl['historical_versions']])}"
                )

            st.markdown("---")
            st.markdown("#### 📄 Danh Sách Chi Tiết Các Cột (Columns Schema)")

            # Chuẩn bị dữ liệu hiển thị
            col_rows = []
            for idx, c in enumerate(tbl["columns"], 1):
                role_label = "-"
                if c["is_pk"]:
                    role_label = "[PK] Khóa chính"
                elif c["is_fk"]:
                    role_label = "[FK] Khóa ngoại"
                elif c["is_h3"]:
                    role_label = "[H3] Tọa độ không gian"

                col_rows.append({
                    "STT": idx,
                    "Tên Cột": c["name"],
                    "Kiểu Dữ Liệu": c["type"],
                    "Bắt Buộc": "Bắt buộc (NOT NULL)" if c["required"] else "Tùy chọn (NULL)",
                    "Vai Trò": role_label,
                    "PII": "Bảo mật PII" if c["is_pii"] else "Thường",
                    "Mô tả": c["description"],
                })

            st.dataframe(
                col_rows,
                column_config={
                    "STT": st.column_config.NumberColumn(width="small"),
                    "Tên Cột": st.column_config.TextColumn(width="medium"),
                    "Kiểu Dữ Liệu": st.column_config.TextColumn(width="small"),
                    "Bắt Buộc": st.column_config.TextColumn(width="medium"),
                    "Vai Trò": st.column_config.TextColumn(width="medium"),
                    "PII": st.column_config.TextColumn(width="small"),
                    "Mô tả": st.column_config.TextColumn(width="large"),
                },
                hide_index=True,
                width="stretch",
            )

            with st.expander("💻 Xem câu lệnh SQL CREATE TABLE (DDL chuẩn)"):
                sql_code = generate_sql_ddl(selected_table)
                st.code(sql_code, language="sql")

# =============================================================================
# TAB 3: DỮ LIỆU PARQUET & JSON CONTRACT MẪU
# =============================================================================
with tab_parquet:
    if selected_table:
        tbl = CATALOG.get_table(selected_table)
        if tbl:
            if tbl["has_parquet"]:
                st.subheader(f"📊 Dữ Liệu Thật Trong `{selected_table}.parquet`")
                st.success("Bảng này có file dữ liệu Parquet thật được lưu trữ trên đĩa.")

                pq_path = Path(tbl["parquet_path"])
                try:
                    df = pl.read_parquet(pq_path)
                    pm1, pm2, pm3 = st.columns(3)
                    pm1.metric("Tổng số dòng", f"{len(df):,} dòng")
                    pm2.metric("Số cột thực tế", f"{len(df.columns)} cột")
                    pm3.metric("Dung lượng file", f"{round(tbl['parquet_size_bytes'] / (1024 * 1024), 2)} MB")

                    st.markdown("##### 15 Dòng Dữ Liệu Đầu Tiên:")
                    st.dataframe(df.head(15).to_pandas(), width="stretch")

                except Exception as e:
                    st.error(f"Lỗi khi đọc file Parquet: {e}")
            else:
                st.subheader(f"📄 Bản Ghi Mẫu JSON Contract Cho `{selected_table}`")
                st.info(
                    f"Bảng này thuộc tầng **{tbl['layer'].upper()}** "
                    "(là bảng sự kiện mô phỏng, bảng trạng thái tổng hợp hoặc hợp đồng AI). "
                    "Hệ thống tự động sinh một bản ghi mẫu hợp lệ theo đúng cấu trúc schema bên dưới:"
                )

                mock_record = CATALOG.generate_mock_record(selected_table)

                # Hiển thị dạng bảng ngang 1 dòng
                st.markdown("##### Bản ghi mẫu (Xem dạng Bảng):")
                st.dataframe([mock_record], width="stretch")

                # Hiển thị dạng JSON format
                st.markdown("##### Bản ghi mẫu (Xem dạng JSON Code):")
                st.code(json.dumps(mock_record, ensure_ascii=False, indent=2), language="json")

# =============================================================================
# TAB 4: TRA CỨU CỘT TOÀN CSDL (657 TRƯỜNG)
# =============================================================================
with tab_search:
    st.subheader(f"🔍 Tra Cứu Cột Toàn CSDL ({total_columns_count} Trường Thuộc {total_tables_count} Bảng)")
    st.caption("Tìm kiếm xem một cột bất kỳ (ví dụ: `driver_id`, `hex`, `amount`, `bonus`, `status`, `battery`...) thuộc về những bảng nào.")

    sc_col1, sc_col2, sc_col3 = st.columns([3, 2, 2])
    with sc_col1:
        keyword = st.text_input("Nhập tên trường hoặc từ khóa mô tả:", value="driver_id").strip().lower()
    with sc_col2:
        layer_filter = st.selectbox(
            "Lọc theo tầng:",
            ["Tất cả"] + list(LAYERS.keys())[1:],
            format_func=lambda x: "Tất cả các tầng" if x == "Tất cả" else f"{LAYERS[x]['icon']} {LAYERS[x]['title']}",
        )
    with sc_col3:
        role_filter = st.selectbox(
            "Lọc theo vai trò cột:",
            ["Tất cả vai trò", "Chỉ khóa chính [PK]", "Chỉ khóa ngoại [FK]", "Chỉ tọa độ [H3]", "Chỉ thông tin PII"],
        )

    all_cols = CATALOG.get_all_columns_flattened()

    # Áp dụng bộ lọc
    filtered_cols = []
    for c in all_cols:
        # Lọc từ khóa
        if keyword and keyword not in c["column"].lower() and keyword not in c["description"].lower():
            continue
        # Lọc tầng
        if layer_filter != "Tất cả" and c["layer"].lower() != layer_filter.lower():
            continue
        # Lọc vai trò
        if role_filter == "Chỉ khóa chính [PK]" and c["role"] != "[PK]":
            continue
        if role_filter == "Chỉ khóa ngoại [FK]" and c["role"] != "[FK]":
            continue
        if role_filter == "Chỉ tọa độ [H3]" and c["role"] != "[H3]":
            continue
        if role_filter == "Chỉ thông tin PII" and c["pii"] != "Có":
            continue

        filtered_cols.append(c)

    if filtered_cols:
        st.success(f"Tìm thấy **{len(filtered_cols)} trường** phù hợp với tiêu chí:")
        st.dataframe(
            filtered_cols,
            column_config={
                "table": st.column_config.TextColumn("Bảng Sở Hữu", width="medium"),
                "layer": st.column_config.TextColumn("Tầng", width="small"),
                "column": st.column_config.TextColumn("Tên Cột", width="medium"),
                "type": st.column_config.TextColumn("Kiểu", width="small"),
                "required": st.column_config.TextColumn("Bắt Buộc", width="small"),
                "role": st.column_config.TextColumn("Vai Trò", width="small"),
                "pii": st.column_config.TextColumn("PII", width="small"),
                "description": st.column_config.TextColumn("Mô Tả Chi Tiết", width="large"),
            },
            hide_index=True,
            width="stretch",
        )
    else:
        st.warning("Không tìm thấy trường nào phù hợp với bộ lọc hiện tại.")

# =============================================================================
# TAB 5: KIẾN TRÚC DỮ LIỆU ĐA TẦNG (MEDALLION / EVENT-SOURCING)
# =============================================================================
with tab_arch:
    st.subheader("🗺️ Bản Đồ Kiến Trúc Dữ Liệu Đa Tầng GSM Simulator")
    st.markdown("""
Hệ thống quản lý dữ liệu của **GSM Simulator** được tổ chức theo kiến trúc đa tầng (Multi-Layer Data Lakehouse) nhằm đảm bảo:
- **Tính bất biến (Immutability)**: Sự kiện đã xảy ra không bao giờ bị sửa đổi.
- **Tính truy vết (Traceability)**: Mọi quyết định của thuật toán AI đều có thể truy ngược về trạng thái và dữ liệu gốc.
- **Phân tách trách nhiệm (Separation of Concerns)**: Tách rời dữ liệu danh mục, nhật ký sự kiện, trạng thái tổng hợp và đầu vào AI.
""")

    st.markdown("### 🔄 Luồng Di Chuyển Của Dữ Liệu:")
    st.code("""
┌───────────────────────────┐      ┌───────────────────────────┐
│   L0: Master Reference    │      │    L1R: Raw GSM Data      │
│  Hồ sơ tài xế, trạm pin,  │      │  13 bảng dữ liệu thô GSM  │
│   chính sách, bản đồ ô    │      │  (13 file Parquet thật)   │
└─────────────┬─────────────┘      └─────────────┬─────────────┘
              │                                  │
              ▼                                  ▼
┌──────────────────────────────────────────────────────────────┐
│                  L1: Core Simulation Event Logs              │
│       Nhật ký cuốc xe, GPS ping, giao dịch đổi pin, app      │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│           L2 & L2I: Aggregated State & Inferred Views        │
│       Mật độ cung cầu ô H3, trạng thái tài xế theo ngày      │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                L3: Feature Views Cho Mô Hình AI              │
│      Kế hoạch ca làm việc, khoán tuần, khoảng cách thưởng    │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│              Advisor: AI Decisioning & Explanations          │
│       Gợi ý phân bổ xe, tư vấn tài xế, giải trình quyết định │
└──────────────────────────────────────────────────────────────┘
""", language="text")

    st.markdown("### 📑 Chi Tiết Các Tầng:")
    for l_key, l_meta in LAYERS.items():
        if l_key == "all":
            continue
        t_list = CATALOG.get_layer_tables(l_key)
        col_sum = sum(CATALOG.get_table(t)["total_columns"] for t in t_list)
        st.markdown(
            f"- **{l_meta['icon']} {l_meta['title']}** (`{len(t_list)} bảng`, `{col_sum} cột`): "
            f"{l_meta['subtitle']}."
        )
