# Hướng Dẫn Cào Dữ Liệu Địa Lý & Tính Toán Ma Trận OSRM Cho Toàn Hà Nội

Thư mục `GSM_SIMULATOR-raw/crawl/` chứa toàn bộ công cụ tự động hóa để chuẩn bị dữ liệu cho việc mở rộng bản đồ Simulator từ Đống Đa sang **Toàn thành phố Hà Nội**.

---

## 1. Danh Mục Các Công Cụ Trong Thư Mục

| Tên script | Vai trò chính |
|---|---|
| `crawl_osm_hanoi.py` | Cào 3 tệp dữ liệu từ OpenStreetMap: Ranh giới Hà Nội (`hanoi_geom.json`), Trạm sạc VinFast (`batt_hanoi.json`), POIs (`poi_hanoi.json`). |
| `generate_full_hanoi_osrm.py` | Tính toán ma trận đường bộ OSRM toàn phần cho **toàn bộ 6.475 ô lục giác H3 Res 8** của Hà Nội (Hỗ trợ **Tự động lưu tiến độ & Resume**, không sợ mất mạng). |

---

## 2. Hướng Dẫn Chạy Tính Toàn Bộ Ma Trận OSRM (925 Ô Res 7)

Ma trận toàn phần giữa 925 ô gồm khoảng 855.000 cặp cự ly.
* **Số block**: 19 x 19 = 361 nhiệm vụ (mỗi block 50 ô).
* **Thời gian tính toán**: Chỉ khoảng **6 - 8 phút** là hoàn tất 100%!
* **Lưu ngay từng block**: Ghi trực tiếp vào thư mục `research/simulation/data/osrm_chunks/`.
* **Hỗ trợ Resume**: Bấm `Ctrl + C` để dừng bất cứ lúc nào, chạy lại sẽ tự động tiếp tục từ block đó.


### Lệnh khởi chạy:
Mở Terminal tại thư mục dự án và chạy:

```powershell
e:\THUCTAP\crawldata\.venv\Scripts\python.exe GSM_SIMULATOR-raw/crawl/generate_full_hanoi_osrm.py
```

### Cách tạm dừng và chạy tiếp:
* **Để tạm dừng**: Bấm tổ hợp phím `Ctrl + C` trên bàn phím.
* **Để tiếp tục**: Chỉ cần gõ lại lệnh trên, script sẽ tự động quét thư mục `osrm_chunks/` và chạy tiếp các block còn lại.

### Lệnh gộp các block thành file Parquet cuối cùng:
Sau khi chạy xong (hoặc khi bạn muốn gộp sớm các block đã có thành file để chạy thử Simulator ngay):

```powershell
e:\THUCTAP\crawldata\.venv\Scripts\python.exe GSM_SIMULATOR-raw/crawl/generate_full_hanoi_osrm.py --merge-only
```
* File kết quả sẽ được ghi vào: `GSM_SIMULATOR-raw/research/simulation/data/osrm_matrix_hanoi.parquet`.

---

## 3. Cấu Hình Chạy Simulator Với Dữ Liệu Toàn Hà Nội

File cấu hình đã được tạo sẵn tại: `GSM_SIMULATOR-raw/configs/pilot_hanoi.yaml`

```yaml
world:
  data_dir: research/simulation/data
  geom_file: hanoi_geom.json        # Ranh giới toàn thành phố Hà Nội
  stations_file: batt_hanoi.json    # 399 trạm sạc & tủ pin VinFast
  poi_file: poi_hanoi.json          # 199 cụm điểm đón trả nhu cầu cao
  h3_res: 7                         # Độ phân giải Res 7 (~925 ô phủ kín toàn Hà Nội)
routing:
  enabled: true
  matrix_file: osrm_matrix_hanoi.parquet

```

### Lệnh chạy mô phỏng:
```powershell
cd e:\THUCTAP\crawldata\GSM_SIMULATOR-raw
e:\THUCTAP\crawldata\.venv\Scripts\python.exe -m gsm_sim.cli run --config configs/pilot_hanoi.yaml --seed 42
```
