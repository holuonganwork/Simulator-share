# DANH MỤC CÁC LOẠI HÀNH VI GIAN LẬN & CƠ CHẾ TRUY THU (F1 - F6)
## CHUYÊN BIỆT DUY NHẤT CHO MÔ HÌNH TAXI CƠ HỮU (COMPANY-OWNED FLEET) TẠI ĐỊA BÀN HÀ NỘI
*(Hiệu lực rà soát và chuẩn hóa tính đến ngày: **23/09/2026**)*

---

## 📌 BÁO CÁO RÀ SOÁT HIỆU LỰC CHÍNH SÁCH TÍNH ĐẾN NGÀY 23/09/2026 (KHU VỰC HÀ NỘI)

Trong hệ thống vận hành và quản lý của Green SM, bộ phận kiểm soát vận hành hiện tại **CHỈ QUẢN LÝ VÀ KIỂM SOÁT DUY NHẤT MÔ HÌNH TAXI CƠ HỮU (100% XE THUỘC SỞ HỮU GSM, TÀI XẾ KÝ HỢP ĐỒNG LAO ĐỘNG)**. Các chính sách áp dụng tại Hà Nội được rà soát như sau:

| Tên chính sách / Quy định | Loại thời hạn | Mốc hiệu lực cụ thể | Tình trạng áp dụng cho Taxi Cơ Hữu tại Hà Nội |
|---|:---:|---|:---:|
| **Bộ Quy tắc Ứng xử & Chế tài mới nhất (Bài #01)** | Có thông báo mới | Ban hành **11/09/2026** đè lên toàn bộ quy chế 2023 - 2025 | **Đang hiệu lực cao nhất (Toàn bộ 1000 xe cơ hữu)** |
| **Đảm bảo thu nhập GF-VIP Hà Nội 650k/ngày (Bài #03)** | Có thông báo mới | Áp dụng từ **31/08/2026** cho đến khi có thông báo mới | **Đang áp dụng tại 12 quận Hà Nội (Bù 90k/cuốc thiếu)** |
| **Đảm bảo thu nhập Taxi cơ hữu 18tr/tháng (Bài #26)** | Có thông báo mới | Áp dụng từ **06/06/2026** cho đến khi có thông báo mới | **Đang áp dụng toàn Hà Nội (Lưới an toàn thu nhập net)** |
| **Thưởng Vượt ngày công chuẩn Taxi (Bài #23)** | Văn bản dừng | Chính thức **TẠM DỪNG từ 12/06/2026** | **ĐÃ DỪNG (Không áp dụng)** |
| **Thưởng Cuốc Điểm Vàng 8.000đ/cuốc (Bài #44)** | **Ngày rõ ràng** | Áp dụng từ 04/05/2026 đến hết ngày **19/07/2026** | **ĐÃ HẾT HẠN (Đã loại bỏ)** |
| **Thưởng Lễ 30/4, 2/9, Tết các năm 2024 - 2025** | **Ngày rõ ràng** | Đã hết hạn theo từng kỳ nghỉ lễ trong quá khứ | **ĐÃ HẾT HẠN (Đã loại bỏ)** |
| *Thưởng hiệu quả xe thuê VF5 giảm phí (Bài #34)* | *Chính sách xe thuê* | *Xét thưởng chu kỳ tháng* | *NGOÀI PHẠM VI (Mô hình xe thuê không quản lý)* |
| *Thưởng Tân Binh Car Platform 3.5 triệu (Bài #10)* | *Chính sách platform* | *25/06/2026 - 25/09/2026* | *NGOÀI PHẠM VI (Mô hình xe cá nhân không quản lý)* |

> **Phạm vi địa lý Hà Nội áp dụng**:  
> - 12 quận trung tâm: Ba Đình, Hoàn Kiếm, Tây Hồ, Long Biên, Cầu Giấy, Đống Đa, Hai Bà Trưng, Hoàng Mai, Thanh Xuân, Nam Từ Liêm, Bắc Từ Liêm, Hà Đông.  
> - Các hub giao thông trọng điểm: Cảng HKQT Nội Bài (sảnh E nhà ga T1, bãi đỗ P1 nhà ga T2), Bến xe Mỹ Đình, Bến xe Giáp Bát, Bến xe Nước Ngầm.  
> - Hệ thống Depot quản lý phương tiện: **Depot Gia Lâm**, **Depot Nam Từ Liêm**, **Depot Yên Nghĩa**, và các Hub giao ca vệ tinh.

---

## 0. ĐẶC TẢ NGHIỆP VỤ & BẢN ĐỒ THU NHẬP MÔ HÌNH TAXI CƠ HỮU GREEN SM HÀ NỘI

> [!IMPORTANT]
> **TUYÊN BỐ PHẠM VI VẬN HÀNH (SCOPE DECLARATION):**  
> Đơn vị kiểm soát hiện tại **CHỈ KIỂM SOÁT DUY NHẤT MÔ HÌNH TAXI CƠ HỮU**. Toàn bộ **1000 phương tiện** trong hệ thống mô phỏng và kho dữ liệu nền tảng đều là **Xe ô tô điện 4 bánh của công ty (VinFast VF5, VF6, VF e34, VF8)**, mã schema `track: "core_owned"`.  
> Các mô hình "Đối tác thuê xe" và "Đối tác Car Platform (xe cá nhân)" thuộc các khối kinh doanh độc lập khác và **HOÀN TOÀN NGOÀI PHẠM VI** của hệ thống này.

### 1. Bản Chất Pháp Lý, Tài Sản & Vận Hành Của Taxi Cơ Hữu
1. **Sở hữu phương tiện (Company Asset)**:
   - 100% xe do Green SM / VinFast sở hữu và đăng ký biển số vận tải.
   - Toàn bộ chi phí đăng kiểm, bảo hiểm trách nhiệm dân sự & thân vỏ, bảo dưỡng định kỳ, thay thế lốp, sửa chữa hỏng hóc đều do GSM chi trả 100%.
   - Xe được lắp đặt phần cứng chuyên dụng: Hộp đen viễn thám **TCU kết nối CAN-bus** truyền dữ liệu ODO thực tế, mức pin SOC, tốc độ và trạng thái xe về máy chủ thời gian thực, độc lập hoàn toàn với điện thoại di động của tài xế.
2. **Quan hệ lao động (Labor Contract)**:
   - Tài xế là **Người lao động chính thức** ký Hợp đồng Lao động (HĐLĐ) có thời hạn hoặc không xác định thời hạn với GSM (`contract_type: "employee"`).
   - Được đóng đầy đủ các chế độ an sinh xã hội: BHXH, BHYT, BHTN theo Luật Lao động Việt Nam.
3. **Quy chế giao nhận xe tại Depot**:
   - Tài xế được phân bổ xe và có nghĩa vụ nhận/trả xe tại Depot chỉ định (Depot Gia Lâm, Nam Từ Liêm, Yên Nghĩa).
   - Đầu ca: Kiểm tra tình trạng xe, ODO, mức pin, nhận ca trên app.
   - Cuối ca: Đưa xe về Depot bàn giao, cắm sạc pin, chốt số ODO và vệ sinh xe theo chuẩn dịch vụ 5 sao.
   - Nghiêm cấm tự ý lưu giữ xe qua đêm ngoài Depot hoặc sử dụng xe công ty vào việc riêng mà không có lệnh điều động.

---

### 2. Cơ Cấu Thu Nhập Chi Tiết Của Tài Xế Taxi Cơ Hữu / GF-VIP Hà Nội

Tài xế Taxi cơ hữu được đảm bảo thu nhập an sinh vững chắc kết hợp với đòn bẩy chia sẻ doanh số:

* **Công thức tổng thu nhập tháng:**  
  $$\text{Tổng Thu Nhập} = \text{Lương Cơ Bản} + \text{Thưởng Doanh Số \& Trách Nhiệm} + \text{Thưởng Ý Thức Chất Lượng} + \text{Thưởng Thâm Niên} + \text{Tiền Tip 100\%} + \text{Bù Sàn Thu Nhập (nếu có)}$$

* **Chi tiết các khoản tiền cụ thể (Hiệu lực 2026):**
  1. **Chính sách Đảm bảo thu nhập ngày (Sàn bảo vệ an sinh - Lưới an toàn):**
     * **GF-VIP (Áp dụng từ 31/08/2026):** Cam kết **650.000 VNĐ / ngày công**. Hệ thống cam kết phát tối thiểu **06 cuốc/ngày**; nếu hệ thống điều phối thiếu đơn, GSM **bù trực tiếp 90.000 VNĐ cho mỗi cuốc thiếu**.
     * **Taxi cơ hữu tiêu chuẩn (Áp dụng từ 06/06/2026):** Cam kết lương net tối thiểu **18.000.000 VNĐ / tháng** (tương đương 600.000 – 715.000 VNĐ / ngày công).
     * *Điều kiện hưởng sàn:* Online $\ge$ 8 giờ/ngày (trong đó ít nhất 3 giờ cao điểm: sáng 06h-09h hoặc chiều 16h-22h), tỷ lệ nhận chuyến $\ge$ 90%, tỷ lệ hoàn thành $\ge$ 90% (riêng GF-VIP $\ge$ 95%).
  2. **Thưởng Trách nhiệm & Chia sẻ Doanh số Vượt Mốc:**
     * Tài xế hưởng từ **25% đến 60%** doanh thu cước của các chuyến xe vượt mốc chỉ tiêu doanh số trong ngày.
  3. **Thưởng Ý thức Chất lượng Dịch vụ:**
     * Mức thưởng: **940.000 – 1.340.000 VNĐ / tháng**.
     * Điều kiện: Đánh giá sao trung bình từ khách hàng $\ge$ 4.90 sao, không có khiếu nại về thái độ phục vụ hay vệ sinh cabin xe.
  4. **Thưởng Thâm niên Cống hiến:**
     * Mức thưởng: Từ **500.000 đến 1.000.000 VNĐ / tháng** cho tài xế gắn bó trên 6 tháng.
  5. **Tiền Tip từ Khách hàng:**
     * Tài xế hưởng trọn **100%** tiền Tip (GSM không thu bất kỳ khoản chiết khấu nào).
  6. **Chính sách Trả lương khi Xe hỏng nằm Depot:**
     * Trường hợp xe gặp sự cố kỹ thuật hoặc bảo dưỡng định kỳ phải nằm Depot 3 – 4 ngày mà không có xe thay thế, tài xế **vẫn được tính lương ngày công bình thường**.

* **Thu nhập thực tế trung bình tại Hà Nội:** Dao động từ **16.000.000 đến 24.000.000 VNĐ / tháng**.

---

## 1. BẢNG TỔNG HỢP CƠ CHẾ TRUY THU GIAN LẬN TAXI CƠ HỮU (F1 - F6)

| ID | Loại hành vi gian lận | Bản chất vi phạm với Taxi Cơ Hữu | Cơ chế tính tiền truy thu (Clawback) | Chế tài bổ sung theo HĐLĐ & ATA |
|:---:|---|---|---|---|
| **F1** | **Chở khách không tạo cuốc** *(Off-app pickup)* | Chiếm đoạt doanh thu xe công ty, sử dụng điện & hao mòn tài sản GSM để thu tiền mặt riêng. | Truy thu **100% doanh thu cước**: $\text{Km GPS} \times \text{Đơn giá cước TB (13.000 - 19.000đ)}$. | Phạt trừ ví 1 - 2 triệu; đình chỉ công tác 7 - 14 ngày hoặc chấm dứt HĐLĐ. |
| **F2** | **Kéo dài hành trình cuốc xe** *(Route deviation)* | Nâng khống cước của khách làm tổn hại uy tín 5 sao GSM, tăng hao mòn và tiêu hao pin xe công ty. | $\text{Số km chênh lệch OSRM} \times \text{Đơn giá cước/km}$. | Hoàn trả 100% tiền thừa cho khách; phạt tài xế 500k - 1 triệu. |
| **F3** | **Làm mất tín hiệu định vị** *(GPS/TCU tampering)* | Tắt GPS/phá sóng hộp TCU để giấu hành vi chở khách ngoài luồng hoặc mang xe đi việc riêng. | Ước tính cước theo **chênh lệch ODO phần cứng VinFast**: $\Delta\text{ODO} \times 13.000\text{đ/km}$. | Đình chỉ vận doanh; thu hồi xe về Depot kiểm định kỹ thuật hộp TCU. |
| **F4** | **Sử dụng xe mục đích cá nhân** *(Personal use ngoài ca)* | Hết ca không trả xe về Depot, tự ý lái xe về quê, chở gia đình hoặc việc riêng ngoài ca trực. | Bồi hoàn chi phí vận hành ngoài ca: $\text{Km ngoài ca} \times 3.500\text{ VNĐ/km}$. | Truy thu 100% chi phí khấu hao; xử lý kỷ luật lao động vi phạm quản lý tài sản. |
| **F5** | **Tạo cuốc xe ảo** *(Fake trips cày KPI)* | Tạo cuốc ma để **trục lợi Sàn đảm bảo thu nhập 650k/ngày (bù 90k/cuốc)** và cày KPI mà không chạy xe. | Thu hồi **100% tiền bù/thưởng** + phạt Nhóm 1b (5 - 10 triệu đồng). | **CHẤM DỨT HĐLĐ VĨNH VIỄN, THU HỒI XE Ô TÔ VỀ DEPOT**, bồi thường thiệt hại. |
| **F6** | **Hủy sai lý do / Kén cuốc** *(Abnormal cancel)* | Kén chọn khách, từ chối cuốc ngõ nhỏ/đường tắc vi phạm cam kết phục vụ taxi 5 sao. | Không truy thu doanh thu (0 VNĐ). | Phạt hành chính trừ ví 1 - 5 triệu; **MẤT TOÀN BỘ SÀN ĐẢM BẢO 650K/NGÀY**. |

---

## 2. CHI TIẾT TỪNG LOẠI HÀNH VI VI PHẠM (F1 - F6) DÀNH CHO TAXI CƠ HỮU TẠI HÀ NỘI

### F1: Chở khách không tạo cuốc (Off-App Pickup / Street Hail / Chiếm Đoạt Doanh Thu Xe Công Ty)
* **Mã kỹ thuật:** `off_app` hoặc `off_app_pickup`
* **Bản chất hành vi với mô hình cơ hữu:**
  * Xe ô tô và chi phí sạc pin tại trạm V-Green do GSM đầu tư và chi trả 100%. Tài xế đón khách vẫy trực tiếp tại các điểm nóng Hà Nội (Sảnh E ga T1 Nội Bài, Bệnh viện Bạch Mai, BV K Tân Triều, Keangnam, Ga Hà Nội) rồi tắt app chở tay bo thu tiền mặt, hoặc nhận chuyến qua app rồi xúi giục khách hủy để lấy tiền mặt đút túi riêng.
  * Hành vi này cấu thành tội **chiếm đoạt tài sản và doanh thu của doanh nghiệp**.
* **Công thức truy thu tài chính (Clawback):**
  $$\text{Số tiền truy thu (VNĐ)} = \text{Quãng đường lăn bánh nghi vấn (km)} \times \text{Đơn giá cước Taxi cơ hữu (VNĐ/km)}$$
  * *Đơn giá định mức theo dòng xe GSM Hà Nội:*
    * Phân hạng A (VF5 / Herio): **13.000 VNĐ/km**.
    * Phân hạng B/C (VF6 / VF e34): **13.800 – 14.500 VNĐ/km**.
    * Phân hạng D (VF8 / GF-VIP): **19.000 VNĐ/km**.
* **Dấu hiệu nhận diện viễn thám:**
  * Cuốc xe trên `trips.parquet` bị đổi trạng thái `cancelled` sau khi xe đã tới điểm đón `pickup_time`.
  * Trên `public_driver_hex_tracking.parquet`, xe vẫn tiếp tục lăn bánh đều đặn từ ô đón đến đúng ô điểm trả của hành khách với tốc độ thông thường (30 – 60 km/h).
  * Cảm biến trọng lượng ghế phụ/ghế sau (`seat_occupied_status: true`) hoặc cảm biến đóng mở cửa xe ghi nhận có người bước lên xe.
* **Chế tài xử phạt (Quy tắc Ứng xử 11/09/2026 & HĐLĐ):**
  * Truy thu 100% doanh thu cước phát sinh.
  * Phạt trừ ví ATA 1.000.000 – 2.000.000 VNĐ; tạm đình chỉ công tác 7 – 14 ngày. Tái phạm lần 2 chuyển Hội đồng Kỷ luật sa thải.

---

### F2: Kéo dài hành trình cuốc xe (Route Deviation / Route Inflation / Làm Tổn Hại Uy Tín 5 Sao)
* **Mã kỹ thuật:** `route_deviation`
* **Bản chất hành vi với mô hình cơ hữu:**
  * Tài xế không tuân thủ lộ trình dẫn đường tối ưu OSRM tại Hà Nội, cố tình đi đường vòng qua các cầu khác hoặc ngõ hẹp nhằm kéo dài số km lăn bánh để nâng tiền cước của khách.
  * Hành vi này vừa ăn chặn tiền của khách làm tổn hại thương hiệu Taxi Green SM 5 sao, vừa gây hao mòn lốp và tiêu hao dung lượng pin xe công ty vô căn cứ.
* **Công thức truy thu tài chính:**
  $$\text{Số tiền truy thu (VNĐ)} = (\text{Quãng đường thực tế GPS} - \text{Quãng đường chuẩn OSRM}) \times \text{Đơn giá mỗi km}$$
* **Dấu hiệu nhận diện viễn thám:**
  * `distance_km` thực tế tính cước vượt quá 1.3 – 1.8 lần so với cự ly chuẩn giữa `pickup_h3` và `drop_h3`.
  * Vết di chuyển xuất hiện các ô lục giác nằm lệch hẳn ra ngoài hành lang giao thông kết nối thông thường mà hệ thống giao thông không có cảnh báo ngập lụt hay phân luồng.
* **Chế tài bổ sung:**
  * Hoàn trả 100% số tiền cước thu vượt cho khách hàng qua ví / tài khoản ngân hàng.
  * Phạt hành chính tài xế từ 500.000 đến 1.000.000 VNĐ; trừ điểm đánh giá chất lượng dịch vụ tháng.

---

### F3: Làm mất tín hiệu định vị (GPS Signal Loss / Tampering / Vô Hiệu Hóa Hộp Viễn Thám TCU)
* **Mã kỹ thuật:** `gps_anomaly` hoặc `signal_loss`
* **Bản chất hành vi với mô hình cơ hữu:**
  * Tài xế cố tình tắt định vị điện thoại, dùng thiết bị phá sóng GPS hoặc can thiệp cáp nối hộp viễn thám TCU trên xe VinFast nhằm che giấu hành vi chở khách ngoài luồng hoặc mang xe công ty đi làm việc riêng.
* **Phương pháp đo cự ly đối soát "chí mạng" cho Taxi Cơ Hữu:**
  * Do xe VinFast có hộp đen TCU lưu trữ độc lập công-tơ-mét điện tử (ODO) vào bộ nhớ bất biến, khi xe kết nối lại mạng hoặc kiểm tra tại Depot:
    $$\Delta\text{ODO} = \text{ODO}_{\text{khi bật lại/về Depot}} - \text{ODO}_{\text{lúc mất sóng}}$$
* **Công thức truy thu tài chính:**
  $$\text{Số tiền truy thu (VNĐ)} = \Delta\text{ODO (km)} \times \text{Đơn giá cước trung bình (13.000 VNĐ/km)}$$
* **Dấu hiệu nhận diện viễn thám:**
  * Xe mất sóng trên 15 phút giữa ca trực trong khu vực nội thành Hà Nội có phủ sóng 4G hoàn hảo; mức pin (SOC) giảm mạnh và ODO xe tăng vọt dù trên bản đồ xe đứng yên.
* **Chế tài bổ sung:**
  * Truy thu 100% cước ước tính theo $\Delta\text{ODO}$.
  * Lập biên bản đình chỉ vận doanh, điều động xe về Depot (Gia Lâm / Nam Từ Liêm / Yên Nghĩa) kiểm định niêm phong hộp đen TCU.

---

### F4: Sử dụng xe mục đích cá nhân ngoài ca trực (Personal Vehicle Use / Lạm Dụng Tài Sản Doanh Nghiệp)
* **Mã kỹ thuật:** `personal_use`
* **Bản chất hành vi (Đặc thù cốt lõi của Taxi Cơ Hữu):**
  * Hết ca trực cam kết (`shift_end_min` / tài xế bấm Offline app), tài xế có nghĩa vụ bàn giao xe về Depot chỉ định tại Hà Nội. Tuy nhiên, tài xế tự ý giữ xe lái về quê ngoại tỉnh (Bắc Ninh, Hải Dương, Hưng Yên, Hà Nam), chở gia đình đi du lịch hoặc phục vụ mục đích cá nhân.
* **Công thức truy thu chi phí hao mòn tài sản:**
  $$\text{Chi phí truy thu (VNĐ)} = \text{Số km lăn bánh ngoài ca trực (km)} \times 3.500\text{ VNĐ/km}$$
  * *Định mức chi phí bồi hoàn 3.500 VNĐ/km bao gồm:*
    * Tiền điện nạp pin tại trụ sạc V-Green / điện Depot: ~450 VNĐ/km.
    * Khấu hao cụm pin Lithium VinFast: ~1.200 VNĐ/km.
    * Khấu hao vỏ xe, lốp, khung gầm và hệ thống treo: ~1.850 VNĐ/km.
* **Dấu hiệu nhận diện viễn thám:**
  * Trạng thái tài xế trên app là `offline`, nhưng hộp TCU xe vẫn liên tục phát ping di chuyển qua hàng loạt ô lục giác H3; tọa độ xe vượt ra ngoài phạm vi địa giới hành chính Hà Nội.
* **Chế tài bổ sung:**
  * Truy thu toàn bộ chi phí bồi hoàn 3.500đ/km trừ trực tiếp vào bảng lương tháng.
  * Khiển trách bằng văn bản, đình chỉ quyền nhận ca tiếp theo; nếu tái phạm sẽ chấm dứt HĐLĐ và thu hồi quyền lái xe.

---

### F5: Tạo cuốc xe ảo (Fake / Ghost Trips / Cấu Kết Trục Lợi Sàn Thu Nhập 650.000đ/Ngày)
* **Mã kỹ thuật:** `fake_trip` hoặc `multi_account`
* **Bản chất hành vi (Quy tắc Ứng xử 11/09/2026 - Lỗi Nhóm 1b - STT 1):**
  * Hành vi dùng điện thoại phụ hoặc cấu kết với người quen tự đặt cuốc xe, tài xế nhận cuốc rồi lập tức bấm hoàn thành chớp nhoáng mà không có khách thật và không có xe lăn bánh thật.

#### MỤC TIÊU TRỤC LỢI DUY NHẤT CỦA TÀI XẾ TAXI CƠ HỮU HÀ NỘI:
1. **Trục lợi Tiền Bù Sàn Đảm Bảo Thu Nhập 650.000 VNĐ / Ngày (GF-VIP Hà Nội - 31/08/2026):**
   * GSM cam kết phát tối thiểu 06 cuốc/ngày (nếu thiếu, **bù 90.000 VNĐ cho mỗi cuốc thiếu**).
   * *Thủ đoạn F5:* Tài xế ngồi yên tại nhà hoặc bãi đỗ, tạo vài cuốc ảo chớp nhoáng để vừa đạt mốc 6 cuốc, vừa đẩy tỷ lệ nhận và hoàn thành lên trên 90% (GF-VIP $\ge$ 95%) nhằm **ngồi im bỏ túi trọn vẹn 650.000đ/ngày** mà không tốn công lái xe và không mất tiền điện sạc pin.
2. **Trục lợi Thưởng Trách Nhiệm Vượt Mốc & Hoàn Thành Chỉ Tiêu Tháng:**
   * Tạo các cuốc ảo ngắn cự ly 1 – 2km để đủ số cuốc định mức tháng hưởng trọn mức lương cam kết 18.000.000 VNĐ/tháng.
3. **Trục lợi Mã Khuyến Mãi / Voucher Trợ Giá Của GSM:**
   * Áp các voucher giảm giá 30k – 100k của công ty vào cuốc ma để rút tiền trợ giá của doanh nghiệp về ví.

#### CÔNG THỨC TRUY THU TOÀN DIỆN CHO F5 TAXI CƠ HỮU:
$$\text{Tổng tiền thu hồi} = \text{Toàn bộ doanh thu cước ảo} + \text{Toàn bộ tiền bù sàn 650k/ngày bị trục lợi} + \text{Tiền phạt Nhóm 1b}$$
* **Khung phạt Nhóm 1b (Quy tắc ứng xử 11/09/2026):**
  * Giá trị trục lợi dưới 500.000đ: **Phạt trừ ví 5.000.000 VNĐ** (lần 1), **8.000.000 VNĐ** (lần 2).
  * Giá trị trục lợi trên 500.000đ: **Phạt trừ ví 10.000.000 VNĐ, CHẤM DỨT HỢP ĐỒNG LAO ĐỘNG VĨNH VIỄN, THU HỒI XE Ô TÔ VỀ DEPOT**, chuyển hồ sơ sang cơ quan điều tra nếu có dấu hiệu tội phạm.

* **Bằng chứng viễn thám "chí mạng":**
  * Trong khi app tài xế báo cuốc xe đang chạy, cảm biến TCU xe báo tốc độ xe = 0 km/h, xe đứng im trong bãi đỗ.
  * Camera cabin AI xác nhận ghế sau hoàn toàn trống không, không có hành khách trên xe.

---

### F6: Hủy sai lý do / Kén chọn cuốc xe (Abnormal Cancellation / Cherry-Picking / Vi Phạm Tiêu Chuẩn 5 Sao)
* **Mã kỹ thuật:** `abnormal_cancel` hoặc `invalid_cancel`
* **Bản chất hành vi:**
  * Tài xế ô tô nhận cuốc nhưng thấy ngõ nhỏ phố cổ (Hoàn Kiếm, Đống Đa) khó lùi xe, hoặc đường tắc giờ tan tầm (Ngã Tư Sở, Cầu Chương Dương) nên ngại đón, liền gọi điện ép khách tự hủy chuyến hoặc bấm hủy chọn sai lý do "Khách không đến" để trốn trách nhiệm.
* **Công thức truy thu:**
  * `Số tiền truy thu = 0 VNĐ` (Do cuốc không hoàn thành nên không phát sinh doanh thu cước).
* **Chế tài xử lý vi phạm nghiêm khắc đối với Taxi Cơ Hữu:**
  * Hủy 1 – 5 cuốc/tuần: **Phạt 1.000.000 VNĐ**.
  * Hủy trên 5 cuốc/tuần: **Phạt 2.000.000 VNĐ**.
  * Tái phạm lần 2: **Phạt 3.000.000 VNĐ**; Tái phạm lần 3: **Phạt 5.000.000 VNĐ/lần**.
  * **HẬU QUẢ VỀ THU NHẬP:** Tỷ lệ hoàn thành bị tụt xuống dưới 90% (GF-VIP dưới 95%) $\rightarrow$ **MẤT HOÀN TOÀN QUYỀN HƯỞNG SÀN ĐẢM BẢO THU NHẬP 650.000 VNĐ/NGÀY** (mất đứt 15 – 18 triệu đồng/tháng).

---

## 3. MA TRẬN ĐỐI SOÁT SCHEMA PHÁT HIỆN GIAN LẬN DÀNH CHO 1000 XE TAXI CƠ HỮU

Hệ thống huy động đầy đủ các tầng schema để kiểm soát chặt chẽ 1000 xe Taxi Cơ Hữu:

### 3.1. Tầng L0 (Master Data & Danh Mục Cơ Hữu)
* [`schemas/l0/driver_profile.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l0/driver_profile.schema.json):
  * `track`: Bắt buộc là `"core_owned"` (100% 1000 xe).
  * `vehicle_type`: Bắt buộc là `"car"`.
  * `vehicle_class`: Phân bổ chuẩn (`A_COMPACT`, `B_SEDAN`, `C_SUV`, `D_LUXURY`).
  * `vehicle_model`: Model VinFast (`VF5`, `VF6`, `VF_e34`, `VF8`).
  * `declared_shift_window`: Ca trực cam kết để đối soát hành vi ngoài ca F4.
* [`schemas/l0/policy_bundle.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l0/policy_bundle.schema.json):
  * Biểu giá cước chuẩn từng phân hạng xe tại Hà Nội (`base_fare_vnd`, `per_km_vnd`).
  * Mức bảo đảm thu nhập sàn ngày: 650.000 VNĐ/ngày, bù 90.000 VNĐ/cuốc thiếu.
  * Tỷ lệ chia sẻ doanh số vượt mốc: 25% – 60%.

### 3.2. Tầng L1 & L1R (Nhật Ký Giao Dịch & 13 Bảng Dữ Liệu Thực Tế GSM)
* [`schemas/l1r/trips.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l1r/trips.schema.json): Đối soát thời gian, tọa độ đón/trả, quãng đường tính tiền `distance_km` và trạng thái chuyến đi.
* [`schemas/l1r/public_driver_hex_tracking.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l1r/public_driver_hex_tracking.schema.json): Vết di chuyển ô lục giác H3 Res 7 của 1000 xe qua từng phút.
* [`schemas/l1r/driver_statistic_daily.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l1r/driver_statistic_daily.schema.json): Kiểm tra tỷ lệ nhận chuyến, hoàn thành chuyến và tỷ lệ hủy đối chiếu với điều kiện sàn 650k/ngày.
* [`schemas/l1r/driver_income_daily.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l1r/driver_income_daily.schema.json): Báo cáo tài chính ngày, ghi nhận tiền lương, thưởng chia sẻ doanh số, tiền bù sàn 650k/ngày và tiền Tip.
* [`schemas/l1r/driver_online_hours_sap_id.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l1r/driver_online_hours_sap_id.schema.json): Quản lý Depot trực thuộc (Gia Lâm, Nam Từ Liêm, Yên Nghĩa), mã SAP nhân sự, đối chiếu giờ online/offline với ca trực.
* [`schemas/l1r/driver_penalization_ATA.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l1r/driver_penalization_ATA.schema.json): Ghi nhận toàn bộ các quyết định truy thu và phạt vi phạm quy chế ứng xử.
* [`schemas/l1r/public_frauds.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/l1r/public_frauds.schema.json): Cảnh báo vi phạm gian lận tự động (F1 – F6).

### 3.3. Tầng Telemetry (Cảm Biến IoT Hộp Viễn Thám TCU Xe VinFast)
* [`schemas/telemetry/vehicle_telemetry_ping.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/telemetry/vehicle_telemetry_ping.schema.json): Dữ liệu phần cứng TCU truyền mỗi 3 – 5 giây: Tọa độ GNSS, công-tơ-mét `odometer_km`, tốc độ `speed_kmh`, dung lượng pin `battery_soc_pct`. Bằng chứng tối cao để đối soát chênh lệch ODO cho F3 và bắt xe đứng yên cho F5.
* [`schemas/telemetry/camera_event.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/telemetry/camera_event.schema.json): Phát hiện che camera cabin (`camera_obscured`) hoặc xác nhận ghế sau trống không khi đang có cuốc xe ảo.
* [`schemas/telemetry/charging_session_log.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/telemetry/charging_session_log.schema.json): Lịch sử cắm sạc pin tại trụ V-Green hoặc sạc Depot.
* [`schemas/telemetry/vehicle_registry.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/telemetry/vehicle_registry.schema.json): Quản lý số khung VIN xe VinFast thuộc sở hữu GSM, gắn với biển số xe và Depot quản lý.

### 3.4. Tầng Case & Tools (Hồ Sơ Xử Lý & Công Cụ AI Agent Điều Tra)
* [`schemas/case/fraud_case.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/case/fraud_case.schema.json) & [`schemas/case/case_resolution.schema.json`](file:///e:/THUCTAP/crawldata/GSM_SIMULATOR-raw/schemas/case/case_resolution.schema.json): Lập hồ sơ vi phạm, phê duyệt mức truy thu và ban hành chế tài kỷ luật lao động.
* **12 Tool schemas điều tra**: `compare_route_to_optimal`, `check_location_integrity`, `pull_evidence_bundle`, `query_policy`, `resolve_case`, `apply_action`...

---

## 4. KẾT LUẬN & CAM KẾT VẬN HÀNH

Hệ thống được thiết lập chặt chẽ nhằm bảo vệ tài sản doanh nghiệp, giữ vững cam kết chất lượng dịch vụ taxi 5 sao của Green SM, đồng thời đảm bảo quyền lợi thu nhập an sinh công bằng và minh bạch cho đội ngũ tài xế Taxi Cơ Hữu làm việc nghiêm túc, tận tụy tại Thủ đô Hà Nội.
