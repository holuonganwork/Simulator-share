"""Registry ĐA PHIÊN BẢN + upcaster — gỡ B-02/ARCH-VERSION (Cycle V).

## Blocker (audit `01-*` §8, treo từ 2026-07-27)

Registry cũ load MỘT file/entity, `schema_version.const` một giá trị, `additionalProperties:
false` ⇒ minor bump chỉ là quy ước tác giả, KHÔNG backward-compatible ở runtime. Bằng chứng vi
phạm đang sống: CHANGELOG có 5 đợt additive mà const chưa từng bump — và Cycle R (2026-07-28)
thêm `rest_taken_min`/`shift_elapsed_min` vào `shift_plan_input` cũng vậy ⇒ cùng chuỗi "1.0.0"
đang mô tả nhiều hình dạng khác nhau. B-02 chặn ĐA-05 (event store append-only không replay
được qua migration) và T-044/ĐA-06.

## Cơ chế mới (phương án 3 của audit §8: file versioned + chọn theo RECORD)

- latest giữ nguyên tên `{entity}.schema.json`; lịch sử: `{entity}@{version}.schema.json`;
- `validate` route theo `record["schema_version"]`; version LẠ ⇒ lỗi tường minh (fail-loud,
  KHÔNG silent-fallback về latest — bài học hidden-fallback đã trả giá 3 lần);
- upcaster pure-function từng bậc, có test round-trip;
- backward-compat phải chứng minh bằng RECORD CŨ ĐÃ PERSIST (data/mock/realdata-v1), đúng câu
  audit chốt: *"Không được gọi minor bump 'backward compatible' cho tới khi test bằng record cũ
  thực sự pass"*.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gsm_core.schema_registry import ALL_ENTITIES, L1R_ENTITIES, SchemaRegistry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def reg():
    return SchemaRegistry(ROOT / "schemas")


def _spi(version: str, with_rest: bool) -> dict:
    d = {
        "schema_version": version, "driver_id": "d-1",
        "t_now": "2026-07-01T09:00:00+07:00", "buckets_remaining": 3,
        "soc_pct": 80.0, "points_now": 10,
        "demand_forecast": [{"bucket": "2026-07-01T09:00:00+07:00",
                             "cell_cluster": "ALL", "expected_orders": 2.0}],
        "policy_bundle_version": "sim-policy-v0",
        "view_version": "t", "source": "MOCK",
    }
    if with_rest:
        d["rest_taken_min"] = 30.0
        d["shift_elapsed_min"] = 120.0
    return d


# ---------- 1. Route theo version của RECORD ----------

def test_old_record_validates_against_its_own_version(reg):
    """Record 1.0.0 (hình dạng CŨ, không trường rest) phải VALID — nếu registry chỉ biết
    latest (1.1.0) thì const-mismatch làm nó fail ⇒ mọi record đã persist thành rác."""
    assert reg.validate("shift_plan_input", _spi("1.0.0", with_rest=False)) == []


def test_new_record_validates_against_latest(reg):
    assert reg.validate("shift_plan_input", _spi("1.1.0", with_rest=True)) == []


def test_lying_producer_is_caught(reg):
    """Record KHAI 1.0.0 nhưng MANG trường của 1.1.0 ⇒ phải INVALID.

    Đây là test giết mutation "route mọi thứ về latest": nếu route sai như vậy, record nói dối
    version vẫn pass và version trở thành trang sức. `additionalProperties:false` của schema
    1.0.0 chính là răng của phép kiểm này."""
    errs = reg.validate("shift_plan_input", _spi("1.0.0", with_rest=True))
    assert errs, "producer khai 1.0.0 mang trường 1.1.0 mà vẫn pass — route version là trang sức"


def test_unknown_version_fails_loud_with_known_list(reg):
    """Version LẠ ⇒ lỗi tường minh LIỆT KÊ version đã biết — không silent-fallback."""
    errs = reg.validate("shift_plan_input", _spi("9.9.9", with_rest=False))
    assert errs, "version lạ mà im lặng pass — hidden fallback"
    assert any("9.9.9" in e and "1.0.0" in e for e in errs), \
        f"lỗi không nêu version lạ + danh sách đã biết: {errs}"


# ---------- 2. versions() + latest ----------

def test_every_entity_has_versions_and_semver_latest(reg):
    import re
    for e in ALL_ENTITIES:
        vs = reg.versions(e)
        assert vs, f"{e}: không có version nào"
        assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in vs), f"{e}: version không semver: {vs}"
        assert reg.schema_version(e) == vs[-1], f"{e}: latest không phải cuối danh sách sort"


def test_shift_plan_input_has_two_versions(reg):
    assert reg.versions("shift_plan_input") == ("1.0.0", "1.1.0")


# ---------- 3. Upcaster ----------

def test_upcast_chain_and_roundtrip(reg):
    from gsm_core.upcasters import upcast
    old = _spi("1.0.0", with_rest=False)
    new = upcast("shift_plan_input", old)
    assert new["schema_version"] == "1.1.0"
    assert reg.validate("shift_plan_input", new) == [], "record sau upcast phải valid ở latest"
    # additive-optional: nội dung nghiệp vụ giữ nguyên
    assert {k: v for k, v in new.items() if k != "schema_version"} == \
           {k: v for k, v in old.items() if k != "schema_version"}


def test_upcast_does_not_mutate_input():
    from gsm_core.upcasters import upcast
    old = _spi("1.0.0", with_rest=False)
    snapshot = dict(old)
    upcast("shift_plan_input", old)
    assert old == snapshot, "upcast mutate input — pure function là hợp đồng"


def test_upcast_at_latest_is_identity():
    from gsm_core.upcasters import upcast
    new = _spi("1.1.0", with_rest=True)
    assert upcast("shift_plan_input", new) == new


# ---------- 4. Backward-compat bằng RECORD ĐÃ PERSIST (điều audit §8 đòi) ----------

def test_persisted_old_records_still_validate(reg):
    """Record THẬT đã persist trong data/mock/realdata-v2 phải validate pass qua registry
    hiện tại — đây là định nghĩa vận hành của 'backward compatible'.

    2026-09-29: đổi từ realdata-v1 sang realdata-v2 (v1 đã bị xoá khỏi repo, không còn
    dùng làm gì — xem configs/data_sources.yaml). Lưu ý: `trips` trong v2 đã ở schema
    1.1.0 (sau bump), không còn là mẫu 1.0.0 "trước bump" như bản gốc test này từng
    kiểm — 12 entity còn lại chưa từng bump nên vẫn giữ nguyên ý nghĩa kiểm tra cũ."""
    import json as _json

    import polars as pl

    def _decode(r: dict) -> dict:
        # Parquet không giữ cột NESTED dict/list — mockgen serialize chúng thành chuỗi JSON
        # (vd `public_mission.rewards`). Giải mã lại là việc của READER khi đọc persist,
        # không phải lỗi schema-versioning; validate phải chạy trên record ĐÃ giải mã.
        out = {}
        for k, v in r.items():
            if isinstance(v, str) and v[:1] in "{[":
                try:
                    out[k] = _json.loads(v)
                    continue
                except (ValueError, TypeError):
                    pass
            out[k] = v
        return out

    checked = 0
    v2_dir = ROOT / "data" / "mock" / "realdata-v2"
    v1_dir = ROOT / "data" / "mock" / "realdata-v1"
    base_dir = v2_dir if v2_dir.exists() else v1_dir
    for entity in L1R_ENTITIES:
        p = base_dir / f"{entity}.parquet"
        if not p.exists():
            continue
        rows = [_decode(r) for r in pl.read_parquet(p).head(3).to_dicts()]
        for r in rows:
            errs = reg.validate(entity, r)
            assert errs == [], f"{entity}: record persist cũ fail: {errs[:2]}"
        checked += len(rows)
    assert checked >= 20, f"chỉ kiểm được {checked} record — bộ persist thiếu, test mất răng"


# ---------- 4b. LAN CAN từ review đối kháng (mỗi cái đều được REPRODUCE trước khi thêm) ----------


def _mini_schemas(tmp_path):
    """Bản sao tối thiểu của schemas/ để tiêm file hỏng — không đụng repo thật."""
    import shutil
    d = tmp_path / "schemas" / "l3"
    d.mkdir(parents=True)
    for f in ("shift_plan_input.schema.json", "shift_plan_input@1.0.0.schema.json"):
        shutil.copy(ROOT / "schemas" / "l3" / f, d / f)
    return tmp_path / "schemas"


def test_garbage_snapshot_file_is_rejected_at_load(tmp_path):
    """File `@version` rác (const không khớp tên / `{}` trống) phải bị TỪ CHỐI lúc load.

    Reproduce của reviewer: `shift_plan_input@1.0.1.schema.json` chứa `{}` từng khiến record
    BỊA ĐẶT validate PASS im lặng (Draft202012Validator({}) nhận mọi thứ) — chính lớp lỗi
    hidden-pass mà B-02 sinh ra để diệt."""
    sdir = _mini_schemas(tmp_path)
    (sdir / "l3" / "shift_plan_input@1.0.1.schema.json").write_text("{}", encoding="utf-8")
    reg = SchemaRegistry(sdir)
    with pytest.raises(ValueError, match="1.0.1"):
        reg.versions("shift_plan_input")


def test_snapshot_const_must_match_filename(tmp_path):
    import json as _j
    sdir = _mini_schemas(tmp_path)
    s = _j.loads((sdir / "l3" / "shift_plan_input@1.0.0.schema.json").read_text(encoding="utf-8"))
    s["properties"]["schema_version"]["const"] = "9.9.9"
    (sdir / "l3" / "shift_plan_input@1.0.0.schema.json").write_text(
        _j.dumps(s), encoding="utf-8")
    reg = SchemaRegistry(sdir)
    with pytest.raises(ValueError, match="const trong file"):
        reg.versions("shift_plan_input")


def test_malformed_version_filename_names_the_file(tmp_path):
    """Tên file `@1.0.1-rc1` từng đánh sập MỌI validate của entity bằng ValueError KHÔNG nêu
    file nào (reproduce của reviewer) — fail-loud phải kèm ngữ cảnh lần ra thủ phạm."""
    sdir = _mini_schemas(tmp_path)
    (sdir / "l3" / "shift_plan_input@1.0.1-rc1.schema.json").write_text("{}", encoding="utf-8")
    reg = SchemaRegistry(sdir)
    with pytest.raises(ValueError, match="1.0.1-rc1"):
        reg.versions("shift_plan_input")


def test_null_schema_version_fails_loud(reg):
    """`schema_version: null` tường minh = producer nói 'không biết' ⇒ fail-loud như version
    lạ, không âm thầm route latest rồi trả const-mismatch khó hiểu."""
    errs = reg.validate("shift_plan_input", {**_spi("1.0.0", False), "schema_version": None})
    assert errs and "null" in errs[0], f"null version không fail-loud: {errs}"


def test_unknown_entity_is_a_clear_error(reg):
    with pytest.raises(ValueError, match="không có trong registry"):
        reg.validate("khong_ton_tai", {"schema_version": "1.0.0"})


def test_upcaster_that_forgets_to_stamp_fails_instead_of_hanging():
    """Upcaster quên bump version từng làm upcast() LẶP VÔ HẠN (reproduce: 10.001 vòng) —
    treo tệ hơn lỗi mù vì không có traceback. Nay phải ValueError nêu entity + version kẹt."""
    from gsm_core import upcasters as U
    key = ("shift_plan_input", "1.0.0")
    good = U.UPCASTERS[key]
    U.UPCASTERS[key] = lambda r: dict(r)          # quên stamp — lỗi kinh điển
    try:
        with pytest.raises(ValueError, match="không TIẾN"):
            U.upcast("shift_plan_input", _spi("1.0.0", False))
    finally:
        U.UPCASTERS[key] = good


# ---------- 5. Lỗ market_state_view (Explore agent tìm ra) ----------

def test_market_state_view_is_registered_and_valid(reg):
    """View L3 `market_state` từng emit `schema_version` mà KHÔNG có schema/entity nào —
    validate không thể chạm tới ⇒ mọi cam kết hình dạng của nó là lời nói suông."""
    from gsm_core.features.market_state import build_market_state
    assert "market_state_view" in ALL_ENTITIES, "market_state_view chưa vào LAYER_OF"
    v = build_market_state(
        t_now="2026-07-01T09:00:00+07:00", bucket_min=60,
        demand_by_cell={"a": 12.0, "b": 0.0},
        supply_now_by_cell={"a": 2, "b": 0},
        supply_incoming_by_cell={"a": 1, "b": 0},
        trips_per_driver_per_bucket=1.5, source="MOCK")
    assert reg.validate("market_state_view", v) == [], "payload thật của producer không khớp schema"
    v_absent = build_market_state(
        t_now="2026-07-01T09:00:00+07:00", bucket_min=60,
        demand_by_cell={"a": 12.0}, supply_now_by_cell=None,
        supply_incoming_by_cell=None, trips_per_driver_per_bucket=1.5, source="MOCK")
    assert reg.validate("market_state_view", v_absent) == [], "nhánh absent không khớp schema"
