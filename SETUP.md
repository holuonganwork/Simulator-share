# 🚀 HƯỚNG DẪN VẬN HÀNH TOÀN DIỆN TỪ ĐẦU (END-TO-END PIPELINE)
## Hệ Thống Mô Phỏng GSM (`gsm_core`) & Phân Hệ Lá Chắn Cuốc Xe (`lcx_core`)

Tài liệu này hướng dẫn chi tiết quy trình chạy **từ đầu khi chưa có dữ liệu gì (from scratch)**: Khởi tạo môi trường $\rightarrow$ Chạy mô phỏng đô thị sinh kho dữ liệu $\rightarrow$ Sinh Telemetry $\rightarrow$ Khởi chạy bộ thanh tra phát hiện gian lận.

---

## ⚠️ 1. CẢNH BÁO QUY MÔ & CẤU HÌNH TRƯỚC KHI CHẠY (BẮT BUỘC ĐỌC)

Hiện tại, toàn bộ hệ thống đang được cấu hình chuẩn thực tế cho toàn thành phố Hà Nội:
* **Lưới không gian:** 925 ô lục giác Uber H3 Resolution 7 (phủ kín 30 quận/huyện/thị xã Hà Nội, diện tích $>3.359\text{ km}^2$, mỗi ô $\approx 5{,}2\text{ km}^2$).
* **Quy mô phương tiện:** **3.000 tài xế xe điện Taxi Cơ Hữu** (`actors.n: 3000`).
* **Quy mô nhu cầu:** **50.000 đơn hàng/ngày** (`demand.orders_per_day: 50000`).

⏱️ **Thời gian chạy mô phỏng 30 ngày:**
Do thuật toán ghép đơn tối ưu (Bipartite Hungarian Matching) và cập nhật động học phương tiện diễn ra liên tục mỗi 5 giây (**13.680 nhịp điều phối/ngày**), việc chạy trọn vẹn 30 ngày liên tục cho 3.000 xe sẽ mất khoảng **160 – 200 phút** tùy thuộc vào CPU.

### 🛠️ Tùy chọn 1: Chạy thử nhanh kiểm tra luồng code (Fast Debug Mode)
Nếu bạn mới clone code về và muốn chạy thử toàn bộ luồng từ đầu chỉ trong **1 – 2 phút**, hãy mở tệp cấu hình:  
👉 `configs/pilot_hanoi.yaml` và tạm thời hạ 2 thông số:
```yaml
demand:
  orders_per_day: 2000       # Giảm từ 50000 xuống 2000
actors:
  n: 100                     # Giảm từ 3000 xuống 100
```
> [!CAUTION]
> Khi giảm xuống 100 xe trên 925 ô H3 (mỗi ô chỉ có $\approx 0{,}1$ xe), các chỉ số vận hành đô thị (tỷ lệ đón khách, tắc đường vành đai) sẽ bị loãng và sai lệch so với thực tế. Chỉ dùng chế độ này để test luồng chạy. Khi cần sinh dữ liệu báo cáo đối soát chuẩn, hãy hoàn trả về **3.000 xe / 50.000 khách**.

### 🛠️ Tùy chọn 2: Chạy chuẩn toàn diện (Full Production Mode)
Giữ nguyên file `configs/pilot_hanoi.yaml` với **3.000 xe** và **50.000 khách**.

---

## 💻 2. THIẾT LẬP MÔI TRƯỜNG LÀM VIỆC

Mở terminal tại thư mục gốc của dự án:

* **Trên Windows (PowerShell):**
```powershell
# 1. Kích hoạt môi trường ảo (nếu có .venv):
.\.venv\Scripts\Activate.ps1

# 2. Thiết lập PYTHONPATH trỏ vào thư mục src (BẮT BUỘC):
$env:PYTHONPATH="src;."
```

* **Trên Linux / macOS (Bash):**
```bash
# 1. Kích hoạt môi trường ảo:
source .venv/bin/activate

# 2. Thiết lập PYTHONPATH:
export PYTHONPATH="src:."
```

---

## 🔄 3. QUY TRÌNH CHẠY TỪ ĐẦU (STEP-BY-STEP PIPELINE)

Khi bắt đầu mà thư mục `data/mock/` chưa có dữ liệu, bạn thực hiện lần lượt theo đúng 4 bước chuẩn dưới đây:

```
[configs/pilot_hanoi.yaml]
            │
            ▼ (Bước 3.1: gsm_core mô phỏng 3.000 xe x 30 ngày)
[data/mock/realdata-v2/ (15 bảng Parquet L1R + L0)]
            │
            ▼ (Bước 3.2: gsm_core sinh Telemetry CAN-bus/TCU)
[data/mock/realdata-v2/vehicle_telemetry_ping.parquet]
            │
            ▼ (Bước 3.3: lcx_core thanh tra & kiểm toán gian lận F1-F6, Tier A, Tier B)
[Báo cáo gian lận, truy thu cước & Top rủi ro tài xế]
```

### Bước 3.1. Chạy mô phỏng `gsm_core` để sinh kho 15 bảng dữ liệu vận hành
Chạy mô phỏng 30 ngày liên tục (1 tháng vận doanh hoàn chỉnh gồm 4 chu kỳ tuần) cho toàn bộ 3.000 xe, xuất ra 15 bảng Parquet chuẩn BigQuery tại `data/mock/realdata-v2`:
```bash
python -m gsm_core.mockgen.realdata --days 30 --seed-base 7000 --out data/mock/realdata-v2 --start-date 2026-07-01 --continuous
```
* **Giải thích tham số:**
  * `--days 30`: Số ngày mô phỏng (nếu muốn test nhanh có thể để `--days 1`).
  * `--seed-base 7000`: Seed đảm bảo kết quả tất định (Deterministic CRN).
  * `--out data/mock/realdata-v2`: Thư mục lưu trữ kết quả.
  * `--continuous`: Kích hoạt chuỗi ngày liên tục (trạng thái pin, tiến độ khoán và nhiệm vụ được mang sang ngày tiếp theo).
  * *Mẹo rút gọn:* Bạn có thể thêm cờ `--telemetry` vào cuối lệnh trên để hệ thống tự động sinh luôn Telemetry trong cùng 1 lần chạy (không cần chạy thêm Bước 3.2).

### Bước 3.2. Sinh bảng dữ liệu Telemetry cho toàn bộ 3.000 xe
Sau khi Bước 3.1 hoàn tất, chạy lệnh này để đọc dữ liệu vận hành và tạo bảng Telemetry CAN-bus/TCU xe điện VinFast chuẩn cho **toàn bộ 3.000 xe** (`d-0` đến `d-2999`):
```bash
python -m gsm_core.mockgen.telemetry_generator --l1-dir data/mock/realdata-v2 --out-file data/mock/realdata-v2/vehicle_telemetry_ping.parquet
```
* **Đặc tính kỹ thuật:**
  * Đồng bộ ODO thực tế, pin SoC, cần số P/D.
  * Tích hợp cảm biến áp lực ghế `seat_occupancy`: ghi nhận `False` khi chạy rỗng/nghỉ trưa/sạc pin để **thoái tội hoàn toàn cho tài xế trung thực (F1)**.
  * Cơ chế streaming ghi theo chunk 50.000 dòng qua PyArrow, duy trì bộ nhớ RAM luôn $< 300\text{ MB}$.

### Bước 3.3. Khởi chạy thanh tra & kiểm toán gian lận qua `lcx_core`
Khi kho dữ liệu `data/mock/realdata-v2` đã có đầy đủ bảng vận hành và bảng Telemetry, tiến hành quét phát hiện gian lận:

1. **Quét toàn diện 6 luật gian lận cốt lõi Taxi Cơ Hữu (F1 – F6):**
   ```bash
   python -c "from lcx_core.frauds import scan_store_frauds; _, summary = scan_store_frauds('data/mock/realdata-v2'); summary.print_summary()"
   ```
   *Lệnh sẽ in bảng tổng hợp chi tiết: số vụ vi phạm F1 (chở khách ngoài app), F2 (kéo dài lộ trình), F3 (tắt định vị TCU), F4 (dùng xe ngoài ca), F5 (cuốc ảo), F6 (kén cuốc huỷ sai), cùng tổng số tiền cước thu hồi và tiền phạt ATA.*

2. **Quét toàn diện các mẫu hình thống kê Tier A + Chấm điểm rủi ro máy học Tier B (IsolationForest):**
   ```bash
   python -m scripts.run_tier_a_scan
   ```
   *Hệ thống tự động nạp cấu hình `data/mock/realdata-v2` (từ `configs/data_sources.yaml`), quét các bất thường thống kê và xuất bảng xếp hạng Top 10 tài xế có điểm rủi ro cao nhất.*

---

## 🧪 4. CÁC LỆNH ĐO KIỂM BENCHMARK & KIỂM THỬ ĐỘC LẬP

### 4.1. Đo đạc độ chính xác thuật toán (Precision & Recall) cho F1–F6
Script này sử dụng phân hệ testbed riêng (`lcx_core.event_sim`), tự sinh dữ liệu và tiêm các kịch bản lỗi có chủ đích theo 3 mức độ (Nhẹ/Vừa/Nặng) để tính toán ma trận nhầm lẫn:
```bash
python -m scripts.run_telemetry_tier_eval --n-days 30 --save-tables data/derived/event_sim
```

### 4.2. Khởi chạy Mock Service Telemetry thời gian thực (FastAPI + Producer)
Kiểm thử dịch vụ nhận và xử lý dòng telemetry thời gian thực qua giao thức HTTP, đo độ trễ p99 $< 100\text{ ms}$:
```bash
python -m scripts.run_telemetry_mock_demo --n-drivers 30 --duration 30 --port 8077
```

### 4.3. Chạy toàn bộ Unit Tests kiểm tra tính toàn vẹn hệ thống
```bash
python -m pytest tests/test_geo.py tests/test_schemas.py tests/test_schema_versioning.py tests/fraud_detection/test_lcx_rule_base_frauds.py -v
```

---

## 📊 5. TRỰC QUAN HÓA BẢN ĐỒ & KẾT QUẢ (STREAMLIT DASHBOARD)

Khởi chạy bảng điều khiển tương tác để xem trực quan mạng lưới 925 ô H3, phân bố cung cầu và vết di chuyển của phương tiện:
```bash
python -m streamlit run src/gsm_sim/dashboard.py
```
*Truy cập trình duyệt theo địa chỉ:* `http://localhost:8501`
