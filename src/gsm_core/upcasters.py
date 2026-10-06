"""Upcaster — nâng record phiên bản CŨ lên hình dạng LATEST, từng bậc, pure function.

Cycle V (2026-07-28, gỡ B-02): consumer cần hình dạng mới (vd ĐA-05 replay event store qua
migration) gọi `upcast(entity, record)`; validate thuần tuý thì KHÔNG cần upcast —
`SchemaRegistry.validate` đã route theo version của record.

Quy tắc viết upcaster (T-044 sẽ dựa vào đây cho envelope v2):

1. **Pure**: không mutate input, trả dict MỚI. Có test canh.
2. **Từng bậc**: `(entity, from_version) -> record ở version KẾ TIẾP`; chuỗi dài do `upcast`
   tự nối — không viết upcaster nhảy cóc (n² hàm khi nhiều version).
3. Additive-optional ⇒ upcaster chỉ stamp version (trường optional vắng mặt vẫn hợp lệ ở
   latest). Đổi NGỮ NGHĨA/xoá trường ⇒ upcaster phải dịch dữ liệu thật và đó là dấu hiệu nên
   cân nhắc MAJOR bump thay vì minor.
"""

from __future__ import annotations

from typing import Callable

# (entity, from_version) -> hàm nâng lên version KẾ TIẾP trong registry.versions(entity)
UPCASTERS: dict[tuple[str, str], Callable[[dict], dict]] = {}


def _register(entity: str, from_version: str):
    def deco(fn):
        UPCASTERS[(entity, from_version)] = fn
        return fn
    return deco


@_register("trips", "1.0.0")
def _trips_100_to_110(record: dict) -> dict:
    """1.0.0 → 1.1.0 (thêm `cancel_reason`/`cancelled_by` optional). Record cũ là cuốc hoàn thành
    hoặc chưa biết lý do hủy ⇒ KHÔNG bịa lý do. Chỉ stamp version."""
    return {**record, "schema_version": "1.1.0"}


@_register("policy_bundle", "1.0.0")
def _pb_100_to_110(record: dict) -> dict:
    """1.0.0 → 1.1.0 (B3 thêm khối `costs` optional). Record cũ không biết chi phí ⇒
    KHÔNG bịa costs (vắng mặt = `resolve_cost_params` trả UNKNOWN — đúng sự thật record
    đó mang). Chỉ stamp version."""
    return {**record, "schema_version": "1.1.0"}


@_register("bonus_gap_input", "1.0.0")
def _bgi_100_to_110(record: dict) -> dict:
    """1.0.0 → 1.1.0 (`D-ADV-04` thêm `historical_rate_method`/`historical_rate_days`, additive-optional).

    ⚠ Record 1.0.0 được sinh bởi quy ước mẫu số **CŨ và SAI** (điểm-của-bucket ÷ giờ online
    TOÀN NGÀY) ⇒ `historical_points_per_hour` của nó **ước NON 2–5×**. Nhưng upcaster **KHÔNG
    được sửa số**: nó không biết giờ-trong-bucket của những ngày đã trôi qua, và bịa ra một hệ số
    quy đổi là đúng cái lỗi "hidden fallback" repo đã trả giá. Cũng **KHÔNG** stamp
    `historical_rate_method` — record cũ **không biết** mẫu số của nó được suy thế nào; vắng mặt là
    sự thật, `day_average_mixed` là suy đoán. Chỉ stamp version.
    """
    return {**record, "schema_version": "1.1.0"}


@_register("allocation_input", "1.0.0")
def _ai_100_to_110(record: dict) -> dict:
    """1.0.0 → 1.1.0 (Cycle 8 thêm `tier_gap_points` per-candidate và `tier_weight`, additive-optional).

    **KHÔNG bịa `tier_gap_points`.** Record 1.0.0 không biết lúc đó tài xế cách mốc bao xa, và
    `0` là một khẳng định CỤ THỂ ("đang đứng đúng mốc") chứ không phải "không biết" — đúng mẫu
    hidden-fallback repo đã trả giá (`soc=None` → đọc thành pin đầy).

    **KHÔNG bịa `tier_weight` = 0** dù đó là mặc định: record cũ được sinh khi hạng tử này CHƯA
    TỒN TẠI, khác với "đã tồn tại và bị tắt". Vắng mặt giữ đúng sự thật đó; consumer đọc vắng mặt
    thành 0 là quyết định của consumer, không phải của upcaster.
    """
    return {**record, "schema_version": "1.1.0"}


@_register("shift_plan_input", "1.0.0")
def _spi_100_to_110(record: dict) -> dict:
    """1.0.0 → 1.1.0 (Cycle R thêm `rest_taken_min`/`shift_elapsed_min`, additive-optional).

    Record 1.0.0 không biết nghỉ-đã-nghỉ ⇒ KHÔNG bịa giá trị (đặt 0.0 là khẳng định "chưa nghỉ
    phút nào" — sai sự thật). Vắng mặt = `_required_rest` dùng công thức mù-state cũ, đúng
    hành vi record đó từng có. Chỉ stamp version."""
    return {**record, "schema_version": "1.1.0"}


@_register("advice_checkpoint", "1.0.0")
def _checkpoint_100_to_110(record: dict) -> dict:
    """Separate-stream trace refs were unknown in 1.0; preserve that uncertainty."""
    return {
        **record,
        "schema_version": "1.1.0",
        "source_decision_id": None,
        "run_id": None,
        "solver_input_refs": [],
        "solver_report_refs": [],
    }


@_register("advice_checkpoint", "1.1.0")
def _checkpoint_110_to_120(record: dict) -> dict:
    """1.2 mang numbers/caveats của solver report + fingerprint vào record.

    Record 1.1 không lưu numbers/caveats ⇒ danh sách RỖNG là sự thật của record đó
    (muốn đủ thì đọc lại artifact qua `solver_report_refs`). `fingerprint` thì TÁI LẬP
    được chính xác từ các material field đã persist — dùng đúng hàm production để
    dedup product khớp giữa record cũ và candidate mới."""
    from gsm_core.lifecycle.checkpoint import checkpoint_fingerprint
    return {**record, "schema_version": "1.2.0",
            "numbers": [], "caveats": [],
            "fingerprint": checkpoint_fingerprint(record)}


@_register("advice_checkpoint", "1.2.0")
def _checkpoint_120_to_130(record: dict) -> dict:
    """1.3 adds semantic numbers plus explicit output-intent/evidence quality.

    Intent and decision identity are reconstructable from the canonical action.  Signal
    quality is not: 1.2 did not persist the semantic signal set, so replay must say
    ``UNMEASURED`` rather than upgrading an old record to sufficient evidence.
    """
    from gsm_core.advisor.checkpoint_intents import enrich_candidate_intent

    enriched = enrich_candidate_intent(record)
    enriched["schema_version"] = "1.3.0"
    enriched["evidence_quality"] = {
        **enriched["evidence_quality"],
        "status": "UNMEASURED",
        "available_signal_ids": [],
        "reasons": ["legacy_record_signal_quality_not_stored"],
    }
    return enriched


@_register("advice_checkpoint_event", "1.0.0")
def _checkpoint_event_100_to_110(record: dict) -> dict:
    """1.1 adds an event enum value but does not reinterpret old events."""
    return {**record, "schema_version": "1.1.0"}


@_register("advice_artifact", "1.0.0")
def _advice_artifact_100_to_110(record: dict) -> dict:
    """1.1 only adds the agent-shadow artifact enum; old payloads are unchanged."""
    return {**record, "schema_version": "1.1.0"}


@_register("composite_episode", "1.0.0")
def _composite_episode_100_to_110(record: dict) -> dict:
    """1.1 adds two lifecycle levels, one episode type rename, and a supersede pointer.

    ``rest_debt_vs_idle`` becomes ``waiting_and_rest_record``: the same subject under a
    name that does not assert a shortfall.  The rename is applied here rather than left to
    readers so a 1.0 record replays under one vocabulary.  ``supersedes_checkpoint_id`` is
    null for every 1.0 record — nothing superseded a card before this version existed, so
    there is nothing to reconstruct.
    """
    renamed = ("waiting_and_rest_record"
               if record.get("episode_type") == "rest_debt_vs_idle"
               else record.get("episode_type"))
    return {**record, "schema_version": "1.1.0", "episode_type": renamed,
            "supersedes_checkpoint_id": record.get("supersedes_checkpoint_id")}


@_register("composite_episode", "1.1.0")
def _composite_episode_110_to_120(record: dict) -> dict:
    """1.2 makes proactive identity/provenance explicit and adds BANNER.

    Old records already contain addressable component/source refs, so both additions are
    reconstructable without inventing evidence.  Their original surface remains intact;
    BANNER is only available to new producers.
    """
    refs = [
        *(signal.get("source_ref") for signal in record.get("component_signals") or ()
          if isinstance(signal, dict)),
        *(record.get("source_event_refs") or ()),
        *(record.get("source_snapshot_refs") or ()),
        *(record.get("source_checkpoint_refs") or ()),
        *(record.get("source_solver_report_refs") or ()),
        *(record.get("source_segment_refs") or ()),
        *(record.get("execution_observation_refs") or ()),
    ]
    return {
        **record,
        "schema_version": "1.2.0",
        "decision_key": str(record.get("decision_key") or record.get("episode_id") or ""),
        "evidence_refs": list(dict.fromkeys(str(ref) for ref in refs if ref)),
    }


@_register("agent_presentation_input", "1.0.0")
def _agent_input_100_to_110(record: dict) -> dict:
    """1.1 adds explicit current/future action context; old input remains replayable."""
    return {**record, "schema_version": "1.1.0",
            "current_action": record.get("canonical_action"),
            "future_plan": []}


from functools import lru_cache


@lru_cache(maxsize=1)
def _registry():
    """Registry dùng chung cho mọi lời gọi upcast — bản đầu tạo instance MỚI mỗi lần gọi,
    tức lru_cache per-instance của registry vô dụng và mỗi upcast đọc lại file từ đĩa."""
    from pathlib import Path

    from .schema_registry import SchemaRegistry
    return SchemaRegistry(Path(__file__).resolve().parents[2] / "schemas")


def upcast(entity: str, record: dict) -> dict:
    """Nâng `record` lên hình dạng LATEST của entity, nối chuỗi từng bậc.

    Record đã ở latest ⇒ trả về chính nó (identity). Kẹt giữa chừng (thiếu upcaster cho một
    bậc) ⇒ ValueError tường minh — không trả nửa vời.

    ## Lan can chống TREO (review đối kháng Cycle V reproduce: 10.001 vòng)

    Upcaster quên stamp version (lỗi kinh điển của mỗi lần bump tương lai) làm vòng while cũ
    lặp VÔ HẠN — treo còn tệ hơn lỗi mù vì không có traceback nào để lần. Nay mỗi bậc phải
    TIẾN THẬT: version output phải nằm trong danh sách đã biết và LỚN HƠN version input;
    số bậc bị chặn bởi len(known)."""
    reg = _registry()
    known = reg.versions(entity)
    order = {v: i for i, v in enumerate(known)}
    cur = record.get("schema_version")
    if cur not in order:
        raise ValueError(f"{entity}: version '{cur}' không nằm trong {list(known)}")
    if cur == known[-1]:
        return record
    out = record
    for _ in range(len(known)):
        if out["schema_version"] == known[-1]:
            return out
        frm = out["schema_version"]
        step = UPCASTERS.get((entity, frm))
        if step is None:
            raise ValueError(
                f"{entity}: thiếu upcaster từ {frm} — chuỗi đứt, không trả kết quả nửa vời")
        out = step(out)
        nxt = out.get("schema_version")
        if nxt not in order or order[nxt] <= order[frm]:
            raise ValueError(
                f"{entity}: upcaster từ {frm} trả version '{nxt}' không TIẾN "
                f"(đã biết: {list(known)}) — quên stamp version? Fail-loud thay vì treo.")
    return out
