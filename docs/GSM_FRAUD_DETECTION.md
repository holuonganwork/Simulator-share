# GSM_FRAUD_DETECTION - La Chan Cuoc Xe

Trien khai chien luoc phat hien gian lan tai xe cho GSM (Green SM): AI Agent goi
tool de dieu tra case, tren nen 4 tang xu ly (luat cung -> ML -> do thi quan he
-> agent). Repo nay TIEU THU du lieu tu repo `GSM_SIMULATOR` (phai clone o vi
tri "thu muc anh em", xem `configs/data_sources.yaml`).

Xem `docs/architecture.md` cho kien truc day du, `docs/data-matrix.md` cho tinh
trang tung mau hinh gian lan tren du lieu that, `docs/phase-status.md` cho tien
do theo 6 pha cua chien luoc goc, va `docs/telemetry-mock-service.md` cho
service mock telemetry thoi gian thuc (Buoc 3 cua nhanh nghien cuu telemetry).

## Cai dat

```bash
python3 -m venv .venv
./.venv/bin/pip install -e .

# De query_policy dung duoc kho chinh sach THAT (khuyen nghi):
./.venv/bin/pip install -e ../GSM_SIMULATOR-main
```

Yeu cau: `GSM_SIMULATOR` da duoc clone o `../GSM_SIMULATOR-main` (tuong doi so
voi thu muc nay), hoac dat bien moi truong `GSM_SIM_ROOT` tro dung.

## Chay thu

```bash
# Bao cao Tier A (8 mau hinh) + Tier B tren toan bo du lieu that
./.venv/bin/python3 scripts/run_tier_a_scan.py

# Chat tai xe (CLI demo)
./.venv/bin/python3 -m lcx_core.chat.driver_chat d-7 route_deviation

# Chat quan ly (CLI demo) - resolve/apply case
./.venv/bin/python3 -m lcx_core.chat.manager_chat d-7

# Bo test (43 test, chay tren du lieu that cua GSM_SIMULATOR + service mock telemetry)
./.venv/bin/python3 -m pytest -q

# Demo service mock telemetry thoi gian thuc (Buoc 3) - 30 tai xe ao, 30 giay,
# in bao cao do tre server-side
./.venv/bin/python3 scripts/run_telemetry_mock_demo.py --n-drivers 30 --duration 30
```

## Cau truc

```
src/lcx_core/
  data/       GsmDataStore - doc 13 bang Parquet + ma tran OSRM tu GSM_SIMULATOR
  tier_a/     8 luat nguong cung day du + 2 stub (thieu du lieu, xem docs/appendix-pending-data.md)
  tier_b/     IsolationForest cham diem rui ro tong hop tu feature Tier A
  tier_c/     stub - can du lieu device/IP tu GSM (chua co)
  agent/      orchestrator.py (pipeline tat dinh) + verification.py + 12 tool
  chat/       driver_chat.py, manager_chat.py (CLI demo)
  labels/     label_store.py - kho case + kho nhan (JSONL append-only, var/cases/)
  telemetry_mock/  Buoc 3: service FastAPI + producer mo phong telemetry thoi gian thuc
schemas/      JSON Schema cho case model, input/output cua 12 tool, va 2 hop dong telemetry
docs/         kien truc, ma tran du lieu, trang thai theo pha, phu luc du lieu con thieu,
              nghien cuu + service mock telemetry
tests/        43 test pytest chay TRUC TIEP tren du lieu that (khong mock rieng)
scripts/      run_tier_a_scan.py, run_telemetry_mock_demo.py - CLI bao cao tong hop
```

## Nguyen tac thiet ke

- **Khong bia du lieu.** Moi mau hinh khong co tin hieu that trong 13 bang duoc
  de la stub nem `MissingDataError` ro rang (xem `tier_a/fake_no_show.py`,
  `tier_a/off_app_cash_pressure.py`, `tier_c/device_account_graph.py`), khong
  gia lap logic tren truong du lieu khong ton tai.
- **Nguong tu du lieu, khong hardcode.** Moi nguong Tier A la percentile do tren
  chinh phan phoi that (`tier_a/base.py::percentile_threshold`), dung phuong
  phap da duoc GSM_SIMULATOR dung cho `abnormal_cancel` (p99 = 0.20 - repo nay
  tai tao dung con so nay, xem `tests/tier_a/test_tier_a_detectors.py`).
- **Fail-closed cho hanh dong that.** `apply_action` tu choi chay neu thieu
  `approver_id` hoac case chua `confirmed`. `notify_driver` tu choi gui neu tin
  chua tu khoa lo nguong hoac so lieu khong truy duoc nguon.
- **Trich dan chinh sach that.** `query_policy` uu tien doc kho chinh sach da
  crawl that cua GSM_SIMULATOR (`FALLBACK_POLICY_DOCS`), fallback ve mot vai
  trich dan da xac minh (khong bia) neu package do chua duoc cai.
