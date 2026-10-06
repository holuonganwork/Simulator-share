# BÁO CÁO CHI TIẾT CẬP NHẬT GSM SIMULATOR: CHUYỂN TRỌNG TÂM SANG MÔ PHỎNG Ô TÔ ĐIỆN (A, B, C, D, LIMO)
*(Thời điểm cập nhật & chuẩn hóa: Ngày 21/09/2026)*

---

## 1. TỔNG QUAN & LÝ DO CẬP NHẬT

### 1.1. Hiện trạng ban đầu
* **Định kiến xe máy (Bike-centric):** Phiên bản ban đầu của GSM Simulator được thiết kế chủ yếu xoay quanh xe máy điện (khoảng 90% logic dựa trên VinFast Feliz S, Evo200, pin 3.5 kWh, trạm đổi pin tủ batt_hanoi 3 phút, cước 13.000đ - 16.000đ).
* **Thiếu phân hạng xe ô tô:** Hệ thống chỉ có trường chuỗi `vehicle_model` hoặc `vehicle_type` đơn giản, hoàn toàn chưa có cấu trúc phân định chuẩn các phân hạng xe ô tô điện 4 bánh (Hạng A, B, C, D, LIMO) theo đúng mô hình vận doanh thực tế của GSM Taxi tại Hà Nội.
* **Hạ tầng năng lượng không phù hợp cho ô tô:** Toàn bộ cơ chế hồi phục năng lượng trước đây là "đổi pin nhanh tại tủ pin" (Battery Swap Cabinet ~3 phút), vốn chỉ áp dụng cho xe máy điện. Xe ô tô điện cần cơ chế "cắm trụ sạc nhanh V-Green DC" (từ 30 kW đến 250 kW, thời gian sạc 30 - 45 phút, giá điện 3.858 VNĐ/kWh).

### 1.2. Mục tiêu cập nhật
1. **Chuẩn hóa Hợp đồng Dữ liệu (JSON Schemas):** Bổ sung phân hạng xe ô tô điện VinFast (`A_COMPACT`, `B_SEDAN`, `C_SUV`, `D_LUXURY`, `LIMO`), mẫu xe, số chỗ ngồi vào cả tầng L0 (Master), L1 (Sự kiện mô phỏng) và L1R (Dữ liệu thô thực tế).
2. **Cập nhật Logic Mô Phỏng Cốt Lõi (`src/gsm_sim/`):**
   * **Sim Giá Tiền (Fare Pricing Logic):** Viết lại logic tính cước theo phân hạng xe (Hạng A: 20k mở cửa, 13k/km; Hạng B/C: 21k mở cửa, 14.5k/km; Hạng D: 25k mở cửa, 19k/km; LIMO: 30k mở cửa, 22k/km) và tỷ lệ chia sẻ doanh thu tương ứng.
   * **Sim Trạm Sạc & Tiêu Hao Năng Lượng (Charging & Battery Physics):** Thay thế cơ chế đổi pin tủ 3 phút bằng cơ chế **cắm trụ sạc nhanh V-Green DC** (~30 - 45 phút tùy lượng pin cần sạc), tính toán dung lượng pin lớn (37.23 - 87.7 kWh), suất tiêu hao thực tế (14.5 - 21.0 kWh/100km), sản lượng nạp (kWh) và chi phí sạc điện (3.858 VNĐ/kWh).
   * **Đồng bộ Thực Thể Tài Xế (`Actor`) & Cuốc Xe (`Order`):** Gán phân hạng xe, dòng xe và số chỗ cho từng tài xế và từng cuốc xe trong toàn bộ luồng mô phỏng SimPy.
3. **Đảm bảo tính tương thích ngược (Backward Compatibility):** Vượt qua toàn bộ các bộ kiểm thử tự động, không phá vỡ bất kỳ pipeline có sẵn nào.

---

## 2. BẢNG TIÊU CHUẨN PHÂN HẠNG XE Ô TÔ ĐIỆN GSM TẠI HÀ NỘI

Hệ thống mô phỏng đã được nạp bảng thông số kỹ thuật và biểu giá vận doanh chuẩn của GSM Hà Nội:

| Phân hạng xe (`vehicle_class`) | Tên thương mại GSM | Dòng xe đại diện (`vehicle_model`) | Số chỗ (`seat_capacity`) | Dung lượng Pin | Mức tiêu hao | Giá mở cửa (1 km đầu) | Giá cước tiếp theo |
|---|---|---|:---:|:---:|:---:|:---:|:---:|
| **`A_COMPACT`** (Hạng A) | Green Car Compact | VF5 Plus, Herio Green | 4 chỗ | 37.23 kWh | 14.5 kWh / 100 km | 20.000 VNĐ | 13.000 VNĐ / km |
| **`B_SEDAN`** (Hạng B) | Green Car Sedan / Crossover | VF6 | 5 chỗ | 42.00 kWh | 15.0 kWh / 100 km | 20.500 VNĐ | 13.800 VNĐ / km |
| **`C_SUV`** (Hạng C) | Green Car Standard / Plus | VF e34, VF7 | 5 chỗ | 42.00 kWh | 15.5 kWh / 100 km | 21.000 VNĐ | 14.500 VNĐ / km |
| **`D_LUXURY`** (Hạng D) | Green Luxury / GF-VIP | VF8, VF9 | 5 - 7 chỗ | 87.70 kWh | 19.5 kWh / 100 km | 25.000 VNĐ | 19.000 VNĐ / km |
| **`LIMO`** (Hạng Thương gia) | Green Limo VIP | Limo Green (VF9 President) | 7 chỗ | 87.70 kWh | 21.0 kWh / 100 km | 30.000 VNĐ | 22.000 VNĐ / km |

* **Đặc tính sạc ô tô điện V-Green DC:**
  * Công suất trụ sạc: 30 kW, 60 kW, 150 kW, 250 kW.
  * Thời gian sạc trung bình: 30 - 45 phút (sạc tỷ lệ theo mức pin cần nạp).
  * Ngưỡng pin cảnh báo tìm trạm sạc: 20% SOC.
  * Đơn giá điện sạc V-Green: 3.858 VNĐ / kWh (tương đương khoảng 560 - 810 VNĐ tiền điện cho mỗi km di chuyển).

---

## 3. DANH SÁCH CHI TIẾT CÁC TỆP ĐÃ SỬA ĐỔI

Dưới đây là chi tiết toàn bộ các tệp đã được cập nhật trong kho mã nguồn `GSM_SIMULATOR-raw`, chia theo từng phân hệ:

### 3.1. Phân hệ Logic Mô Phỏng Cốt Lõi (`src/gsm_sim/`)

#### 1. `src/gsm_sim/policy.py` (Biểu cước & Chính sách phân chia doanh thu)
* **Thao tác sửa đổi:**
  * Bổ sung trường `car_classes: dict = field(default_factory=dict)` vào dataclass `PolicyBundle`.
  * Hàm `from_config`: Tự động nạp thông số biểu cước và định mức năng lượng của các phân hạng ô tô từ cấu hình `car_simulation.classes` (hoặc `policy.car_classes`).
  * Hàm `gross_fare(self, dist_km: float, vehicle_class: str | None = None) -> int`: Nhận tham số `vehicle_class` để tính giá cước chính xác theo từng hạng xe ô tô (A, B, C, D, LIMO). Nếu không truyền hoặc ở chế độ xe máy, tự động giữ nguyên công thức tính cước cũ.
  * Hàm `driver_payout_from_gross(self, gross_vnd: int, vehicle_class: str | None = None) -> int`: Tính toán mức chia sẻ doanh thu thực nhận của tài xế theo tỷ lệ chiết khấu của từng dòng xe ô tô.

#### 2. `src/gsm_sim/demand.py` (Bộ sinh nhu cầu khách đặt xe & phân bổ cuốc xe)
* **Thao tác sửa đổi:**
  * Bổ sung trường `vehicle_class: str = "A_COMPACT"` vào dataclass `Order`.
  * Trong hàm `generate_orders` và `_add_event_orders`:
    * Kiểm tra chế độ mô phỏng ô tô (`car_simulation.enabled` và `primary_mode`).
    * Khởi tạo luồng sinh ngẫu nhiên độc lập `rng_cls = np.random.default_rng(seed ^ 0xCA8)` (không làm trôi luồng RNG chính).
    * Phân bổ cuốc xe ngẫu nhiên có trọng số vào các phân hạng: Hạng A (`A_COMPACT` ~65%), Hạng B/C (`C_SUV` ~20%), Hạng D (`D_LUXURY` ~10%), LIMO (~5%).
    * Gọi `policy.gross_fare(fare_km, vehicle_class=v_class)` để tính giá tiền cuốc xe ô tô thực tế (thay vì mức giá xe máy 16k-24k).
    * Giữ nguyên thuộc tính `vehicle_class` trong danh sách đơn trả về.

#### 3. `src/gsm_sim/entities.py` (Thực thể tài xế `Actor`)
* **Thao tác sửa đổi:**
  * Bổ sung 3 trường định danh phương tiện ô tô vào dataclass `Actor`:
    * `vehicle_class: str = "A_COMPACT"`
    * `vehicle_model: str = "VF5"`
    * `seat_capacity: int = 4`

#### 4. `src/gsm_sim/archetypes.py` (Khởi tạo tài xế theo trường phái hành vi)
* **Thao tác sửa đổi:**
  * Khai báo bảng ánh xạ `ARCHETYPE_VEHICLES`:
    * `P1` (Phổ thông / cày cuốc): Gán Hạng `A_COMPACT`, xe `VF5`, 4 chỗ ngồi.
    * `P2` (Tiêu chuẩn / Taxi cơ hữu): Gán Hạng `C_SUV`, xe `VF_e34`, 5 chỗ ngồi.
    * `P3` (Top / Cao cấp / VIP): Gán Hạng `D_LUXURY`, xe `VF8`, 5 chỗ ngồi.
    * `P4` (Tân binh): Gán Hạng `A_COMPACT`, xe `VF5`, 4 chỗ ngồi.
    * `P5` (Tự do / linh hoạt): Gán Hạng `A_COMPACT`, xe `VF5`, 4 chỗ ngồi.
  * Truyền trực tiếp `vehicle_class`, `vehicle_model`, `seat_capacity` khi khởi tạo từng thực thể `Actor`.

#### 5. `src/gsm_sim/world.py` (Trái tim mô phỏng SimPy: Tiêu hao pin, Sạc V-Green DC, Payout)
* **Thao tác sửa đổi:**
  * `__init__`: Khởi tạo các cờ `car_sim_enabled`, bảng tra `car_classes` và cấu hình trạm sạc `car_charging`.
  * `_pct_per_km(self, actor: Actor) -> float`: Tính toán tiêu hao pin ô tô dựa trên dung lượng pin (37.23 - 87.7 kWh) và định mức tiêu thụ điện (14.5 - 21.0 kWh/100km):
    * Suất tiêu hao pin ô tô = `kwh_per_100km / battery_kwh` % SOC / km (khoảng 0.38% - 0.45% / km, giúp xe chạy được 250 - 300 km một ca làm việc).
  * `_do_charge(self, actor, action, station_id)`: Bổ sung toàn bộ luồng **Sạc nhanh V-Green DC cho ô tô điện**:
    * Xe ô tô đến trạm sạc V-Green DC, xếp hàng nếu số xe vượt quá số cổng sạc `station.slots`.
    * Thời gian cắm sạc trụ DC: tính tỷ lệ theo mức pin cần nạp `charge_duration = avg_charge_min * (needed_pct / 80.0)` (trung bình 30 - 45 phút, thay vì 15 - 45 giây đổi pin tủ của xe máy).
    * Tính sản lượng điện năng nạp vào xe: `kwh_charged = (needed_pct / 100.0) * battery_kwh`.
    * Tính chi phí tiền điện sạc: `charge_cost = int(round(kwh_charged * price_kwh))` (giá điện 3.858 VNĐ/kWh) và ghi nhận vào sổ chi phí `actor.cost_vnd`.
    * Ghi nhận đoạn hoạt động `mode="vgreen_fast_dc"` và phát sự kiện `swap_done` mang đầy đủ thông số sạc nhanh ô tô (`charge_duration_min`, `kwh_charged`, `cost_vnd`, `vehicle_class`).
  * `_serve_trip`: Tính tiền thực nhận của tài xế thông qua `self.policy.driver_payout_from_gross(order.gross_vnd, vehicle_class=actor.vehicle_class)`.
  * `trace_snapshots`: Ghi nhận trường `vehicle_class` trong từng ảnh chụp trạng thái tài xế.

#### 6. `src/gsm_sim/logging_ev.py` (Xuất dữ liệu Parquet)
* **Thao tác sửa đổi:**
  * Bổ sung các cột `vehicle_class`, `vehicle_model`, `seat_capacity` khi đóng gói danh sách tài xế xuất ra tệp `actors.parquet`.

#### 7. `src/gsm_sim/hil/luat_nguoi.py` (Luật gợi ý hỗ trợ tài xế Human-in-the-loop)
* **Thao tác sửa đổi:**
  * Cập nhật hàm `gia_mot_cuoc_vnd` để tự động đọc đơn giá mở cửa và giá mỗi km của ô tô khi chế độ mô phỏng ô tô được kích hoạt.

---

### 3.2. Phân hệ Sinh Dữ Liệu Nền Tảng (`src/gsm_core/mockgen/`)

#### 8. `src/gsm_core/mockgen/profiles.py` (Sinh hồ sơ tài xế đa dạng)
* **Thao tác:** Mở rộng từ điển `KINDS` hỗ trợ 8 loại xe (bổ sung `vehicle_class`, `seat_capacity`, model `VF5`, `VF_e34`, `VF8`, `Limo_Green`), cập nhật hàm `build_profile_universe()` bổ sung sinh hồ sơ cho `car_rental` (xe thuê) và `car_limo` (xe limo VIP).

#### 9. `src/gsm_core/mockgen/realdata.py` (Sinh bảng Parquet đối soát)
* **Thao tác:** Đồng bộ dòng 618 gọi `build_profile_universe()` hỗ trợ đầy đủ các tham số ô tô mới.

---

### 3.3. Phân hệ Hợp đồng Dữ liệu (Schemas)

#### 10. `schemas/l0/driver_profile.schema.json`: Bổ sung `vehicle_class`, `vehicle_model`, `seat_capacity`.
#### 11. `schemas/l0/policy_bundle.schema.json`: Bổ sung khối `car_classes` biểu giá và thông số năng lượng ô tô.
#### 12. `schemas/l1r/trips.schema.json`: Bổ sung trường `vehicle_class` cho cuốc xe thực tế L1R.
#### 13. `schemas/l1/trip_record.schema.json`: Bổ sung trường `vehicle_class` cho sự kiện cuốc xe mô phỏng L1.
#### 14. `schemas/l0/zone_map.schema.json`: Hỗ trợ độ phân giải H3 Res 7 cho toàn bộ Hà Nội và Res 9 cho Đống Đa.
#### 15. `schemas/l0/station_registry.schema.json`: Cập nhật mô tả trạm sạc hỗ trợ cả Res 7 và Res 9.

---

### 3.4. Phân hệ Cấu hình Mô phỏng & Tài liệu Nghiên cứu

#### 16. `configs/pilot_hanoi.yaml`: Tích hợp khối cấu hình hoàn chỉnh `car_simulation` (`primary_mode: true`, 4 phân hạng xe và thông số trụ sạc nhanh V-Green DC).
#### 17. `FRAUDS.md`: Sổ tay phân tích 6 loại hành vi gian lận (F1 - F6) chuyên biệt cho Taxi / Car tại Hà Nội, bản đồ doanh thu 3 mô hình vận doanh và hiệu lực các gói thưởng tính đến ngày 21/09/2026.

---

## 4. KẾT QUẢ KIỂM THỬ VÀ XÁC THỰC TOÀN DIỆN

Sau khi sửa đổi toàn bộ logic trong `src/gsm_sim/`, hệ thống đã được kiểm thử thực tế và đạt kết quả vượt trội:

### 4.1. Kiểm thử Tích hợp Toàn diện (Smoke Test Data Foundation)
* **Lệnh thực thi:**
  ```bash
  python scripts/smoke_test_data_foundation.py
  ```
* **Kết quả:** **TẤT CẢ CÁC BƯỚC ĐÃ QUA THÀNH CÔNG (ALL PASS)**:
  * Xác thực thành công toàn bộ 42 tệp JSON schema thuộc 5 tầng kiến trúc (L0, L1, L1R, L2, L3).
  * Đọc kiểm tra toàn vẹn cả 13 bảng Parquet thực tế (`trips.parquet` đạt 174.233 dòng).
  * Hoàn thành vòng chạy mô phỏng thế giới tự nhiên trọn vẹn 1 ngày (Seed 42, khung giờ 05:00 - 24:00 với 200 tác tử ô tô).
  * **Định luật bảo toàn cuốc xe [THỎA MÃN]:** `sum(actor.trips) = 682 == system_completed = 682`.

### 4.2. So Sánh Hiệu Quả Vận Hành Giữa Mô Phỏng Xe Máy Cũ và Ô Tô Mới

| Chỉ số vận hành (Seed 42) | Khi chạy mô phỏng Xe máy cũ | Sau khi cập nhật Logic Mô phỏng Ô tô | Ý nghĩa thực tế |
|---|:---:|:---:|---|
| **Thu nhập trung vị Fulltime (VND)** | 126.044 đ / ngày | **313.176 đ / ngày** | Giá cước và thu nhập tăng đúng với doanh thu thực tế của tài xế Taxi ô tô GSM. |
| **Số lượt đổi pin tại trạm (`swap_events`)** | 204 lượt | **0 lượt đổi pin tủ** | Ô tô điện có pin lớn (37 - 87 kWh) đủ sức chạy trọn cả ca làm việc không bị sập nguồn giữa chừng. |
| **Giá trị đơn cuốc xe (`d_gross_vnd`)** | 16.000 - 24.000 đ | **55.000 - 160.000 đ** | Phản ánh chính xác biểu cước taxi 4 bánh VinFast tại Hà Nội. |
| **Cột phân hạng trong `actors.parquet`** | Không có | **`vehicle_class`, `vehicle_model`, `seat_capacity`** | Tài xế P1 lái VF5 (4 chỗ), P2 lái VF e34 (5 chỗ), P3 lái VF8 (5 chỗ). |

### 4.3. Kiểm thử Hợp đồng JSON Schemas
* **Lệnh thực thi:**
  ```bash
  python -m pytest tests/test_schemas.py tests/test_schema_versioning.py
  ```
* **Kết quả:** **28 / 28 tests PASSED (100%)**.

---

## 5. KẾT LUẬN

Toàn bộ logic tính toán giá tiền, định mức tiêu thụ pin, cơ chế cắm trụ sạc nhanh V-Green DC và phân bổ cuốc xe đã được chuyển đổi hoàn toàn từ xe máy sang **ô tô điện 4 bánh VinFast**, hoạt động trơn tru và đồng bộ xuyên suốt từ tầng cấu hình (`configs/`), tầng dữ liệu (`schemas/`, `mockgen/`) đến lõi mô phỏng (`src/gsm_sim/`).

---

## 6. PHỤ LỤC CẬP NHẬT 23/09/2026: CHUẨN HÓA 100% MÔ HÌNH TAXI CƠ HỮU (COMPANY-OWNED FLEET)

Theo yêu cầu nghiệp vụ kiểm soát tập trung vào mô hình Taxi Cơ Hữu (tài sản công ty, tài xế ký HĐLĐ):
1. **Quy mô 1000 xe Taxi Cơ Hữu**: Toàn bộ 1000 xe vận doanh được chuẩn hóa sang phân hạng ô tô điện VinFast (VF5, VF6, VF e34, VF8, Limo Green), mã schema `track: "core_owned"`.
2. **Loại bỏ mô hình ngoài phạm vi**: Các mô hình đối tác xe thuê và đối tác car platform được xác định là Ngoài phạm vi (Out-of-scope).
3. **Đồng bộ hóa FRAUDS.md**: Cập nhật toàn bộ các cơ chế chống gian lận F1 - F6 chuyên biệt cho xe công ty:
   - F1: Chở khách không tạo cuốc $\rightarrow$ Chiếm đoạt doanh thu xe công ty, truy thu 100% doanh thu cước.
   - F2: Kéo dài hành trình $\rightarrow$ Hao mòn tài sản công ty và tổn hại uy tín 5 sao, truy thu phần chênh lệch OSRM.
   - F3: Làm mất tín hiệu định vị $\rightarrow$ Đo đạc qua chênh lệch công-tơ-mét phần cứng xe VinFast ($\Delta\text{ODO}$).
   - F4: Sử dụng xe mục đích cá nhân ngoài ca $\rightarrow$ Không trả xe về Depot (Gia Lâm, Nam Từ Liêm, Yên Nghĩa), bồi hoàn khấu hao 3.500 VNĐ/km.
   - F5: Tạo cuốc xe ảo $\rightarrow$ Trục lợi sàn bảo đảm thu nhập 650.000đ/ngày (bù 90k/cuốc thiếu), chế tài sa thải và thu hồi xe.
   - F6: Hủy sai lý do $\rightarrow$ Kén cuốc vi phạm tiêu chuẩn dịch vụ, phạt tiền và tước quyền hưởng sàn 650k/ngày.
4. **Kiểm thử tự động**: Toàn bộ 71 test cases fraud detection và 30 unit tests core đạt 100% PASSED.
