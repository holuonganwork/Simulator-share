# Hướng Dẫn Toàn Diện Về Công Việc "Làm Giàu Dữ Liệu" (Data Enrichment)
**Dự án: GSM_FRAUD_DETECTION (Lá Chắn Cuốc Xe)**  
*Tài liệu tổng hợp dễ hiểu nhất về mục đích, kiến trúc, 8 hạng mục triển khai và phương pháp kiểm chứng độ tin cậy của dữ liệu.*

---

## 1. Tại Sao Phải "Làm Giàu Dữ Liệu"? (Cội Nguồn Vấn Đề)

Hệ thống phát hiện gian lận tài xế ban đầu nhận dữ liệu mô phỏng từ `GSM_SIMULATOR` (gồm 13 bảng dữ liệu chuyến đi, cuốc xe, tài xế). Tuy nhiên, khi bắt tay vào triển khai thực tế, nhóm phát triển phát hiện ra **2 "nút thắt cổ chai" chí mạng**:

1. **"Ra đề thi mà không có bảng đáp án" (Thiếu Ground Truth - Nhãn gian lận):**
   - Ngoại trừ mẫu hình hủy cuốc bất thường (`abnormal_cancel`) có sẵn nhãn từ trước, **7 mẫu hình gian lận cốt lõi còn lại hoàn toàn KHÔNG CÓ nhãn**.
   - Nếu không có nhãn, các kỹ sư viết luật phát hiện gian lận ra nhưng **chịu chết không thể biết luật chạy có đúng không**: Bắt trúng bao nhiêu % (**Precision**)? Có bỏ lọt kẻ gian nào không (**Recall**)?
2. **"Dữ liệu gốc quá nghèo nàn, thiếu tín hiệu thực tế":**
   - Xe điện VinFast / Taxi Xanh SM có camera trong xe, có pin, có trạm sạc, có app tài xế... nhưng dữ liệu gốc ban đầu lại **không ghi nhận log camera, không có log đổi pin, không có trạng thái đóng/mở app, không có phân rã giá cước theo mưa gió**.
   - Chuyến đi chỉ lưu "Điểm đón" và "Điểm trả" (2 đầu mút), hoàn toàn không có vết di chuyển từng khúc cua (waypoints) để biết tài xế có cố tình chạy vòng vèo kiếm thêm tiền không.

> **Mục tiêu cốt lõi của "Làm giàu dữ liệu" (Pha 2.5):**
> - **"Bơm" thêm các loại dữ liệu cảm biến/sự kiện mới** để mở khóa các chiêu trò gian lận tinh vi đặc thù của xe điện.
> - **"Tiêm" các hành vi gian lận có chủ đích** vào dữ liệu để lần đầu tiên đo lường được tỷ lệ bắt trúng/bỏ sót bằng con số định lượng cụ thể.

---

## 2. Hai Cánh Quân Chính Của Công Việc Làm Giàu Dữ Liệu

Toàn bộ công việc được chia thành 2 cánh quân chiến lược rất rõ ràng:

```
                          CHIẾN LƯỢC LÀM GIÀU DỮ LIỆU
                                      │
         ┌────────────────────────────┴────────────────────────────┐
         ▼                                                         ▼
   [CÁNH QUÂN 1]                                             [CÁNH QUÂN 2]
Tiêm gian lận vào dữ liệu có sẵn                         Bổ sung dữ liệu & tín hiệu mới
  (Fraud Injector - Sim World)                               (Telemetry & Derived Data)
         │                                                         │
 • Mục tiêu: Tạo "bảng đáp án" độc lập                     • Mục tiêu: Thêm dữ liệu cảm biến,
   để đo Precision / Recall thật                             camera, pin, giá cước, lộ trình
 • Hạng mục 7 (Ưu tiên số 1)                               • 7 hạng mục còn lại (1, 2, 3, 4, 5, 6, 8)
```

---

## 3. Chi Tiết 8 Hạng Mục Triển Khai (Theo Ngôn Ngữ Dễ Hiểu Nhất)

### 🎯 CÁNH QUÂN 1: TIÊM GIAN LẬN CÓ KIỂM SOÁT (Hạng mục 7 - Ưu tiên cao nhất)

* **Hạng mục 7: Sim world các cách cheat (`lcx_core/fraud_injector/injector.py`)**
  * **Cách làm:** Hệ thống đọc dữ liệu gốc lên RAM (không sửa file trên đĩa), đóng vai "kẻ gian" tạo thêm các chuyến đi có hành vi gian lận ở 3 cấp độ: **Nhẹ (light)**, **Vừa (medium)**, **Nặng (heavy)**.
  * **Quy tắc "Không lộ đề":** Bảng đáp án (nhãn ai là kẻ gian tiêm vào) được cất riêng ở một bảng khác. Bộ phát hiện (Detector) chạy tự nhiên trên dữ liệu đã tiêm mà không hề biết trước ai là người bị tiêm.
  * **Các chiêu trò được tiêm:** Cố tình đi vòng đường, làm giả tọa độ GPS nhảy cóc, ngắt mạng/tắt GPS giữa đường, chẻ 1 cuốc dài thành nhiều cuốc ngắn, đứng im câu giờ chờ lấy phí chờ, chạy cuốc ảo tự đặt tự đi để ăn thưởng KPI...
  * **Phát hiện thú vị từ thực tế:**
    * Nhờ tiêm thử mới phát hiện ra lỗi **"Tự che mắt" (Self-masking)** ở trò cuốc ảo KPI: Khi kẻ gian gian lận quá nhiều cuốc, chính số cuốc đó làm phình to độ lệch chuẩn chung của toàn hệ thống, khiến công thức thống kê Z-score tự làm mờ đi hành vi bất thường của chính kẻ gian!

---

### 🚗 CÁNH QUÂN 2: BỔ SUNG DỮ LIỆU & TÍN HIỆU HOÀN TOÀN MỚI

#### Khối Telemetry & Cảm biến xe (Hạng mục 2, 4, 5, 6):

* **Hạng mục 4: Hành động & Trạng thái tài xế (`telemetry_tier/virtual_online_status.py`)**
  * *Vấn đề:* Có tài xế bật app để đấy nhận tiền trợ cấp ca làm hoặc chờ thưởng giờ cao điểm, nhưng người thì ngồi quán cafe ngủ, ném điện thoại hoặc để app chạy ngầm.
  * *Làm giàu gì:* Bổ sung chuỗi sự kiện: `went_online` (bật app), `app_background` (chuyển app ra sau), `driver_break` (nghỉ ngơi), `manual_location_toggle` (tắt GPS thủ công).
  * *Kết quả:* Bắt được mẫu gian lận **"Treo online ảo"**.

* **Hạng mục 2: Camera AI "Mắt thần" trong xe (`telemetry_tier/camera_intervention.py`)**
  * *Vấn đề:* Xe taxi điện có camera đối diện tài xế, nhưng trước đây không có dữ liệu camera để bắt gian lận.
  * *Làm giàu gì:* Sinh dữ liệu cảnh báo AI trên xe (`schemas/telemetry/camera_event.schema.json`): camera bị lấy giẻ che (`camera_obscured`), bị tháo/bẻ góc (`camera_tampered`), hoặc nhận diện khuôn mặt không trùng hồ sơ (`face_mismatch` - cho người ngoài mượn nick lái xe).
  * *Kết quả:* Bắt được hành vi **Can thiệp cảm biến giám sát an toàn**.

* **Hạng mục 6: Log đổi pin & Danh bộ xe (`telemetry_tier/ghost_vehicle.py`, `telemetry_tier/battery_swap_validation.py`)**
  * *Vấn đề:* Xe điện có quy trình đổi pin và mã định danh xe (VIN). Trước đây chưa có dữ liệu kiểm soát này.
  * *Làm giàu gì:* Thêm bảng đăng ký xe chính thức (`vehicle_registry.schema.json`) và bảng lịch sử đổi pin (`charging_session_log.schema.json`).
  * *Kết quả:* 
    1. Bắt **"Xe ma"**: Xe gửi định vị nhận khách nhưng mã số khung (VIN) không hề có trong danh sách xe đăng ký của GSM.
    2. Bắt **"Đổi pin ảo"**: Về mặt vật lý, đổi/sạc pin chỉ tăng từ 0.25% - 0.65%/giây. Nếu log báo pin từ 15% vọt lên 98% chỉ trong 5 giây thì chắc chắn là log ảo/gian lận để trục lợi tiền trợ cấp sạc pin.

* **Hạng mục 5: Tích hợp Telemetry thời gian thực (`src/lcx_core/telemetry_mock/`, `event_sim/`)**
  * Đóng gói toàn bộ luồng sự kiện cảm biến trên thành service API (FastAPI) và bộ giả lập động (`simulator.py`) để hệ thống chạy thử nghiệm như một luồng dữ liệu thời gian thực sống động.

---

#### Khối Dữ liệu dẫn xuất: Giá cước, Bản đồ, Hồ sơ (Hạng mục 8, 3, 1):

* **Hạng mục 8: Phân rã giá cước & Thời tiết (`derived_data/fare_breakdown.py`, `derived_tier/artificial_surge_collusion.py`)**
  * *Vấn đề:* Giá cuốc taxi thường tăng vọt (Surge Pricing) khi trời mưa to hoặc cuối tuần. Nhưng dữ liệu gốc không lưu thời tiết lúc đi cuốc.
  * *Làm giàu gì:* Tách giá cước thành: Giá gốc × Hệ số thứ trong tuần (`dow_multiplier`) × Hệ số mưa (`rain_multiplier`).
  * *Kết quả:* Bắt được trò **"Thông đồng thổi giá ảo"**: Một nhóm tài xế ở cùng một khu vực hẹn nhau đồng loạt tắt app hoặc từ chối cuốc vào lúc **trời không hề mưa**, khiến thuật toán điều phối tưởng thiếu xe cục bộ nên tự động tăng giá cước x1.5, x2.0. Sau khi giá tăng, các tài xế này mới đồng loạt bật app lên nhận cuốc giá cao để chia nhau.

* **Hạng mục 3: Lộ trình chi tiết từng khúc cua (`derived_data/trip_waypoints.py`)**
  * *Làm giàu gì:* Dùng bản đồ đường phố OSRM để nội suy ra từng tọa độ dọc đường mà tài xế đã đi qua, thay vì chỉ kẻ một đường chim bay từ A đến B.
  * *Phát hiện:* Khi so sánh, cách đo theo 2 điểm mút và cách đo theo toàn bộ lộ trình chi tiết bắt ra **hai nhóm tài xế lệch đường hoàn toàn khác nhau** (chỉ trùng 2/14 tài xế). Điều này chứng minh dữ liệu lộ trình chi tiết quan trọng thế nào.

* **Hạng mục 1: Hồ sơ tài xế chuyên sâu (`scripts/build_driver_profile_enriched.py`)**
  * *Làm giàu gì:* Gộp dữ liệu lịch sử vi phạm, tổng số tiền từng bị phạt, ngày bắt đầu chạy, loại hợp đồng của từng tài xế thành một bảng hồ sơ 360 độ (`data/derived/driver_profile_enriched.parquet`) để AI Agent có thể tra cứu ngay khi thẩm tra một vụ gian lận.

*(📌 Ghi chú: Việc mở rộng bản đồ toàn Hà Nội và bản đồ trạm sạc mới đã được thống nhất để ngoài phạm vi vì tốn kém tài nguyên tính lại toàn bộ ma trận khoảng cách OSRM).*

---

## 4. Bảng Kết Quả Thực Nghiệm: Precision & Recall Sau Khi Làm Giàu

Nhờ toàn bộ công việc làm giàu dữ liệu trên, dự án đã có được **bảng kết quả thực nghiệm chuẩn xác** (`docs/injector-eval-results.md`):

| Mẫu gian lận | Trước khi làm giàu | Sau khi làm giàu & tiêm dữ liệu (Recall) | Đánh giá độ nhạy của luật |
|---|:---:|:---:|---|
| **Đi vòng đường** (`route_deviation`) | Không biết | **100%** | Bắt rất nhạy (cần tinh chỉnh để bớt bắt nhầm). |
| **Nhảy cóc GPS** (`gps_distance_inflation`) | Không biết | **100%** | Bắt trúng toàn bộ các trường hợp cố tình phóng đại quãng đường. |
| **Ngắt mạng/GPS** (`location_dropout`) | Không biết | **100%** | Nhận ra thời gian ngắt GPS tự nhiên có thể tới 20h nếu tài xế nghỉ ca. |
| **Chẻ cuốc dài** (`split_long_trip`) | 0 cờ (0%) | **100%** | Đã chứng minh được logic luật hoạt động hoàn hảo khi có dữ liệu đúng. |
| **Câu giờ phí chờ** (`wait_fee_manipulation`) | Không biết | **100%** (ở mức vừa/nặng) | Phát hiện nếu tài xế lách ở mức nhẹ (15 điểm dừng) thì luật hiện tại chưa tóm được. |
| **Cuốc ảo ăn KPI** (`fake_ride_for_kpi`) | Không biết | **90%** (mức nặng) | Phát hiện ra hiện tượng "tự che mắt" (Self-masking). |
| **Can thiệp camera** (`camera_intervention`) | Chưa có luật | **100%** | Mở khóa thành công mẫu hình gian lận mới. |
| **Đổi pin ảo / Xe ma** | Chưa có luật | **100%** | Bắt chuẩn xác 100% các vi phạm phi vật lý về pin và xe. |
| **Thổi giá cước Surge** (`artificial_surge_collusion`)| Chưa có luật | **100%** (ở mức vừa/nặng) | Bắt được nhóm tài xế tạo khan hiếm xe nhân tạo khi không mưa. |

---

## 5. Làm Sao Để Kiểm Chứng Dữ Liệu Sinh Ra Là Đáng Tin Cậy?

Dữ liệu sinh ra tuân thủ **4 nguyên tắc bảo chứng khoa học**:

1. **Neo chặt vào phân phối thực tế & quy luật vật lý thật:**
   - Đổi pin không thể vượt tốc độ sạc vật lý của xe điện (0.25% - 0.65%/giây).
   - Ngắt GPS neo vào percentile p99 thật (~20 giờ) thay vì bịa một con số giả định.
2. **Kiểm thử theo 3 cấp độ tăng dần (Light ➔ Medium ➔ Heavy):**
   - Mức Nhẹ (Light) nằm sát ngưỡng bình thường để đo độ lách luật. Mức Nặng (Heavy) mới bắt được 90% - 100%.
3. **Bất biến & Truy vết được (Immutability & Traceability):**
   - 13 file Parquet gốc **không bị sửa một byte nào**.
   - Mọi dòng dữ liệu tiêm đều có nhãn `source = "INJECTED_FRAUD"`, các cột suy luận mang nhãn `synthetic_fields`.
4. **Không lộ đề:** Bảng đáp án (`ground_truth`) được lưu riêng, các hàm detect chạy hoàn toàn mù về nguồn gốc dữ liệu.

### Cách tự kiểm chứng trực tiếp trên máy:
```bash
# 1. Chạy 71 unit/regression tests:
./.venv/bin/python3 -m pytest -q

# 2. Chạy đánh giá Precision/Recall cho 7 mẫu hình Tier A:
./.venv/bin/python3 scripts/run_injector_eval.py --n-per-severity 40

# 3. Chạy đánh giá cho các mẫu Telemetry mới (camera, pin, xe ma, online ảo):
./.venv/bin/python3 scripts/run_telemetry_tier_eval.py

# 4. Chạy kiểm tra mẫu thổi giá cước Surge:
./.venv/bin/python3 scripts/run_surge_collusion_eval.py
```

---

## 6. Kết Luận Trong 1 Câu

> **"Làm giàu dữ liệu" trong dự án chính là bước chuyển mình từ việc *"viết luật trong bóng tối và đoán mò"* sang việc *"chủ động dựng lên các kịch bản gian lận và bổ sung cảm biến xe điện thật để đo lường chính xác luật nào bắt trúng, luật nào bị lách"*.**
