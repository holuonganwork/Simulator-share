# Ma tran mau hinh gian lan x muc do san sang du lieu

> **LUU Y (2026-09-24):** cac so lieu ben duoi do tren bo du lieu CU (90 ngay, 150 driver, con ~75% xe may).
> Repo nay nay chi ho tro 100% o to Taxi Co Huu; chua do lai tren bo moi. `trips` gio co ca cuoc
> `cancelled` (schema 1.1.0) nen cac dong 'trips.status 100% completed' o cuoi file khong con dung.
> Mau hinh F1/F3/F4/F5/F6 (off_app_pickup, tcu_signal_loss, personal_use, fake_trip,
> invalid_driver_cancel) do bang `scripts/run_telemetry_tier_eval.py`, khong nam trong bang nay.

Ket qua chay that tren `data/mock/realdata-v1` (90 ngay, seed 7000, 150 driver
profile). Chay lai: `./.venv/bin/python3 scripts/run_tier_a_scan.py`.

| Mau hinh | Trang thai | n_evaluated | n_flagged | Ghi chu |
|---|---|---:|---:|---|
| abnormal_cancel | Day du | 12.831 | 122 | Khop CHINH XAC voi public_frauds.parquet (122 dong) - phep kiem tra hoi quy |
| route_deviation | Day du (co caveat) | 87.857 | 878 | distance_km lech he thong -32% so voi road_km OSRM (co ve la duong chim, khong phai duong mang luoi) - xem route_deviation.py |
| gps_distance_inflation | Day du | 1.480.008 | 11.661 | Hard cap van toc theo loai xe (bike 70km/h, car 110km/h) |
| location_dropout | Day du | 78.920 | 789 | Khoang trong thoi gian giua 2 ban ghi hex, percentile p99 |
| split_long_trip | Day du (0 ket qua) | 0 | 0 | Logic dung, nhung customer_id KHONG BAO GIO lap lai trong mock hien co |
| wait_fee_manipulation | Proxy THO | 12.831 | 98 | Khong doi chieu duoc voi ly do hop le (ket xe) |
| ride_preview_selection | Proxy THO, mot chieu | 12.595 | 103 | Khong co log cuoc bi tu choi de doi chieu |
| fake_ride_for_kpi | Proxy THO | 1.719 | 0 | Z-score tren tien thuong tuan - 0 bat thuong tren du lieu mock nay |
| fake_no_show | STUB - thieu du lieu | - | - | trips.status khong co gia tri lien quan no-show |
| off_app_cash_pressure | STUB - thieu du lieu | - | - | Khong co trang thai 'arrived', khong co ban ghi cancelled |
| multi_account / cau ket | Chua dong (Tier C) | - | - | Khong co device_id/IMEI/IP trong bat ky bang nao |

## Phat hien phu khi trien khai (khong co trong artifact chien luoc goc)

1. **trips.status 100% la 'completed'** trong du lieu hien co - khong co dong
   `cancelled`/`assigned` nao du enum cho phep. Anh huong truc tiep den
   `off_app_cash_pressure` va `fake_no_show`.
2. **customer_id khong bao gio lap lai** (max_repeats=1 tren 174.233 dong) -
   `split_long_trip` dung logic nhung luon tra ve 0 tren du lieu mock nay.
3. **distance_km lech he thong ~-32%** so voi `road_km` cua OSRM - goi y
   `distance_km` duoc tinh theo duong chim (haversine) chu khong phai theo
   mang luoi duong nhu OSRM. Percentile van dung duoc (tu hieu chinh theo phan
   phoi) nhung khong nen doc `deviation_pct` nhu mot con so tuyet doi.
4. **driver_penalization_ATA chi co 1 gia tri `penalty_type`** trong du lieu
   hien co (`clawback_khoan`) trong so 5 gia tri enum - 4 loai con lai
   (`conduct`, `late`, `acceptance`, `other`) chua tung duoc sinh.
