"""Deterministic WHY copy for an immutable AdviceCheckpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from gsm_core.advisor.checkpoint_templates import _ACTION_LABELS, _window_text
from gsm_core.vn_format import render_number_vn


@dataclass(frozen=True)
class CheckpointWhyFactPack:
    """The already-persisted facts allowed into a checkpoint explanation."""

    checkpoint_id: str
    reason_code: str
    topic: str
    action_code: str
    future_action_code: str | None
    future_window: str | None
    evidence_status: str
    facts: tuple[dict, ...]
    numbers: tuple[dict, ...]
    caveats: tuple[dict, ...]
    historical: bool = False

    @classmethod
    def from_checkpoint(cls, checkpoint: dict, *, facts: Iterable[dict],
                        numbers: Iterable[dict], caveats: Iterable[dict],
                        historical: bool) -> "CheckpointWhyFactPack":
        future = next((step for step in (checkpoint.get("future_plan") or [])
                       if isinstance(step, dict)), {})
        action = checkpoint.get("current_action") or {}
        return cls(
            checkpoint_id=str(checkpoint.get("checkpoint_id") or ""),
            reason_code=str(checkpoint.get("reason_code") or ""),
            topic=str(checkpoint.get("topic") or ""),
            action_code=str(action.get("code") or "NO_ACTION"),
            future_action_code=str(future.get("code")) if future.get("code") else None,
            future_window=_window_text(future.get("window")),
            evidence_status=str(
                (checkpoint.get("evidence_quality") or {}).get("status") or "UNKNOWN"),
            facts=tuple(dict(item) for item in facts if isinstance(item, dict)),
            numbers=tuple(dict(item) for item in numbers if isinstance(item, dict)),
            caveats=tuple(dict(item) for item in caveats if isinstance(item, dict)),
            historical=historical,
        )


@dataclass(frozen=True)
class CheckpointWhyRender:
    text: str
    used_fact_ids: tuple[str, ...]
    used_number_ids: tuple[str, ...]
    used_caveat_ids: tuple[str, ...]


def _number_lines(pack: CheckpointWhyFactPack) -> tuple[str, ...]:
    lines = []
    for number in pack.numbers:
        if number.get("value") is None or not number.get("unit"):
            continue
        label = str(number.get("label") or "Số liệu đã ghi nhận")
        unit = str(number["unit"])
        if unit == "vnd_per_order":
            display = render_number_vn(number["value"], "vnd") + "/cuốc"
        elif unit == "points_per_hour":
            display = f"{float(number['value']):.3f}".rstrip("0").rstrip(".")
            display = display.replace(".", ",") + " điểm/giờ"
        elif unit == "km":
            display = f"{float(number['value']):.1f}".rstrip("0").rstrip(".")
            display = display.replace(".", ",") + " km"
        elif unit in {"vnd", "points", "hours", "trips", "ratio", "percent",
                      "minutes", "count"}:
            display = render_number_vn(number["value"], unit)
        else:
            # Unknown units stay typed in the evidence payload, but their internal identifier
            # must not become driver-facing copy.  The producer label carries the meaning.
            display = str(number["value"])
        lines.append(f"{label}: {display}")
    return tuple(lines)


def _why_lead(pack: CheckpointWhyFactPack) -> str:
    if pack.reason_code == "idea.IC-01":
        return "Thẻ này dựa trên kế hoạch đầu ca và trạng thái đã được ghi nhận lúc hiển thị."
    if pack.topic == "bonus_eligibility" or pack.action_code == "PROTECT_ELIGIBILITY":
        return "Thẻ này dựa trên tiến độ mốc thưởng và kế hoạch ca đã được ghi nhận lúc hiển thị."
    if (pack.topic == "energy" or pack.action_code == "SWAP"
            or pack.future_action_code == "SWAP"):
        return "Thẻ này dựa trên trạng thái năng lượng và bước tiếp theo đã được ghi trong kế hoạch."
    if pack.topic == "shift_timing" or pack.future_action_code:
        return "Thẻ này dựa trên phiên bản kế hoạch đang có hiệu lực lúc hiển thị."
    action = _ACTION_LABELS.get(pack.action_code, "trạng thái hiện tại")
    return f"Thẻ này dựa trên {action} đã được ghi nhận lúc hiển thị."


def render_checkpoint_why(pack: CheckpointWhyFactPack) -> CheckpointWhyRender:
    """Render WHY without calculating, fetching, or calling a model."""
    parts = [_why_lead(pack)]
    numbers = _number_lines(pack)
    if numbers:
        parts.append("Các số liệu trong bản ghi: " + "; ".join(numbers) + ".")
    if pack.future_action_code:
        future = _ACTION_LABELS.get(pack.future_action_code, "bước tiếp theo")
        window = f" trong khung {pack.future_window}" if pack.future_window else ""
        parts.append(f"Bước tiếp theo đã ghi: {future}{window}.")
    if pack.evidence_status == "SUFFICIENT":
        parts.append("Bản ghi có đủ bằng chứng cần thiết cho thẻ này.")
    elif pack.evidence_status:
        parts.append("Phần giải thích chỉ dùng các bằng chứng đã có trong bản ghi.")
    if pack.historical:
        parts.append("Đây là giải thích lịch sử, không phải lời khuyên hiện hành.")
    return CheckpointWhyRender(
        text=" ".join(parts),
        used_fact_ids=tuple(str(item["id"]) for item in pack.facts if item.get("id")),
        used_number_ids=tuple(str(item["id"]) for item in pack.numbers
                              if item.get("id") and item.get("value") is not None
                              and item.get("unit")),
        used_caveat_ids=(),
    )
