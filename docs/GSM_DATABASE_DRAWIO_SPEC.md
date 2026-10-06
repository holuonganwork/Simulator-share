# HỒ SƠ ĐẶC TẢ CƠ SỞ DỮ LIỆU GSM (13 BẢNG - 218 TRƯỜNG)
## DÙNG ĐỂ TẠO FILE XML IMPORT VÀO DRAW.IO QUA GEMINI WEB

> **HƯỚNG DẪN DÙNG VỚI GEMINI WEB:**
> Bạn chỉ cần copy toàn bộ nội dung file này vào khung chat Gemini Web kèm câu lệnh prompt ở mục 1.

---

## 1. CÂU LỆNH PROMPT MẪU CHO GEMINI WEB

```text
Bạn là chuyên gia thiết kế kiến trúc cơ sở dữ liệu và công cụ draw.io.
Dưới đây là tài liệu đặc tả chi tiết 13 bảng với đúng 218 trường dữ liệu của hệ thống xe điện GSM.
Hãy sinh ra cho tôi toàn bộ mã nguồn XML của draw.io (định dạng mxfile/mxGraphModel) để tôi có thể Copy và dán vào draw.io (Menu: Arrange -> Insert -> Advanced -> XML hoặc gõ trực tiếp vào mã nguồn).

Yêu cầu thiết kế sơ đồ trên draw.io:
1. Mỗi bảng là một Entity/Table rõ ràng gồm: Header tên bảng có màu riêng theo từng nhóm nghiệp vụ, danh sách các cột kèm kiểu dữ liệu, đánh dấu rõ ràng [PK] (Khóa chính), [FK] (Khóa ngoại).
2. Sắp xếp bố cục (Layout) trực quan thành 5 cụm (Swimlanes / Subgraphs):
   - Cụm 1 (Màu xanh dương): Hồ sơ tài xế (kpi_driver_platform_calculator_gbq đặt ở trung tâm).
   - Cụm 2 (Màu xanh lá): Vận hành cuốc xe & H3 Tracking (trips, public_driver_hex_tracking, driver_bike_stoppoints).
   - Cụm 3 (Màu vàng cam): Tài chính & Giờ cao điểm (driver_income_daily, driver_orders_rush_hours).
   - Cụm 4 (Màu tím): Nhiệm vụ & Gamification (public_mission, public_user_mission_progress, public_mission_earn_history).
   - Cụm 5 (Màu đỏ nhạt): Rủi ro & Gian lận (driver_penalization_ATA, public_frauds).
3. Vẽ đầy đủ các đường mũi tên liên kết (Relationship Connectors) giữa các bảng thể hiện quan hệ 1-N.
4. Mã XML xuất ra phải hợp lệ 100%, không rút gọn bằng dấu ba chấm (...), sẵn sàng import vào draw.io.
```

---

## 2. TỔNG QUAN HỆ THỐNG CSDL GSM
- **Tổng số bảng thực tế:** 13 bảng
- **Tổng số trường (columns):** Đúng 218 trường dữ liệu
- **Đơn vị nghiệp vụ:** Nền tảng điều phối & quản lý tài xế xe điện GSM (Xanh SM)
- **Các thực thể trung tâm:** `driver_id` (Tài xế), `trip_id / order_id` (Chuyến đi), `mission_id` (Nhiệm vụ), `current_hex / init_hex` (Ô bản đồ Uber H3)

---

## 3. SƠ ĐỒ QUAN HỆ KHÓA CHÍNH - KHÓA NGOẠI (ER RELATIONSHIPS)

### 3.1. Từ bảng trung tâm Tài xế (`kpi_driver_platform_calculator_gbq`):
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `trips.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `driver_statistic_daily.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `driver_online_hours_sap_id.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `driver_income_daily.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `driver_orders_rush_hours.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `driver_bike_stoppoints.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `public_driver_hex_tracking.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `driver_penalization_ATA.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `public_frauds.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `public_user_mission_progress.driver_id`
- `kpi_driver_platform_calculator_gbq.driver_id` (1) ---> (N) `public_mission_earn_history.driver_id`

### 3.2. Từ bảng Nhiệm vụ thưởng (`public_mission`):
- `public_mission.id` (1) ---> (N) `public_user_mission_progress.mission_id`
- `public_mission.id` (1) ---> (N) `public_mission_earn_history.mission_id`

### 3.3. Từ bảng Chuyến đi (`trips`):
- `trips.trip_id` (1) ---> (N) `public_mission_earn_history.order_id`

---

## 4. ĐẶC TẢ CHI TIẾT TỪNG BẢNG VÀ 218 CỘT

### 1. NHÓM VẬN HÀNH & DI CHUYỂN (Core Operations & H3 Tracking)

#### Bảng: `trips` (21 cột)
- **Ý nghĩa nghiệp vụ:** trips
- **Mô tả chức năng:** Trip-level dispatch (UC5 mật độ cuốc). ENGINEER shape từ trip_record ta. customer_id drop.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `trip_id` | `string` | Bắt buộc | [PK]  |
| 2 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 3 | `customer_id` | `['string', 'null']` | Tùy chọn |   |
| 4 | `service_type` | `string` | Bắt buộc |   |
| 5 | `status` | `string` | Bắt buộc |   |
| 6 | `request_time` | `string` | Bắt buộc | [DateTime] ISO-8601 UTC+7 |
| 7 | `assign_time` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 8 | `pickup_time` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 9 | `complete_time` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 10 | `pickup_h3` | `string` | Bắt buộc |   |
| 11 | `drop_h3` | `string` | Bắt buộc |   |
| 12 | `distance_km` | `number` | Tùy chọn |   |
| 13 | `duration_seconds` | `['integer', 'null']` | Tùy chọn |   |
| 14 | `gross_vnd` | `integer` | Bắt buộc |  VND nguyên (không âm) |
| 15 | `commission_vnd` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 16 | `rush_hour` | `boolean` | Tùy chọn |   |
| 17 | `travel_mode` | `['string', 'null']` | Tùy chọn |   |
| 18 | `created_at` | `string` | Tùy chọn | [DateTime] ISO-8601 UTC+7 |
| 19 | `datastream_metadata` | `['object', 'null']` | Tùy chọn |  CDC Datastream metadata — không dùng logic |
| 20 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 21 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `public_driver_hex_tracking` (21 cột)
- **Ý nghĩa nghiệp vụ:** public_driver_hex_tracking
- **Mô tả chức năng:** Chuyển động H3 + reposition (UC5). target_hex/reached = reposition mission GSM.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `id` | `string` | Bắt buộc | [PK]  |
| 2 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 3 | `campaign_id` | `['string', 'null']` | Tùy chọn |   |
| 4 | `log_id` | `['string', 'null']` | Tùy chọn |   |
| 5 | `init_hex` | `['string', 'null']` | Tùy chọn | [H3 Spatial Index]  |
| 6 | `current_hex` | `string` | Bắt buộc | [H3 Spatial Index]  |
| 7 | `last_hex` | `['string', 'null']` | Tùy chọn | [H3 Spatial Index]  |
| 8 | `target_hex` | `['string', 'null']` | Tùy chọn | [H3 Spatial Index]  |
| 9 | `last_seen_at` | `string` | Bắt buộc | [DateTime] ISO-8601 UTC+7 |
| 10 | `entered_current_hex_at` | `['string', 'null']` | Tùy chọn | [H3 Spatial Index]  |
| 11 | `stay_duration_seconds` | `integer` | Bắt buộc |   |
| 12 | `reached_target` | `['boolean', 'null']` | Tùy chọn |   |
| 13 | `reached_target_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 14 | `hex_history` | `['array', 'null']` | Tùy chọn | [H3 Spatial Index]  |
| 15 | `created_at` | `string` | Tùy chọn | [DateTime] ISO-8601 UTC+7 |
| 16 | `updated_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 17 | `schedule_job_id` | `['string', 'null']` | Tùy chọn |   |
| 18 | `datastream_metadata` | `['object', 'null']` | Tùy chọn |  CDC Datastream metadata — không dùng logic |
| 19 | `tracking_status` | `string` | Bắt buộc |   |
| 20 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 21 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `driver_bike_stoppoints` (6 cột)
- **Ý nghĩa nghiệp vụ:** driver_bike_stoppoints
- **Mô tả chức năng:** Số điểm dừng/ngày (UC2/UC5 idle proxy). rush ⊆ total.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 2 | `local_date` | `string` | Bắt buộc | [DateTime]  |
| 3 | `total_stoppoints` | `integer` | Bắt buộc |   |
| 4 | `total_stoppoints_rush_hour` | `integer` | Bắt buộc |   |
| 5 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 6 | `schema_version` | `string` | Bắt buộc |   |


### 2. NHÓM HỒ SƠ TÀI XẾ & CA LÀM VIỆC (Driver Profile & Work Shift)

#### Bảng: `kpi_driver_platform_calculator_gbq` (23 cột)
- **Ý nghĩa nghiệp vụ:** kpi_driver_platform_calculator_gbq
- **Mô tả chức năng:** Tính KPI tuần + thưởng (UC3) — NỀN S5 khoán. PII optional (scrub). Số target/threshold có thể ở meta → TBC.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `id` | `string` | Bắt buộc | [PK]  |
| 2 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 3 | `driver_name` | `['string', 'null']` | Tùy chọn |   |
| 4 | `sap_id` | `['string', 'null']` | Tùy chọn |   |
| 5 | `status` | `['string', 'null']` | Tùy chọn |   |
| 6 | `week_key` | `string` | Bắt buộc |   |
| 7 | `week_start` | `string` | Bắt buộc |   |
| 8 | `week_end` | `string` | Bắt buộc |   |
| 9 | `kpi_month` | `['integer', 'null']` | Tùy chọn |   |
| 10 | `kpi_year` | `['integer', 'null']` | Tùy chọn |   |
| 11 | `email` | `['string', 'null']` | Tùy chọn |   |
| 12 | `tel` | `['string', 'null']` | Tùy chọn |   |
| 13 | `engname` | `['string', 'null']` | Tùy chọn |   |
| 14 | `depot_code` | `['string', 'null']` | Tùy chọn |   |
| 15 | `depot_name` | `['string', 'null']` | Tùy chọn |   |
| 16 | `vehicle_vin_number` | `['string', 'null']` | Tùy chọn |   |
| 17 | `vehicle_license_plate` | `['string', 'null']` | Tùy chọn |   |
| 18 | `vehicle_model` | `['string', 'null']` | Tùy chọn |   |
| 19 | `country` | `['string', 'null']` | Tùy chọn |   |
| 20 | `type` | `['string', 'null']` | Tùy chọn |   |
| 21 | `last_updated_date` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 22 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 23 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `driver_online_hours_sap_id` (12 cột)
- **Ý nghĩa nghiệp vụ:** driver_online_hours_sap_id
- **Mô tả chức năng:** Số giờ online/ngày (UC1). PII (full_name/phone/sap) optional — tool scrub bỏ.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `local_date` | `string` | Bắt buộc | [DateTime]  |
| 2 | `schedule_date` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 3 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 4 | `full_name` | `['string', 'null']` | Tùy chọn |   |
| 5 | `sap_profile_id` | `['string', 'null']` | Tùy chọn |   |
| 6 | `hub_id` | `['string', 'null']` | Tùy chọn |   |
| 7 | `depot_id` | `['string', 'null']` | Tùy chọn |   |
| 8 | `phone_number` | `['string', 'null']` | Tùy chọn |   |
| 9 | `driver_type` | `['string', 'null']` | Tùy chọn |   |
| 10 | `online_time` | `number` | Bắt buộc | [DateTime] giờ |
| 11 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 12 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `driver_statistic_daily` (17 cột)
- **Ý nghĩa nghiệp vụ:** driver_statistic_daily
- **Mô tả chức năng:** Snapshot KPI vận doanh daily (UC1/UC4). rate = count/request.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `local_date` | `string` | Bắt buộc | [DateTime]  |
| 2 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 3 | `completed_count` | `integer` | Bắt buộc |   |
| 4 | `accepted_count` | `integer` | Bắt buộc |   |
| 5 | `cancelled_count` | `integer` | Bắt buộc |   |
| 6 | `total_request_calculate_complete` | `integer` | Tùy chọn |   |
| 7 | `total_request_calculate_cancel` | `integer` | Tùy chọn |   |
| 8 | `total_request_calculate_accept` | `integer` | Tùy chọn |   |
| 9 | `count_cancel_not_relate_driver` | `integer` | Tùy chọn |   |
| 10 | `total_rating` | `['number', 'null']` | Tùy chọn |   |
| 11 | `total_order_rating` | `integer` | Tùy chọn |   |
| 12 | `count_rating_5_star` | `integer` | Tùy chọn |   |
| 13 | `acceptance_rate` | `number` | Bắt buộc |   |
| 14 | `fulfillment_rate` | `number` | Bắt buộc |   |
| 15 | `cancellation_rate` | `number` | Bắt buộc |   |
| 16 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 17 | `schema_version` | `string` | Bắt buộc |   |


### 3. NHÓM TÀI CHÍNH & KHUNG GIỜ (Income & Rush Hours)

#### Bảng: `driver_income_daily` (10 cột)
- **Ý nghĩa nghiệp vụ:** driver_income_daily
- **Mô tả chức năng:** Thu nhập daily (UC3/UC4). commission=driver_payout; total_fee=gross; revenue_not_relate_driver=phần nền tảng. total_core_order ⊆ total_order.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 2 | `order_date` | `string` | Bắt buộc | [DateTime]  |
| 3 | `commission` | `integer` | Bắt buộc |  VND nguyên (không âm) |
| 4 | `total_order` | `integer` | Bắt buộc |   |
| 5 | `total_fee` | `integer` | Bắt buộc |  VND nguyên (không âm) |
| 6 | `revenue_not_relate_driver` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 7 | `avg_daily_revenue` | `number` | Tùy chọn |   |
| 8 | `total_core_order` | `integer` | Tùy chọn |   |
| 9 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 10 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `driver_orders_rush_hours` (16 cột)
- **Ý nghĩa nghiệp vụ:** driver_orders_rush_hours
- **Mô tả chức năng:** Doanh số tách rush/normal hour (UC2). normal+rush=total.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 2 | `local_date` | `string` | Bắt buộc | [DateTime]  |
| 3 | `total_order` | `integer` | Bắt buộc |   |
| 4 | `commission` | `integer` | Bắt buộc |  VND nguyên (không âm) |
| 5 | `total_fee` | `integer` | Bắt buộc |  VND nguyên (không âm) |
| 6 | `revenue_not_relate_driver` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 7 | `total_order_normal_hour` | `integer` | Tùy chọn |   |
| 8 | `commission_normal_hour` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 9 | `total_fee_normal_hour` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 10 | `revenue_not_relate_driver_normal_hour` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 11 | `total_order_rush_hour` | `integer` | Tùy chọn |   |
| 12 | `commission_rush_hour` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 13 | `total_fee_rush_hour` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 14 | `revenue_not_relate_driver_rush_hour` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 15 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 16 | `schema_version` | `string` | Bắt buộc |   |


### 4. NHÓM NHIỆM VỤ & THƯỞNG GAMIFICATION (Missions & Rewards)

#### Bảng: `public_mission` (30 cột)
- **Ý nghĩa nghiệp vụ:** public_mission (public_mission)
- **Mô tả chức năng:** Catalog mini-task (UC8) — NỀN S6 knapsack. rewards + khung giờ + rule.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `id` | `string` | Bắt buộc | [PK]  |
| 2 | `created_at` | `string` | Tùy chọn | [DateTime] ISO-8601 UTC+7 |
| 3 | `updated_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 4 | `deleted_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 5 | `created_by` | `['string', 'null']` | Tùy chọn |   |
| 6 | `updated_by` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 7 | `mission_type` | `string` | Bắt buộc |   |
| 8 | `parent_id` | `['string', 'null']` | Tùy chọn |   |
| 9 | `name` | `string` | Bắt buộc |   |
| 10 | `state` | `['string', 'null']` | Tùy chọn |   |
| 11 | `audience` | `['string', 'null']` | Tùy chọn |   |
| 12 | `description` | `['string', 'null']` | Tùy chọn |   |
| 13 | `start_time` | `string` | Bắt buộc | [DateTime] ISO-8601 UTC+7 |
| 14 | `end_time` | `string` | Bắt buộc | [DateTime] ISO-8601 UTC+7 |
| 15 | `point_id` | `['string', 'null']` | Tùy chọn |   |
| 16 | `rewards` | `object` | Bắt buộc |  phần thưởng (vnd/point) + điều kiện |
| 17 | `mission_claim` | `['string', 'null']` | Tùy chọn |   |
| 18 | `mission_code` | `['string', 'null']` | Tùy chọn |   |
| 19 | `time_claim_reward` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 20 | `rule_code` | `['string', 'null']` | Tùy chọn |   |
| 21 | `meta_data` | `['object', 'null']` | Tùy chọn |   |
| 22 | `contract_type` | `['string', 'null']` | Tùy chọn |   |
| 23 | `qualify_execute_code` | `['string', 'null']` | Tùy chọn |   |
| 24 | `status` | `['string', 'null']` | Tùy chọn |   |
| 25 | `datastream_metadata` | `['object', 'null']` | Tùy chọn |  CDC Datastream metadata — không dùng logic |
| 26 | `business_code` | `['string', 'null']` | Tùy chọn |   |
| 27 | `show_only` | `['boolean', 'null']` | Tùy chọn |   |
| 28 | `is_ddi_mission` | `['boolean', 'null']` | Tùy chọn |   |
| 29 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 30 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `public_user_mission_progress` (14 cột)
- **Ý nghĩa nghiệp vụ:** public_user_mission_progress
- **Mô tả chức năng:** Tiến độ mission per driver (UC8) — ENGINEER. progress ≤ target.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `id` | `string` | Bắt buộc | [PK]  |
| 2 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 3 | `mission_id` | `string` | Bắt buộc | [FK -> public_mission]  |
| 4 | `progress_count` | `integer` | Bắt buộc |   |
| 5 | `target_count` | `integer` | Bắt buộc |   |
| 6 | `progress_value_vnd` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 7 | `target_value_vnd` | `integer` | Tùy chọn |  VND nguyên (không âm) |
| 8 | `state` | `string` | Bắt buộc |   |
| 9 | `started_at` | `string` | Tùy chọn | [DateTime] ISO-8601 UTC+7 |
| 10 | `updated_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 11 | `claimed_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 12 | `datastream_metadata` | `['object', 'null']` | Tùy chọn |  CDC Datastream metadata — không dùng logic |
| 13 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 14 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `public_mission_earn_history` (23 cột)
- **Ý nghĩa nghiệp vụ:** public_mission_earn_history
- **Mô tả chức năng:** Lịch sử nhận thưởng mission (UC3/UC8). earn từ mission.rewards. customer_id drop.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `id` | `string` | Bắt buộc | [PK]  |
| 2 | `created_at` | `string` | Tùy chọn | [DateTime] ISO-8601 UTC+7 |
| 3 | `updated_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 4 | `deleted_at` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 5 | `mission_id` | `string` | Bắt buộc | [FK -> public_mission]  |
| 6 | `order_id` | `['string', 'null']` | Tùy chọn | [FK -> trips]  |
| 7 | `order_status` | `['string', 'null']` | Tùy chọn |   |
| 8 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 9 | `customer_id` | `['string', 'null']` | Tùy chọn |   |
| 10 | `service_type` | `['string', 'null']` | Tùy chọn |   |
| 11 | `order_time` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 12 | `complete_time` | `['string', 'null']` | Tùy chọn | [DateTime]  |
| 13 | `travel_mode` | `['string', 'null']` | Tùy chọn |   |
| 14 | `sap_contract_type` | `['string', 'null']` | Tùy chọn |   |
| 15 | `type` | `['string', 'null']` | Tùy chọn |   |
| 16 | `count_order` | `integer` | Bắt buộc |   |
| 17 | `count_stoppoint` | `integer` | Tùy chọn |   |
| 18 | `earn` | `integer` | Bắt buộc |  VND nguyên (không âm) |
| 19 | `description` | `['string', 'null']` | Tùy chọn |   |
| 20 | `datastream_metadata` | `['object', 'null']` | Tùy chọn |  CDC Datastream metadata — không dùng logic |
| 21 | `reward_level` | `['string', 'null']` | Tùy chọn |   |
| 22 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 23 | `schema_version` | `string` | Bắt buộc |   |


### 5. NHÓM RỦI RO & CHẾ TÀI (Risk, Fraud & Penalties)

#### Bảng: `driver_penalization_ATA` (13 cột)
- **Ý nghĩa nghiệp vụ:** driver_penalization_ATA (penalization_ATA)
- **Mô tả chức năng:** Sự kiện phạt/trừ tiền (UC6) — ENGINEER. amount có source policy.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `penalization_id` | `string` | Bắt buộc |   |
| 2 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 3 | `local_date` | `string` | Bắt buộc | [DateTime]  |
| 4 | `week_key` | `['string', 'null']` | Tùy chọn |   |
| 5 | `penalty_type` | `string` | Bắt buộc |   |
| 6 | `amount_vnd` | `integer` | Bắt buộc |  VND nguyên (không âm) |
| 7 | `reason` | `['string', 'null']` | Tùy chọn |   |
| 8 | `related_metric` | `['string', 'null']` | Tùy chọn |   |
| 9 | `ata_code` | `['string', 'null']` | Tùy chọn |   |
| 10 | `status` | `string` | Bắt buộc |   |
| 11 | `created_at` | `string` | Tùy chọn | [DateTime] ISO-8601 UTC+7 |
| 12 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 13 | `schema_version` | `string` | Bắt buộc |   |


#### Bảng: `public_frauds` (12 cột)
- **Ý nghĩa nghiệp vụ:** public_frauds (public_frauds)
- **Mô tả chức năng:** Cờ bất thường (UC7) — ENGINEER, nhãn INFERRED, KHÔNG kết tội.

| STT | Tên trường (Column) | Kiểu dữ liệu | Bắt buộc | Phân loại Khóa & Ý nghĩa |
|---|---|---|---|---|
| 1 | `fraud_id` | `string` | Bắt buộc |   |
| 2 | `driver_id` | `string` | Bắt buộc | [FK -> kpi_driver]  |
| 3 | `detected_at` | `string` | Bắt buộc | [DateTime] ISO-8601 UTC+7 |
| 4 | `fraud_type` | `string` | Bắt buộc |   |
| 5 | `severity` | `string` | Bắt buộc |   |
| 6 | `confidence` | `number` | Bắt buộc |   |
| 7 | `evidence_ref` | `['string', 'null']` | Tùy chọn |   |
| 8 | `status` | `string` | Bắt buộc |   |
| 9 | `created_at` | `string` | Tùy chọn | [DateTime] ISO-8601 UTC+7 |
| 10 | `datastream_metadata` | `['object', 'null']` | Tùy chọn |  CDC Datastream metadata — không dùng logic |
| 11 | `source` | `string` | Bắt buộc |  Nhãn nguồn bắt buộc (CLAUDE.md §5) |
| 12 | `schema_version` | `string` | Bắt buộc |   |

