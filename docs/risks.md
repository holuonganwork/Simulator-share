# Rui ro va gioi han cua thiet ke hien tai

## Rui ro van hanh (tu chien luoc goc, van dung)

- **Gan co oan anh huong thu nhap that.** `apply_action` fail-closed
  (`approver_id` bat buoc, case phai `confirmed`) chinh la co che giam thieu -
  xem `tests/agent/test_case_lifecycle.py` cho bo test day du cac duong tu choi.
- **Ke gian thich nghi voi nguong cong khai.** `notify_driver` chan tu khoa lo
  ky thuat phat hien (`BLOCKED_KEYWORDS` trong `notify_driver.py`) - danh sach
  nay CAN duoc mo rong dinh ky khi phat hien cach dien dat moi co the lo thong tin.
- **Du lieu vi tri nhay cam.** `var/cases/*.jsonl` va `var/chat/*.jsonl` chua
  driver_id va toa do hex - repo hien KHONG ma hoa/an danh hoa cac file nay,
  can bo sung truoc khi dung voi du lieu that (khac voi mock).

## Gioi han rieng cua trien khai nay (khong co trong chien luoc goc)

- **`verify_message` so sanh chuoi ky tu, khong parse gia tri.** "0.4" va "40%"
  duoc coi la hai con so khac nhau du cung mot gia tri - nguoi soan message
  phai dung DUNG dinh dang so nhu trong evidence. Day la lua chon co chu dich
  (fail-closed: tha chan nham con hon lot mot so bia), nhung co the gay kho
  chiu khi dung that - xem `tests/agent/test_case_lifecycle.py::test_notify_driver_blocks_unverifiable_numbers`.
- **Tier B (IsolationForest) chua co nhan doc lap de danh gia.** Diem rui ro
  hien la THU HANG tuong doi, khong phai xac suat gian lan da hieu chuan -
  xem `docs/metrics.md`.
- **`map_match_trace` va `check_location_integrity` dung proxy tren luoi H3
  res-9 theo phut**, khong phai GPS lien tuc - co the bo sot spoofing tinh vi
  o quy mo nho hon 1 o hex (~170m canh o Ha Noi).
- **label_store la JSONL append-only tren dia, khong phai DB that.** Phu hop
  quy mo demo/nghien cuu; can chuyen sang DB that (Postgres...) truoc khi dua
  vao san xuat co nhieu quan ly dong thoi (rui ro ghi de/race condition khong
  duoc xu ly o day).

## Agent khong phu hop cho chan real-time

Khong doi - `orchestrator.investigate()` chi duoc goi cho case da qua Tier A,
khong bao gio duoc goi tren tung GPS ping. Xem `docs/architecture.md`.
