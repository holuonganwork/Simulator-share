"""CompositeEpisode contract — derived driver-journey state, shared by every capability.

An episode is **not** a card.  It is a validated statement about the driver's journey
built from signals that already exist in the run: raw events, observer snapshots,
solver reports, checkpoint revisions, execution segments and the income ledger.
Routing decides afterwards whether an episode deserves a surface at all, and only a
subset of surfaces ever becomes an ``AdviceCheckpoint``.

Three invariants are enforced here rather than trusted:

1. **Provenance closure** — every number and every derived fact must name the component
   signals it came from, and each of those signals must exist on the episode.  A derived
   number with no traceable source cannot be constructed.
2. **Past-only evidence** — no component signal may carry a timestamp after
   ``detected_at``.  This makes the "future leak" class of bug a contract error instead of
   a review finding.
3. **Observed versus inferred** — every signal declares whether it was recorded by the
   world (``OBSERVED``) or reconstructed by us (``INFERRED``).  An episode built on any
   inferred signal cannot claim observed confidence.

The module has no simulator, UI, network or database dependency so the same contract is
usable from shadow replay, contract tests and hand-built fixtures.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable


class EpisodeContractError(ValueError):
    """Raised when an episode violates provenance, ordering or safety invariants."""


class EvidenceMode(str, Enum):
    """Did the world record this, or did we reconstruct it?"""

    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"


class DataMode(str, Enum):
    REAL = "REAL"
    LIVE = "LIVE"
    MOCK = "MOCK"
    SIMULATED = "SIMULATED"


class Authority(str, Enum):
    """Who is entitled to make this claim.

    ``SOLVER`` and ``POLICY`` are canonical: their values may be presented as decisions.
    ``OBSERVATION`` is a record of what happened.  ``DERIVED_RULE`` is our own
    deterministic reading of the above and must never outrank a solver verdict.
    """

    SOLVER = "SOLVER"
    POLICY = "POLICY"
    OBSERVATION = "OBSERVATION"
    DERIVED_RULE = "DERIVED_RULE"


class Surface(str, Enum):
    """Where an episode is allowed to appear.  Not every surface interrupts."""

    NUDGE = "NUDGE"                  # one primary actionable card, safety gated
    PLAN_STRIP = "PLAN_STRIP"        # persistent now/next strip, updates in place
    BANNER = "BANNER"                # proactive persistent notice, never a popup
    INSIGHT_DRAWER = "INSIGHT_DRAWER"  # passive composite insight, pull to read
    PRIORITY_BOARD = "PRIORITY_BOARD"  # objective/target status board
    TIMELINE = "TIMELINE"            # episode history lane
    RECAP = "RECAP"                  # end of shift screen
    WHY = "WHY"                      # explanation attached to another surface
    BRIEF = "BRIEF"                  # pre-shift brief


class InterruptionClass(str, Enum):
    """How much of the driver's attention budget a surface is allowed to spend."""

    INTERRUPTIVE = "INTERRUPTIVE"    # consumes the single primary slot
    PERSISTENT = "PERSISTENT"        # always on screen, update-in-place, no new card
    PASSIVE = "PASSIVE"              # visible only when the driver looks
    ON_DEMAND = "ON_DEMAND"          # only after an explicit tap
    DEFERRED = "DEFERRED"            # end of shift / next shift


class EpisodeStatus(str, Enum):
    OPEN = "OPEN"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


# Per-type lifecycle states.  Kept as a flat vocabulary so projections stay simple and a
# typo becomes a contract error instead of a silently new state.
LIFECYCLE_STATES = frozenset({
    "WATCHING", "DETECTED", "BUILDING", "ACTIONABLE", "RECOVERING", "RECOVERED",
    "STABLE", "UNSTABLE", "UPDATING", "EXPLAINED", "CLOSED_INFEASIBLE",
    "SUMMARISED", "EXPIRED",
    # Target lifecycle (A7).  ``AT_RISK`` exists so the transition is expressible, but a
    # detector may only reach it from a second solver verdict — never by re-deriving risk
    # from payout or gap.  In the pilot config no driver has a second S1 record, so the
    # state is currently unreachable *by data*, which is the honest outcome.
    "TARGET_ACTIVE", "AT_RISK", "TARGET_CLOSED",
})

# ``WATCHING`` is level 1 of a two-level episode: the disruption is observed but no
# canonical action exists to prepare for, so it may inform and must never interrupt.
PASSIVE_ONLY_STATES = frozenset({"WATCHING"})

# States that describe a *target* rather than the driver.  Guarded in ``validate_episode``.
TARGET_LIFECYCLE_STATES = frozenset({"TARGET_ACTIVE", "AT_RISK", "TARGET_CLOSED"})

TERMINAL_LIFECYCLE_STATES = frozenset({
    "RECOVERED", "CLOSED_INFEASIBLE", "SUMMARISED", "EXPIRED",
})


@dataclass(frozen=True)
class ComponentSignal:
    """One atomic input to an episode, with the provenance needed to defend it."""

    signal_type: str
    at_min: float
    value: Any
    unit: str
    source: str                       # producing component, e.g. "sim.event.order_skipped_soc"
    source_ref: str                   # addressable id: event index, checkpoint id, artifact id
    evidence_mode: EvidenceMode
    data_mode: DataMode
    confidence: float = 1.0
    freshness_min: float | None = None

    def __post_init__(self) -> None:
        if not self.signal_type:
            raise EpisodeContractError("component signal thiếu signal_type")
        if not self.source or not self.source_ref:
            raise EpisodeContractError(
                f"signal {self.signal_type!r} thiếu source/source_ref — không có provenance")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise EpisodeContractError(
                f"signal {self.signal_type!r} có confidence ngoài [0,1]: {self.confidence}")

    @property
    def ref(self) -> str:
        """Stable handle used by derived facts to point back at this signal."""
        return f"{self.signal_type}@{self.at_min:.3f}#{self.source_ref}"


@dataclass(frozen=True)
class DerivedFact:
    """A statement we computed.  It must name the signals it was computed from."""

    key: str
    value: Any
    unit: str
    method: str                       # how it was derived, in one short phrase
    from_signals: tuple[str, ...]     # ComponentSignal.ref values
    evidence_mode: EvidenceMode = EvidenceMode.INFERRED

    def __post_init__(self) -> None:
        if not self.from_signals:
            raise EpisodeContractError(
                f"derived fact {self.key!r} không truy ngược được về component signal nào")
        if not self.method:
            raise EpisodeContractError(f"derived fact {self.key!r} thiếu method")


@dataclass(frozen=True)
class EpisodeNumber:
    """A number that may be shown to the driver.  ``source`` is mandatory."""

    key: str
    value: Any
    unit: str
    source: str
    from_signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source:
            raise EpisodeContractError(
                f"number {self.key!r} không có source — số hiển thị phải có nguồn")


@dataclass(frozen=True)
class ActionRef:
    """A canonical action.  Episodes copy solver output; they never author an action."""

    code: str
    label_id: str
    window: dict[str, str] | None = None
    authority: Authority = Authority.SOLVER
    source_ref: str = ""


@dataclass(frozen=True)
class CompositeEpisode:
    """Validated derived state for one driver, one episode type, at one revision."""

    # --- identity -------------------------------------------------------------
    schema_version: str
    episode_id: str
    run_id: str
    session_id: str | None
    actor_id: int
    driver_id: str
    episode_type: str
    decision_key: str
    material_revision: int
    supersedes_episode_id: str | None

    # --- timing (minutes from the run base day; the adapter renders ISO) -------
    detected_at: float
    started_at: float
    last_updated_at: float
    valid_from: float
    valid_until: float | None
    evidence_window_start: float
    evidence_window_end: float

    # --- state ----------------------------------------------------------------
    status: EpisodeStatus
    lifecycle_state: str
    current_action: ActionRef | None
    future_plan: tuple[ActionRef, ...]
    action_window: dict[str, str] | None
    recommended_surface: Surface
    interruption_class: InterruptionClass
    priority: int
    confidence: float
    authority: Authority
    data_mode: DataMode
    is_mock: bool

    # --- evidence -------------------------------------------------------------
    component_signals: tuple[ComponentSignal, ...]
    evidence_refs: tuple[str, ...]
    derived_facts: tuple[DerivedFact, ...]
    numbers: tuple[EpisodeNumber, ...]
    caveats: tuple[str, ...]
    source_event_refs: tuple[str, ...] = ()
    source_snapshot_refs: tuple[str, ...] = ()
    source_checkpoint_refs: tuple[str, ...] = ()
    source_solver_report_refs: tuple[str, ...] = ()
    source_segment_refs: tuple[str, ...] = ()
    execution_observation_refs: tuple[str, ...] = ()

    # --- presentation ---------------------------------------------------------
    template_key: str = ""
    presentation_params: dict[str, Any] = field(default_factory=dict)
    allowed_actions: tuple[str, ...] = ()
    safety_flags: tuple[str, ...] = ()
    privacy_flags: tuple[str, ...] = ()
    material_fingerprint: str = ""

    # The AdviceCheckpoint this episode is a *new state of*, not a companion to.  When it
    # is set, routing repaints that card instead of raising a second one, and the episode
    # can never consume an interruption of its own.  A7 exists entirely to say something
    # about a target the driver already has a card for; a separate card would be the same
    # subject twice.
    supersedes_checkpoint_id: str | None = None

    # -------------------------------------------------------------------------
    @property
    def evidence_mode(self) -> EvidenceMode:
        """An episode is only OBSERVED when every signal under it was observed."""
        if any(signal.evidence_mode is EvidenceMode.INFERRED
               for signal in self.component_signals):
            return EvidenceMode.INFERRED
        return EvidenceMode.OBSERVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "actor_id": int(self.actor_id),
            "driver_id": self.driver_id,
            "episode_type": self.episode_type,
            "decision_key": self.decision_key,
            "material_revision": int(self.material_revision),
            "supersedes_episode_id": self.supersedes_episode_id,
            "detected_at_min": round(float(self.detected_at), 3),
            "started_at_min": round(float(self.started_at), 3),
            "last_updated_at_min": round(float(self.last_updated_at), 3),
            "valid_from_min": round(float(self.valid_from), 3),
            "valid_until_min": (None if self.valid_until is None
                                else round(float(self.valid_until), 3)),
            "evidence_window_start_min": round(float(self.evidence_window_start), 3),
            "evidence_window_end_min": round(float(self.evidence_window_end), 3),
            "status": self.status.value,
            "lifecycle_state": self.lifecycle_state,
            "current_action": (None if self.current_action is None else {
                "code": self.current_action.code,
                "label_id": self.current_action.label_id,
                "window": self.current_action.window,
                "authority": self.current_action.authority.value,
                "source_ref": self.current_action.source_ref,
            }),
            "future_plan": [{
                "code": step.code, "label_id": step.label_id, "window": step.window,
                "authority": step.authority.value, "source_ref": step.source_ref,
            } for step in self.future_plan],
            "action_window": self.action_window,
            "recommended_surface": self.recommended_surface.value,
            "interruption_class": self.interruption_class.value,
            "priority": int(self.priority),
            "confidence": round(float(self.confidence), 4),
            "authority": self.authority.value,
            "evidence_mode": self.evidence_mode.value,
            "data_mode": self.data_mode.value,
            "is_mock": bool(self.is_mock),
            "component_signals": [{
                "signal_type": s.signal_type, "at_min": round(float(s.at_min), 3),
                "value": s.value, "unit": s.unit, "source": s.source,
                "source_ref": s.source_ref, "evidence_mode": s.evidence_mode.value,
                "data_mode": s.data_mode.value, "confidence": round(float(s.confidence), 4),
                "freshness_min": s.freshness_min, "ref": s.ref,
            } for s in self.component_signals],
            "evidence_refs": list(self.evidence_refs),
            "derived_facts": [{
                "key": f.key, "value": f.value, "unit": f.unit, "method": f.method,
                "from_signals": list(f.from_signals),
                "evidence_mode": f.evidence_mode.value,
            } for f in self.derived_facts],
            "numbers": [{
                "key": n.key, "value": n.value, "unit": n.unit, "source": n.source,
                "from_signals": list(n.from_signals),
            } for n in self.numbers],
            "caveats": list(self.caveats),
            "source_event_refs": list(self.source_event_refs),
            "source_snapshot_refs": list(self.source_snapshot_refs),
            "source_checkpoint_refs": list(self.source_checkpoint_refs),
            "source_solver_report_refs": list(self.source_solver_report_refs),
            "source_segment_refs": list(self.source_segment_refs),
            "execution_observation_refs": list(self.execution_observation_refs),
            "template_key": self.template_key,
            "presentation_params": dict(self.presentation_params),
            "allowed_actions": list(self.allowed_actions),
            "safety_flags": list(self.safety_flags),
            "privacy_flags": list(self.privacy_flags),
            "material_fingerprint": self.material_fingerprint,
            "supersedes_checkpoint_id": self.supersedes_checkpoint_id,
        }


SCHEMA_VERSION = "1.2.0"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def episode_identity(run_id: str, actor_id: int, episode_type: str,
                     started_at: float) -> str:
    """Deterministic episode id, stable across revisions of the same episode.

    The id deliberately excludes ``detected_at`` and every material field: a revision of
    the same episode must keep its identity so the UI can update in place instead of
    stacking a second card.
    """
    digest = hashlib.sha256(_canonical_json({
        "run_id": run_id, "actor_id": int(actor_id), "episode_type": episode_type,
        "started_at": round(float(started_at), 3),
    }).encode("utf-8")).hexdigest()[:24]
    return f"ep-{digest}"


def material_fingerprint(episode_type: str, lifecycle_state: str,
                         current_action: ActionRef | None,
                         future_plan: Iterable[ActionRef],
                         action_window: dict[str, str] | None,
                         numbers: Iterable[EpisodeNumber]) -> str:
    """Digest only what changes the meaning for the driver.

    Detection time, evidence refs and confidence are excluded on purpose: re-detecting
    the same situation one minute later is not a new thing to say.  The first future step
    *is* included — a plan whose next action moved is a different statement.
    """
    head = next(iter(future_plan), None)
    material = {
        "episode_type": episode_type,
        "lifecycle_state": lifecycle_state,
        "current_action": None if current_action is None else {
            "code": current_action.code, "window": current_action.window},
        "future_head": None if head is None else {"code": head.code, "window": head.window},
        "action_window": action_window,
        "numbers": sorted(
            [{"key": n.key, "value": n.value, "unit": n.unit} for n in numbers],
            key=lambda item: str(item["key"])),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


# Words an episode template is never allowed to encode, grouped by the boundary they break.
BANNED_PARAM_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "causal_income": ("uplift", "income_gain", "extra_vnd", "thanks_to", "caused_by",
                      "attributed_income", "loss_vnd", "lost_income"),
    "per_order": ("accept_this_order", "decline_this_order", "cancel_this_order",
                  "reject_order"),
    "adherence": ("adherence", "followed_advice", "compliance_rate", "obeyed",
                  "ignored_advice"),
}


def validate_episode(episode: CompositeEpisode) -> CompositeEpisode:
    """Fail closed on every invariant the product boundary depends on."""
    if episode.schema_version != SCHEMA_VERSION:
        raise EpisodeContractError(
            f"schema_version {episode.schema_version!r} != {SCHEMA_VERSION!r}")
    if not episode.component_signals:
        raise EpisodeContractError(
            f"episode {episode.episode_type!r} không có component signal nào")
    if not episode.decision_key.strip():
        raise EpisodeContractError(
            f"episode {episode.episode_type!r} thiếu decision_key")
    if not episode.evidence_refs:
        raise EpisodeContractError(
            f"episode {episode.episode_type!r} thiếu evidence_refs")
    if len(set(episode.evidence_refs)) != len(episode.evidence_refs):
        raise EpisodeContractError(
            f"episode {episode.episode_type!r} có evidence_refs trùng lặp")
    if episode.lifecycle_state not in LIFECYCLE_STATES:
        raise EpisodeContractError(
            f"lifecycle_state không hợp lệ: {episode.lifecycle_state!r}")

    # --- 1. past-only evidence ------------------------------------------------
    for signal in episode.component_signals:
        if float(signal.at_min) > float(episode.detected_at) + 1e-6:
            raise EpisodeContractError(
                f"FUTURE LEAK: signal {signal.signal_type!r} tại {signal.at_min} "
                f"nằm sau detected_at {episode.detected_at}")
    if float(episode.evidence_window_end) > float(episode.detected_at) + 1e-6:
        raise EpisodeContractError(
            f"FUTURE LEAK: evidence_window_end {episode.evidence_window_end} "
            f"vượt detected_at {episode.detected_at}")
    if float(episode.evidence_window_start) > float(episode.evidence_window_end) + 1e-6:
        raise EpisodeContractError("evidence window đảo ngược")
    if float(episode.started_at) > float(episode.detected_at) + 1e-6:
        raise EpisodeContractError("started_at nằm sau detected_at")
    if (episode.valid_until is not None
            and float(episode.valid_until) < float(episode.valid_from) - 1e-6):
        raise EpisodeContractError("valid_until nằm trước valid_from")

    # --- 2. provenance closure ------------------------------------------------
    known = {signal.ref for signal in episode.component_signals}
    missing_component_refs = sorted(
        {signal.source_ref for signal in episode.component_signals}
        - set(episode.evidence_refs))
    if missing_component_refs:
        raise EpisodeContractError(
            f"episode {episode.episode_type!r} không đưa component source vào "
            f"evidence_refs: {missing_component_refs}")
    for fact in episode.derived_facts:
        missing = [ref for ref in fact.from_signals if ref not in known]
        if missing:
            raise EpisodeContractError(
                f"derived fact {fact.key!r} trỏ tới signal không tồn tại: {missing}")
    for number in episode.numbers:
        missing = [ref for ref in number.from_signals if ref not in known]
        if missing:
            raise EpisodeContractError(
                f"number {number.key!r} trỏ tới signal không tồn tại: {missing}")

    # --- 3. observed versus inferred -----------------------------------------
    if (episode.evidence_mode is EvidenceMode.INFERRED
            and "inferred_evidence" not in episode.safety_flags):
        raise EpisodeContractError(
            f"episode {episode.episode_type!r} dựng trên signal INFERRED nhưng thiếu "
            "safety flag 'inferred_evidence'")

    # --- 4. product boundary --------------------------------------------------
    blob = _canonical_json({
        "template_key": episode.template_key,
        "params": episode.presentation_params,
        "numbers": [n.key for n in episode.numbers],
        "facts": [f.key for f in episode.derived_facts],
    }).lower()
    for boundary, fragments in BANNED_PARAM_FRAGMENTS.items():
        hit = next((fragment for fragment in fragments if fragment in blob), None)
        if hit is not None:
            raise EpisodeContractError(
                f"episode {episode.episode_type!r} vi phạm ranh giới {boundary}: {hit!r}")

    # --- 5. canonical action ownership ---------------------------------------
    for action in (episode.current_action, *episode.future_plan):
        if action is None:
            continue
        if action.authority is not Authority.SOLVER:
            raise EpisodeContractError(
                f"action {action.code!r} không mang authority SOLVER — episode không "
                "được tự tạo hành động")
        if not action.source_ref:
            raise EpisodeContractError(
                f"action {action.code!r} thiếu source_ref về bản ghi solver")

    # --- 6. interruption budget ----------------------------------------------
    if (episode.interruption_class is InterruptionClass.INTERRUPTIVE
            and episode.recommended_surface is not Surface.NUDGE):
        raise EpisodeContractError(
            "chỉ NUDGE mới được INTERRUPTIVE — surface khác không tiêu ngân sách ngắt quãng")
    if (episode.lifecycle_state in PASSIVE_ONLY_STATES
            and episode.interruption_class is InterruptionClass.INTERRUPTIVE):
        raise EpisodeContractError(
            f"lifecycle_state {episode.lifecycle_state!r} là mức quan sát (level 1) — "
            "không được ngắt quãng khi chưa có hành động canonical để chuẩn bị")

    # --- 7. target lifecycle --------------------------------------------------
    # A target state is a claim about a *solver verdict*, so it may only ride on an
    # episode that carries the verdict's own authority.  This is what stops AT_RISK from
    # being re-derived out of payout, gap or trip count by a future detector.
    if episode.lifecycle_state in TARGET_LIFECYCLE_STATES:
        if episode.authority is not Authority.SOLVER:
            raise EpisodeContractError(
                f"target state {episode.lifecycle_state!r} cần authority SOLVER, "
                f"nhận {episode.authority.value}")
        if not episode.source_solver_report_refs:
            raise EpisodeContractError(
                f"target state {episode.lifecycle_state!r} thiếu source_solver_report_refs")
    if episode.supersedes_checkpoint_id and not episode.source_checkpoint_refs:
        raise EpisodeContractError(
            "supersedes_checkpoint_id đặt nhưng không có source_checkpoint_refs — "
            "không truy được card bị thay thế")
    return episode


def finalize(episode: CompositeEpisode) -> CompositeEpisode:
    """Stamp the material fingerprint and validate in one place."""
    stamped = replace(episode, material_fingerprint=material_fingerprint(
        episode.episode_type, episode.lifecycle_state, episode.current_action,
        episode.future_plan, episode.action_window, episode.numbers))
    return validate_episode(stamped)
