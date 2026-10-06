# THƯ MỤC CÁC THỰC THỂ PHÁT HIỆN GIAN LẬN (FRAUD ENTITY SCHEMAS)

> **Phân hệ áp dụng:** Đội xe Taxi Cơ Hữu Hà Nội (1000 xe VinFast VF5, VF6, VF e34, VF8 thuộc 100% sở hữu GSM, quản lý theo Hợp đồng lao động).  
> **Quy chuẩn đối soát:** Quy tắc Ứng xử GSM ngày 11/09/2026, Quyết định Sàn Đảm Bảo Thu Nhập 650.000 VNĐ/ngày (GF-VIP Hà Nội - 31/08/2026), và Bảng chế tài truy thu ATA.

---

## 1. Danh Sách 6 Thực Thể Schema (6 Entities)

| File Schema | Mã Vi Phạm | Tên Lỗi Kỹ Thuật & Nghiệp Vụ | Số Trường (Properties) | Số Trường Bắt Buộc | Bằng Chứng Tối Cao |
| :--- | :--- | :--- | :---: | :---: | :--- |
| [`f1_off_app_pickup.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f1_off_app_pickup.schema.json) | **F1** | Chở khách ngoài app / Nhận cuốc dọc đường (Off-app Pickup / Street Hail) | 35 | 18 | Hủy cuốc trên app nhưng xe vẫn lăn bánh có khách (Cảm biến áp suất ghế phụ/sau + Camera AI Cabin + Nhảy ODO) |
| [`f2_route_deviation.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f2_route_deviation.schema.json) | **F2** | Chạy sai lộ trình / Cố tình đi lòng vòng tăng tiền cước (Route Deviation) | 30 | 19 | OSRM Road Distance vs ODO thực tế, độ lệch vượt P99 theo phân loại 3 cấp đường (Cao tốc/Trục chính/Đường phố) |
| [`f3_tcu_signal_loss.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f3_tcu_signal_loss.schema.json) | **F3** | Mất tín hiệu hộp viễn thám TCU bất thường (TCU Signal Loss / GPS Jamming) | 31 | 15 | Mất tín hiệu viễn thám >15 phút trong ca, ODO nhảy $\ge 3.0$ km và pin sụt giảm khi có tín hiệu lại |
| [`f4_personal_use.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f4_personal_use.schema.json) | **F4** | Sử dụng xe công ty vào mục đích cá nhân ngoài ca trực (Personal Use / Geofence Breach) | 26 | 16 | Xe di chuyển $>10$ km ngoài ca trực cam kết hoặc vượt ranh giới thành phố Hà Nội $>3$ km |
| [`f5_fake_trip.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f5_fake_trip.schema.json) | **F5** | Tạo cuốc xe ảo / Cấu kết trục lợi Sàn 650.000đ/ngày (Fake Trips / Ghost Trips) | 31 | 19 | App báo cuốc đang chạy nhưng TCU xe báo vận tốc = 0 km/h, cần số P/N, ghế trống, camera cabin trống |
| [`f6_invalid_driver_cancel.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f6_invalid_driver_cancel.schema.json) | **F6** | Hủy sai lý do / Kén chọn cuốc xe (Abnormal Cancellation / Cherry-Picking) | 42 | 21 | Hủy với lý do "khách không đến" nhưng khoảng cách xe tới điểm đón $>150$m (chưa từng vào Geofence đón) |

---

## 2. Chi Tiết Các Nhóm Trường Cốt Lõi Bên Trong Từng Thực Thể

Mỗi thực thể vi phạm được chuẩn hóa thành 5 khối thuộc tính thống nhất:

### Khối 1: Định Danh & Master Data (Identifiers & Core Metadata)
- `incident_id`: UUID duy nhất của từng sự vụ phát hiện.
- `fraud_type`: Hằng số loại gian lận (`F1_OFF_APP_PICKUP`, `F2_ROUTE_DEVIATION`, `F3_TCU_SIGNAL_LOSS`, `F4_PERSONAL_USE`, `F5_FAKE_TRIP`, `F6_INVALID_DRIVER_CANCEL`).
- `detected_at`: Thời điểm phát hiện vi phạm (ISO 8601).
- `driver_id`: Mã định danh nhân sự tài xế Taxi Cơ Hữu.
- `vehicle_id` & `vin`: Biển kiểm soát Hà Nội (29E-xxx) và số khung 17 ký tự xe VinFast thuộc sở hữu GSM.
- `depot_id`: Phân bổ trạm quản lý (`DEPOT_GIA_LAM`, `DEPOT_NAM_TU_LIEM`, `DEPOT_YEN_NGHIA`).
- `track`: Bắt buộc là `core_owned` (Taxi Cơ Hữu 100% hợp đồng lao động).

### Khối 2: Bằng Chứng Viễn Thám Phần Cứng Xe VinFast (Hardware Telemetry Evidence)
- **TCU GNSS & ODO:** Tọa độ GPS, công-tơ-mét trước/sau, vận tốc tức thời, trạng thái ngắt giắc cắm hoặc dùng thiết bị phá sóng GPS.
- **CAN-bus & Cảm biến ghế:** Vị trí tay số (`P`, `R`, `N`, `D`), cảm biến trọng lượng ghế phụ và hàng ghế sau (`front_passenger_seat_occupied`, `rear_seat_occupied`).
- **Camera AI Cabin:** Phát hiện che chắn camera (`camera_obscured`), đếm số lượng người thực tế ngồi trong cabin (`detected_passenger_count`).
- **Năng lượng Pin & Trụ Sạc:** Mức tiêu thụ pin (`battery_soc_drop_pct`), trạng thái sạc trụ V-Green hoặc trụ Depot.

### Khối 3: Bằng Chứng Dữ Liệu Ứng Dụng (App & Transaction Tier)
- Trạng thái cuốc xe: Đã nhận, đã hủy, đã hoàn thành.
- Lý do hủy chuyến của tài xế (`cancel_reason`).
- Lịch sử liên lạc với hành khách: Số cuộc gọi VoIP (`call_attempt_count`), thời gian kết nối, tin nhắn trao đổi trong app.
- Hàng rào địa lý (Geofence): Bán kính đón khách 150m, ranh giới hành chính thành phố Hà Nội.

### Khối 4: Tài Chính, Truy Thu & Hậu Quả Thu Nhập (Financial Clawback & Penalties)
- **Truy thu F1:** Toàn bộ doanh thu cước "chạy chui" tính theo biểu giá chuẩn 13.000 - 19.000 đ/km.
- **Truy thu F2:** Tiền cước phát sinh do đi vòng đường, hoàn tiền trực tiếp cho khách hàng.
- **Truy thu F3:** 13.000 đ/km cho toàn bộ số km ODO nhảy chênh lệch khi mất sóng TCU.
- **Truy thu F4:** Chi phí khấu hao và tiền điện sạc pin: 3.500 đ/km cho toàn bộ km di chuyển cá nhân ngoài ca trực.
- **Truy thu F5:** Thu hồi toàn bộ cước ảo + Toàn bộ tiền bù sàn thu nhập 650.000 đ/ngày bị trục lợi. Phạt trừ ví 5.000.000đ - 10.000.000đ theo Lỗi Nhóm 1b.
- **Chế tài F6:** Phạt ATA 1.000.000đ (1-5 cuốc/tuần), 2.000.000đ (>5 cuốc/tuần). **Đặc biệt: Hủy cuốc làm tụt tỷ lệ hoàn thành khiến tài xế MẤT HOÀN TOÀN Sàn Thu Nhập 650.000 VNĐ/ngày** (thiệt hại 15 - 18 triệu đồng/tháng).

### Khối 5: Phân Loại Mức Độ & Quản Trị Vụ Việc (Audit & Case Escalation)
- `severity`: Cấp độ nguy cơ (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`).
- `confidence_score`: Điểm xác suất tin cậy của thuật toán phát hiện (0.0 đến 1.0).
- `investigation_status`: Vòng đời xử lý sự vụ (`DETECTED` -> `PENDING_DRIVER_EXPLANATION` -> `ESCALATED_TO_DEPOT` -> `RESOLVED_CONFIRMED`).
- `created_case_id`: Liên kết trực tiếp tới thực thể hồ sơ xử lý kỷ luật [`schemas/case/fraud_case.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/case/fraud_case.schema.json).

---

## 3. Liên Kết Kiến Trúc Hệ Thống

```
               [ Phần Cứng Xe VinFast (TCU / CAN-bus / Camera AI) ]
                                      │
                                      ▼
                       schemas/telemetry/*.schema.json
                                      │
                                      ▼
                        schemas/l1r/*.schema.json
                                      │
                                      ▼
        ┌───────────────────────────────────────────────────────────┐
        │             schemas/frauds/*.schema.json                  │
        │   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐   │
        │   │  F1: Off-App  │ │  F2: Deviate  │ │  F3: TCU Loss │   │
        │   └───────────────┘ └───────────────┘ └───────────────┘   │
        │   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐   │
        │   │  F4: Personal │ │  F5: FakeTrip │ │  F6: InvalCan │   │
        │   └───────────────┘ └───────────────┘ └───────────────┘   │
        └─────────────────────────────┬─────────────────────────────┘
                                      │
                                      ▼
                       schemas/case/fraud_case.schema.json
                                      │
                                      ▼
                schemas/l1r/driver_penalization_ATA.schema.json
```
