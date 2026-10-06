# GSM Multi-Layer Schema & ERD Explorer

Công cụ Streamlit trực quan hóa toàn diện **51 bảng CSDL chính thức (657 trường dữ liệu)** và **12 bản ghi snapshot lịch sử** thuộc 7 tầng kiến trúc dữ liệu của hệ sinh thái GSM Simulator.

---

## 🏛️ Kiến Trúc 7 Tầng Dữ Liệu (51 Bảng)

| Tầng | Tên Tầng | Số Bảng | Số Cột | Ý Nghĩa Nghiệp Vụ |
|---|---|---|---|---|
| **L0** | Reference Master Data | 5 | 44 | Bảng danh mục gốc: Hồ sơ tài xế (`driver_profile`), chính sách giá (`policy_bundle`), danh mục dịch vụ (`service_catalog`), trạm đổi pin (`station_registry`), bản đồ vùng (`zone_map`). |
| **L1** | Simulation Event Logs Core | 6 | 51 | Nhật ký sự kiện mô phỏng bất biến: Log GPS (`gps_ping`), cuốc xe (`trip_record`), đổi pin (`swap_transaction`), chi trả thu nhập (`payout_ledger`), sự kiện app (`app_event`), đổi chính sách (`policy_change_event`). |
| **L1R** | Raw GSM Data (Parquet) | 13 | 218 | **13 bảng dữ liệu thô GSM thật** đối ứng 1-1 với 13 file `.parquet` trong `data/mock/realdata-v1/` (`trips`, `driver_income_daily`, `public_mission`, `public_frauds`...). |
| **L2 / L2I** | Aggregated State & Inferences | 5 | 43 | Trạng thái tổng hợp cung/cầu theo ô H3 (`demand_field`, `supply_field`), trạng thái tài xế theo ngày (`driver_day_state`), trạm pin (`station_state`), hoạt động suy luận (`inferred_activity`). |
| **L3** | AI Feature Views | 10 | 105 | Bảng đặc trưng đầu vào cho mô hình tối ưu AI / Solver: Phân bổ xe (`allocation_input`), ca làm việc (`shift_plan_input`), chỉ tiêu khoán (`weekly_khoan_input`), nhiệm vụ (`mission_select_input`)... |
| **Advisor** | AI Decision Engine | 12 | 196 | Hợp đồng giao tiếp và giải trình quyết định của AI Agent: `advice_request`, `advice_checkpoint`, `composed_advice`, `solver_report`... |
| **Tổng** | **Toàn Bộ Hệ Thống** | **51** | **657** | **Mạng lưới liên kết hoàn chỉnh đa tầng.** |

---

## 🌟 5 Tính Năng Nổi Bật Trong Giao Diện

1. **🕸️ Sơ Đồ ERD Có Dây Nối (Interactive Canvas)**:
   - Sử dụng thư viện đồ họa **Vis.js Network**.
   - Mỗi bảng là một card SQL hiển thị tên bảng, tầng kiến trúc, số cột và danh sách các cột chính có gắn nhãn: `[PK]` (Khóa chính), `[FK]` (Khóa ngoại), `[H3]` (Tọa độ không gian).
   - Dây nối có mũi tên định hướng liên kết từ bảng này sang bảng khác theo các khóa ngoại (trục xương sống: `driver_id`, `station_id`, `trip_id`, `mission_id`, `checkpoint_id`).
   - Hỗ trợ thao tác kéo thả chuột (drag & drop), phóng to thu nhỏ (zoom), di chuyển tự do (pan), click vào một bảng để làm nổi bật các bảng liên kết xung quanh.
   - Xuất mã **Mermaid ERD** cho Notion/Obsidian.

2. **📋 Cấu Trúc Bảng SQL**:
   - Xem chi tiết từng bảng dạng `DESCRIBE <table>`: Tên cột, kiểu dữ liệu, bắt buộc (NOT NULL), vai trò, đánh dấu bảo mật PII, mô tả chi tiết.
   - Liệt kê các liên kết đi ra (Outgoing FKs) và bảng tham chiếu vào (Incoming FKs).
   - Tự động sinh câu lệnh SQL `CREATE TABLE` (DDL) chuẩn cơ sở dữ liệu quan hệ.

3. **📊 Dữ Liệu Parquet & JSON Contract Mẫu**:
   - Đối với 13 bảng L1R: Đọc trực tiếp file `.parquet` thực tế, hiển thị số dòng, dung lượng và 15 dòng dữ liệu mẫu.
   - Đối với các bảng L0, L1, L2, L3, Advisor: Tự động sinh bản ghi mẫu JSON hợp lệ theo đúng schema để xem dạng bảng và dạng mã JSON.

4. **🔍 Tra Cứu Cột Toàn CSDL**:
   - Tìm kiếm bất kỳ tên trường nào trong toàn bộ 657 cột.
   - Hỗ trợ bộ lọc theo tầng và lọc theo vai trò (Chỉ khóa chính, chỉ khóa ngoại, chỉ tọa độ H3, chỉ trường PII).

5. **🗺️ Bản Đồ Kiến Trúc Dữ Liệu Đa Tầng**:
   - Trực quan hóa luồng di chuyển dữ liệu từ dữ liệu thô (L0, L1R) qua trạng thái tổng hợp (L2), bảng đặc trưng (L3) đến hệ thống ra quyết định AI (Advisor).

---

## 🚀 Hướng Dẫn Chạy Ứng Dụng

Mở terminal tại thư mục `GSM_SIMULATOR-raw` và chạy lệnh:

```bash
streamlit run preview_schema/app.py
```

Trình duyệt sẽ tự động mở tại địa chỉ: `http://localhost:8501`.
(Nếu đang chạy sẵn, chỉ cần bấm **F5** hoặc nút **Rerun** trên giao diện Streamlit để cập nhật giao diện mới nhất).
