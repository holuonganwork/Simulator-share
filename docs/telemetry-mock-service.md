# Buoc 3 - service mock telemetry (da build)

Trien khai theo dung ke hoach o `docs/telemetry-research.md` va muc 12.3 cua
artifact chien luoc: mot service ingest that (khong phai doc Parquet tinh) +
mot producer mo phong tai xe di chuyen that tren luoi H3 that cua GSM_SIMULATOR.

## Chay demo 1 lenh

```bash
./.venv/bin/python3 scripts/run_telemetry_mock_demo.py --n-drivers 30 --duration 30
```

Script tu khoi dong service (uvicorn trong 1 thread), chay producer trong
`--duration` giay, roi in bao cao do tre server-side va doi chieu voi rang
buoc <100ms/ping (muc 03 chien luoc).

Ket qua tham khao (30 tai xe, 30s, seed=42):

| Chi so | Ping | Event |
|---|---:|---:|
| So luong | 240 | 97 |
| p50 (ms) | 3.5 | 2.3 |
| p95 (ms) | 7.5 | 4.9 |
| p99 (ms) | 13.1 | 6.9 |
| Ty le tu choi | 0% | 0% |

p99 = 13.1ms, dat rang buoc <100ms voi bien do rong - hop ly vi day la validate
JSON schema + ghi bo nho/JSONL trong cung 1 process, chua co do tre mang that
qua nhieu hop (service that se cong them do tre network/queue).

## Chay service doc lap (khong qua script demo)

```bash
./.venv/bin/uvicorn lcx_core.telemetry_mock.service:app --port 8077
```

Roi goi rieng producer tu tien trinh khac:

```python
from lcx_core.telemetry_mock.producer import run_producer
run_producer("http://127.0.0.1:8077", n_drivers=30, duration_seconds=30)
```

## Kien truc

```
src/lcx_core/telemetry_mock/
  schemas.py   - validate_ping/validate_event, nguon su that la 2 file JSON
                 Schema trong schemas/telemetry/ (Buoc 2), KHONG dinh nghia
                 lai field bang Pydantic o noi khac.
  store.py     - TelemetryStore: buffer bo nho + ghi JSONL (var/telemetry/) +
                 LatencyTracker (p50/p95/p99 tren toi da 5000 mau gan nhat).
  drivers.py   - may trang thai tai xe ao THUAN (khong goi mang) - xem so do
                 trang thai ben duoi. Tick 1 lan tra ve (pings, events) can gui.
  service.py   - FastAPI app: POST /telemetry/gps-ping, POST /telemetry/trip-event,
                 GET /telemetry/stats, GET /health.
  producer.py  - vong lap goi drivers.tick() cho N tai xe, POST qua HTTP that
                 (httpx) - day la "duong di du lieu" can do tre, khac voi goi
                 ham Python truc tiep.
```

### So do trang thai 1 tai xe ao (drivers.py)

```
ONLINE_IDLE --(cho ngau nhien)--> co hai nhanh:
  85%: OFFER_PENDING --(tu choi 10%)--> ONLINE_IDLE
                     --(chap nhan)--> ENROUTE_PICKUP --> ARRIVED
  15%: xanhnow_otp_matched (khop tai cho, khong qua dieu phoi) --> ARRIVED

ARRIVED --(no-show 5%, tru truong hop xanhnow)--> ONLINE_IDLE
        --(binh thuong)--> TRIP_ACTIVE

TRIP_ACTIVE --(huy giua chuyen 2%)--> ONLINE_IDLE
            --(hoan thanh)--> ONLINE_IDLE

Rieng: bat ky luc nao dang ONLINE_IDLE va dung tai station_hex voi
battery_pct < 20% -> SWAPPING (120-300s mo phong) -> ONLINE_IDLE, battery
nhay len 95-100%.
```

Di chuyen giua 2 diem dung `h3.grid_path_cells()` tren chinh 316 o hex that
lay tu `research/simulation/data/osrm_matrix_dd.parquet` cua GSM_SIMULATOR
(fallback ve 8 o cung quanh neu khong tim thay repo do) - xe di qua tung o hex
trung gian thay vi "day" thang tu diem A sang B.

## Loi da phat hien va sua khi build (dang ghi lai, khong giau di)

`schemas/telemetry/trip_lifecycle_event.schema.json` ban dau dung
`"const": 15` cho `offer_countdown_seconds`. JSON Schema doi khop `const` voi
CA gia tri `null`, nen moi event KHAC `offer_received` (dung `null` hop le cho
truong nay) deu bi tu choi sai - phat hien khi chay thu lan dau: 17/24 event bi
tu choi oan (`n_rejected: 17`). Da bo `const`, chi giu mo ta gia tri quan sat
duoc la 15. Test hoi quy:
`tests/telemetry_mock/test_service.py::test_event_with_null_countdown_accepted_regression`
va `tests/telemetry_mock/test_drivers.py::test_all_emitted_payloads_match_schema`
(validate MOI payload sinh ra qua dung 2 schema, se bat loi tuong tu ngay lap tuc).

Ngoai ra, ban nhap Buoc 2 ghi nham `battery_level_pct`/`charging_status` la
"chi co o xe hoi" - trong khi chinh nghien cuu V-Green (docs/telemetry-research.md)
noi ro xe may dien moi la doi tuong chinh cua doi pin. Da sua lai schema thanh
"co o ca hai loai xe" truoc khi viet producer, de tranh code producer dung
theo mo ta sai.

## Chua lam (Buoc 4, ngoai pham vi yeu cau lan nay)

Noi lai Tier A (`tier_a/gps_distance_inflation.py`, `location_dropout.py`) vao
luong nay thay vi doc Parquet tinh - can thiet ke lai cac ham `detect()` dang
nhan `GsmDataStore` (batch) de nhan them mot nguon "live" (vd doc lai tu
`var/telemetry/pings.jsonl` hoac subscribe truc tiep tu TelemetryStore). Chua
dong cham gi vao Tier A trong lan nay.
