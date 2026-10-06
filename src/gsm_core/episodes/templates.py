"""Deterministic Vietnamese copy for composite episodes.

Template mode is the runtime default and the fallback for every other mode.  Rendering is
a pure ``dict -> str``: no model, no clock, no lookup that could differ between two runs
of the same episode.

Two rules are enforced by :func:`render`, not left to the writer:

* every placeholder in a template must be present in ``presentation_params`` — a missing
  fact raises instead of rendering "None" at a driver;
* no template may state that something happened *because of* something else, or that an
  amount was gained or lost.  Those sentences are the ones that turn an observation into
  a claim we cannot defend.
"""

from __future__ import annotations

import re
from string import Formatter

from gsm_core.episodes.contract import CompositeEpisode, EvidenceMode


class TemplateError(ValueError):
    """Raised when copy is missing, incomplete or breaks the product boundary."""


TEMPLATES: dict[str, dict[str, str]] = {
    # Two keys for one situation: at the first observation the evidence window is a
    # point, and "trong 0.0 phút gần đây" is not a sentence anyone should read.
    "episode.energy_pressure_building_first": {
        "title": "Năng lượng đang chịu áp lực",
        "body": ("Hệ thống vừa ghi nhận {soc_skip_count} lần hành trình không phù hợp với "
                 "mức pin hiện tại."),
    },
    "episode.energy_pressure_building": {
        "title": "Năng lượng đang chịu áp lực",
        "body": ("Trong {window_minutes} phút gần đây, hệ thống ghi nhận {soc_skip_count} lần "
                 "hành trình không phù hợp với mức pin hiện tại."),
    },
    # Level 1 of the same episode.  It reports the observation and stops there: no swap is
    # named because no live solver record names one, and no window is promised.
    "episode.energy_watch": {
        "title": "Theo dõi năng lượng",
        "body": ("Hệ thống ghi nhận {soc_skip_count} lần hành trình không phù hợp với mức "
                 "pin hiện tại. Chưa có kế hoạch đổi pin còn hiệu lực trong bản ghi."),
    },
    "episode.energy_episode_recovered": {
        "title": "Đã xử lý xong việc đổi pin",
        "body": ("Hệ thống ghi nhận bắt đầu đổi pin lúc {swap_start_at}, hoàn tất lúc "
                 "{swap_done_at}, và tính lại kế hoạch lúc {replan_at}."),
    },
    "episode.plan_slipping_not_driver_failing": {
        "title": "Kế hoạch đã được điều chỉnh",
        "body": ("Kế hoạch được cập nhật sau một gián đoạn được ghi nhận lúc "
                 "{disruption_at}. Trước đó kế hoạch là {previous_now} rồi {previous_next}; "
                 "hiện tại là {new_now} rồi {new_next}. Đây là điều chỉnh theo trạng thái "
                 "quan sát được, không phải đánh giá bác làm sai."),
    },
    "episode.plan_unstable": {
        "title": "Kế hoạch đang cập nhật",
        "body": "Kế hoạch đã đổi {revision_count} lần theo dữ kiện mới.",
    },
    "episode.plan_stabilized_again": {
        "title": "Kế hoạch đã ổn định",
        "body": ("Sau {revision_count} lần cập nhật, kế hoạch giữ nguyên trong "
                 "{quiet_minutes} phút gần đây."),
    },
    # Renamed from ``rest_debt_vs_idle``.  The old copy asserted a shortfall ("nợ nghỉ")
    # from a quantity nobody measured: idle is derived from the timeline, rest is a
    # recorded segment, and 216 of 270 actors do have rest recorded.  This wording states
    # only the two records and that they are counted separately — which is the whole
    # defensible content — and never crosses into how the driver feels (spec §1.2b).
    "episode.waiting_and_rest_record": {
        "title": "Bác đã chờ liên tục {continuous_idle_minutes} phút",
        "body": ("Kế hoạch tiếp theo vẫn hiển thị bên dưới để bác biết nên giữ khoảng "
                 "chờ hay chuẩn bị cho mốc kế tiếp. Thời gian chờ và REST được ghi riêng."),
    },
    # Terminal revision is projected away rather than rendered.  Keeping a valid empty
    # template makes the revision schema-complete without giving it driver-facing copy.
    "episode.waiting_expired": {
        "title": "Khoảng chờ đã kết thúc",
        "body": "",
    },
    "episode.income_quality_not_amount": {
        "title": "Payout đến từ đâu",
        "body": ("Payout đã ghi nhận gồm {source_count} nguồn. Hệ thống cũng ghi nhận "
                 "{cancellation_count} lần huỷ sau nhận — đây là bối cảnh vận hành, "
                 "không phải kết luận về tiền."),
    },
    # A shift with no cancellations should not be told it had zero of them.  "Ghi nhận 0
    # lần huỷ" is a sentence that exists only because the template had a slot to fill.
    "episode.income_quality_no_cancellation": {
        "title": "Payout đến từ đâu",
        "body": "Payout đã ghi nhận gồm {source_count} nguồn.",
    },
    "episode.stop_chasing_infeasible_target": {
        "title": "Mốc thưởng này không còn khả thi",
        "body": ("Theo dữ kiện lúc {verdict_at}, solver kết luận mốc này không đạt được "
                 "trong thời gian còn lại. Hệ thống sẽ không nhắc lại mốc này. Payout đã "
                 "ghi nhận không thay đổi."),
    },
    # Describes the driver's own recorded activity across two adjacent past windows and
    # stops there.  No cause, no forecast, no place to go — the utilisation number is a
    # record of what happened, not a reason for it.
    "episode.operational_phase_change": {
        "title": "Nhịp vận hành đã đổi",
        "body": ("Trong {window_minutes} phút gần đây, tỉ lệ thời gian có khách là "
                 "{recent_pct}% trên {recent_trips} cuốc; {window_minutes} phút liền trước "
                 "là {previous_pct}% trên {previous_trips} cuốc. Đây là ghi nhận hoạt động "
                 "của riêng bác, không phải dự báo nhu cầu."),
    },
}

# Phrases that would turn a record into a claim.  Checked against rendered output so a
# new template cannot introduce them by accident.
BANNED_PHRASES: tuple[str, ...] = (
    "nhờ đó", "giúp bạn kiếm thêm", "làm tăng thu nhập", "bị mất",
    "mất tiền", "đã khiến", "gây ra", "chắc chắn sẽ", "đảm bảo thu nhập",
    "bạn đã sai", "lỗi của bạn", "bạn chưa nghỉ", "bạn đang mệt",
    # 🔴 Chặn CẢ HAI xưng hô (thêm 2026-08-22). Repo vừa chuyển toàn bộ câu chữ sang "bác";
    # một danh sách chỉ biết "bạn" sẽ để lọt đúng dạng câu mà code bây giờ có khả năng sinh ra.
    # Ranh giới sức khoẻ (§1.2b) không được phụ thuộc vào việc hôm nay ta gọi tài xế là gì.
    "bác đã sai", "lỗi của bác", "bác chưa nghỉ", "bác đang mệt", "bác mệt",
    "hãy nhận đơn", "hãy từ chối", "hãy huỷ",
    # Health boundary (spec §1.2b): fatigue is a latent variable this system does not
    # measure, so no copy may assert a rest shortfall or a bodily state.  Listed
    # explicitly rather than left to reviewer memory — the previous A5 wording built a
    # deficit ("nợ nghỉ") out of a quantity that was never observed.
    "nợ nghỉ", "bạn mệt", "chưa nghỉ đủ", "cần nghỉ ngay", "nên nghỉ ngay",
    "sức khỏe đang bị ảnh hưởng", "sức khoẻ đang bị ảnh hưởng", "quá sức",
)


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def _tidy(value):
    """Drop a meaningless trailing ``.0`` so copy reads "60 phút", not "60.0 phút"."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def render(template_key: str, params: dict) -> dict[str, str]:
    """Render one episode's copy, failing closed on missing facts or banned claims."""
    template = TEMPLATES.get(template_key)
    if template is None:
        raise TemplateError(f"không có template cho {template_key!r}")
    rendered: dict[str, str] = {}
    for slot, text in template.items():
        missing = sorted(name for name in _placeholders(text)
                         if params.get(name) is None)
        if missing:
            raise TemplateError(
                f"{template_key}.{slot} thiếu dữ kiện: {missing} — không render số rỗng")
        rendered[slot] = text.format(
            **{key: _tidy(value) for key, value in params.items()})
    blob = " ".join(rendered.values()).lower()
    hit = next((phrase for phrase in BANNED_PHRASES if phrase in blob), None)
    if hit is not None:
        raise TemplateError(f"{template_key} chứa cụm bị cấm: {hit!r}")
    return rendered


_NUMBER_LABELS = {
    "soc_skip_count": "lần bỏ đơn vì pin",
    "window_minutes": "cửa sổ quan sát",
    "soc_pct": "pin hiện tại",
    "soc_after_pct": "pin sau khi đổi",
    "swap_start_at": "bắt đầu đổi pin lúc",
    "swap_done_at": "hoàn tất đổi pin lúc",
    "replan_at": "tính lại kế hoạch lúc",
    "disruption_at": "gián đoạn lúc",
    "revision_at": "cập nhật kế hoạch lúc",
    "revision_count": "số lần cập nhật",
    "quiet_minutes": "giữ nguyên trong",
    "idle_minutes": "tổng thời gian chờ",
    "continuous_idle_minutes": "đã chờ liên tục",
    "idle_block_count": "số khoảng chờ",
    "elapsed_minutes": "đã vào ca",
    "rest_minutes_recorded": "nghỉ đã ghi nhận",
    "recent_pct": "tỉ lệ có khách gần đây",
    "previous_pct": "tỉ lệ có khách trước đó",
    "recent_trips": "cuốc gần đây",
    "previous_trips": "cuốc trước đó",
    "delta_pp": "chênh lệch",
    "payout_total_vnd": "payout đã ghi nhận",
    "payout_trip_vnd": "tiền cuốc",
    "payout_day_bonus_vnd": "thưởng ngày",
    "payout_mission_vnd": "thưởng nhiệm vụ",
    "payout_newbie_vnd": "hỗ trợ tân binh",
    "cancellation_count": "huỷ sau nhận",
    "gap_points": "còn thiếu",
}


# §4 timeline: an observation, our reading of it, a recommendation, what the driver chose
# and what the world then recorded are five different kinds of statement.  Collapsing them
# into one list is how "advice" quietly becomes "outcome".  The lane is derived from the
# authority and surface the episode already carries, and the *label* is produced here
# rather than in the browser — the client is not allowed to author driver-facing text.
LANE_OF_AUTHORITY: dict[str, tuple[str, str]] = {
    "OBSERVATION": ("OBSERVED_EVENT", "Đã ghi nhận"),
    "SOLVER": ("ADVICE", "Kế hoạch từ solver"),
    "POLICY": ("ADVICE", "Chính sách"),
    "DERIVED_RULE": ("DERIVED_EXPLANATION", "Hệ thống suy ra"),
}

# Driver-facing name for each lifecycle state.  States with no entry are internal and are
# not shown at all rather than shown raw.
STATE_LABELS: dict[str, str] = {
    "WATCHING": "Đang theo dõi",
    "DETECTED": "Đã ghi nhận",
    "BUILDING": "Đang hình thành",
    "ACTIONABLE": "Cần chuẩn bị",
    "RECOVERED": "Đã xử lý xong",
    "UPDATING": "Đang cập nhật",
    "UNSTABLE": "Đang cập nhật",
    "STABLE": "Ổn định",
    "CLOSED_INFEASIBLE": "Đã đóng",
    "AT_RISK": "Đang gặp rủi ro",
    "TARGET_ACTIVE": "Đang theo đuổi",
    "TARGET_CLOSED": "Đã đóng",
    "SUMMARISED": "Tổng kết",
}


def _window_label(window: dict | None) -> str | None:
    """``{start,end}`` ISO → "18:00 → 19:00".

    Formatted here, not in the browser: an ISO stamp on a phone is noise, and the client
    is not allowed to derive display values of its own.
    """
    if not window:
        return None
    def _hm(value):
        text = str(value or "")
        return text[11:16] if len(text) >= 16 else None
    start, end = _hm(window.get("start")), _hm(window.get("end"))
    if start and end:
        return f"{start} → {end}"
    return start or end


#: Below this the episode is reported as an inference, not as a record.  0.7 is the band
#: the existing detectors already separate on: solver-backed evidence carries 0.8, while
#: the timeline-gap and window detectors carry 0.5-0.6.
_SUFFICIENT_CONFIDENCE = 0.7


def evidence_quality(episode: CompositeEpisode) -> dict:
    """Grade the evidence this episode is actually standing on.

    ``present()`` used to stamp a literal ``{"status": "SUFFICIENT"}`` on every episode.
    That made the grade unfalsifiable: A5 is built from an INFERRED gap in the timeline at
    confidence 0.6 and was reported with the same badge as an OBSERVED solver record.  The
    contract exists to keep those apart, so the grade is now derived from the signals.

    Three grades, in the order they are checked:

    * ``INSUFFICIENT`` — nothing to stand on (no signals at all).
    * ``INFERRED`` — at least one signal is a derivation rather than an observation, or
      the detector's own confidence is below the band.  This is a real answer, not a
      failure: the caller may still show it, but must not present it as a record.
    * ``SUFFICIENT`` — every signal observed, confidence in band.
    """
    signals = episode.component_signals
    if not signals:
        return {"status": "INSUFFICIENT", "evidence_mode": None,
                "confidence": float(episode.confidence), "reasons": ("no_signal",)}

    modes = {signal.evidence_mode for signal in signals}
    inferred = EvidenceMode.INFERRED in modes
    low = float(episode.confidence) < _SUFFICIENT_CONFIDENCE
    reasons = tuple(
        name for name, hit in (("inferred_signal", inferred), ("low_confidence", low))
        if hit)
    mode = EvidenceMode.INFERRED if inferred else EvidenceMode.OBSERVED
    return {
        "status": "INFERRED" if reasons else "SUFFICIENT",
        "evidence_mode": mode.value,
        "confidence": float(episode.confidence),
        "reasons": reasons,
    }


def present(episode: CompositeEpisode) -> dict:
    """Full presentation payload for one episode: copy, numbers, caveats, provenance."""
    copy = render(episode.template_key, episode.presentation_params)
    lane, lane_label = LANE_OF_AUTHORITY.get(
        episode.authority.value, ("DERIVED_EXPLANATION", "Hệ thống suy ra"))
    return {
        "episode_id": episode.episode_id,
        "episode_type": episode.episode_type,
        "decision_key": episode.decision_key,
        "evidence_refs": list(episode.evidence_refs),
        "evidence_quality": evidence_quality(episode),
        "surface": episode.recommended_surface.value,
        "interruption_class": episode.interruption_class.value,
        "lifecycle_state": episode.lifecycle_state,
        "state_label": STATE_LABELS.get(episode.lifecycle_state, ""),
        "lane": lane,
        "lane_label": lane_label,
        "supersedes_checkpoint_id": episode.supersedes_checkpoint_id,
        "status": episode.status.value,
        "material_revision": episode.material_revision,
        "title": copy["title"],
        "body": copy["body"],
        "current_action": (None if episode.current_action is None else {
            "code": episode.current_action.code,
            "label_id": episode.current_action.label_id,
            "window": episode.current_action.window,
            "window_label": _window_label(episode.current_action.window)}),
        "future_plan": [{"code": step.code, "label_id": step.label_id,
                          "window": step.window,
                          "window_label": _window_label(step.window)}
                         for step in episode.future_plan],
        "action_window": episode.action_window,
        "action_window_label": _window_label(episode.action_window),
        "numbers": [{"key": n.key, "label": _NUMBER_LABELS.get(n.key, n.key),
                      "value": _tidy(n.value), "unit": n.unit, "source": n.source}
                     for n in episode.numbers],
        "caveats": list(episode.caveats),
        "evidence_mode": episode.evidence_mode.value,
        "data_mode": episode.data_mode.value,
        "is_mock": episode.is_mock,
        "authority": episode.authority.value,
        "confidence": round(float(episode.confidence), 3),
        "evidence_window": {"start_min": round(float(episode.evidence_window_start), 1),
                             "end_min": round(float(episode.evidence_window_end), 1)},
        "allowed_actions": list(episode.allowed_actions),
        "safety_flags": list(episode.safety_flags),
        "presentation_source": "template",
    }


def assert_no_banned_phrase(text: str) -> None:
    lowered = str(text).lower()
    hit = next((phrase for phrase in BANNED_PHRASES if phrase in lowered), None)
    if hit is not None:
        raise TemplateError(f"chuỗi chứa cụm bị cấm: {hit!r}")


_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", str(text)).strip()
