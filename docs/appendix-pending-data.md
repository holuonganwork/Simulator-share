# Phu luc - mau hinh cho du lieu bo sung tu GSM

Ba mau hinh sau KHONG the trien khai tu 13 bang Parquet hien co cua
GSM_SIMULATOR. Da xac minh truc tiep (grep + doc schema + kiem tra gia tri that
trong parquet), khong phai suy doan.

**Cap nhat (docs/telemetry-research.md, sau Buoc 1 quan sat truc tiep 4 app
trong he sinh thai):** mau hinh 2 va 3 duoi day can duoc DOC LAI voi mot sac
thai khac. Truoc day ket luan "khong co bang chung that" cho trang thai
`arrived_at_pickup`; quan sat UI cua app Xanh SM Driver xac nhan trang thai nay
**ton tai that** trong san pham (nut "Da den diem don" + kiem tra GPS ban
kinh). Dieu do co nghia GSM_SIMULATOR (nguon du lieu cua repo nay) chua mock
truong nay, chu KHONG PHAI ban than nang luc do khong ton tai trong he thong
that. Neu Buoc 3 cua ke hoach mock telemetry (`docs/telemetry-research.md`,
`schemas/telemetry/`) hoan thanh, hai mau hinh nay co the chuyen tu "cho du
lieu GSM" sang "tu mock duoc" ma khong can cho GSM cap gi them.

## 1. Da tai khoan / thue tai khoan + cau ket (Tier C)

**Xac minh:** grep toan bo `schemas/l0/*.json` va `schemas/l1/*.json` cua
GSM_SIMULATOR - khong co truong `device_id`, `IMEI`, `IP`, hay `BSSID` o bat ky
dau. `src/lcx_core/tier_c/device_account_graph.py` la stub nem
`MissingDataError` ro rang thay vi tra ve ket qua rong/gia.

**Du lieu can xin:** device_id/IMEI hoac log dang nhap, de dung nguong
(strategy goc dung chu "chu do") de xay dung Tier C (do thi quan he).

## 2. Bao "khach khong den" gia (fake_no_show)

**Xac minh:** `schemas/l1r/trips.schema.json` co enum status
`["completed", "cancelled", "assigned"]` nhung KHONG co gia tri lien quan
no-show; trong `data/mock/realdata-v1/trips.parquet` 100% (174.233/174.233)
dong co `status='completed'`. `driver_penalization_ATA.penalty_type` chi co
gia tri `'clawback_khoan'` trong du lieu hien co.

**Du lieu can xin:** vi tri thuc te luc tai xe bam "bao khach khong den", thoi
gian da dung cho that (khong chi tong hop `stoppoints`).

## 3. Ep huy chuyen tien mat (off_app_cash_pressure)

**Xac minh:** `trips.schema.json` khong co trang thai `arrived` - khong the
phan biet "huy truoc khi gap khach" voi "huy sau khi gap khach" tu du lieu hien
co. Khong co dong `cancelled` nao trong `trips.parquet` de doi chieu.

**Du lieu can xin:** trang thai chi tiet hon cho trip (vd `arrived_at_pickup`)
va/hoac log huy cuoc kem thoi diem so voi `pickup_time`.

## 4. Lam dung XanhNow (bat khach vay doc duong) - mau hinh MOI, phat hien qua Buoc 1

**Xac minh:** quan sat truc tiep app Xanh SM Driver - tinh nang XanhNow cho
phep khop cuoc qua ma OTP 4 so nhap tai cho, KHONG qua tong dai dieu phoi
trung tam, khong co kiem chung cheo. Day la mot con duong cuoc ao/cau ket MOI,
chua co trong 15 mau hinh khao sat ban dau (muc 02 artifact chien luoc) lan bo
6 ma F1-F6 cua mentor.

**Khac voi 3 muc tren:** mau hinh nay KHONG can du lieu thiet bi/IP (Tier C).
Co the do bang thong ke don gian mot khi co truong `via_xanhnow` (xem
`schemas/telemetry/trip_lifecycle_event.schema.json`) - ty le cuoc via_xanhnow
bat thuong cao tren tong cuoc cua mot tai xe, dung dung phuong phap percentile
da ap dung cho `abnormal_cancel`. Hien CHUA co du lieu (ke ca mock) vi
truong nay chi moi xuat hien trong hop dong telemetry nhap, chua duoc sinh boi
service mock nao (Buoc 3 chua build).

---

Khi mot trong ba loai du lieu tren duoc GSM cap, dua dung mau hinh tuong ung
tro lai lo trinh chinh (Pha 1 cua rieng mau do) thay vi bat dau lai tu dau -
interface cua `src/lcx_core/tier_c/device_account_graph.py` va hai module stub
trong `src/lcx_core/tier_a/` da duoc thiet ke san de khong phai sua chu ky ham
o noi khac khi cai logic that vao.
