# ĐẶC TẢ CHI TIẾT 6 BỘ LUẬT BẮT LỖI GIAN LẬN (RULE-BASED FRAUD DETECTION SPECIFICATION)

> **Dành cho:** Đội xe Taxi Cơ Hữu Hà Nội (1000 xe VinFast VF5, VF6, VF e34, VF8 thuộc 100% sở hữu GSM, 100% hợp đồng lao động).  
> **Căn cứ pháp lý & nghiệp vụ:** [FRAUDS.md](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/FRAUDS.md), Bộ Quy tắc Ứng xử GSM ngày 11/09/2026, Quyết định Sàn Đảm Bảo Thu Nhập 650.000 VNĐ/ngày (GF-VIP Hà Nội - 31/08/2026), và Bảng chế tài truy thu ATA.  
> **Vị trí mã nguồn:** Thư mục [`src/lcx_core/frauds/`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/)

---

## 1. Kiến Trúc Tổng Thể & Bản Đồ Mã Nguồn

Hệ thống bắt lỗi theo luật cứng (Rule-based) được thiết kế theo nguyên tắc **Độc lập - Mô-đun hóa - Khớp chuẩn Schema 100%**:

```
src/lcx_core/frauds/
│
├── __init__.py                     # Export các hàm detect và FraudDetectorEngine
├── _common.py                      # Tiện ích chung: chuẩn hóa VIN 17 ký tự, Haversine GPS, parse ISO
├── f1_off_app_pickup.py            # Bắt lỗi F1: Chở khách ngoài app
├── f2_route_deviation.py           # Bắt lỗi F2: Đi lòng vòng tăng cước
├── f3_tcu_signal_loss.py           # Bắt lỗi F3: Rút giắc / Phá sóng mất tín hiệu TCU
├── f4_personal_use.py              # Bắt lỗi F4: Dùng xe công ty vào việc riêng ngoài ca
├── f5_fake_trip.py                 # Bắt lỗi F5: Tạo cuốc ảo trục lợi Sàn 650k/ngày
├── f6_invalid_driver_cancel.py     # Bắt lỗi F6: Hủy sai lý do kén chọn cuốc xe
└── detector_engine.py              # Engine trung tâm: điều phối quét đồng loạt, phân loại & tổng hợp tài chính
```

Tất cả kết quả đầu ra của 6 bộ luật này đều **được kiểm toán tự động vượt qua 100% cấu trúc JSON Schema Draft 2020-12** tương ứng tại thư mục [`schemas/frauds/`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/).

---

## 2. Đặc Tả Chi Tiết Từng Bộ Luật & Hướng Dẫn Tinh Chỉnh Tham Số

---

### 🚨 F1: Chở Khách Không Tạo Cuốc / Nhận Khách Vẫy (Off-App Pickup / Street Hail)
* **File mã nguồn:** [`src/lcx_core/frauds/f1_off_app_pickup.py`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/f1_off_app_pickup.py)
* **File Schema đối soát:** [`schemas/frauds/f1_off_app_pickup.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f1_off_app_pickup.schema.json)

#### A. Logic & Điều Kiện Kích Hoạt (Triggers)
Hệ thống kết luận tài xế vi phạm F1 khi thỏa mãn đồng thời 4 điều kiện:
1. **Cuốc xe bị hủy sau khi đến:** Cuốc xe có `status == "cancelled"` và tài xế đã bấm đến điểm đón (`arrived_at_pickup_time` không rỗng).
2. **Cửa sổ thời gian di chuyển (Dynamic Time Window):** Trong vòng `WINDOW_MINUTES = 45` phút ngay sau khi hủy cuốc, xe không đỗ lại mà tiếp tục lăn bánh.
   * *Đặc biệt (Xử lý trường hợp nhận cuốc mới):* Nếu trong 45 phút này tài xế nhận một cuốc xe mới hợp lệ trên app (`dispatched`, `accepted`, `ongoing`), cửa sổ quan sát **ngay lập tức đóng lại tại thời điểm cuốc mới phát sinh** (`window_end = min(cancel_time + 45p, next_trip_start)`). Mọi km chạy đón khách hoặc phục vụ cuốc mới tuyệt đối không bị tính nhầm là gian lận.
3. **Vận tốc lăn bánh:** Có ít nhất `MIN_MOVING_PINGS = 5` pings ghi nhận vận tốc `speed_kmh >= 10.0` km/h.
4. **Bằng chứng có người trên xe:**
   * Cảm biến áp lực trọng lượng ghế phụ/ghế sau `seat_any_passenger == True` (hoặc `seat_occupancy.any_passenger_seat == True`).
   * Trên hệ thống xe không có cuốc nào đang chạy (`trip_id is null` hoặc rỗng).
   * Camera AI cabin (nếu có) củng cố `detected_passenger_count >= 1`.

> 🛡️ **NGUYÊN TẮC THOÁI TỘI TUYỆT ĐỐI (EXONERATION FOR CRUISING & LUNCH BREAK):**  
> Nếu xe lăn bánh trên đường (`speed_kmh > 0`) trong trạng thái không có cuốc trên app (`trip_id is null`), **NHƯNG cảm biến ghế phụ và ghế sau KHÔNG phát hiện tải trọng người ngồi** (`seat_occupancy.any_passenger_seat == False`):  
> $\longrightarrow$ **TÀI XẾ ĐƯỢC THOÁI TỘI HOÀN TOÀN (100% KHÔNG VI PHẠM).**  
> Hệ thống xác định đây là các hành vi vận hành bình thường, hợp lệ của người lao động Taxi Cơ Hữu:  
> 1. Tài xế lái xe chạy rỗng đi tìm khách hoặc chuyển vùng đón khách (cruising / repositioning).  
> 2. Tài xế lái xe đi ăn trưa / nghỉ ngơi giữa ca (lunch break).  
> 3. Tài xế lái xe di chuyển đến trạm sạc pin V-Green hoặc bãi đỗ xe (Depot).  
> Chỉ khi cảm biến ghế phụ/sau phát hiện có tải trọng hành khách ($\ge 15\text{ kg}$) trong lúc không có cuốc thì mới kích hoạt điều tra F1.

#### B. Công Thức Truy Thu & Chế Tài
$$\text{Tiền truy thu} = \text{km\_moved\_off\_app} \times \text{Đơn giá cước km}$$
* Đơn giá theo hạng xe: VF5 (13.000đ/km), VF6 (13.800đ/km), VF e34 (14.500đ/km), VF8 (19.000đ/km).
* Phạt tiền trừ ví ATA: 1.000.000 VNĐ (lần 1) hoặc 2.000.000 VNĐ (lần 2, đình chỉ ca trực).

#### C. Các Hằng Số Ngưỡng Cứng & Vị Trí Sửa Trong File
Mở file `f1_off_app_pickup.py` (từ dòng 26) để điều chỉnh:
* `WINDOW_MINUTES = 45`: Cửa sổ phút theo dõi xe sau khi hủy cuốc.
* `MIN_MOVING_SPEED_KMH = 10.0`: Tốc độ tối thiểu xác định xe đang chạy (tránh bắt nhầm khi xe nhích bãi đỗ).
* `MIN_MOVING_PINGS = 5`: Số ping tối thiểu liên tiếp có khách.
* `DEFAULT_RATE_PER_KM_VND = 13_000`: Đơn giá truy thu mặc định.

---

### 🚨 F2: Kéo Dài Hành Trình / Đi Lòng Vòng Tăng Cước (Route Deviation)
* **File mã nguồn:** [`src/lcx_core/frauds/f2_route_deviation.py`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/f2_route_deviation.py)
* **File Schema đối soát:** [`schemas/frauds/f2_route_deviation.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f2_route_deviation.schema.json)

#### A. Logic & Điều Kiện Kích Hoạt (Triggers)
1. **Cuốc hoàn thành:** `status == "completed"`.
2. **Loại trừ cuốc quá ngắn:** Quãng đường tối ưu `optimal_road_km >= 0.5` km (tránh sai số % khi mẫu số nhỏ).
3. **Vượt ngưỡng P99:** Tỷ lệ giữa quãng đường tính tiền `actual_distance_km` và quãng đường tối ưu đường bộ tra từ ma trận OSRM Hà Nội `optimal_road_km` vượt ngưỡng:
   $$\text{deviation\_ratio} = \frac{\text{actual\_distance\_km}}{\text{optimal\_road\_km}} \ge 1.25 \quad (\text{độ lệch } \ge 25\%)$$
4. **Kiểm tra miễn trừ (Exemption Check):** Không có cảnh báo ngập lụt, cấm đường phân luồng hoặc khách hàng yêu cầu đi đường vòng (`traffic_justification.justified == False`).

#### B. Công Thức Truy Thu & Hoàn Tiền Khách Hàng
$$\text{excess\_fare\_vnd} = (\text{actual\_distance\_km} - \text{optimal\_road\_km}) \times 13.000\text{ đ/km}$$
* Hoàn trả **100% tiền cước thu vượt** về ví hành khách (`customer_refund_status: "refunded_to_wallet"`).
* Phạt trừ ví ATA: 500.000 VNĐ (lần 1) hoặc 1.000.000 VNĐ (tái phạm).

#### C. Các Hằng Số Ngưỡng Cứng & Vị Trí Sửa Trong File
Mở file `f2_route_deviation.py` (từ dòng 26) để điều chỉnh:
* `MIN_ROAD_KM = 0.5`: Cự ly tối thiểu của cuốc được đưa vào kiểm toán.
* `DEFAULT_P99_THRESHOLD_RATIO = 1.25`: Ngưỡng vi phạm (1.25 tương đương chạy dài hơn 25%). Muốn siết chặt đặt `1.20`, muốn nới lỏng đặt `1.30`.

---

### 🚨 F3: Mất Tín Hiệu Hộp Viễn Thám TCU (TCU Signal Loss / GPS Jamming)
* **File mã nguồn:** [`src/lcx_core/frauds/f3_tcu_signal_loss.py`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/f3_tcu_signal_loss.py)
* **File Schema đối soát:** [`schemas/frauds/f3_tcu_signal_loss.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f3_tcu_signal_loss.schema.json)

#### A. Logic & Điều Kiện Kích Hoạt (Triggers)
1. **Thời gian gián đoạn sóng:** Khoảng trống giữa 2 telemetry pings liên tiếp trong ca trực `gap_minutes >= 15` phút.
2. **Nhảy công-tơ-mét ODO:** Ngay khi có tín hiệu trở lại, chênh lệch ODO CAN-bus phần cứng:
   $$\text{odo\_jump\_km} = \text{odo\_after} - \text{odo\_before} \ge 3.0\text{ km}$$
3. **Bằng chứng tiêu thụ pin:** Mức pin SOC sụt giảm `battery_soc_drop_pct > 0%`, chứng minh xe không hề đỗ tại chỗ mà lăn bánh ngầm khi mất sóng.

#### B. Công Thức Truy Thu & Chế Tài Phần Cứng
$$\text{Tiền truy thu} = \text{odo\_jump\_km} \times 13.000\text{ đ/km}$$
* **Triệu hồi kiểm tra phần cứng:** Đặt cờ `hardware_inspection_required: True` triệu hồi xe về Depot kiểm tra giắc cắm hộp TCU hoặc phát hiện máy phá sóng GPS.
* Hình thức kỷ luật: Khiển trách bằng văn bản và truy thu tiền vào bảng lương.

#### C. Các Hằng Số Ngưỡng Cứng & Vị Trí Sửa Trong File
Mở file `f3_tcu_signal_loss.py` (từ dòng 28) để điều chỉnh:
* `MIN_SIGNAL_LOSS_MINUTES = 15.0`: Thời gian mất sóng tối thiểu tính là bất thường (phút).
* `MIN_ODO_JUMP_KM = 3.0`: Bước nhảy ODO tối thiểu chứng minh xe có di chuyển.
* `DEFAULT_RATE_PER_KM_VND = 13_000`: Đơn giá truy thu.

---

### 🚨 F4: Dùng Xe Công Ty Ngoài Ca Trực (Personal Use / Geofence Breach)
* **File mã nguồn:** [`src/lcx_core/frauds/f4_personal_use.py`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/f4_personal_use.py)
* **File Schema đối soát:** [`schemas/frauds/f4_personal_use.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f4_personal_use.schema.json)

#### A. Logic & Điều Kiện Kích Hoạt (Triggers)
1. **Thời điểm ngoài ca:** Telemetry phát sinh ngoài khung giờ ca trực cam kết (`declared_shift_start_min` đến `declared_shift_end_min`), ví dụ ca 06:00 - 18:00 nhưng xe chạy lúc 22:00.
2. **Không có cuốc của công ty:** Không có cuốc xe nào đang phục vụ trên app (`trip_id is null`).
3. **Quãng đường di chuyển:** Tổng quãng đường lăn bánh `km_off_shift >= 3.0` km (hoặc đi ra ngoài địa giới Hà Nội quá 3.0 km).

#### B. Cơ Cấu Chi Phí Bồi Hoàn 3.500 VNĐ / KM (Bóc Tách Khấu Hao)
Toàn bộ chi phí vận hành xe điện VinFast được truy thu theo định mức chuẩn tại FRAUDS.md:
$$\text{total\_clawback\_cost\_vnd} = \text{km\_off\_shift} \times 3.500\text{ VNĐ}$$
* **Chi phí điện sạc pin:** $450\text{ đ/km}$ (trụ V-Green / trạm Depot).
* **Khấu hao cụm pin Lithium VinFast:** $1.200\text{ đ/km}$.
* **Hao mòn khung gầm, lốp và bảo dưỡng:** $1.850\text{ đ/km}$.
* **Hình thức trừ:** Khấu trừ trực tiếp vào bảng lương tháng (`deducted_from_payroll: True`).

#### C. Các Hằng Số Ngưỡng Cứng & Vị Trí Sửa Trong File
Mở file `f4_personal_use.py` (từ dòng 28) để điều chỉnh:
* `MIN_PERSONAL_USE_KM = 3.0`: Số km chạy ngoài ca tối thiểu để tính là dùng việc riêng.
* `COST_PER_KM_VND = 3500`: Định mức chi phí bồi hoàn 1 km xe điện.

---

### 🚨 F5: Tạo Cuốc Xe Ảo Trục Lợi Sàn 650.000đ/Ngày (Fake / Ghost Trips)
* **File mã nguồn:** [`src/lcx_core/frauds/f5_fake_trip.py`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/f5_fake_trip.py)
* **File Schema đối soát:** [`schemas/frauds/f5_fake_trip.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f5_fake_trip.schema.json)

#### A. Logic & Bằng Chứng Viễn Thám "Chí Mạng"
1. **App báo hoàn thành:** Cuốc xe hoàn thành (`trip_status == "completed"`) với cự ly khai khống `billed_distance_km >= 0.8` km.
2. **Xe đứng bất động:** Vận tốc tối đa ghi nhận từ hộp đen TCU `hardware_speed_max_kmh <= 3.0` km/h.
3. **Cần số không vào D:** Vị trí tay số CAN-bus luôn ở vị trí đỗ `P` hoặc số mo `N`.
4. **ODO không quay:** Độ biến thiên công-tơ-mét `hardware_odo_range_km <= 0.3` km.
5. **Khoang xe trống không:** Cảm biến trọng lượng xác nhận `seat_occupied_passenger == False` và camera cabin xác nhận `camera_passenger_count == 0`.

#### B. Mục Tiêu Trục Lợi & Chế Tài Nhóm 1b
* **Động cơ của tài xế Taxi Cơ Hữu:** Ngồi yên tại nhà tạo vài cuốc ảo để vừa đủ mốc 6 cuốc/ngày, vừa đẩy tỷ lệ hoàn thành lên trên 90%/95% nhằm **ngồi im bỏ túi trọn vẹn 650.000đ/ngày** tiền bù sàn GF-VIP mà không tốn công lái xe và không mất tiền sạc điện.
* **Tổng số tiền thu hồi:**
  $$\text{Tổng thu hồi} = \text{Tiền cước ảo} + \text{650.000đ bù sàn} + \text{10.000.000đ phạt Nhóm 1b}$$
* **Hậu quả pháp lý:** **CHẤM DỨT HỢP ĐỒNG LAO ĐỘNG VĨNH VIỄN, THU HỒI XE Ô TÔ VỀ DEPOT**.

#### C. Các Hằng Số Ngưỡng Cứng & Vị Trí Sửa Trong File
Mở file `f5_fake_trip.py` (từ dòng 30) để điều chỉnh:
* `MIN_APP_STATED_KM = 0.8`: Cự ly cuốc ảo tối thiểu cần quét (km).
* `MAX_STATIONARY_SPEED_KMH = 3.0`: Ngưỡng vận tốc tối đa coi là xe đứng yên.
* `MAX_STATIONARY_ODO_DELTA_KM = 0.3`: Giới hạn ODO biến thiên.
* `GROUP_1B_PENALTY_VND = 10_000_000`: Mức phạt Nhóm 1b (5 triệu - 10 triệu).

---

### 🚨 F6: Hủy Sai Lý Do / Kén Chọn Cuốc Xe (Abnormal Cancel / Cherry-Picking)
* **File mã nguồn:** [`src/lcx_core/frauds/f6_invalid_driver_cancel.py`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/f6_invalid_driver_cancel.py)
* **File Schema đối soát:** [`schemas/frauds/f6_invalid_driver_cancel.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/schemas/frauds/f6_invalid_driver_cancel.schema.json)

#### A. Logic & Bằng Chứng Hàng Rào Địa Lý (Geofence)
1. **Tài xế bấm hủy:** Cuốc xe có `status == "cancelled"` do tài xế hủy (`cancelled_by == "driver"`).
2. **Khai gian lý do:** Tài xế chọn lý do `khach_khong_den`, `rider_no_show`, `cannot_contact_rider`.
3. **Đối soát khoảng cách:**
   * Tài xế chưa từng bấm "Đã đến điểm đón" (`arrived_at_pickup_time is None`).
   * Hoặc khoảng cách tối thiểu từ xe đến tọa độ đón khách `min_distance_to_pickup_meters > 150.0` mét.
4. **Không chờ khách:** Thời gian xe thực tế dừng chờ tại điểm đón `actual_wait_at_pickup_seconds == 0` (quy chuẩn GSM bắt buộc phải chờ tối thiểu 300 giây = 5 phút).

#### B. Hậu Quả Chết Người Đối Với Thu Nhập Tài Xế
* Tiền truy thu cước: 0 VNĐ (do cuốc không hoàn thành).
* Phạt trừ ví ATA: 1.000.000 VNĐ (1-5 cuốc/tuần) hoặc 2.000.000 VNĐ (>5 cuốc/tuần).
* **MẤT TRỌN VẸN SÀN THU NHẬP 650.000 VNĐ / NGÀY:** Do tỷ lệ hoàn thành bị tụt giảm xuống dưới 90% (GF-VIP dưới 95%), tài xế bị tước quyền hưởng mức sàn cam kết ngày (thiệt hại 15 - 18 triệu đồng/tháng).

#### C. Các Hằng Số Ngưỡng Cứng & Vị Trí Sửa Trong File
Mở file `f6_invalid_driver_cancel.py` (từ dòng 34) để điều chỉnh:
* `ARRIVAL_GEOFENCE_METERS = 150.0`: Bán kính Geofence xác nhận xe đã đến đón (mét).
* `REQUIRED_WAIT_SECONDS = 300`: Thời gian chờ bắt buộc trước khi được hủy (5 phút).
* `DAILY_FLOOR_AMOUNT_VND = 650_000`: Mức tiền sàn ngày bị tước bỏ.
* `ATA_PENALTY_1_TO_5_VND = 1_000_000` và `ATA_PENALTY_OVER_5_VND = 2_000_000`.

---

## 3. Bộ Điều Phối Trung Tâm & Phân Loại Vi Phạm (`FraudDetectorEngine`)

* **File mã nguồn:** [`src/lcx_core/frauds/detector_engine.py`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-check/src/lcx_core/frauds/detector_engine.py)

### Chức Năng Cốt Lõi:
1. **Nạp đa nguồn dữ liệu:** Hỗ trợ nhận dữ liệu từ `EventDataStore` (bộ nhớ RAM) hoặc `GsmDataStore` (13 file Parquet).
2. **Kích hoạt đồng bộ:** Gọi cả 6 hàm bắt lỗi F1 đến F6 qua 1 lệnh gọi duy nhất.
3. **Phân loại chính xác (Fraud Classification):** Mỗi sự vụ được gắn nhãn định danh chuẩn:
   `F1_OFF_APP_PICKUP`, `F2_ROUTE_DEVIATION`, `F3_TCU_SIGNAL_LOSS`, `F4_PERSONAL_USE`, `F5_FAKE_TRIP`, `F6_INVALID_DRIVER_CANCEL`.
4. **Kiểm toán Schema tức thời:** Tự động nạp file `.schema.json` từ `schemas/frauds/` và gọi `jsonschema.validate()` để bảo đảm 100% bản ghi không bao giờ bị lỗi cấu trúc dữ liệu.
5. **Tổng hợp tài chính doanh nghiệp:**
   * Tổng tiền truy thu cước/khấu hao bồi hoàn.
   * Tổng tiền phạt trừ ví theo Quy chế 11/09/2026.
   * Tổng số tiền sàn 650.000đ/ngày bị từ chối chi trả.
   * Danh sách tài xế vi phạm cần chuyển hồ sơ sang Depot.

---

## 4. Bảng Tra Cứu Toàn Bộ Tham Số Ngưỡng Cứng (Master Tuning Sheet)

| Tên Hằng Số | Thuộc File | Giá Trị Mặc Định | Ý Nghĩa Nghiệp Vụ | Hướng Dẫn Tinh Chỉnh |
| :--- | :--- | :---: | :--- | :--- |
| `WINDOW_MINUTES` | `f1_off_app_pickup.py` | 45 phút | Cửa sổ theo dõi xe sau khi hủy cuốc | Giảm xuống 30 nếu chỉ muốn bắt cuốc ngắn; tăng lên 60 để bắt cuốc đi tỉnh. |
| `MIN_MOVING_PINGS` | `f1_off_app_pickup.py` | 5 pings | Số ping liên tiếp xe lăn bánh có khách | Giảm xuống 3 nếu muốn quét nhạy hơn; tăng lên 8 nếu muốn loại bỏ triệt để nhiễu. |
| `DEFAULT_P99_THRESHOLD_RATIO` | `f2_route_deviation.py` | 1.25 | Tỷ lệ đường thực tế / tối ưu OSRM | Đặt 1.25 (vượt 25%). Đặt 1.20 nếu muốn siết chặt kỷ luật lộ trình. |
| `MIN_ROAD_KM` | `f2_route_deviation.py` | 0.5 km | Cự ly tối thiểu cuốc xét duyệt | Giữ nguyên 0.5 km để tránh biến động sai số phần trăm ở cuốc ngắn. |
| `MIN_SIGNAL_LOSS_MINUTES` | `f3_tcu_signal_loss.py` | 15.0 phút | Thời gian mất sóng tối thiểu | Đặt 15 phút theo chuẩn FRAUDS.md (tránh bắt nhầm khi xe đi qua hầm Kim Liên/sóng chập chờn). |
| `MIN_ODO_JUMP_KM` | `f3_tcu_signal_loss.py` | 3.0 km | Bước nhảy ODO tối thiểu | Giữ 3.0 km để chắc chắn xe đã di chuyển chui trong lúc tắt sóng. |
| `MIN_PERSONAL_USE_KM` | `f4_personal_use.py` | 3.0 km | Quãng đường xe chạy ngoài ca trực | Đặt 3.0 km theo chuẩn schema. |
| `COST_PER_KM_VND` | `f4_personal_use.py` | 3.500 VNĐ | Tiền bồi hoàn 1 km xe điện VinFast | Bắt buộc 3.500đ theo quy định (gồm 450đ điện + 1.200đ pin + 1.850đ gầm bệ). |
| `MAX_STATIONARY_SPEED_KMH` | `f5_fake_trip.py` | 3.0 km/h | Vận tốc tối đa coi là xe đứng im | Đặt 3.0 km/h để loại trừ trôi xe nhẹ trong bãi đỗ. |
| `MAX_STATIONARY_ODO_DELTA_KM` | `f5_fake_trip.py` | 0.3 km | Độ lệch ODO tối đa của cuốc ảo | Đặt 0.3 km theo chuẩn schema. |
| `ARRIVAL_GEOFENCE_METERS` | `f6_invalid_driver_cancel.py` | 150.0 m | Bán kính Geofence điểm đón khách | Đặt 150m theo tiêu chuẩn ứng dụng đặt xe GSM. |
| `REQUIRED_WAIT_SECONDS` | `f6_invalid_driver_cancel.py` | 300 giây | Thời gian tài xế bắt buộc phải chờ | 300 giây = 5 phút quy chuẩn đón khách của Green SM. |
| `DAILY_FLOOR_AMOUNT_VND` | `f5`, `f6` | 650.000 VNĐ | Mức bảo đảm sàn thu nhập ngày | Cam kết của GSM cho Taxi Cơ Hữu Hà Nội (QĐ GF-VIP 31/08/2026). |

---

## 5. Hướng Dẫn Kích Hoạt Trong Mã Nguồn

### Cách 1: Quét Toàn Bộ Từ Một DataStore (Tiện Lợi Nhất)
```python
import sys
sys.path.insert(0, "src")

from lcx_core.frauds import scan_store_frauds

# Giả sử bạn có store (EventDataStore hoặc GsmDataStore)
incidents, summary = scan_store_frauds(store, validate_schemas=True)

# In báo cáo tổng hợp
summary.print_summary()

# Duyệt từng sự vụ vi phạm
for inc in incidents:
    print(f"[{inc['fraud_type']}] Tài xế {inc['driver_id']} - Xe {inc['vehicle_id']} - Phạt {inc.get('ata_penalty_vnd', 0):,}đ")
```

### Cách 2: Gọi Độc Lập Một Bộ Luật Cụ Thể (Ví dụ F1)
```python
from lcx_core.frauds import detect_f1_off_app_pickup

f1_cases = detect_f1_off_app_pickup(
    trips_df=trips_df,
    telemetry_df=telemetry_df,
    min_moving_pings=5,
    rate_per_km_vnd=13000,
)
print(f"Phát hiện {len(f1_cases)} vụ chở khách ngoài app F1")
```
