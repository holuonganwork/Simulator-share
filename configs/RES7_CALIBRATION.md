# BÁO CÁO ĐIỀU CHỈNH THAM SỐ KHÔNG GIAN H3 CHO ĐỘ PHÂN GIẢI RES 7 (HÀ NỘI)

Tài liệu này giải thích chi tiết các tham số không gian trong file cấu hình [`configs/pilot_hanoi.yaml`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/configs/pilot_hanoi.yaml) đã được hiệu chỉnh từ lưới Res 9 (bản đồ cũ quận Đống Đa) sang lưới **Res 7 (toàn thành phố Hà Nội)**.

---

## 1. Bản chất thay đổi hình học giữa Res 9 và Res 7

| Chỉ số hình học H3 | Res 9 (Quận Đống Đa cũ) | Res 7 (Toàn Hà Nội mới) | Tỷ lệ thay đổi |
| :--- | :--- | :--- | :--- |
| **Độ dài 1 cạnh lục giác** | ~0.174 km (174 m) | ~1.41 km (1.410 m) | Lớn gấp ~8 lần |
| **Diện tích 1 ô lục giác** | ~0.105 km² (10.5 ha) | ~5.16 km² (516 ha) | Lớn gấp ~49 lần |
| **Khoảng cách tâm 2 ô kề nhau** | ~0.35 km (350 m) | ~2.2 - 2.4 km | Lớn gấp ~6.5 lần |
| **Tổng số ô phủ vùng** | 215 ô (chỉ 1 quận Đống Đa) | 925 ô (toàn bộ 30 quận/huyện) | Phủ toàn thành phố |

Vì mỗi ô Res 7 có kích thước lớn hơn rất nhiều so với Res 9, các tham số dựa trên "số vòng ô" (ring count `k`) hoặc "bước nhảy ô" (`km_per_cell`) nếu giữ nguyên số cũ của Res 9 sẽ dẫn đến các lỗi logic không gian nghiêm trọng.

---

## 2. Chi tiết các tham số đã hiệu chỉnh trong `pilot_hanoi.yaml`

### 2.1. Bán kính quét tìm tài xế của Dispatcher (`candidate_ring_k` & `candidate_ring_k_max`)
- **Vị trí:** Dòng 127 và Dòng 162.
- **Giá trị cũ (Res 9):** `candidate_ring_k: 4` và `candidate_ring_k_max: 8`.
- **Giá trị mới (Res 7):** `candidate_ring_k: 2` và `candidate_ring_k_max: 3`.
- **Lý do điều chỉnh:**
  - Ở Res 9, khoảng cách giữa các ô là 0.35 km nên `k = 8` chỉ tương đương bán kính `8 x 0.35 = 2.8 km`.
  - Nhưng ở Res 7, khoảng cách giữa 2 ô là 2.3 km. Nếu để `k = 8`, bán kính quét sẽ lên tới `8 x 2.3 = 18.4 km` (chứa tới 217 ô, chiếm 1/4 diện tích Hà Nội).
  - Vì trần thời gian chạy đón khách tối đa là 11 phút (`eta_max_min: 11` ~ tương đương xe chạy được 3.5 - 4.5 km đường phố), nên việc quét tới 18 km là thừa thãi và gây lãng phí CPU duyệt hàng trăm ô không cần thiết.
  - Chọn `k = 2` (19 ô, bán kính ~4.6 km) hoặc tối đa `k = 3` (37 ô, bán kính ~6.9 km) là vừa vặn hoàn hảo với ngưỡng thời gian 11 phút.

---

### 2.2. Khoảng cách ước tính giữa các ô (`demand.drop_km_per_cell`)
- **Vị trí:** Dòng 83.
- **Giá trị cũ (Res 9):** `drop_km_per_cell: 0.35` (ghi chú cũ: *đường kính cell res9 ~0.35km*).
- **Giá trị mới (Res 7):** `drop_km_per_cell: 2.2`.
- **Lý do điều chỉnh:**
  - Tham số này được hàm `_sample_drop` trong `demand.py` dùng để tính số vòng lục giác `k` mở rộng khi bốc ngẫu nhiên điểm trả khách:
    `k = max(1, int(round(dist_km / km_per_cell)) + 2)`
  - Với một cuốc xe cự ly trung vị 3.5 km:
    - Nếu để `0.35`: `k = round(3.5 / 0.35) + 2 = 12`! Vành `k = 12` ở Res 7 có bán kính hơn 27 km (bay ra ngoài biên giới Hà Nội).
    - Khi sửa thành `2.2`: `k = round(3.5 / 2.2) + 2 = 4` rings (khoảng 8.8 km), bao bọc một vùng hợp lý quanh điểm đón để chọn điểm trả.

---

### 2.3. Độ mềm hàm suy giảm khoảng cách (`demand.drop_softness_km`)
- **Vị trí:** Dòng 84.
- **Giá trị cũ (Res 9):** `drop_softness_km: 0.5`.
- **Giá trị mới (Res 7):** `drop_softness_km: 1.5`.
- **Lý do điều chỉnh:**
  - Trong `demand.py`, xác suất chọn ô trả khách tỉ lệ với: `weights = exp(-dists / softness)`.
  - Ở Res 9, bước nhảy khoảng cách giữa các ô lân cận chỉ 0.3 km nên `softness = 0.5` phân bổ xác suất mượt mà.
  - Ở Res 7, khoảng cách giữa 2 ô liền kề đã nhảy vọt lên 2.2 km. Nếu giữ `softness = 0.5`, thì một ô chỉ lệch 2.2 km sẽ bị phạt: `exp(-2.2 / 0.5) = exp(-4.4) = 0.012` (xác suất rơi thẳng đứng xuống còn 1.2%).
  - Tăng lên `1.5` giúp hàm phân phối xác suất mềm mại hơn, không bị triệt tiêu các ô lân cận.

---

### 2.4. Cự ly cuốc xe tối đa (`demand.trip_km_max`)
- **Vị trí:** Dòng 80.
- **Giá trị cũ:** `trip_km_max: 12.0`.
- **Giá trị mới (Res 7):** `trip_km_max: 25.0`.
- **Lý do điều chỉnh:**
  - Ở phạm vi pilot Đống Đa cũ (diện tích 9.9 km²), cuốc xe bị chặn trần 12 km là hợp lý.
  - Khi mở rộng ra toàn thành phố Hà Nội (Res 7), nhiều cuốc xe đi liên quận (từ Hoàn Kiếm sang Hà Đông, Gia Lâm, Nam Từ Liêm, sân bay Nội Bài) có cự ly từ 15 đến 25 km. Nâng trần lên 25 km để phản ánh đúng thực tế di chuyển đô thị lớn.

---

### 2.5. Giới hạn quãng đường điều phối của AI Advisor (`advice.positioning_max_extra_km`)
- **Vị trí:** Dòng 480.
- **Giá trị cũ (Res 9):** `positioning_max_extra_km: 2.0`.
- **Giá trị mới (Res 7):** `positioning_max_extra_km: 4.5`.
- **Lý do điều chỉnh:**
  - Đây là "lan can an toàn" (safety guardrail) khống chế quãng đường chạy rỗng tối đa khi AI Advisor khuyên tài xế rảnh di chuyển sang ô khác (`standby_zone`) để đón khách.
  - Ở Res 9, các ô cách nhau chỉ 350m nên 2.0 km là tài xế có thể đi sang 4-5 ô lân cận.
  - Nhưng ở Res 7, khoảng cách giữa tâm 2 ô kề nhau đã là ~2.2 - 2.4 km! Nếu giữ mức 2.0 km, tài xế không thể di chuyển sang bất kỳ ô nào khác (lời khuyên điều phối vùng của Advisor sẽ bị khóa cứng 100%).
  - Nâng lên `4.5` km cho phép Advisor khuyên tài xế dịch chuyển trong phạm vi 1 đến 2 ô Res 7 lân cận có nhu cầu đặt xe cao.

---

## 3. Bảng tổng hợp đối chiếu thay đổi

| Tham số cấu hình | File YAML | Giá trị cũ (Res 9) | Giá trị mới (Res 7) | Ý nghĩa nghiệp vụ |
| :--- | :--- | :--- | :--- | :--- |
| `candidate_ring_k` | `dispatcher` | 4 | **2** | Bán kính đĩa gom ứng viên ban đầu (~4.6 km) |
| `candidate_ring_k_max` | `dispatcher` | 8 | **3** | Vành đai tối đa quét ứng viên (~6.9 km) |
| `drop_km_per_cell` | `demand` | 0.35 km | **2.2 km** | Khoảng cách tâm ô Res 7 dùng để tính ring trả khách |
| `drop_softness_km` | `demand` | 0.5 km | **1.5 km** | Bán kính mềm giãn xác suất chọn ô trả khách |
| `trip_km_max` | `demand` | 12.0 km | **25.0 km** | Cắt trần cự ly cuốc xe cho phạm vi toàn thành phố |
| `positioning_max_extra_km` | `advice` | 2.0 km | **4.5 km** | Quãng đường chạy rỗng tối đa Advisor được phép gợi ý |
