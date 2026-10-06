# Nghien cuu telemetry that - Buoc 1 & 2 (quan sat app + hop dong nhap)

Nguon: quan sat truc tiep 4 app trong he sinh thai (Xanh SM khach, Xanh SM Driver,
V-Green, VinFast) + doi chieu voi cau truc telemetry cua du an mo
`vinfast-connected-car` (khong phai spec chinh thuc, VinFast da khoa private API
tu giua 2026). Muc dich: lam Buoc 2 cua ke hoach mock telemetry - chot mot "hop
dong" nhap de dung API mock (Buoc 3, chua build) va vay lai
`schemas/l1/gps_ping.schema.json` cua GSM_SIMULATOR.

Hai schema nhap tuong ung: `schemas/telemetry/vehicle_telemetry_ping.schema.json`
va `schemas/telemetry/trip_lifecycle_event.schema.json`. Tham so van hanh (tan
suat ping, thoi luong doi pin...) o `configs/telemetry_mock.yaml`.

## Phat hien thay doi danh gia truoc do

1. **He thong that dung lat/lng, KHONG dung H3.** Lop H3 trong
   `public_driver_hex_tracking.parquet` la mot tang TONG HOP/AN DANH ma
   GSM_SIMULATOR dung cho MOCK, khong phai dinh dang telemetry goc. Giai thich
   duoc vi sao bang do ghi "su kien doi o" chu khong phai "ping deu dan" (da
   phat hien truoc do trong artifact chien luoc, muc 11 - gio co ly do ky thuat
   ro rang hon).

2. **Trang thai `arrived_at_pickup` TON TAI THAT trong san pham, kem geofence
   check.** Day la thay doi quan trong nhat: `docs/appendix-pending-data.md` va
   hai module `tier_a/fake_no_show.py`, `tier_a/off_app_cash_pressure.py` truoc
   day ket luan "khong co bang chung that" cho trang thai nay. Quan sat UI xac
   nhan NGUOC LAI - Xanh SM Driver bat buoc tai xe bam "Da den diem don" va he
   thong that su kiem tra GPS trong ban kinh cho phep. Ket luan can sua:
   **khong phai thieu du lieu ve nguyen tac, ma la GSM_SIMULATOR chua mock
   truong nay** - khac nhau ve ham y (co the tu mock duoc mot khi co hop dong
   telemetry, khong bat buoc phai cho GSM cap du lieu that).

3. **Xe hoi (TCU) va xe may (dien thoai) la HAI nguon telemetry khac nhau.** Xe
   may dien (Feliz S/Evo200/Klara S) khong co TCU rieng - dinh vi hoan toan phu
   thuoc dien thoai tai xe. Xe hoi VinFast co TCU rieng, van stream duoc du
   lieu (odometer, gear, seat_occupancy...) NGAY CA KHI dien thoai tai xe mat
   ket noi/tat dinh vi - day la tin hieu MANH HON han cho "ngat dinh vi giua
   chuyen" so voi cach lam hien tai cua `location_dropout.py` (chi suy tu
   khoang trong log dien thoai).

4. **Phat hien ke ho moi: XanhNow (bat khach vay doc duong).** Khop cuoc qua ma
   OTP 4 so, KHONG qua tong dai dieu phoi - khong co kiem chung cheo. Day la
   mot con duong "cuoc ao"/cau ket MOI, chua co trong ca 15 mau hinh cu lan bo
   F1-F6 cua mentor. Diem quan trong: khac voi da tai khoan/cau kiet (can du
   lieu thiet bi/IP dang thieu hoan toan), mau hinh nay co the do bang
   THONG KE DON GIAN - ty le cuoc `via_xanhnow` bat thuong cao tren tong cuoc
   cua mot tai xe - dung dung phuong phap percentile da ap dung cho
   `abnormal_cancel`, KHONG can Tier C.

## Bang anh xa app -> telemetry -> tin hieu gian lan (nguyen ban nguoi dung cung cap)

| Su kien tren app | Dau vet telemetry | Y nghia chong gian lan |
|---|---|---|
| Bam "Bat dau chuyen" | `gear='D'`, `speed>0`, odometer tang, `seat_occupancy=true` | Bam bat dau ma xe van o P, odometer dung yen -> cuoc ao |
| Bam "Khach khong den" (huy) | `seat_occupancy=true` va xe van di chuyen ve huong dich | Huy gia nhung van cho khach chui -> off-app/ep huy |
| Doi pin tai tu V-Green | Dung tai toa do tu ~120-300s; `battery_level_pct` nhay 15%->98% | Hop thuc hoa dung xe giua ca (khong phai tat may) |
| Tat dinh vi tren dien thoai | Dien thoai mat ket noi nhung xe (TCU) van stream odometer/speed | Chung minh tai xe co y tat mang dien thoai |

## Viec can lam tiep (Buoc 1 con lai + Buoc 2 hoan tat)

- **Chua do duoc `arrival_geofence_radius_m` that** (bao nhieu met thi nut "Da
  den" bi khoa) - can thu nghiem thuc te hoac hoi doi GSM, hien de `null` trong
  schema.
- **Tan suat ping goc tu thiet bi chua chac chan** - 3-5s la tan suat CAP NHAT
  TREN BAN DO (co the da qua noi suy), khong chac la tan suat GOC. Ghi ro trong
  `configs/telemetry_mock.yaml` la CAN THAN, khong phai so da xac nhan.
- Buoc 3 (dung service mock phat telemetry that, xem muc 12.3 artifact chien
  luoc) van CHUA build - hai schema o day la nguyen lieu dau vao cho buoc do.
