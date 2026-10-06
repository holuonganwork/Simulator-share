"""Auditable intent and signal-quality registry for driver checkpoints.

The registry answers a question that candidate-frequency reports cannot answer:
*what is this checkpoint trying to communicate, and is its evidence good enough for
that output?*  It deliberately separates signal sufficiency, signal quality, output
semantics and lifecycle reachability so a high candidate count cannot hide a useless
card.

This module owns no trigger thresholds and performs no simulator lookup.  Runtime code
may use :func:`assess_signal_set` as a fail-closed gate; the retrospective audit uses the
same registry to cover canonical checkpoints, all 33 idea cards, the 12 historical
combos, the eight implemented episode types and the three legacy time anchors.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable, Mapping

from gsm_core.episodes.detectors import DETECTORS, EPISODE_SIGNALS


class OutputMode(str, Enum):
    ACTION = "ACTION"
    EXPLANATION = "EXPLANATION"
    OBSERVATION = "OBSERVATION"
    SUMMARY = "SUMMARY"
    INTERNAL_ONLY = "INTERNAL_ONLY"


class SignalReadiness(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    LOW_QUALITY = "LOW_QUALITY"
    UNMEASURED = "UNMEASURED"


class Verdict(str, Enum):
    KEEP = "KEEP"
    REWRITE_OUTPUT = "REWRITE_OUTPUT"
    ENRICH_FACTS = "ENRICH_FACTS"
    BLOCK_DATA_QUALITY = "BLOCK_DATA_QUALITY"
    PASSIVE_ONLY = "PASSIVE_ONLY"
    MERGE_OR_UPDATE_IN_PLACE = "MERGE_OR_UPDATE_IN_PLACE"
    RETIRE_OR_REJECT = "RETIRE_OR_REJECT"
    UNMEASURED_NO_PRODUCER = "UNMEASURED_NO_PRODUCER"


class FailureCause(str, Enum):
    TRIGGER = "TRIGGER"
    STATE = "STATE"
    COMBINATION = "COMBINATION"
    DATA_QUALITY = "DATA_QUALITY"
    SEMANTICS_OUTPUT = "SEMANTICS_OUTPUT"
    ROUTING_LIFECYCLE = "ROUTING_LIFECYCLE"
    EVALUATION = "EVALUATION"


@dataclass(frozen=True)
class SignalObservation:
    """Quality flags for one signal at the moment an output would be produced."""

    signal_id: str
    present: bool = True
    fresh: bool = True
    trusted: bool = True
    provenance: bool = True
    grain_ok: bool = True


@dataclass(frozen=True)
class SignalAssessment:
    status: SignalReadiness
    missing_signal_ids: tuple[str, ...] = ()
    low_quality_signal_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        row = asdict(self)
        row["status"] = self.status.value
        return row


@dataclass(frozen=True)
class IntentSpec:
    intent_id: str
    family: str
    title: str
    driver_job: str
    output_intent: str
    surface: str
    mode: OutputMode
    required_signals: tuple[str, ...]
    optional_signals: tuple[str, ...]
    quality_rules: tuple[str, ...]
    producer: str
    signal_readiness: SignalReadiness
    output_quality: str
    lifecycle_reachability: str
    evidence_grade: str
    verdict: Verdict
    failure_causes: tuple[FailureCause, ...]
    fix_route: str
    # Detector nào thực sự phục vụ intent này.  Trước P4.0 đây là kiến thức truyền miệng:
    # 29 dòng khai ``producer="episode-runtime"`` trong khi chỉ có tám detector, và không
    # dòng nào nói dòng nào.  Hệ quả là ba câu không trả lời được bằng dữ liệu — IC nào đã
    # có nhà, IC nào còn mồ côi, và sửa một detector thì hỏng lời hứa của IC nào.
    #
    # ``None`` là một lời khai, không phải chỗ trống: nó nói "intent này chưa có producer
    # runtime".  Cặp gate ở cuối module giữ hai chiều thẳng hàng.
    serving_episode: str | None = None

    def to_dict(self) -> dict:
        row = asdict(self)
        row["mode"] = self.mode.value
        row["signal_readiness"] = self.signal_readiness.value
        row["verdict"] = self.verdict.value
        row["failure_causes"] = [cause.value for cause in self.failure_causes]
        return row


_BASE_QUALITY = (
    "mọi signal có source_ref/provenance",
    "signal còn fresh tại thời điểm hiển thị",
    "grain thời gian phù hợp với kết luận",
    "MOCK/SIMULATED phải gắn nhãn; không nâng thành LIVE",
)


def _spec(intent_id: str, family: str, title: str, driver_job: str,
          output_intent: str, surface: str, mode: OutputMode,
          required: Iterable[str], *, optional: Iterable[str] = (),
          producer: str = "research-only",
          readiness: SignalReadiness = SignalReadiness.SUFFICIENT,
          output_quality: str = "CLEAR", lifecycle: str = "REACHABLE",
          evidence: str = "CONTRACT_ONLY", verdict: Verdict = Verdict.KEEP,
          causes: Iterable[FailureCause] = (), fix: str = "giữ contract hiện tại",
          quality: Iterable[str] = (), serving: str | None = None) -> IntentSpec:
    return IntentSpec(
        intent_id=intent_id, family=family, title=title, driver_job=driver_job,
        output_intent=output_intent, surface=surface, mode=mode,
        required_signals=tuple(required), optional_signals=tuple(optional),
        quality_rules=tuple(_BASE_QUALITY) + tuple(quality), producer=producer,
        signal_readiness=readiness, output_quality=output_quality,
        lifecycle_reachability=lifecycle, evidence_grade=evidence, verdict=verdict,
        failure_causes=tuple(causes), fix_route=fix, serving_episode=serving)


# Canonical checkpoint producers.  ONLINE is explicitly retained as an audited no-op,
# not a driver-facing card; its future SWAP evidence is consumed by IC-06/A1 instead.
_CANONICAL = (
    _spec("CP-S1-BONUS", "canonical", "Bảo vệ điều kiện thưởng",
          "Biết mốc nào còn khả thi và còn thiếu gì", "Nêu trạng thái mốc, phần còn thiếu và policy đang áp dụng",
          "nudge", OutputMode.ACTION, ("s1_verdict", "policy_version", "validity"),
          optional=("gap_points", "trips_needed"), producer="S1-runtime",
          output_quality="REWRITE", evidence="MEASURED_REACHABILITY",
          verdict=Verdict.REWRITE_OUTPUT, causes=(FailureCause.SEMANTICS_OUTPUT,),
          fix="dùng semantic number key; nói rõ mốc/gap thay vì 'giữ nhịp' chung chung"),
    _spec("CP-S2-ONLINE", "canonical", "ONLINE duy trì",
          "Không bị làm phiền khi không có quyết định mới", "Không phát card; chỉ cập nhật plan strip nếu future plan đổi vật chất",
          "none", OutputMode.INTERNAL_ONLY, ("s2_current_action", "validity"),
          producer="S2-runtime", output_quality="NO_VALUE_AS_CARD",
          evidence="MEASURED_REACHABILITY", verdict=Verdict.RETIRE_OR_REJECT,
          causes=(FailureCause.SEMANTICS_OUTPUT, FailureCause.EVALUATION),
          fix="giữ silent maintenance; tách future SWAP sang episode chuẩn bị"),
    _spec("CP-S2-SWAP", "canonical", "Đổi pin trong cửa sổ hiện tại",
          "Biết khi nào cần đổi pin và vì sao là bây giờ", "Nêu action SWAP, cửa sổ, SOC/evidence và kế hoạch sau swap",
          "nudge", OutputMode.ACTION, ("solver_action", "swap_window", "validity", "soc_state"),
          optional=("future_online",), producer="S2-runtime", output_quality="REWRITE",
          evidence="MEASURED_REACHABILITY", verdict=Verdict.REWRITE_OUTPUT,
          causes=(FailureCause.SEMANTICS_OUTPUT,),
          fix="thay câu 'bảo vệ phần ca còn lại' bằng facts pin/window có nguồn"),
    _spec("CP-S2-REST", "canonical", "Nghỉ trong cửa sổ kế hoạch",
          "Hiểu đây là action từ plan, không phải chẩn đoán sức khỏe", "Nêu window và ràng buộc plan; không suy mệt hoặc nghỉ thiếu",
          "nudge", OutputMode.ACTION, ("solver_action", "rest_window", "validity"),
          producer="S2/S7-runtime", readiness=SignalReadiness.LOW_QUALITY,
          output_quality="REWRITE", evidence="MEASURED_REACHABILITY",
          verdict=Verdict.BLOCK_DATA_QUALITY,
          causes=(FailureCause.DATA_QUALITY, FailureCause.SEMANTICS_OUTPUT),
          fix="giữ fail-closed theo quyết định rest hiện hành; chỉ mở khi policy/source đủ authority"),
    _spec("CP-S2-BOUNDARY", "canonical", "Ranh giới kết ca",
          "Biết kết ca hay kéo dài theo boundary nào", "Nêu END/EXTEND, boundary và constraint đang khóa quyết định",
          "nudge", OutputMode.ACTION, ("terminal_boundary", "solver_or_policy_action", "validity"),
          producer="RULE/S2-partial", readiness=SignalReadiness.INSUFFICIENT,
          output_quality="REWRITE", lifecycle="NO_COMPLETE_PRODUCER",
          evidence="UNMEASURED", verdict=Verdict.ENRICH_FACTS,
          causes=(FailureCause.STATE, FailureCause.TRIGGER),
          fix="yêu cầu terminal event + approved END/EXTEND authority trước khi phát"),
    _spec("CP-S4-REPOSITION", "canonical", "Tái định vị có kiểm soát cung",
          "Không bị dồn tới điểm nóng không còn capacity", "Chỉ giải thích allocation trong simulator",
          "showcase", OutputMode.INTERNAL_ONLY, ("demand", "supply_now", "supply_incoming", "capacity"),
          producer="S4-simulator-only", readiness=SignalReadiness.LOW_QUALITY,
          lifecycle="SIMULATOR_ONLY", evidence="UNMEASURED",
          verdict=Verdict.BLOCK_DATA_QUALITY, causes=(FailureCause.DATA_QUALITY,),
          fix="không phát production cho tới khi có live supply/capacity authority"),
    _spec("CP-POSTSHIFT-RECAP", "canonical", "Tổng kết sau ca",
          "Hiểu ca đã diễn ra thế nào và tiền đến từ đâu", "Tóm tắt phase, income lineage, plan revisions và observed actions",
          "recap", OutputMode.SUMMARY, ("terminal_event", "journey", "income_ledger"),
          optional=("checkpoint_lifecycle", "execution_observations"), producer="episode-runtime",
          output_quality="REWRITE", lifecycle="REACHABLE_WITH_CENSOR_BUG",
          evidence="MEASURED_REACHABILITY", verdict=Verdict.REWRITE_OUTPUT,
          causes=(FailureCause.STATE, FailureCause.SEMANTICS_OUTPUT),
          fix="không dùng planned shift_end thay terminal event; giải thích nguồn tiền và các phase"),
)


# Which detector actually serves each Idea Card.
#
# Measured, not guessed.  The pairing comes from the IC-33 viability study
# (``research/audit/2026-08-06-ic33-checkpoint-viability/``) cross-read with the P2/P3/P4
# signal audit: each IC below fires on evidence one of the eight detectors already emits.
# An IC absent from this table has **no runtime producer** — that is the honest answer, and
# it is what keeps ``producer``/``lifecycle_reachability`` from being decoration.
#
# The number that matters for P5 lives in ``ic33-ranking.csv`` column ``facts``: only eight
# of the 33 are ON-RECORD, i.e. their evidence sits on a record the agent may cite.  Whole
# families here are SIM-ONLY, and ``stop_chasing_infeasible_target``'s two cards (IC-22,
# IC-26) are NEEDS-CONTRACT at 0% coverage because S1 persists only a READY record.  That is
# why a canonical WHY is buildable today for the energy and plan families and not for the
# target family — a producer gap, not a product decision.
_IDEA_SERVED_BY: dict[int, str] = {
    5: "energy_pressure_building",          # ON-RECORD, 86,1% coverage
    6: "energy_pressure_building",          # ON-RECORD, 79,1%
    8: "energy_pressure_building",          # SIM-ONLY, 2,5% — supporting evidence only
    9: "energy_episode_recovered",          # SIM-ONLY, 19,8%
    11: "plan_slipping_not_driver_failing",  # ON-RECORD, 94,8% — the widest WHY carrier
    12: "plan_stability",                   # ON-RECORD, 23,4%
    14: "plan_slipping_not_driver_failing",  # SIM-ONLY, 30,5%
    16: "waiting_and_rest_record",          # SIM-ONLY, 77,8%
    18: "operational_phase_change",         # SIM-ONLY, 91,7%
    25: "income_quality_not_amount",        # SIM-ONLY, 73,0%
    26: "stop_chasing_infeasible_target",   # NEEDS-CONTRACT, 0% — blocked at the producer
    29: "income_quality_not_amount",        # ON-RECORD, 100%
}


# Complete retrospective inventory of the 33 previously proposed UI capabilities.  The
# concise metadata below is expanded into full audit rows by ``_idea_specs``.
_IDEAS = (
    (1, "La bàn ca hôm nay", "brief", "plan,energy", "Định hướng mốc ca từ S2+SOC; policy/S1 bổ sung khi đã có", "KEEP"),
    (2, "Khung hoạt động mạnh của riêng bác", "history", "multiday,income,intraday_time", "Nêu pattern cùng khung giờ của chính tài xế", "BLOCK_DATA_QUALITY"),
    (3, "Nhịp 90 phút đầu", "passive", "intraday_income,comparable_days,online_time", "So nhịp đầu ca với baseline cá nhân", "ENRICH_FACTS"),
    (4, "Bản đồ quyền lợi trong ca", "brief", "policy,mission,bonus,settlement", "Gom quyền lợi nhưng tách confirmed/pending", "BLOCK_DATA_QUALITY"),
    (5, "Lộ trình năng lượng của ca", "plan_strip", "soc,plan,future_windows", "Cho biết pin trở thành ràng buộc lúc nào", "KEEP"),
    (6, "Chuẩn bị đổi pin trước điểm gãy", "nudge", "future_swap,soc,window_persistence,validity", "ONLINE bây giờ, chuẩn bị SWAP sắp tới", "KEEP"),
    (7, "Pin đang hao nhanh hơn kế hoạch", "why", "soc_series,plan_revision,activity", "Giải thích window pin bị kéo sớm", "ENRICH_FACTS"),
    (8, "Vòng lặp bỏ lỡ vì pin", "passive", "repeated_soc_skip,current_solver_action", "Nêu pattern pin; chỉ action khi solver cho phép", "PASSIVE_ONLY"),
    (9, "Ca đã phục hồi sau đổi pin?", "timeline", "pre_swap,swap_done,post_soc,replan", "Đóng vòng observed swap rồi re-plan", "KEEP"),
    (10, "Ma sát đổi pin", "recap", "swap_start,swap_done_or_failed,wait,plan_revision", "Nêu thời gian chờ và việc plan được tính lại", "PASSIVE_ONLY"),
    (11, "Vì sao kế hoạch vừa đổi", "why", "old_plan,new_plan,changed_facts,provenance", "So trước-hiện tại bằng changed facts", "KEEP"),
    (12, "Kế hoạch đang đổi quá nhiều", "plan_strip", "semantic_revisions,time_window", "Gom churn và cập nhật một strip", "MERGE_OR_UPDATE_IN_PLACE"),
    (13, "Thực tế đang lệch kế hoạch", "passive", "planned_state,observed_state,revision", "Nêu lệch plan mà không quy lỗi", "PASSIVE_ONLY"),
    (14, "Kế hoạch phục hồi sau gián đoạn", "timeline", "disruption,replan,stable_state", "Nêu plan đã được tính lại sau gián đoạn", "KEEP"),
    (15, "Cửa sổ nghỉ đang trôi", "plan_strip", "rest_window,now,plan_revision", "Cập nhật window nghỉ tại chỗ", "BLOCK_DATA_QUALITY"),
    (16, "Nghỉ chủ động hay chỉ đang chờ?", "passive", "idle_blocks,explicit_rest", "Tách chờ suy diễn khỏi nghỉ quan sát", "REWRITE_OUTPUT"),
    (17, "Chuỗi chờ lặp lại", "passive", "repeated_idle,safe_state", "Nêu pattern chờ, không đề xuất khu vực", "PASSIVE_ONLY"),
    (18, "Nhịp hoạt động đang chuyển pha", "passive", "two_past_windows,min_trips,utilization", "So hai cửa sổ hoạt động đã qua", "PASSIVE_ONLY"),
    (19, "Quãng chạy rỗng đang tăng", "passive", "empty_segments,two_past_windows", "Nêu tỷ trọng chạy rỗng; chưa có action vị trí", "PASSIVE_ONLY"),
    (20, "Thời gian bị bào mòn bởi cuốc hủy", "recap", "cancel_cluster,journey_time", "Nêu thời gian gián đoạn, không quy tiền mất", "PASSIVE_ONLY"),
    (21, "Nhịp quyết định đơn đang thay đổi", "history", "offer_outcomes,comparable_windows", "Nêu thay đổi hành vi dạng analytics", "BLOCK_DATA_QUALITY"),
    (22, "Trạng thái mốc thưởng vừa đổi", "priority_board", "two_s1_verdicts,policy_version", "Nêu transition mốc thay vì snapshot đơn", "ENRICH_FACTS"),
    (23, "Mission, bonus và thời gian đang xung đột", "priority_board", "mission,bonus,time,canonical_priority", "Một primary action, mục tiêu khác là context", "ENRICH_FACTS"),
    (24, "Thu nhập hôm nay so với chính bác", "history", "intraday_income,comparable_days", "So cùng khung giờ với baseline cá nhân", "BLOCK_DATA_QUALITY"),
    (25, "Thu nhập hôm nay đến từ đâu", "recap", "canonical_income_ledger,settlement_status", "Giải thích lineage từng nguồn tiền", "REWRITE_OUTPUT"),
    (26, "Mục tiêu không còn khả thi: bảo toàn ca", "priority_board", "s1_infeasible,time_remaining,policy", "Dừng nhắc mục tiêu, không tự bịa action thay thế", "KEEP"),
    (27, "Cổng kết ca an toàn", "nudge", "terminal_boundary,approved_action,energy,policy", "END/EXTEND fail-closed theo authority", "ENRICH_FACTS"),
    (28, "Nhật ký lời khuyên và hành động", "history", "presented,intent,execution,outcome", "Tách bốn lane, không nhập causal", "KEEP"),
    (29, "Recap hành trình ca", "recap", "terminal_event,journey,income,plan", "Kể 3–5 moment vật chất của ca", "REWRITE_OUTPUT"),
    (30, "Pattern đang lặp qua nhiều ngày", "history", "prior_only_history,comparable_days", "Nêu trend nhiều ngày, không quy nguyên nhân", "BLOCK_DATA_QUALITY"),
    (31, "Kế hoạch nghỉ cho ca kế tiếp", "brief", "prior_day_idle,prior_day_rest,next_shift", "Đưa observation hôm trước vào brief, revalidate hôm nay", "BLOCK_DATA_QUALITY"),
    (32, "Điều phối vị trí không dồn tài xế", "showcase", "demand,supply_incoming,capacity", "Giải thích anti-herding trong simulator", "BLOCK_DATA_QUALITY"),
    (33, "Môi trường làm kế hoạch đổi như thế nào", "showcase", "environment_series,old_plan,new_plan", "Giải thích tác động trong scenario mô phỏng", "BLOCK_DATA_QUALITY"),
)


def _idea_specs() -> tuple[IntentSpec, ...]:
    rows: list[IntentSpec] = []
    for number, title, surface, raw_required, intent, verdict_name in _IDEAS:
        required = tuple(raw_required.split(","))
        verdict = Verdict(verdict_name)
        blocked = verdict is Verdict.BLOCK_DATA_QUALITY
        enrich = verdict is Verdict.ENRICH_FACTS
        passive = verdict is Verdict.PASSIVE_ONLY
        rewrite = verdict is Verdict.REWRITE_OUTPUT
        mode = (OutputMode.ACTION if surface == "nudge" else
                OutputMode.SUMMARY if surface in {"brief", "recap"} else
                OutputMode.EXPLANATION if surface in {"why", "plan_strip", "priority_board"}
                else OutputMode.OBSERVATION)
        readiness = (SignalReadiness.LOW_QUALITY if blocked else
                     SignalReadiness.INSUFFICIENT if enrich else
                     SignalReadiness.SUFFICIENT)
        causes: list[FailureCause] = []
        if blocked:
            causes.append(FailureCause.DATA_QUALITY)
        if enrich:
            causes.extend((FailureCause.STATE, FailureCause.COMBINATION))
        if rewrite:
            causes.append(FailureCause.SEMANTICS_OUTPUT)
        if passive:
            causes.append(FailureCause.SEMANTICS_OUTPUT)
        serving = _IDEA_SERVED_BY.get(number)
        producer = ("checkpoint-runtime" if number == 1 else
                    "episode-runtime" if serving else "research-only")
        lifecycle = ("REACHABLE" if producer in {"episode-runtime", "checkpoint-runtime"}
                     else "NO_RUNTIME_PRODUCER")
        evidence = ("MEASURED_REACHABILITY"
                    if producer in {"episode-runtime", "checkpoint-runtime"}
                    else "RESEARCH_PROBE")
        fix = {
            Verdict.KEEP: "giữ detector/contract; kiểm copy bằng output-intent rubric",
            Verdict.REWRITE_OUTPUT: "giữ ý định, viết lại output bằng facts có semantic key",
            Verdict.ENRICH_FACTS: "chưa phát action; bổ sung producer cho đủ bộ tín hiệu tối thiểu",
            Verdict.BLOCK_DATA_QUALITY: "fail-closed đến khi có source/freshness/grain đáng tin",
            Verdict.PASSIVE_ONLY: "giữ observation/passive; không nâng thành action",
            Verdict.MERGE_OR_UPDATE_IN_PLACE: "gom cùng decision_key và repaint, không đẻ card mới",
        }[verdict]
        rows.append(_spec(
            f"IC-{number:02d}", "idea_card", title,
            f"Tài xế cần {title.lower()} mà không phải tự đọc raw trace", intent,
            surface, mode, required, producer=producer, readiness=readiness,
            output_quality="REWRITE" if rewrite else "CLEAR",
            lifecycle=lifecycle, evidence=evidence, verdict=verdict, causes=causes,
            fix=fix, serving=serving))
    return tuple(rows)


_COMBOS = (
    ("energy_pressure_building", "Áp lực năng lượng đang tích tụ", "nudge", "soc_skip,future_swap,validity", Verdict.KEEP),
    ("energy_episode_recovered", "Đã ghi nhận swap và re-plan", "timeline", "soc_skip,swap_done,replan", Verdict.KEEP),
    # Đổi tên ở P4.0 (2026-08-14) từ ``plan_stabilized_again``.  Nghĩa giữ nguyên; cái tên
    # cũ chưa từng khớp detector nào, nên dòng này tự khai REACHABLE cho một producer không
    # tồn tại trong khi detector thật lại vắng mặt khỏi inventory.
    ("plan_stability", "Plan ổn định sau churn", "plan_strip", "semantic_revisions,quiet_window", Verdict.MERGE_OR_UPDATE_IN_PLACE),
    ("plan_slipping_not_driver_failing", "Plan đổi sau gián đoạn", "why", "disruption,old_plan,new_plan", Verdict.KEEP),
    ("waiting_and_rest_record", "Tách thời gian chờ và nghỉ", "passive", "idle_blocks,explicit_rest", Verdict.REWRITE_OUTPUT),
    ("operational_phase_change", "Nhịp hoạt động đổi giữa hai cửa sổ", "passive", "two_past_windows,min_trips", Verdict.PASSIVE_ONLY),
    ("objective_collision", "Nhiều mục tiêu cùng tranh quỹ ca", "priority_board", "mission,bonus,energy,canonical_priority", Verdict.ENRICH_FACTS),
    ("stop_chasing_infeasible_target", "Mốc không còn khả thi", "priority_board", "s1_infeasible,policy,time_remaining", Verdict.KEEP),
    ("income_quality_not_amount", "Cấu phần payout và ma sát", "recap", "income_ledger,terminal_event", Verdict.REWRITE_OUTPUT),
    ("newbie_protection_mode", "Quyền lợi tân binh có provenance", "brief", "tenure,policy,settlement", Verdict.BLOCK_DATA_QUALITY),
    ("tomorrows_shift_lesson", "Bài học ca trước cho ca sau", "brief", "prior_only_history,next_shift", Verdict.BLOCK_DATA_QUALITY),
    ("why_no_relocation", "Vì sao không điều phối tới điểm nóng", "showcase", "demand,supply_incoming,capacity", Verdict.BLOCK_DATA_QUALITY),
)


def _combo_specs() -> tuple[IntentSpec, ...]:
    rows = []
    for combo, intent, surface, required, verdict in _COMBOS:
        # Ask the detectors instead of keeping a second list of their names.  The literal
        # allowlist that used to live here is how ``plan_stabilized_again`` came to advertise
        # MEASURED_REACHABILITY for a producer nobody wrote: the copy drifted and no gate
        # compared it with anything.  Same lesson as ``_EPISODE_REQUIRED`` below.
        implemented = combo in DETECTORS
        mode = (OutputMode.ACTION if surface == "nudge" else
                OutputMode.SUMMARY if surface in {"brief", "recap"} else
                OutputMode.EXPLANATION if surface in {"why", "plan_strip", "priority_board"}
                else OutputMode.OBSERVATION)
        readiness = (SignalReadiness.LOW_QUALITY if verdict is Verdict.BLOCK_DATA_QUALITY else
                     SignalReadiness.INSUFFICIENT if verdict is Verdict.ENRICH_FACTS else
                     SignalReadiness.SUFFICIENT)
        causes = ((FailureCause.DATA_QUALITY,) if readiness is SignalReadiness.LOW_QUALITY else
                  (FailureCause.COMBINATION,) if readiness is SignalReadiness.INSUFFICIENT else ())
        rows.append(_spec(
            f"COMBO-{combo}", "combo", combo.replace("_", " "),
            "Hiểu một diễn biến nhiều tín hiệu thay vì các event rời", intent,
            surface, mode, tuple(required.split(",")),
            producer="episode-runtime" if implemented else "research-only",
            readiness=readiness, output_quality="REWRITE" if verdict is Verdict.REWRITE_OUTPUT else "CLEAR",
            lifecycle="REACHABLE" if implemented else "NO_RUNTIME_PRODUCER",
            evidence="MEASURED_REACHABILITY" if implemented else "RESEARCH_PROBE",
            verdict=verdict, causes=causes,
            fix=("đi qua CompositeEpisode và canonical lifecycle" if implemented
                 else "không tạo producer đến khi đủ contract dữ liệu")))
    return tuple(rows)


# Read from the detectors instead of restating them.  The literal copy that used to live
# here named signals six of the eight detectors never emit (``s1_infeasible`` for a
# detector emitting ``bonus_feasibility_verdict``, ``semantic_plan_revision`` for one
# emitting ``plan_revision``, and so on), so :func:`assess_signal_set` would have returned
# INSUFFICIENT for every episode as soon as it was fed observations built from real
# signal names.  Nothing consumed it yet, which is exactly why the drift survived.
_EPISODE_REQUIRED = dict(EPISODE_SIGNALS)


def _assert_combo_declarations(detectors: Mapping[str, object],
                               combo_names: Iterable[str]) -> None:
    """The research inventory and the runtime must name the same eight things.

    ``_EPISODE_REQUIRED`` above closed the ``EP-`` branch by reading the detectors instead
    of restating them.  This closes the ``COMBO-`` branch, which stayed a hand-written list
    and duly drifted: it advertised ``plan_stabilized_again`` as measured while the detector
    that exists — ``plan_stability`` — had no row at all.  Nothing consumed the discrepancy,
    which is precisely why it survived two audits.

    A combo naming something outside ``detectors`` is *not* an error — four of them describe
    designs nobody has built, and saying so is the point.  The two errors are a detector with
    no row (invisible to the audit) and a name appearing twice (two verdicts, one producer).
    """
    names = list(combo_names)
    missing = sorted(set(detectors) - set(names))
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if missing or duplicated:
        raise RuntimeError(
            "inventory combo lệch với DETECTORS — "
            f"detector không có dòng combo: {missing}; "
            f"combo trùng tên: {duplicated}")


def _episode_specs() -> tuple[IntentSpec, ...]:
    rows = []
    for name, required in _EPISODE_REQUIRED.items():
        action = name == "energy_pressure_building"
        rewrite = name in {"waiting_and_rest_record", "income_quality_not_amount"}
        rows.append(_spec(
            f"EP-{name}", "episode", name.replace("_", " "),
            "Nhận một trạng thái hành trình đã ghép và có provenance",
            "Truyền đúng ý định episode bằng facts đủ chất lượng",
            "nudge" if action else "projection",
            OutputMode.ACTION if action else OutputMode.EXPLANATION,
            required, producer="episode-runtime", output_quality="REWRITE" if rewrite else "CLEAR",
            evidence="MEASURED_REACHABILITY",
            verdict=Verdict.REWRITE_OUTPUT if rewrite else Verdict.KEEP,
            causes=(FailureCause.SEMANTICS_OUTPUT,) if rewrite else (),
            fix="nối adapter vào canonical checkpoint lifecycle; giữ projection cho passive"))
    return tuple(rows)


_LEGACY = (
    _spec("LEGACY-09H", "legacy_anchor", "Card cố định 09:00", "Nhận brief đầu ca có dữ kiện",
          "Không dùng giờ cố định làm trigger nếu chưa có ca/state", "legacy", OutputMode.SUMMARY,
          ("actual_shift_start", "plan"), producer="dormant-cards.js",
          readiness=SignalReadiness.INSUFFICIENT, lifecycle="DORMANT",
          verdict=Verdict.RETIRE_OR_REJECT, causes=(FailureCause.TRIGGER,),
          fix="retire sau khi xác nhận không còn caller; brief dựa terminal/shift state thật"),
    _spec("LEGACY-14H", "legacy_anchor", "Card cố định 14:00", "Nhận insight khi có thay đổi thật",
          "Không phát filler theo đồng hồ", "legacy", OutputMode.OBSERVATION,
          ("material_state_change",), producer="dormant-cards.js",
          readiness=SignalReadiness.INSUFFICIENT, lifecycle="DORMANT",
          verdict=Verdict.RETIRE_OR_REJECT, causes=(FailureCause.TRIGGER,),
          fix="retire; dùng episode/decision moment"),
    _spec("LEGACY-21H30", "legacy_anchor", "Card cố định 21:30", "Nhận recap khi ca thật sự kết thúc",
          "Không dùng planned time thay terminal evidence", "legacy", OutputMode.SUMMARY,
          ("terminal_event", "journey", "income_ledger"), producer="dormant-cards.js",
          readiness=SignalReadiness.INSUFFICIENT, lifecycle="DORMANT",
          verdict=Verdict.RETIRE_OR_REJECT, causes=(FailureCause.STATE, FailureCause.TRIGGER),
          fix="retire; recap chỉ mở sau end_shift/day_end_settle"),
)


INTENT_REGISTRY: tuple[IntentSpec, ...] = (
    *_CANONICAL, *_idea_specs(), *_combo_specs(), *_episode_specs(), *_LEGACY)
INTENT_BY_ID: dict[str, IntentSpec] = {row.intent_id: row for row in INTENT_REGISTRY}

if len(INTENT_BY_ID) != len(INTENT_REGISTRY):
    raise RuntimeError("checkpoint intent registry có intent_id trùng")
if len([row for row in INTENT_REGISTRY if row.family == "idea_card"]) != 33:
    raise RuntimeError("checkpoint intent registry phải phủ đúng 33 Idea Card")
if len([row for row in INTENT_REGISTRY if row.family == "combo"]) != 12:
    raise RuntimeError("checkpoint intent registry phải phủ đúng 12 combo")
if set(_EPISODE_REQUIRED) != {
        row.intent_id.removeprefix("EP-") for row in INTENT_REGISTRY
        if row.family == "episode"}:
    raise RuntimeError("checkpoint intent registry thiếu episode runtime")
_assert_combo_declarations(DETECTORS, [combo for combo, *_rest in _COMBOS])
if any(row.serving_episode and row.serving_episode not in DETECTORS
       for row in INTENT_REGISTRY):
    raise RuntimeError("serving_episode trỏ tới detector không tồn tại")


_ACTION_INTENT = {
    "PROTECT_ELIGIBILITY": "CP-S1-BONUS",
    "ONLINE": "CP-S2-ONLINE",
    "SWAP": "CP-S2-SWAP",
    "REST": "CP-S2-REST",
    "END": "CP-S2-BOUNDARY",
    "EXTEND": "CP-S2-BOUNDARY",
    "REPOSITION_SIM_ONLY": "CP-S4-REPOSITION",
    "NO_ACTION": "CP-S2-ONLINE",
}


def intent_for_candidate(candidate: Mapping) -> IntentSpec:
    reason = str(candidate.get("reason_code") or "")
    if reason.startswith("idea."):
        intent_id = reason.removeprefix("idea.")
        if intent_id in INTENT_BY_ID:
            return INTENT_BY_ID[intent_id]
    if reason.startswith("episode."):
        episode_id = "EP-" + reason.removeprefix("episode.")
        if episode_id in INTENT_BY_ID:
            return INTENT_BY_ID[episode_id]
    action = str((candidate.get("current_action") or {}).get("code") or "NO_ACTION")
    intent_id = _ACTION_INTENT.get(action)
    if intent_id is None:
        raise KeyError(f"không có output intent cho action {action!r}")
    return INTENT_BY_ID[intent_id]


def assess_signal_set(spec: IntentSpec,
                      observations: Mapping[str, SignalObservation]) -> SignalAssessment:
    """Classify missing input separately from present-but-untrustworthy input."""
    missing = tuple(signal_id for signal_id in spec.required_signals
                    if signal_id not in observations or not observations[signal_id].present)
    if missing:
        return SignalAssessment(
            SignalReadiness.INSUFFICIENT, missing_signal_ids=missing,
            reasons=("missing_required_signal",))
    low_quality = tuple(signal_id for signal_id in spec.required_signals
                        if not all((observations[signal_id].fresh,
                                    observations[signal_id].trusted,
                                    observations[signal_id].provenance,
                                    observations[signal_id].grain_ok)))
    if low_quality:
        reasons: list[str] = []
        for signal_id in low_quality:
            item = observations[signal_id]
            if not item.fresh:
                reasons.append(f"stale:{signal_id}")
            if not item.trusted:
                reasons.append(f"untrusted:{signal_id}")
            if not item.provenance:
                reasons.append(f"missing_provenance:{signal_id}")
            if not item.grain_ok:
                reasons.append(f"wrong_grain:{signal_id}")
        return SignalAssessment(
            SignalReadiness.LOW_QUALITY, low_quality_signal_ids=low_quality,
            reasons=tuple(reasons))
    return SignalAssessment(SignalReadiness.SUFFICIENT)


def output_intent_payload(spec: IntentSpec) -> dict:
    must_answer = {
        OutputMode.ACTION: ("WHAT_CHANGED", "ACTION_NOW", "WINDOW", "WHY", "EVIDENCE", "UNCERTAINTY"),
        OutputMode.EXPLANATION: ("WHAT_CHANGED", "WHY", "EVIDENCE", "UNCERTAINTY"),
        OutputMode.OBSERVATION: ("WHAT_CHANGED", "EVIDENCE", "NO_ACTION", "UNCERTAINTY"),
        OutputMode.SUMMARY: ("WHAT_CHANGED", "EVIDENCE", "UNCERTAINTY"),
        OutputMode.INTERNAL_ONLY: ("NO_ACTION",),
    }[spec.mode]
    return {"intent_id": spec.intent_id, "mode": spec.mode.value,
            "driver_job": spec.driver_job, "must_answer": list(must_answer)}


_NUMBER_LABELS = {
    "soc_skip_count": "Số lần hành trình không phù hợp mức pin",
    "window_minutes": "Cửa sổ quan sát",
    "soc_pct": "Mức pin hiện tại",
    "soc_after_pct": "Mức pin sau đổi",
    "revision_count": "Số lần kế hoạch cập nhật",
    "quiet_minutes": "Thời gian kế hoạch giữ ổn định",
    "idle_minutes": "Tổng thời gian chờ",
    "idle_block_count": "Số khoảng chờ",
    "payout_total_vnd": "Tổng payout đã ghi nhận",
    "payout_trip_vnd": "Payout từ cuốc",
    "payout_day_bonus_vnd": "Thưởng ngày đã ghi nhận",
    "payout_mission_vnd": "Thưởng nhiệm vụ đã ghi nhận",
    "payout_newbie_vnd": "Hỗ trợ tân binh đã ghi nhận",
    "cancellation_count": "Số lần huỷ sau nhận",
    "gap_points": "Số điểm còn thiếu",
}


def semantic_number(number: Mapping, index: int) -> dict:
    """Give a solver number a stable semantic key without guessing its meaning.

    Producers that know the meaning must provide ``key``/``label``.  Legacy solver
    reports only provide value/unit/source; those receive an explicit legacy key and a
    generic unit label rather than being falsely called payout, points, or trips.
    """
    key = str(number.get("key") or f"solver_number_{index + 1}")
    unit = str(number.get("unit") or "")
    label = number.get("label") or _NUMBER_LABELS.get(key)
    if not label:
        label = {
            "vnd": "Số tiền từ solver",
            "points": "Số điểm từ solver",
            "trips": "Số cuốc từ solver",
            "percent": "Tỷ lệ từ solver",
            "pct": "Tỷ lệ từ solver",
            "minute": "Thời gian từ solver",
            "minutes": "Thời gian từ solver",
        }.get(unit, "Số liệu từ solver")
    return {"key": key, "label": str(label), "value": number.get("value"),
            "unit": unit, "source": str(number.get("source") or "unknown")}


def enrich_candidate_intent(
        candidate: Mapping, *, observations: Mapping[str, SignalObservation] | None = None
        ) -> dict:
    """Attach the 1.3 intent/evidence fields to a candidate before policy evaluation."""
    result = dict(candidate)
    spec = intent_for_candidate(result)
    if observations is not None and spec.signal_readiness is SignalReadiness.SUFFICIENT:
        assessment = assess_signal_set(spec, observations)
    else:
        assessment = SignalAssessment(spec.signal_readiness)
    available = sorted(
        signal_id for signal_id, observation in (observations or {}).items()
        if observation.present)
    if not available:
        available = sorted(str(value) for value in result.get("available_signal_ids") or ())
    result["source_episode_id"] = result.get("source_episode_id") or result.get("episode_id")
    result["episode_type"] = result.get("episode_type")
    action = str((result.get("current_action") or {}).get("code") or "NO_ACTION")
    result["decision_key"] = str(result.get("decision_key") or (
        f"{result.get('driver_id')}:{result.get('topic')}:{action}"))
    result["output_intent"] = dict(
        result.get("output_intent") or output_intent_payload(spec))
    result["evidence_quality"] = dict(result.get("evidence_quality") or {
        "status": assessment.status.value,
        "required_signal_ids": list(spec.required_signals),
        "available_signal_ids": available,
        "reasons": list(assessment.reasons),
    })
    refs = result.get("evidence_refs") or [
        result.get("snapshot_ref"), result.get("solver_artifact_ref"),
        *(result.get("solver_input_refs") or ()),
        *(result.get("solver_report_refs") or ()),
    ]
    result["evidence_refs"] = list(dict.fromkeys(
        str(ref) for ref in refs if ref))
    result["numbers"] = [semantic_number(number, index)
                         for index, number in enumerate(result.get("numbers") or ())]
    data_mode = str(result.get("data_mode") or "synthetic").lower()
    result["data_mode"] = {
        "simulated": "synthetic", "mock": "mock-realdata",
    }.get(data_mode, data_mode)
    return result


def registry_rows() -> list[dict]:
    return [row.to_dict() for row in INTENT_REGISTRY]
