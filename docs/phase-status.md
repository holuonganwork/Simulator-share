# Trang thai theo 6 pha cua chien luoc goc

| Pha | Noi dung | Trang thai trong repo nay |
|---|---|---|
| 0 | Xac nhan nen du lieu | **Xong.** `src/lcx_core/data/gsm_loader.py` doc that ca 13 bang + ma tran OSRM, `GsmDataStore.describe()` xac nhan schema/so dong. |
| 1 | Luat cung Tier A | **8/10 xong day du**, 2 la proxy "THO" co tai lieu ro (`wait_fee_manipulation`, `ride_preview_selection`, `fake_ride_for_kpi` cung la proxy). 2 mau hinh (`fake_no_show`, `off_app_cash_pressure`) la stub - xem `docs/appendix-pending-data.md`. |
| 2 | Tang cuong ML | **Xong o muc co ban.** `src/lcx_core/tier_b/risk_scorer.py` dung IsolationForest (khong giam sat, vi chua co nhan doc lap du) tren feature gop tu Tier A. Can thay bang mo hinh giam sat khi co du case `confirmed`. |
| 3 | Agent dieu phoi | **Xong khung.** `src/lcx_core/agent/orchestrator.py` la pipeline TAT DINH (thu tu co dinh, khong de LLM tu chon tool), `verification.py` chan message chua so lieu khong nguon. Chua noi LLM that (khong co API key trong repo). |
| 4 | Hai giao dien chat | **Demo CLI hoat dong.** `src/lcx_core/chat/driver_chat.py`, `manager_chat.py` - da test end-to-end voi du lieu that, bao gom `flag_case` -> `resolve_case` -> `apply_action`. Chua co giao dien web/app that. |
| 5 | Vong phan hoi | **Ha tang san sang, chua co du lieu de retrain.** `src/lcx_core/labels/label_store.py` la kho nhan append-only. `scripts/` chua co script retrain vi chua co du case `confirmed` that (can van hanh Pha 4 truoc). |

## Pha 2.5 - Chien luoc lam giau du lieu (mentor giao 2026-09-18)

Phu luc song song voi Pha 2, tach thanh 4 pha con A-D (xem artifact chien luoc
lam giau du lieu). Trang thai:

| Pha con | Noi dung | Trang thai |
|---|---|---|
| A | Fraud injector cho 7 mau hinh Tier A da co luat (uu tien cao nhat) | **Xong.** `src/lcx_core/fraud_injector/` + `scripts/run_injector_eval.py`. Lan dau tien co Precision/Recall do duoc cho 6/7 mau hinh. |
| B | Mo rong action/state + telemetry (giu online ao) | **Xong.** `src/lcx_core/event_sim/` + `telemetry_tier/virtual_online_status.py`. |
| C | Sensor/camera + ban do sac | **Xong PHAN LOG/detector** (camera_intervention, battery_swap_validation, ghost_vehicle). Mo rong ban do tram sac moi ngoai pham vi (theo chi dao). |
| D | Hanh trinh vi tri + thoi tiet->gia + khong gian/ho so | **Xong PHAN KHONG GIAN HIEN CO** (`derived_data/`: fare_breakdown + artificial_surge_collusion, trip_waypoints, driver_profile_enriched). Mo rong ban do ra toan Ha Noi ngoai pham vi (theo chi dao). |

Ket qua Precision/Recall day du cho ca 3 pha con B/C/D + cac loi da phat hien/
sua khi xay dung: xem `docs/injector-eval-results.md` (muc "Khoi 1"/"Khoi 2").

## Kiem tra

```bash
./.venv/bin/python3 -m pytest -q                       # 71 test, tat ca chay tren du lieu that/sinh co kiem soat
./.venv/bin/python3 scripts/run_tier_a_scan.py          # bao cao Tier A + Tier B day du
./.venv/bin/python3 scripts/run_injector_eval.py        # Precision/Recall Pha A (7 mau hinh Tier A)
./.venv/bin/python3 scripts/run_telemetry_tier_eval.py  # Precision/Recall Khoi 1 (4 mau hinh moi)
./.venv/bin/python3 scripts/run_surge_collusion_eval.py # Precision/Recall Khoi 2 (artificial_surge_collusion)
./.venv/bin/python3 scripts/run_waypoint_accuracy_check.py     # so sanh do chinh xac route_deviation 2-diem vs ca lo trinh
./.venv/bin/python3 scripts/build_driver_profile_enriched.py   # sinh data/derived/driver_profile_enriched.parquet
```
