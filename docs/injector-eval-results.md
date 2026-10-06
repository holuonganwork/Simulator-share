# Ket qua Precision/Recall that - Pha A (fraud injector)

Pha A cua "Chien luoc lam giau du lieu" (8 hang muc mentor giao, uu tien cao
nhat = muc 7 "sim world cac cach cheat"). Tai lieu nay ghi lai KET QUA THAT lan
dau tien cho ca 7 mau hinh Tier A da co luat (ngoai abnormal_cancel - mau hinh
duy nhat da co nhan doc lap that tu truoc, xem docs/metrics.md).

## Phuong phap

`src/lcx_core/fraud_injector/injector.py` doc du lieu that qua `GsmDataStore`,
THEM (khong sua) cac dong tong hop dai dien hanh vi gian lan tham so hoa vao
BAN SAO trong bo nho cua tung bang lien quan, o 3 muc do nghiem trong (light/
medium/heavy). Nhan doc lap duoc giu trong mot DataFrame TACH BIET, khong bao
gio dua vao bang ma detector doc - detector Tier A chay KHONG SUA MOT DONG CODE
NAO. Chi tiet xem docstring `injector.py` va `scripts/run_injector_eval.py`.

Chay lai:

```bash
./.venv/bin/python3 scripts/run_injector_eval.py --n-per-severity 40 --save
```

## Ket qua (n=40/muc do, seed=42, chay 2026-09-18T04:29:40Z)

| Mau hinh | Recall light | Recall medium | Recall heavy | Precision among flags | n_flags (tren du lieu da tiem) |
|---|---|---|---|---|---|
| route_deviation | 100% | 100% | 100% | 13.6% | 880 |
| gps_distance_inflation | 100% | 100% | 100% | 1.0% | 11781 |
| location_dropout | 100% | 100% | 100% | 15.2% | 790 |
| split_long_trip | 100% | 100% | 100% | 100% | 120 |
| wait_fee_manipulation | 0% | 100% | 100% | 75.5% | 106 |
| fake_ride_for_kpi | 0% | 52.5% | 90% | 100% | 57 |
| ride_preview_selection | 100% | 100% | 100% | 97.6% | 123 |

**"Precision among flags"** = trong toan bo flag cua detector tren du lieu that
+ da tiem, bao nhieu % trung voi mot don vi DA BIET la fraud (da tiem). Day
KHONG PHAI precision chuan (khong co nhan am doc lap cho phan du lieu that con
lai - xem "Gioi han" ben duoi) - chi doc duoc nhu "trong cac flag hien co, bao
nhieu % la fraud co chu dich da biet".

## Nhan xet tung mau hinh

- **route_deviation**: recall 100% o ca 3 muc vi nguong p99 hien tai RAT THAP
  (am, do do lech he thong -32% cua chinh du lieu that - xem docstring
  `route_deviation.py`) - hau nhu bat ky do vong duong nao cung vuot nguong.
  Danh gia: rule dang RAT NHAY (over-sensitive) tren du lieu nay, can luu y khi
  dua vao production that (nguong se can hieu chuan lai tren distance_km dung
  don vi, khong con do lech he thong).
- **gps_distance_inflation**: recall 100%, nhung n_flags=11781 tren du lieu that
  la con so lon - rule hard-cap toc do co the dang qua nhay voi nhieu doan
  "nhay hex" that khong lien tuc trong du lieu mock (khong han la gian lan).
- **location_dropout**: recall 100% SAU KHI sua injector de tinh muc do nghiem
  trong DONG THEO p99 that (~20 gio) thay vi so tuyet doi co dinh (30 phut-6
  gio nhu gia dinh ban dau). Phat hien quan trong: khoang trong TU NHIEN giua 2
  lan bat GPS cua MOT driver co the len toi hang chuc gio (driver khong lai xe
  lien tuc moi ngay trong 3 thang du lieu) - bat ky nguong tuyet doi nao thiet
  ke truoc deu se sai neu khong doi chieu voi phan phoi that truoc.
- **split_long_trip**: recall 100%, precision_among_flags=100% vi tren du lieu
  that goc detector nay LUON tra ve 0 flag (customer_id khong bao gio lap lai -
  xem docstring `split_long_trip.py`) - toan bo 120 flag deu la cua injector.
  Day la bang chung dau tien detector nay HOAT DONG DUNG logic, chi la chua co
  du lieu that phu hop de kich hoat.
- **wait_fee_manipulation**: recall light=0%, medium/heavy=100%. Nguong that
  (21 diem dung gio cao diem) CAO HON gia tri "light" da tiem (15) - tuc la mot
  hanh vi thao tung phi cho o muc nhe co the LACH duoc rule hien tai. Day la so
  lieu that ve do nhay cua rule, khong phai loi injector.
- **fake_ride_for_kpi**: recall light=0%, medium=52.5%, heavy=90% - **phat hien
  quan trong nhat cua Pha A**. Vi detector TU TINH mean/stdev tren CA tap du
  lieu (bao gom ca tuan da tiem) moi lan chay, mot outlier cang lon cang tu lam
  phinh to do lech chuan dung de chuan hoa chinh no ("self-masking effect") -
  z-score thuc te luon thap hon nhieu so voi z-score du dinh khi tiem. Day la
  mot diem yeu THAT cua phuong phap z-score tu tham chieu (da duoc canh bao
  trong CAVEAT cua chinh `fake_ride_for_kpi.py`), khong phai loi cua injector -
  va la ly do de nghi Pha B/C uu tien them nguon nhan doc lap khac (vd Tier C
  device/account graph) thay vi chi dua vao thong ke tu than.
- **ride_preview_selection**: recall 100% ca 3 muc, hop ly vi nguong p1 that
  (0.625) cao hon nhieu so voi cac gia tri da tiem (<=0.45).

## Gioi han con lai

1. **Chua co precision chuan tren toan bo dan so.** Van chua co nhan AM (xac
   nhan "khong phai fraud") doc lap cho phan du lieu that con lai - can van
   hanh chat quan ly that (Pha 5, xem docs/metrics.md) de co nhan `false_positive`
   that truoc khi tinh duoc precision chuan.
2. **fake_ride_for_kpi nhay voi seed/N** do chinh hieu ung self-masking noi
   tren (N nho -> 1 outlier anh huong manh hon toi stdev). Test hoi quy
   (`tests/fraud_injector/test_injector.py`) dung nguong 60% (thay vi ~90% o
   run n=40 day) de chiu duoc bien thien nay o quy mo test nho hon.
3. Injector CHUA phu 2 mau hinh con stub (`fake_no_show`, `off_app_cash_pressure`)
   va Tier C (`device_account_graph`) - can du lieu moi tu Pha B/C/D truoc (xem
   artifact chien luoc lam giau du lieu, muc 04 lo trinh).

## Khoi 1 (2026-09-18) - action/state, camera, telemetry, charging log

Tiep theo Pha A, trien khai het hang muc 2 (sensor/camera), 4 (action/state),
5 (tich hop telemetry), 6-PHAN LOG (charging_session_log + vehicle_registry -
KHONG bao gom mo rong ban do tram sac, ngoai pham vi theo yeu cau). Khac voi
Pha A (lam giau du lieu THAT co san), o day KHONG co du lieu that de lam giau -
`lcx_core.event_sim` SINH MOI mot quan the driver-ngay (binh thuong + gian lan
tiem co tham so) dung chung quy uoc voi `telemetry_mock` (Buoc 1-3). Chi tiet
kien truc: xem docstring `src/lcx_core/event_sim/simulator.py`.

Chay lai: `./.venv/bin/python3 scripts/run_telemetry_tier_eval.py --save`

| Mau hinh | Recall light/medium/heavy | Precision among flags | n_flags |
|---|---|---|---|
| virtual_online_status | 100% / 100% / 100% | 41.7% | 108 |
| camera_intervention | 100% / 100% / 100% | 100% | 45 |
| battery_swap_validation | 100% / 100% / 100% | 100% | 45 |
| ghost_vehicle | 100% / 100% / 100% | 100% | 45 |

Loi da phat hien va sua khi xay dung (ghi lai thay vi giau di):

- **camera_intervention** ban dau tra ve 0 flag hoan toan: quan the "driver-
  ngay" chi gom cac dong DA CO su kien camera (bang thua), khien percentile p99
  tu tham chieu vao chinh 30-45 dong da tiem (giong het loi route_deviation o
  Pha A). Sua bang cach (1) dung quan the DAY DU tu chinh cac ca lam viec that
  (went_online) de tinh mau so, va (2) doi han sang NGUONG CUNG (>=2 su kien do
  tin cay cao/ngay) thay vi percentile - phu hop hon voi quy mo du lieu nho
  hon nhieu so voi Tier A (hang tram thay vi hang tram nghin dong).
- **went_online/app_foreground/app_background** duoc them vao chinh may trang
  thai LIVE cua `telemetry_mock/drivers.py` (khong chi trong `event_sim` danh
  gia rieng) - phat hien mot loi tiem an trong `_make_event()`: ham nay co
  side-effect TU DONG sinh `trip_id` moi neu `driver.trip_id is None`, se gan
  nham mot trip_id gia cho su kien CAP PHIEN (went_online) khong thuoc ve
  chuyen nao ca. Da sua bang tham so `session_level=True` de tat side-effect
  nay va cho phep `trip_id=null` (schema da duoc cap nhat tuong ung).

## Khoi 2 (2026-09-18) - thoi tiet->gia, hanh trinh vi tri, ho so tai xe

Hang muc 8 (surge_multiplier tu rain config, mo mau hinh moi) + hang muc 3
(trip_waypoints, cai thien do chinh xac cho route_deviation, KHONG mo mau moi)
+ hang muc 1 PHAN HO SO (KHONG bao gom mo rong khong gian - ngoai pham vi).

**Gioi han quan trong cua hang muc 8** (ghi lai day du trong docstring
`lcx_core/derived_data/fare_breakdown.py`): scenario sinh `trips.parquet` hien
co la `dry_weekday` voi `environment.rain.series=[]` - KHONG co mua nao trong
luc sinh du lieu that, va trips.parquet KHONG luu cuong do mua tai thoi diem
tung cuoc. `rain_mm` o day la MO PHONG LAI (seed co dinh, dung dung cong thuc
bao hoa `x/(x+r_peak_mmph)` va tham so that trong `pilot_dongda.yaml`) de
CHUNG MINH co che, khong phai tai tao lich su that. `dow_multiplier` (thu
Sau/cuoi tuan) LA THAT, suy truc tiep tu `request_time`.

Chay lai: `./.venv/bin/python3 scripts/run_surge_collusion_eval.py --save`

| Muc do | Recall | Ghi chu |
|---|---|---|
| light (3 cuoc surge/ngay) | 0% | Duoi nguong p99 that (7) - mot phat hien that ve do nhay cua rule, khong phai loi |
| medium (8 cuoc/ngay) | 100% | |
| heavy (15 cuoc/ngay) | 100% | |

precision_among_flags = 63.8% (47 flag, 30 trung don vi da tiem - 17 flag con
lai la driver-ngay THAT cung vuot nguong ngau nhien, hop ly voi ban chat mua
mo phong ngau nhien).

Loi da phat hien va sua: muc do "medium" ban dau dat dung 6 cuoc/ngay =
CHINH nguong tinh duoc (7 sau khi tiem, nhung ban dau trung 6 truoc khi chinh) -
`>` la so sanh CHAT (khong phai `>=`) nen bi bo lot o ranh gioi. Ngoai ra
`rain_mm` tiem ban dau lay tu `uniform(10,18)` - can duoi (10mm) doi khi cho ra
`rain_multiplier` NGAY DUOI nguong p90 that, khien don vi tiem khong duoc dem
la "surge cao" o buoc trung gian. Ca hai da duoc nang bien an toan hon
(SEVERITY_PARAMS 3/8/15, rain_mm uniform 15-22).

**Hang muc 3 (trip_waypoints)**: `scripts/run_waypoint_accuracy_check.py` so
sanh nguong p99 tinh tu 2-diem-mut (hien dung trong route_deviation.py) voi
nguong tinh tu CA LO TRINH (noi suy qua OSRM). Ket qua tren mau 1465 cuoc bike:
hai cach cho ra tap flag GAN NHU KHONG TRUNG NHAU (chi 2/14 trung) - xac nhan
ro rang route_deviation hien tai (2 diem mut) va phien ban day du lo trinh se
bat NHUNG TAI XE KHAC NHAU. Chua thay the route_deviation.py (dung nhu danh
gia uu tien: "cai thien do chinh xac, khong mo mau moi") - can quyet dinh sau
cach nao phan anh dung "di vong" hon truoc khi doi.

**Hang muc 1 (driver_profile_enriched)**: `scripts/build_driver_profile_enriched.py`
sinh 150 ho so, trong do `violation_count_total`/`violation_amount_vnd_total`
la THAT (gop tu `driver_penalization_ATA.parquet` co san - 37/150 driver co
vi pham that), con `join_date`/`contract_type` la SYNTHETIC (ghi ro trong cot
`synthetic_fields` cua chinh bang) vi khong co bang onboarding trong du lieu
goc de tai tao. Khong mo mau hinh moi (dung danh gia cua tai lieu chien luoc:
"nen dai han").

## Pham vi KHONG lam (theo yeu cau)

Mo rong ban do/khong gian ra toan Ha Noi (phan con lai cua hang muc 1) va mo
rong ban do tram sac (phan con lai cua hang muc 6) - ca hai deu doi hoi tinh
lai toan bo ma tran OSRM/POI cho khu vuc moi, ngoai pham vi lan lam giau du
lieu nay theo chi dao.
