# schemas/ — Data schema registry (T-038 C0)

Nguồn sự thật về contract dữ liệu core. Spec: `specs/core-data-schema-and-advisor-architecture.md`.
Validate qua `gsm_core.schema_registry.SchemaRegistry` — solver/agent/mockgen, UI backend
(`POST /api/v1/advice/action`) và sim đều đi qua đây.

## Cấu trúc tầng

| Folder | Tầng | Tính chất |
|---|---|---|
| `l0/` | Reference (policy, profile, station, zone, service) | slowly-changing, versioned |
| `l1/` | Event log (app events, trips, GPS, swap, ledger, policy change) | **immutable, observable-only — CẤM latent** |
| `l2/` | State fields (supply/demand/station/driver-day) | derivation job có version |
| `l2i/` | Inferred views | **tầng RIÊNG — nhãn INFERRED + rule version bắt buộc** |
| `l3/` | Feature views cho solver | read-only |
| `advisor/` | I/O pipeline (request / solver report / composed advice) + lifecycle event log legacy (ĐA-05). AdviceCheckpoint v2 dùng stream riêng `advice_artifact@1.1`, `advice_checkpoint@1.1`, `advice_checkpoint_event@1.1`, presentation schemas đóng và identity `checkpoint_id` riêng; không overload/backfill `decision_id`, không dual-write legacy lifecycle. Metrics vẫn báo riêng `decision_adherence`, `event_adherence`, `accept_rate`, `execution_rate` | đóng băng contract |

## Quy ước (spec §1.6)

- **Versioning:** `schema_version` semver per entity. Thêm field = optional + minor bump.
  Bỏ field = đánh `deprecated_since` trong description + giữ ≥1 chu kỳ, KHÔNG xóa thẳng.
  **ĐA PHIÊN BẢN từ 2026-07-28 (Cycle V — gỡ B-02/ARCH-VERSION):** registry route validate theo
  `record["schema_version"]`; file **latest** giữ tên `{entity}.schema.json`, phiên bản **lịch
  sử** là `{entity}@{version}.schema.json` cùng thư mục; version lạ ⇒ **fail-loud** kèm danh
  sách đã biết (không silent-fallback). Upcaster từng-bậc, pure-function ở
  `src/gsm_core/upcasters.py`. Backward compatibility là ĐIỀU ĐƯỢC TEST bằng record đã persist
  (`tests/test_schema_versioning.py::test_persisted_old_records_still_validate`), không còn là
  quy ước tác giả. Bump đầu tiên: `shift_plan_input` 1.0.0 → 1.1.0 (2 trường rest, Cycle R).
- **`source` bắt buộc** mọi record: `MOCK | REAL | ESTIMATED | COARSE | INFERRED`
  (CLAUDE.md §5 — mock phải gắn nhãn; không trộn mock với data thật).
- **`x-sensitivity`**: field PII (driver_id, lat/lon thô) — cơ chế thu hẹp/anonymize sau.
- **`x-availability: TBC-với-GSM`**: field chưa chắc GSM export được — bảng fallback
  trong spec §1.6 (ESTIMATED/COARSE labels).
- **Latent cấm ở L1** (taxonomy reliability-upgrade §3.5): meals/fatigue/belief/patience
  là sim-only ground truth — test `test_latent_fields_absent_from_l1` enforce.
- **⚠ CẢNH BÁO (quan trọng nhất):** `SchemaRegistry` dựng `Draft202012Validator` **KHÔNG có
  `format_checker`** ⇒ mọi `"format": "date-time"` trong schema chỉ là TÀI LIỆU, **không chặn
  gì** ở validate-time. Lớp chặn thật là `pattern` regex + parse thật (`datetime.fromisoformat`)
  tại boundary (`event_log.append`) — bản thân regex không kiểm được lịch hợp lệ (vd
  `2026-02-31` vẫn khớp mọi pattern, xem X-1).
- **Data quality assumption:** L1 nhận data ĐÃ clean/normalize (dedup, UTC+7, hợp lệ)
  — pipeline ingest ngoài scope (spec §0).
- Sim và data thật GSM dùng CÙNG schema — chỉ đổi `source`.

## Sửa schema thế nào (quy trình bump từ Cycle V)

1. **Snapshot hình dạng cũ** thành `{entity}@{version_cũ}.schema.json` — hình dạng cũ lấy từ
   **git HEAD trước thay đổi** (`git show HEAD:schemas/...`), KHÔNG copy file hiện tại: nếu
   hình dạng đã bị sửa tại chỗ trước đó (như Cycle R từng làm) thì file hiện tại KHÔNG còn là
   hình dạng cũ. Thêm hậu tố `@{version}` vào `$id`. ⚠ Registry TỪ CHỐI file snapshot có const
   không khớp version trên tên file — đặt sai là fail ngay lúc load, không âm thầm.
2. Sửa file latest `.schema.json` + **bump `schema_version.const`** theo semver. Narrowing
   bugfix **KHÔNG bump** ⇒ **PHẢI khai lý do tường minh trong CHANGELOG** + chứng minh không
   record persist nào mang giá trị bị siết (đồng bộ với entry CHANGELOG 2026-07-29 muộn đang
   viện dẫn quy tắc này).
3. Viết **upcaster** `({entity}, {version_cũ})` trong `src/gsm_core/upcasters.py` — additive
   optional ⇒ chỉ stamp version; đổi ngữ nghĩa ⇒ dịch dữ liệu thật (và cân nhắc MAJOR).
4. Cập nhật `LATEST_VERSIONS` trong `tests/test_schemas.py` (pin tường minh) + `CHANGELOG.md`
   (version, ngày, lý do, tương thích).
5. Chạy `uv run pytest tests/test_schema_versioning.py tests/test_schemas.py tests/test_mockgen.py`
   — record persist cũ PHẢI còn validate pass (định nghĩa vận hành của backward-compat).
6. Biến mới phải điền được bảng traceability spec §1.7 (feature + pain hoặc lý-do-hạ-tầng) — điều kiện T-039.
