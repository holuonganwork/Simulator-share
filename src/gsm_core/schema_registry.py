"""Schema registry — load + validate JSON Schema cho mọi entity L0–L3/advisor.

Spec: core-data-schema-and-advisor-architecture.md §1. Registry là NGUỒN SỰ THẬT
về contract dữ liệu; solver/agent/mockgen đều validate qua đây.

## ĐA PHIÊN BẢN (Cycle V, 2026-07-28 — gỡ B-02/ARCH-VERSION)

Bản cũ load MỘT file/entity và mọi schema mang `schema_version.const` một giá trị ⇒ đã có
**5 đợt additive ship mà const không đổi** (CHANGELOG) — "1.0.0" mô tả nhiều hình dạng, minor
bump chỉ là quy ước tác giả, record cũ không có gì bảo chứng. B-02 vì thế chặn ĐA-05 (event
store append-only phải replay được qua migration) và T-044/ĐA-06.

Cơ chế (phương án 3 của audit `01-*` §8 — file versioned, chọn theo RECORD):

- **latest** giữ nguyên tên `{entity}.schema.json`; **lịch sử** là `{entity}@{version}.schema.json`
  cùng thư mục (bump = snapshot hình cũ sang tên @, sửa latest + bump const, viết upcaster,
  CHANGELOG — quy trình đầy đủ trong `schemas/README.md`);
- `validate(entity, record)` đọc `record["schema_version"]` và route đúng schema phiên bản đó.
  **Version LẠ ⇒ lỗi tường minh kèm danh sách version đã biết** — KHÔNG silent-fallback về
  latest (hidden fallback là mẫu lỗi đã trả giá 3 lần: supply_hhi=0.0, soc=None→pin đầy,
  argmax bias). Record thiếu `schema_version` rơi về latest — schema nào cũng `required` trường
  này nên sẽ fail với thông điệp rõ.
- Upcaster (old→new, pure function) sống ở `gsm_core/upcasters.py`, có test round-trip.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

# E1a (UPDATE-151 r08 REVIEW-092-5): validator dựng KHÔNG format_checker ⇒ `format: date-time`
# trên 15 schema là chữ chết — `2029-02-31T00:00:00Z` lọt vào store append-only. ⚠ KHÔNG dùng
# `Draft202012Validator.FORMAT_CHECKER` suông: jsonschema chỉ kiểm date-time khi có gói
# `rfc3339-validator`, mà repo KHÔNG có (đã kiểm 2026-08-06: 'date-time' not in checkers) —
# truyền nó vào là placebo đúng họ D-R12. Đăng ký checker THẬT bằng `datetime.fromisoformat`
# (Python ≥3.11 nhận cả hậu tố 'Z' lẫn offset) — bắt được cả ngày không tồn tại lẫn chuỗi rác.
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _check_date_time(value):
    if not isinstance(value, str):
        return True                       # type sai đã có "type": "string" bắt — không kiểm kép
    datetime.fromisoformat(value.replace("Z", "+00:00") if value.endswith("Z") else value)
    return True

# entity -> layer folder (spec §1.2–1.5 + §2–3; đếm hiện tại: xem ALL_ENTITIES)
LAYER_OF: dict[str, str] = {
    "policy_bundle": "l0", "driver_profile": "l0", "station_registry": "l0",
    "zone_map": "l0", "service_catalog": "l0",
    "app_event": "l1", "trip_record": "l1", "gps_ping": "l1",
    "swap_transaction": "l1", "payout_ledger": "l1", "policy_change_event": "l1",
    "supply_field": "l2", "demand_field": "l2", "station_state": "l2",
    "driver_day_state": "l2",
    "inferred_activity": "l2i",
    "shift_plan_input": "l3", "bonus_gap_input": "l3",
    "session_summary_input": "l3", "allocation_input": "l3",
    # PI-4b: view cho S5 (khoán tuần) + S6 (mission knapsack)
    "weekly_khoan_input": "l3", "mission_select_input": "l3",
    "idle_reduction_input": "l3",  # PI-5b: S7 UC5
    "penalty_explain_input": "l3", "anomaly_alert_input": "l3",  # PI-5c: S8/S9 UC6/UC7
    # Cycle V (2026-07-28): view T-045a từng emit schema_version mà KHÔNG có schema/entity —
    # validate không thể chạm tới (lỗ do Explore agent tìm ra). Nay có cả hai.
    "market_state_view": "l3",
    "advice_request": "advisor", "solver_report": "advisor", "composed_advice": "advisor",
    # Cycle W (ĐA-05, 2026-07-29): event log lifecycle append-only — envelope audit 04-* §6
    "advice_lifecycle_event": "advisor",
    # AdviceCheckpoint shadow presentation lifecycle — tách khỏi adherence legacy.
    "advice_artifact": "advisor", "advice_checkpoint": "advisor",
    "advice_checkpoint_event": "advisor",
    # Composite episode = derived journey state feeding the checkpoint layer, not a card.
    "composite_episode": "advisor",
    "agent_presentation_input": "advisor", "agent_presentation_output": "advisor",
    "agent_explanation_input": "advisor", "agent_explanation_output": "advisor",
    # l1r = L1-real: mirror 13 bảng thật gsm-data-prod (re-ground — UPDATE-033, PI-1)
    "driver_statistic_daily": "l1r", "driver_online_hours_sap_id": "l1r",
    "driver_orders_rush_hours": "l1r", "driver_bike_stoppoints": "l1r",
    "kpi_driver_platform_calculator_gbq": "l1r", "driver_income_daily": "l1r",
    "trips": "l1r", "public_driver_hex_tracking": "l1r", "public_mission": "l1r",
    "public_mission_earn_history": "l1r", "public_user_mission_progress": "l1r",
    "driver_penalization_ATA": "l1r", "public_frauds": "l1r",
}
ALL_ENTITIES: list[str] = sorted(LAYER_OF)
L1R_ENTITIES: list[str] = sorted(e for e, layer in LAYER_OF.items() if layer == "l1r")


def _semver_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _const_of(schema: dict, where: str) -> str:
    """Đọc `properties.schema_version.const` — lỗi PHẢI nêu file/ngữ cảnh.

    Review đối kháng Cycle V reproduce được: thiếu const ⇒ bare `KeyError('const')` không nêu
    entity/file nào — fail-loud nhưng MÙ, người vận hành không lần ra thủ phạm."""
    try:
        c = schema["properties"]["schema_version"]["const"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{where}: schema thiếu properties.schema_version.const "
                         f"({exc!r}) — mọi schema của registry bắt buộc có") from exc
    return str(c)


class SchemaRegistry:
    def __init__(self, schemas_dir: str | Path):
        self.dir = Path(schemas_dir)

    def _layer(self, entity: str) -> str:
        layer = LAYER_OF.get(entity)
        if layer is None:
            raise ValueError(f"entity '{entity}' không có trong registry "
                             f"(LAYER_OF có {len(LAYER_OF)} entity — xem ALL_ENTITIES)")
        return layer

    @lru_cache(maxsize=None)
    def versions(self, entity: str) -> tuple[str, ...]:
        """Mọi phiên bản đã biết của entity, sort semver tăng dần (cuối = latest).

        latest = const của `{entity}.schema.json`; lịch sử = các file `{entity}@{v}.schema.json`.
        Hai lan can từ review đối kháng (đều đã reproduce trước khi thêm):
        - tên file `@version` không phải semver X.Y.Z (vd `@1.0.1-rc1`) từng đánh sập MỌI
          validate của entity bằng ValueError không ngữ cảnh — nay lỗi nêu ĐÚNG file;
        - file `@version` có const KHÔNG khớp tên file (hoặc `{}` rác) từng khiến record bịa
          validate PASS im lặng — nay từ chối ngay lúc liệt kê.
        """
        layer = self._layer(entity)
        latest = _const_of(self.schema(entity), f"{entity} (latest)")
        hist = []
        for p in (self.dir / layer).glob(f"{entity}@*.schema.json"):
            v = p.stem.replace(".schema", "").split("@", 1)[1]
            try:
                _semver_key(v)
            except ValueError as exc:
                raise ValueError(f"tên file version không phải semver X.Y.Z: {p} "
                                 f"('{v}') — đổi tên hoặc xoá file") from exc
            file_const = _const_of(json.loads(p.read_text(encoding="utf-8")), str(p))
            if file_const != v:
                raise ValueError(f"{p}: const trong file = '{file_const}' ≠ version trên tên "
                                 f"file = '{v}' — file snapshot hỏng, không nạp")
            hist.append(v)
        return tuple(sorted({*hist, latest}, key=_semver_key))

    @lru_cache(maxsize=None)
    def schema(self, entity: str, version: str | None = None) -> dict:
        """Schema của entity ở `version`; None = LATEST (file không hậu tố)."""
        layer = self._layer(entity)
        if version is None:
            path = self.dir / layer / f"{entity}.schema.json"
        else:
            latest_path = self.dir / layer / f"{entity}.schema.json"
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            if _const_of(latest, f"{entity} (latest)") == version:
                return latest
            path = self.dir / layer / f"{entity}@{version}.schema.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"{entity}: không có schema cho version '{version}' tại {path} — "
                    f"các version đã biết: {list(self.versions(entity))}")
        return json.loads(path.read_text(encoding="utf-8"))

    @lru_cache(maxsize=None)
    def _validator(self, entity: str, version: str | None = None) -> Draft202012Validator:
        # format_checker: xem comment `_FORMAT_CHECKER` đầu file (E1a — date-time phải có răng)
        return Draft202012Validator(self.schema(entity, version),
                                    format_checker=_FORMAT_CHECKER)

    def schema_version(self, entity: str) -> str:
        """Phiên bản LATEST (const của file không hậu tố) — giữ nguyên ngữ nghĩa API cũ."""
        return _const_of(self.schema(entity), f"{entity} (latest)")

    def validate(self, entity: str, record: dict) -> list[str]:
        """Trả list lỗi (rỗng = hợp lệ). Route theo `record["schema_version"]`.

        - version đã biết ⇒ validate ĐÚNG schema phiên bản đó (record cũ được xét theo hợp
          đồng THỜI ĐIỂM NÓ SINH RA — định nghĩa vận hành của backward compatibility);
        - version LẠ ⇒ fail-loud kèm danh sách đã biết;
        - thiếu `schema_version` ⇒ xét theo latest (mọi schema `required` trường này nên sẽ
          fail với thông điệp rõ, không im lặng).
        """
        rv = record.get("schema_version") if isinstance(record, dict) else None
        known = self.versions(entity)
        # `schema_version: null` TƯỜNG MINH ≠ thiếu khoá: null là producer chủ động nói "không
        # biết version" — phải fail-loud như version lạ, không âm thầm route về latest rồi để
        # const-mismatch trả thông điệp khó hiểu ("'1.1.0' was expected"). Review đối kháng
        # reproduce case này.
        if isinstance(record, dict) and "schema_version" in record and rv is None:
            return [f"schema_version: null — producer phải khai version thật; các phiên bản "
                    f"đã biết của {entity}: {list(known)}"]
        if rv is not None and rv not in known:
            return [f"schema_version: '{rv}' không nằm trong các phiên bản đã biết của "
                    f"{entity} {list(known)} — không fallback về latest (fail-loud)"]
        use = rv if (rv is not None and rv != known[-1]) else None
        return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
                for e in self._validator(entity, use).iter_errors(record)]

    def validate_many(self, entity: str, records: list[dict]) -> dict[int, list[str]]:
        """Validate hàng loạt: {index: errors} chỉ chứa record lỗi."""
        out: dict[int, list[str]] = {}
        for i, r in enumerate(records):
            errs = self.validate(entity, r)
            if errs:
                out[i] = errs
        return out
