# Ban do 15 mau hinh gian lan theo 2 giai doan

## Giai doan cho cuoc (truoc khi khach len xe)

| Mau hinh | Trien khai | Module |
|---|---|---|
| Huy cuoc co chon loc | Day du | `tier_a/abnormal_cancel.py` |
| "Xem truoc" don de chon loc cuoc | Proxy tho, mot chieu | `tier_a/ride_preview_selection.py` |
| Cuoc ao de dat KPI thuong | Proxy tho | `tier_a/fake_ride_for_kpi.py` |
| Gia dinh vi (GPS spoofing) | Gop vao gps_distance_inflation (chung co che voi trong chuyen) | `tier_a/gps_distance_inflation.py` |
| Da tai khoan / thue tai khoan | Thieu du lieu (Tier C) | `tier_c/device_account_graph.py` (stub) |
| Giu trang thai online "ao" | **Moi (2026-09-18, hang muc 4 lam giau du lieu)** - Precision/Recall do duoc qua du lieu tiem, xem `docs/injector-eval-results.md` | `telemetry_tier/virtual_online_status.py` |

## Giai doan trong chuyen (tu luc don den khi tra khach)

| Mau hinh | Trien khai | Module |
|---|---|---|
| Di vong tang cuoc (long-hauling) | Day du (co caveat ve don vi khoang cach) | `tier_a/route_deviation.py` |
| Gia GPS phong dai quang duong | Day du | `tier_a/gps_distance_inflation.py` |
| Tach 1 cuoc dai thanh nhieu cuoc ngan | Day du (0 ket qua tren mock hien co - xem data-matrix.md) | `tier_a/split_long_trip.py` |
| Ngat dinh vi giua chuyen | Day du | `tier_a/location_dropout.py` |
| Thao tung phi cho | Proxy tho | `tier_a/wait_fee_manipulation.py` |
| Ket thuc chuyen som tren app | Chua lam - khong co field doi chieu | - |
| Ep huy de chuyen tien mat | STUB - thieu trang thai 'arrived' | `tier_a/off_app_cash_pressure.py` |
| Bao "khach khong den" gia | STUB - khong co field no-show | `tier_a/fake_no_show.py` |
| Cau ket chay cuoc khong | Thieu du lieu (Tier C) | `tier_c/device_account_graph.py` (stub) |

Chi tiet co che + tin hieu ly thuyet cho tung mau hinh: xem artifact chien luoc
goc (link chia se dau du an). Bang tren chi tap trung vao TRANG THAI TRIEN KHAI
THAT trong repo nay.

## Mau hinh moi tu "Chien luoc lam giau du lieu" (mentor giao 2026-09-18)

Ngoai 15 mau hinh goc o tren, 8 hang muc lam giau du lieu (tru mo rong ban do
Ha Noi va ban do tram sac - ngoai pham vi lan nay) mo them 4 mau hinh moi
HOAN TOAN, chua tung co trong tai lieu chien luoc goc:

| Mau hinh | Trien khai | Module | Nguon du lieu |
|---|---|---|---|
| Can thiep cam bien "Mat than" | Day du, Precision/Recall do duoc | `telemetry_tier/camera_intervention.py` | `lcx_core.event_sim` (sinh moi, khong co du lieu that de doi chieu) |
| Xac thuc doi pin hop le | Day du (hard-cap vat ly) | `telemetry_tier/battery_swap_validation.py` | `lcx_core.event_sim` |
| Xe "ma" (VIN khong khop dang ky) | Day du (kiem tra toan ven du lieu) | `telemetry_tier/ghost_vehicle.py` | `lcx_core.event_sim` |
| Thong dong tao tang gia gia | Proxy THO (xem CAVEAT trong module) | `derived_tier/artificial_surge_collusion.py` | `lcx_core.derived_data.fare_breakdown` (dan xuat tu du lieu that + rain mo phong lai) |

## Mau hinh F1/F3/F4/F5/F6 cua Taxi Co Huu (FRAUDS.md, 2026-09-24)

Sinh boi `lcx_core.event_sim.vehicle_scenarios` (bang `trips` co cuoc huy + ly do, `telemetry` ODO/toc do/ghe sau, `declared_shifts`), detector trong `telemetry_tier/`. F2 dung `tier_a/route_deviation.py`.

| Ma | Mau hinh | Trien khai | Module |
|---|---|---|---|
| F1 | Cho khach khong tao cuoc | Day du (du lieu tong hop) | `telemetry_tier/off_app_pickup.py` |
| F2 | Keo dai hanh trinh | Xem tier_a (caveat distance_km duong chim) | `tier_a/route_deviation.py` |
| F3 | Mat tin hieu GPS/TCU | Day du (du lieu tong hop) | `telemetry_tier/tcu_signal_loss.py` |
| F4 | Dung xe ca nhan ngoai ca | Day du (du lieu tong hop) | `telemetry_tier/personal_use.py` |
| F5 | Tao cuoc xe ao | Day du (du lieu tong hop; chua co tien bu san) | `telemetry_tier/fake_trip.py` |
| F6 | Huy sai ly do / ken cuoc | Day du (du lieu tong hop) | `telemetry_tier/invalid_driver_cancel.py` |

Ket qua Precision/Recall day du + gioi han cua tung mau hinh: xem
`docs/injector-eval-results.md`.
