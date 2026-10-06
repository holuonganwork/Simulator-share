"""Composite episode detectors A1–A7.

Every detector obeys the same three rules, which is why they can share one contract:

* **Fire on the last required signal, never before it.**  An episode about a completed
  swap fires when the re-plan lands, not when the battery first became a problem.  An
  episode about waiting fires *while the driver is still waiting*, not at the moment the
  wait ends — saying "you have been waiting a while" to someone who just got a trip is
  the failure mode this rule exists to prevent.
* **Read only what already happened.**  Detectors may scan the whole view to locate the
  firing minute, but every value they put on the episode must carry a timestamp at or
  before ``detected_at``.  ``tests/test_episode_no_future_leak.py`` proves this by
  re-running each detector against ``view.truncated(detected_at)``.
* **Copy canonical output, never author it.**  ``current_action`` and ``future_plan``
  come from a persisted solver record with its checkpoint id as ``source_ref``.  A
  detector that cannot find a solver record does not invent one; it declines to fire.

Thresholds live in :data:`PROBES`.  They are discovery probes, not approved policy, and
``scripts/episode_threshold_sensitivity.py`` reports how the population moves when they
change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable

from gsm_core.episodes.contract import (
    ActionRef,
    Authority,
    CompositeEpisode,
    ComponentSignal,
    DataMode,
    DerivedFact,
    EpisodeNumber,
    EpisodeStatus,
    EvidenceMode,
    InterruptionClass,
    SCHEMA_VERSION,
    Surface,
    episode_identity,
    finalize,
)
from gsm_core.episodes.view import ActorRunView, CheckpointView

# --------------------------------------------------------------------------- probes
PROBES: dict[str, float] = {
    # A1: how far back a SOC disruption still counts as the same energy episode.
    "energy_window_min": 90.0,
    # A1: how long the *same* planned swap window must already have been on the plan
    # before it is treated as real rather than as one poll's artifact.
    "swap_window_persistence_min": 20.0,
    # A2: how long after a completed swap a re-plan still closes the episode.
    "recovery_replan_window_min": 60.0,
    # A3: how long after a disruption a plan revision is still worth explaining.
    "slip_window_min": 60.0,
    # A4: churn is >= this many material revisions inside the churn window ...
    "churn_revisions": 3.0,
    "churn_window_min": 120.0,
    # ... and the plan counts as stable again after this much quiet.
    "stable_quiet_min": 60.0,
    # A5: owner-approved proactive banner after one continuous 30-minute wait.  This is a
    # product trigger, not a calibrated claim about fatigue; copy is limited to the
    # observed plan and separately recorded wait/rest facts.
    "continuous_idle_banner_min": 30.0,
    # A8: adjacent past windows for the operational-phase comparison.  120 rather than 60
    # because the median 60-minute window contains one completed trip, and one trip either
    # way moves utilisation by ~100 points.
    "phase_window_min": 120.0,
    "phase_min_trips_each_window": 3.0,
    "phase_material_delta_pp": 25.0,
    # A6: how many settled income sources make a breakdown worth showing.
    "income_min_sources": 2.0,
}

DISRUPTION_KINDS = ("order_skipped_soc", "order_cancelled_after_accept", "swap_failed")


def clock(minute: float) -> str:
    """Minute-of-day → ``HH:MM`` for anything a driver reads.

    Simulation minutes are the engine's unit, not a person's.  "phút 1111.2" is
    unreadable on a phone; 18:31 is the same fact.  Durations stay in minutes — those
    really are minutes to a driver — so only timestamps go through here.
    """
    total = int(round(float(minute)))
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


# ------------------------------------------------------------------------- helpers
def _event_signal(view: ActorRunView, kind: str, at_min: float, value, unit: str,
                  ref: str) -> ComponentSignal:
    return ComponentSignal(
        signal_type=kind, at_min=float(at_min), value=value, unit=unit,
        source=f"sim.event.{kind}", source_ref=ref,
        evidence_mode=EvidenceMode.OBSERVED, data_mode=view.data_mode)


def _checkpoint_signal(view: ActorRunView, checkpoint: CheckpointView, signal_type: str,
                       value, unit: str) -> ComponentSignal:
    return ComponentSignal(
        signal_type=signal_type, at_min=float(checkpoint.at_min), value=value, unit=unit,
        source=f"solver.{'+'.join(checkpoint.solver_set) or '?'}.checkpoint",
        source_ref=checkpoint.checkpoint_id,
        evidence_mode=EvidenceMode.OBSERVED, data_mode=view.data_mode,
        confidence=float(checkpoint.confidence or 0.0))


def _snapshot_signal(view: ActorRunView, at_min: float, signal_type: str, value,
                     unit: str) -> ComponentSignal | None:
    snapshot = view.snapshot_at(at_min)
    if snapshot is None:
        return None
    return ComponentSignal(
        signal_type=signal_type, at_min=float(snapshot.t_min), value=value, unit=unit,
        source="sim.observer_snapshot", source_ref=snapshot.ref or f"snap@{snapshot.t_min}",
        evidence_mode=EvidenceMode.OBSERVED, data_mode=view.data_mode)


def _live_validity(at_min: float, candidate: float | None) -> float | None:
    """A validity boundary that has already passed is not a validity boundary.

    Solver records carry the window of the *record*, which may have closed before the
    episode's firing minute.  Copying it verbatim produced episodes whose window ended
    before it began; the contract caught it.  A closed window becomes ``None`` (the
    episode has no bounded validity) and callers that genuinely need a live plan check
    :func:`_plan_is_live` first and decline instead.
    """
    if candidate is None:
        return None
    return None if float(candidate) < float(at_min) - 1e-6 else float(candidate)


def _plan_is_live(checkpoint: CheckpointView, at_min: float) -> bool:
    """True when the solver record still covers ``at_min``."""
    return (checkpoint.valid_until_min is None
            or float(checkpoint.valid_until_min) >= float(at_min) - 1e-6)


def _plan_actions(checkpoint: CheckpointView) -> tuple[ActionRef | None, tuple[ActionRef, ...]]:
    """Copy now/next straight off a persisted solver record.

    One presentation rule is applied here: a "next" step identical to the current action
    in both code *and* window is a restatement, not a next step, and is dropped.  S2
    schedules legitimately contain such a step (``next_action`` can share the current
    bucket), and rendering it produced "Bây giờ: ONLINE · 23:00 → 00:00 / Sắp tới:
    ONLINE · 23:00 → 00:00" on screen — which teaches the driver that the two labels mean
    nothing.  The canonical record is untouched; ``source_ref`` still points at it.
    """
    current = None
    if checkpoint.current_code:
        current = ActionRef(
            code=checkpoint.current_code,
            label_id=f"action.{checkpoint.current_code.lower()}",
            window=checkpoint.action_window, authority=Authority.SOLVER,
            source_ref=checkpoint.checkpoint_id)
    future: tuple[ActionRef, ...] = ()
    if checkpoint.future_code:
        restates_current = (
            current is not None
            and checkpoint.future_code == current.code
            and (checkpoint.future_window or None) == (current.window or None))
        if not restates_current:
            future = (ActionRef(
                code=checkpoint.future_code,
                label_id=f"action.{checkpoint.future_code.lower()}",
                window=checkpoint.future_window, authority=Authority.SOLVER,
                source_ref=checkpoint.checkpoint_id),)
    return current, future


def _build(view: ActorRunView, *, episode_type: str, started_at: float,
           detected_at: float, lifecycle_state: str, status: EpisodeStatus,
           surface: Surface, interruption: InterruptionClass, priority: int,
           authority: Authority, signals: list[ComponentSignal],
           facts: list[DerivedFact], numbers: list[EpisodeNumber],
           caveats: list[str], template_key: str, params: dict,
           evidence_window_start: float, current_action: ActionRef | None = None,
           future_plan: tuple[ActionRef, ...] = (), action_window: dict | None = None,
           valid_until: float | None = None, revision: int = 1,
           supersedes: str | None = None, safety_flags: tuple[str, ...] = (),
           allowed_actions: tuple[str, ...] = (),
           solver_report_refs: tuple[str, ...] = (),
           supersedes_checkpoint_id: str | None = None,
           decision_key: str | None = None,
           confidence: float = 0.8) -> CompositeEpisode:
    flags = list(safety_flags)
    if any(s.evidence_mode is EvidenceMode.INFERRED for s in signals):
        if "inferred_evidence" not in flags:
            flags.append("inferred_evidence")
    source_refs = tuple(dict.fromkeys([
        *(signal.source_ref for signal in signals),
        *solver_report_refs,
    ]))
    return finalize(CompositeEpisode(
        schema_version=SCHEMA_VERSION,
        episode_id=episode_identity(view.run_id, view.actor_id, episode_type, started_at),
        run_id=view.run_id, session_id=None, actor_id=view.actor_id,
        driver_id=view.driver_id, episode_type=episode_type,
        decision_key=(decision_key or
                      f"{view.driver_id}:shift:{view.run_id}:{episode_type}:{started_at:.3f}"),
        material_revision=int(revision), supersedes_episode_id=supersedes,
        detected_at=float(detected_at), started_at=float(started_at),
        last_updated_at=float(detected_at), valid_from=float(detected_at),
        valid_until=valid_until,
        evidence_window_start=float(evidence_window_start),
        evidence_window_end=float(detected_at),
        status=status, lifecycle_state=lifecycle_state,
        current_action=current_action, future_plan=future_plan,
        action_window=action_window, recommended_surface=surface,
        interruption_class=interruption, priority=int(priority),
        confidence=float(confidence), authority=authority,
        data_mode=view.data_mode, is_mock=view.is_mock,
        component_signals=tuple(signals), evidence_refs=source_refs,
        derived_facts=tuple(facts),
        numbers=tuple(numbers), caveats=tuple(caveats),
        source_event_refs=tuple(s.source_ref for s in signals
                                if s.source.startswith("sim.event.")),
        source_snapshot_refs=tuple(s.source_ref for s in signals
                                   if s.source == "sim.observer_snapshot"),
        source_checkpoint_refs=tuple(s.source_ref for s in signals
                                     if s.source.startswith("solver.")),
        source_solver_report_refs=tuple(solver_report_refs),
        template_key=template_key, presentation_params=dict(params),
        allowed_actions=allowed_actions, safety_flags=tuple(flags),
        supersedes_checkpoint_id=supersedes_checkpoint_id))


# ------------------------------------------------------------------ A1 energy pressure
def _energy_watch(view: ActorRunView, skips: list, started_at: float, at: float,
                  revision: int) -> CompositeEpisode:
    """Level 1: the disruption is on the record, but nothing is planned to prepare for.

    Deliberately thinner than level 2.  It carries no ``current_action`` and no
    ``future_plan`` — there is no solver record to copy one from, and inventing one is the
    exact failure the contract forbids.  Authority is therefore ``OBSERVATION``: this
    reports what the world logged, it does not recommend.
    """
    in_window = [e for e in skips
                 if started_at - 1e-6 <= float(e.t_min) <= at + 1e-6]
    signals = [_event_signal(view, "order_skipped_soc", float(e.t_min),
                             e.detail.get("order_id"), "order_id", e.ref)
               for e in in_window]
    soc = view.soc_at(at)
    soc_signal = _snapshot_signal(view, at, "soc_observed", soc, "pct")
    if soc_signal is not None:
        signals.append(soc_signal)
    skip_refs = tuple(s.ref for s in signals if s.signal_type == "order_skipped_soc")
    facts = [DerivedFact(
        key="soc_skip_count", value=len(in_window), unit="count",
        method="đếm order_skipped_soc trong cửa sổ episode, chỉ tới thời điểm phát hiện",
        from_signals=skip_refs)]
    numbers = [EpisodeNumber("soc_skip_count", len(in_window), "lần",
                             "sim.event.order_skipped_soc", skip_refs)]
    if soc is not None and soc_signal is not None:
        numbers.append(EpisodeNumber("soc_pct", round(float(soc), 1), "%",
                                     "sim.observer_snapshot", (soc_signal.ref,)))
    return _build(
        view, episode_type="energy_pressure_building", started_at=started_at,
        detected_at=at, lifecycle_state="WATCHING", status=EpisodeStatus.OPEN,
        surface=Surface.INSIGHT_DRAWER, interruption=InterruptionClass.PASSIVE,
        priority=30, authority=Authority.OBSERVATION, signals=signals, facts=facts,
        numbers=numbers,
        caveats=["Số lần bỏ đơn vì pin là quan sát, không phải dự báo.",
                 "Chưa có kế hoạch đổi pin còn hiệu lực để chuẩn bị.",
                 "Hệ thống không khuyên nhận hay từ chối một đơn cụ thể."],
        template_key="episode.energy_watch",
        params={"soc_skip_count": len(in_window)},
        evidence_window_start=started_at, revision=revision,
        allowed_actions=("expand_why",), confidence=0.7)


def detect_energy_pressure_building(view: ActorRunView) -> list[CompositeEpisode]:
    """Energy pressure, at two levels — observation and preparation.

    A low SOC number on its own is not an episode: the driver can read the battery, and
    measurement says so plainly — 234 of 270 actors drop to 25% at some point and the
    fleet's median SOC floor is 13.7%.  A card keyed on SOC alone would fire for almost
    everyone and separate nothing.  What the driver cannot see is that the battery has
    **started costing them offers**.  That observation is the subject of the episode.

    Level 2 ``ACTIONABLE`` additionally needs a live, committed planned swap: something
    concrete to prepare for, which earns the interruption.

    Level 1 ``WATCHING`` is the same observation with no live plan behind it.  It exists
    because the strict version discarded it: of 67 real SOC-skips, 30 were dropped purely
    because the solver record naming the swap had already expired, costing 28 actors any
    acknowledgement that their battery was affecting their work.  Staleness of a *record*
    is not absence of an *event*.  Level 1 carries ``OBSERVATION`` authority, names no
    action, and is barred from interrupting by the contract itself.
    """
    window = PROBES["energy_window_min"]
    skips = view.events_of("order_skipped_soc")
    if not skips:
        return []

    episodes: list[CompositeEpisode] = []
    started_at: float | None = None
    revision = 0
    for skip in skips:
        at = float(skip.t_min)
        if started_at is not None and at - started_at > window:
            started_at = None          # previous pressure episode aged out
            revision = 0
        plan = view.latest_plan_at(at)
        actionable_plan = (plan is not None and plan.future_code == "SWAP"
                           and _plan_is_live(plan, at))
        if started_at is None:
            started_at = at
        revision += 1
        if not actionable_plan:
            episodes.append(_energy_watch(view, skips, started_at, at, revision))
            continue
        in_window = [e for e in skips
                     if started_at - 1e-6 <= float(e.t_min) <= at + 1e-6]
        signals = [_event_signal(view, "order_skipped_soc", float(e.t_min),
                                 e.detail.get("order_id"), "order_id", e.ref)
                   for e in in_window]
        signals.append(_checkpoint_signal(view, plan, "planned_swap", plan.future_code,
                                          "action_code"))
        soc = view.soc_at(at)
        soc_signal = _snapshot_signal(view, at, "soc_observed", soc, "pct")
        if soc_signal is not None:
            signals.append(soc_signal)

        skip_refs = tuple(s.ref for s in signals if s.signal_type == "order_skipped_soc")
        facts = [DerivedFact(
            key="soc_skip_count", value=len(in_window), unit="count",
            method="đếm order_skipped_soc trong cửa sổ episode, chỉ tới thời điểm phát hiện",
            from_signals=skip_refs)]
        numbers = [EpisodeNumber("soc_skip_count", len(in_window), "lần",
                                 "sim.event.order_skipped_soc", skip_refs)]
        if at - started_at > 0.05:
            # At the first observation the window is a point.  "cửa sổ quan sát: 0 phút"
            # is not a fact worth a row on a phone screen.
            numbers.append(EpisodeNumber("window_minutes", round(at - started_at, 1),
                                         "phút", "episode.evidence_window", skip_refs))
        if soc is not None and soc_signal is not None:
            numbers.append(EpisodeNumber("soc_pct", round(float(soc), 1), "%",
                                         "sim.observer_snapshot", (soc_signal.ref,)))

        # ACTIONABLE means the planned swap is worth preparing for, which needs the plan
        # to have *committed* to it — either by restating the same window for a while, or
        # by recomputing and still landing on it.  One poll saying SWAP is not a commitment.
        # Both tests read only records at or before ``at``.
        revisions_after_start = [r for r in view.plan_revisions_until(at)
                                 if float(r.at_min) >= started_at]
        window_start = (plan.future_window or {}).get("start")
        same_window = [c for c in view.checkpoints_until(at, solver="S2")
                       if c.future_code == "SWAP"
                       and (c.future_window or {}).get("start") == window_start]
        persisted_min = (at - float(same_window[0].at_min)) if same_window else 0.0
        committed = (bool(revisions_after_start)
                     or persisted_min >= PROBES["swap_window_persistence_min"])
        state = ("ACTIONABLE" if committed
                 else "BUILDING" if len(in_window) >= 2 else "DETECTED")
        current, future = _plan_actions(plan)
        episodes.append(_build(
            view, episode_type="energy_pressure_building", started_at=started_at,
            detected_at=at, lifecycle_state=state, status=EpisodeStatus.OPEN,
            surface=Surface.NUDGE if state == "ACTIONABLE" else Surface.PLAN_STRIP,
            interruption=(InterruptionClass.INTERRUPTIVE if state == "ACTIONABLE"
                          else InterruptionClass.PERSISTENT),
            priority=10, authority=Authority.SOLVER, signals=signals, facts=facts,
            numbers=numbers,
            caveats=["Số lần bỏ đơn vì pin là quan sát, không phải dự báo.",
                     "Hệ thống không khuyên nhận hay từ chối một đơn cụ thể."],
            template_key=("episode.energy_pressure_building"
                          if at - started_at > 0.05
                          else "episode.energy_pressure_building_first"),
            params={"soc_skip_count": len(in_window),
                    "window_minutes": round(at - started_at, 1)},
            evidence_window_start=started_at, current_action=current, future_plan=future,
            action_window=plan.action_window,
            valid_until=_live_validity(at, plan.valid_until_min),
            revision=revision, allowed_actions=("expand_why", "dismiss"),
            # Reuse the canonical S2 decision identity when available: the energy
            # episode enriches that plan with evidence; it is not a second decision.
            decision_key=(plan.decision_key or
                          f"{view.driver_id}:shift:{view.run_id}:swap:{plan.checkpoint_id}"),
            confidence=0.75))
    return episodes


# ---------------------------------------------------------------- A2 energy recovered
def detect_energy_episode_recovered(view: ActorRunView) -> list[CompositeEpisode]:
    """Close the energy loop only when the world says it closed.

    Required in order: a SOC disruption, a swap that *started*, a swap that *completed*,
    an SOC reading after it, and a plan recomputed afterwards.  The episode fires on the
    re-plan — the last of those — so it never announces a recovery that has not happened.
    """
    skips = view.events_of("order_skipped_soc")
    if not skips:
        return []
    swaps = view.events_of("go_swap")
    dones = view.events_of("swap_done")
    episodes: list[CompositeEpisode] = []
    consumed: set[float] = set()

    for skip in skips:
        t0 = float(skip.t_min)
        if any(abs(t0 - used) < 1e-6 for used in consumed):
            continue
        swap = next((e for e in swaps
                     if t0 < float(e.t_min) <= t0 + PROBES["energy_window_min"]), None)
        if swap is None:
            continue
        done = next((e for e in dones if float(e.t_min) >= float(swap.t_min)), None)
        if done is None:
            continue
        replan = next((r for r in view.plan_revisions_until(view.shift_end_min)
                       if float(done.t_min) < float(r.at_min)
                       <= float(done.t_min) + PROBES["recovery_replan_window_min"]), None)
        if replan is None:
            continue
        at = float(replan.at_min)
        consumed.add(t0)

        soc_after = view.soc_at(float(done.t_min))
        signals = [
            _event_signal(view, "order_skipped_soc", t0, skip.detail.get("order_id"),
                          "order_id", skip.ref),
            _event_signal(view, "go_swap", float(swap.t_min), True, "bool", swap.ref),
            _event_signal(view, "swap_done", float(done.t_min), True, "bool", done.ref),
            _checkpoint_signal(view, replan, "plan_recomputed", replan.semantic,
                               "plan_semantic"),
        ]
        soc_signal = _snapshot_signal(view, float(done.t_min), "soc_after_swap",
                                      soc_after, "pct")
        if soc_signal is not None:
            signals.append(soc_signal)

        numbers = [
            EpisodeNumber("swap_start_at", clock(float(swap.t_min)), "",
                          "sim.event.go_swap", (signals[1].ref,)),
            EpisodeNumber("swap_done_at", clock(float(done.t_min)), "",
                          "sim.event.swap_done", (signals[2].ref,)),
            EpisodeNumber("replan_at", clock(at), "",
                          "solver.S2.checkpoint", (signals[3].ref,)),
        ]
        if soc_after is not None and soc_signal is not None:
            numbers.append(EpisodeNumber("soc_after_pct", round(float(soc_after), 1), "%",
                                         "sim.observer_snapshot", (soc_signal.ref,)))
        facts = [DerivedFact(
            key="recovery_sequence_complete", value=True, unit="bool",
            method="skip → go_swap → swap_done → plan revision, theo đúng thứ tự thời gian",
            from_signals=tuple(s.ref for s in signals))]

        current, future = _plan_actions(replan)
        episodes.append(_build(
            view, episode_type="energy_episode_recovered", started_at=t0, detected_at=at,
            lifecycle_state="RECOVERED", status=EpisodeStatus.RECOVERED,
            surface=Surface.TIMELINE, interruption=InterruptionClass.PASSIVE,
            priority=40, authority=Authority.OBSERVATION, signals=signals, facts=facts,
            numbers=numbers,
            caveats=["Đây là lịch sử quan sát được, không khẳng định đổi pin làm tăng thu nhập.",
                     "Việc hệ thống ghi nhận đổi pin không có nghĩa bác đã làm theo một lời khuyên."],
            template_key="episode.energy_episode_recovered",
            params={"swap_start_at": clock(float(swap.t_min)),
                    "swap_done_at": clock(float(done.t_min)),
                    "replan_at": clock(at),
                    "soc_after_pct": None if soc_after is None else round(float(soc_after), 1)},
            evidence_window_start=t0, current_action=current, future_plan=future,
            supersedes=episode_identity(view.run_id, view.actor_id,
                                        "energy_pressure_building", t0),
            confidence=0.9))
    return episodes


# ------------------------------------------------------------------- A3 plan slipping
def detect_plan_slipping(view: ActorRunView) -> list[CompositeEpisode]:
    """Explain a changed plan after an observed disruption, without assigning blame.

    Two facts and one non-fact.  Fact: a disruption was recorded.  Fact: the plan's
    now/next changed afterwards.  Non-fact: that the first caused the second — the
    episode carries the pairing as context and says so in a caveat.
    """
    disruptions = sorted(
        (e for kind in DISRUPTION_KINDS for e in view.events_of(kind)),
        key=lambda e: float(e.t_min))
    if not disruptions:
        return []
    episodes: list[CompositeEpisode] = []
    used_revisions: set[str] = set()

    for event in disruptions:
        td = float(event.t_min)
        before = [c for c in view.checkpoints_until(td, solver="S2")]
        revision = next(
            (r for r in view.plan_revisions_until(view.shift_end_min)
             if td < float(r.at_min) <= td + PROBES["slip_window_min"]
             and r.checkpoint_id not in used_revisions), None)
        if revision is None or not before:
            continue
        used_revisions.add(revision.checkpoint_id)
        at = float(revision.at_min)
        previous = before[-1]

        signals = [
            _event_signal(view, event.kind, td, event.detail.get("order_id"), "order_id",
                          event.ref),
            _checkpoint_signal(view, previous, "plan_before", previous.semantic,
                               "plan_semantic"),
            _checkpoint_signal(view, revision, "plan_after", revision.semantic,
                               "plan_semantic"),
        ]
        facts = [DerivedFact(
            key="plan_changed_after_disruption", value=True, unit="bool",
            method=("so sánh now/next của bản kế hoạch trước gián đoạn với bản sau; "
                    "đây là quan hệ THỜI GIAN, không phải quan hệ nhân quả"),
            from_signals=tuple(s.ref for s in signals))]
        numbers = [
            EpisodeNumber("disruption_at", clock(td), "",
                          f"sim.event.{event.kind}", (signals[0].ref,)),
            EpisodeNumber("revision_at", clock(at), "",
                          "solver.S2.checkpoint", (signals[2].ref,)),
        ]
        current, future = _plan_actions(revision)
        episodes.append(_build(
            view, episode_type="plan_slipping_not_driver_failing", started_at=td,
            detected_at=at, lifecycle_state="EXPLAINED", status=EpisodeStatus.OPEN,
            surface=Surface.BANNER, interruption=InterruptionClass.PERSISTENT, priority=40,
            authority=Authority.OBSERVATION, signals=signals, facts=facts, numbers=numbers,
            caveats=["Kế hoạch đổi theo trạng thái quan sát được, không phải đánh giá bác làm sai.",
                     "Hệ thống ghi nhận hai việc xảy ra nối tiếp; nó không kết luận việc này gây ra việc kia."],
            template_key="episode.plan_slipping_not_driver_failing",
            params={"disruption_kind": event.kind, "disruption_at": clock(td),
                    "previous_now": previous.current_code, "previous_next": previous.future_code,
                    "new_now": revision.current_code, "new_next": revision.future_code},
            evidence_window_start=td, current_action=current, future_plan=future,
            valid_until=_live_validity(at, revision.valid_until_min),
            allowed_actions=("expand_why", "dismiss"),
            decision_key=f"{view.driver_id}:shift:{view.run_id}:plan",
            confidence=0.7))
    return episodes


# ----------------------------------------------------------------- A4 plan stabilized
def detect_plan_stability(view: ActorRunView) -> list[CompositeEpisode]:
    """Track whether the plan is churning or has settled, as a strip badge.

    ``UNSTABLE`` fires on the revision that completes a churn burst.  ``STABLE`` fires on
    a *timer*: ``last_revision + stable_quiet_min``.  Firing on a timer is what keeps this
    detector honest — the naive version asks "was the next revision more than an hour
    later", which cannot be answered without reading the future.
    """
    revisions = view.plan_revisions_until(view.shift_end_min)
    if not revisions:
        return []
    window = PROBES["churn_window_min"]
    needed = int(PROBES["churn_revisions"])
    quiet = PROBES["stable_quiet_min"]
    episodes: list[CompositeEpisode] = []

    churn_start: float | None = None
    churn_end: float | None = None
    churn_members: list[CheckpointView] = []
    for revision in revisions:
        at = float(revision.at_min)
        recent = [r for r in revisions if at - window <= float(r.at_min) <= at]
        if len(recent) < needed:
            continue
        churn_start = float(recent[0].at_min)
        churn_end = at
        churn_members = recent

    if churn_start is None or churn_end is None:
        return episodes

    plan = view.latest_plan_at(churn_end)
    signals = [_checkpoint_signal(view, r, "plan_revision", r.semantic, "plan_semantic")
               for r in churn_members]
    refs = tuple(s.ref for s in signals)
    facts = [DerivedFact(
        key="material_revisions_in_window", value=len(churn_members), unit="count",
        method=("đếm bản kế hoạch có now/next KHÁC bản trước trong cửa sổ trượt; "
                "poll lặp lại cùng nội dung không được tính"),
        from_signals=refs)]
    current, future = _plan_actions(plan) if plan is not None else (None, ())
    episodes.append(_build(
        view, episode_type="plan_stability", started_at=churn_start, detected_at=churn_end,
        lifecycle_state="UNSTABLE", status=EpisodeStatus.OPEN, surface=Surface.PLAN_STRIP,
        interruption=InterruptionClass.PERSISTENT, priority=50,
        authority=Authority.OBSERVATION, signals=signals, facts=facts,
        numbers=[EpisodeNumber("revision_count", len(churn_members), "lần",
                               "solver.S2.checkpoint", refs)],
        caveats=["Kế hoạch thay đổi nhiều lần là phản ứng với dữ kiện mới, không phải lỗi."],
        template_key="episode.plan_unstable",
        params={"revision_count": len(churn_members)},
        evidence_window_start=churn_start, current_action=current, future_plan=future,
        revision=1, decision_key=f"{view.driver_id}:shift:{view.run_id}:plan",
        confidence=0.8))

    # STABLE is evaluated on a timer.  At churn_end + quiet the past alone answers it:
    # "has any revision landed since churn_end?"
    settle_at = churn_end + quiet
    if settle_at > float(view.shift_end_min):
        return episodes
    later = [r for r in revisions if churn_end < float(r.at_min) <= settle_at]
    if later:
        return episodes
    plan_then = view.latest_plan_at(settle_at)
    stable_signals = list(signals)
    if plan_then is not None:
        stable_signals.append(_checkpoint_signal(view, plan_then, "plan_current",
                                                 plan_then.semantic, "plan_semantic"))
    stable_refs = tuple(s.ref for s in stable_signals)
    # Two different claims are being made on one card, and they expire differently.
    #
    # The **badge** ("the plan changed N times and has been quiet for an hour") is a
    # statement about the past.  It does not stop being true when an S2 record reaches the
    # end of its validity window, so it carries no ``valid_until`` at all.  It used to
    # inherit the record's window, and the visual review is what caught the consequence:
    # ``plan_then.valid_until_min`` can land on *exactly* ``settle_at``, producing a card
    # whose validity ends at the minute it was born.  ``shown_at`` then evicted the whole
    # episode id — including the earlier UNSTABLE revision — and the plan strip read
    # "chưa có bản cập nhật nào" for the remaining five hours of the shift.
    #
    # The **now/next actions** are a live claim, copied from a record that may since have
    # expired, so they are attached only while that record still covers the minute with
    # room to spare.  A badge with no actions is honest; stale actions are not.
    record_still_live = (plan_then is not None
                         and (plan_then.valid_until_min is None
                              or float(plan_then.valid_until_min) > settle_at + 1e-6))
    current2, future2 = _plan_actions(plan_then) if record_still_live else (None, ())
    episodes.append(_build(
        view, episode_type="plan_stability", started_at=churn_start, detected_at=settle_at,
        lifecycle_state="STABLE", status=EpisodeStatus.OPEN, surface=Surface.PLAN_STRIP,
        interruption=InterruptionClass.PERSISTENT, priority=70,
        authority=Authority.OBSERVATION, signals=stable_signals,
        facts=[DerivedFact(
            key="quiet_minutes_since_last_revision", value=round(quiet, 1), unit="minutes",
            method="đo từ bản revision cuối tới thời điểm phát hiện; không nhìn về tương lai",
            from_signals=stable_refs)],
        numbers=[EpisodeNumber("revision_count", len(churn_members), "lần",
                               "solver.S2.checkpoint", stable_refs),
                 EpisodeNumber("quiet_minutes", round(quiet, 1), "phút",
                               "episode.timer", stable_refs)],
        caveats=["Cửa sổ hiện tại được giữ nếu dữ kiện không đổi; đây không phải cam kết."],
        template_key="episode.plan_stabilized_again",
        params={"revision_count": len(churn_members), "quiet_minutes": round(quiet, 1)},
        evidence_window_start=churn_start, current_action=current2, future_plan=future2,
        valid_until=None,
        revision=2, decision_key=f"{view.driver_id}:shift:{view.run_id}:plan",
        confidence=0.8))
    return episodes


# ------------------------------------------------------- A5 waiting and rest record
def detect_waiting_and_rest_record(view: ActorRunView) -> list[CompositeEpisode]:
    """Raise a calm HUD banner when the *current* wait reaches 30 minutes.

    The timer is the product trigger approved by the owner.  It is not a fatigue proxy:
    the detector only states the continuous wait, the REST minutes recorded so far, and
    the canonical plan that is live at the anchor.  A plan is mandatory because a banner
    with only “you have waited 30 minutes” changes no decision.

    Each qualifying wait produces an OPEN revision at ``t0 + 30`` and an EXPIRED revision
    when the timeline records the end of that wait.  A truncated view marks its last block
    as open, so the first revision never reads the future end merely to schedule removal.
    """
    threshold = PROBES["continuous_idle_banner_min"]
    episodes: list[CompositeEpisode] = []
    for wait in sorted(view.idle_blocks(min_minutes=threshold), key=lambda b: float(b.t0)):
        started = float(wait.t0)
        at = started + threshold
        if at > float(view.shift_end_min) + 1e-6:
            continue
        # At a half-open block's exact end the driver is no longer waiting.  A truncated
        # live view carries ``is_open=True`` and is the one case where t1==anchor still
        # means the wait continues.
        if not wait.is_open and float(wait.t1) <= at + 1e-6:
            continue
        plan = view.latest_plan_at(at)
        if plan is None or not _plan_is_live(plan, at):
            continue
        current, future = _plan_actions(plan)
        if current is None and not future:
            continue

        rest_min = round(view.minutes_of("rest", float(view.shift_start_min), at), 1)
        # Measured from the block, not copied from the probe.  The two agree at the anchor
        # by construction — which is exactly why reporting the constant went unnoticed —
        # but only the measured form stays right if the anchor ever moves, and only it
        # makes the closing revision below able to state the real total.
        elapsed = round(at - started, 1)
        idle_signal = ComponentSignal(
            signal_type="continuous_idle_elapsed", at_min=at, value=elapsed,
            unit="minutes", source="sim.journey.timeline_gap",
            source_ref=wait.ref or f"idle@{started:.3f}",
            evidence_mode=EvidenceMode.INFERRED, data_mode=view.data_mode,
            confidence=0.6)
        rest_signal = ComponentSignal(
            signal_type="rest_minutes_recorded", at_min=at, value=rest_min,
            unit="minutes", source="sim.segment.rest",
            source_ref=f"rest-scan@{at:.3f}",
            evidence_mode=EvidenceMode.OBSERVED, data_mode=view.data_mode)
        plan_signal = _checkpoint_signal(
            view, plan, "plan_current", plan.semantic, "plan_semantic")
        signals = [idle_signal, rest_signal, plan_signal]
        refs = tuple(signal.ref for signal in signals)
        key = f"{view.driver_id}:shift:{view.run_id}:wait:{started:.3f}"
        episodes.append(_build(
            view, episode_type="waiting_and_rest_record", started_at=started,
            detected_at=at, lifecycle_state="DETECTED", status=EpisodeStatus.OPEN,
            surface=Surface.BANNER, interruption=InterruptionClass.PERSISTENT,
            priority=35, authority=Authority.DERIVED_RULE, signals=signals,
            facts=[DerivedFact(
                key="continuous_wait_reached_banner_threshold", value=True, unit="bool",
                method=("đo từ đầu khoảng idle tới mốc kích hoạt; đối chiếu REST đã ghi "
                        "nhận và kế hoạch còn hiệu lực tại chính mốc đó"),
                from_signals=refs, evidence_mode=EvidenceMode.INFERRED)],
            numbers=[
                EpisodeNumber("continuous_idle_minutes", elapsed, "phút",
                              "sim.journey.timeline_gap", (idle_signal.ref,)),
                EpisodeNumber("rest_minutes_recorded", rest_min, "phút",
                              "sim.segment.rest", (rest_signal.ref,)),
            ],
            caveats=["Thời gian chờ và thời gian nghỉ được theo dõi riêng.",
                     "Khoảng chờ là số suy ra từ timeline, không phải khai báo của bác.",
                     "Hệ thống không kết luận trạng thái sức khoẻ từ khoảng chờ này."],
            template_key="episode.waiting_and_rest_record",
            params={"continuous_idle_minutes": elapsed},
            evidence_window_start=started, current_action=current, future_plan=future,
            action_window=plan.action_window,
            # The banner is only as live as the plan it shows.  Leaving this ``None`` is
            # what made "còn hiệu lực tới X" unanswerable downstream: the pack had a plan
            # to render and no boundary to render it against.
            valid_until=_live_validity(at, plan.valid_until_min),
            revision=1, allowed_actions=("dismiss",),
            safety_flags=("inferred_evidence", "health_boundary_respected"),
            decision_key=key, confidence=0.6))

        # Only a genuinely closed block may remove the banner.  A clipped open block in a
        # truncated view ends at ``at`` too, but ``is_open`` prevents that false closure.
        if wait.is_open:
            continue
        closed_at = float(wait.t1)
        closed_signal = ComponentSignal(
            signal_type="idle_block_closed", at_min=closed_at,
            value=round(wait.minutes, 1), unit="minutes",
            source="sim.journey.timeline_gap",
            source_ref=wait.ref or f"idle@{started:.3f}",
            evidence_mode=EvidenceMode.INFERRED, data_mode=view.data_mode,
            confidence=0.6)
        episodes.append(_build(
            view, episode_type="waiting_and_rest_record", started_at=started,
            detected_at=closed_at, lifecycle_state="EXPIRED", status=EpisodeStatus.EXPIRED,
            surface=Surface.BANNER, interruption=InterruptionClass.PERSISTENT,
            priority=35, authority=Authority.DERIVED_RULE, signals=[closed_signal],
            facts=[],
            # The closing revision is where "how long did I wait?" is answered later, and
            # it shipped with no number at all — so the only figure the record could ever
            # produce for this wait was the 30-minute trigger.  ``wait.minutes`` is the
            # block the timeline actually recorded.
            numbers=[EpisodeNumber("continuous_idle_minutes", round(wait.minutes, 1),
                                   "phút", "sim.journey.timeline_gap",
                                   (closed_signal.ref,))],
            caveats=[], template_key="episode.waiting_expired",
            params={"continuous_idle_minutes": round(wait.minutes, 1)},
            evidence_window_start=started, revision=2,
            safety_flags=("inferred_evidence", "health_boundary_respected"),
            decision_key=key, confidence=0.6))
    return episodes


# ------------------------------------------------------------------- A6 income quality
def detect_income_quality(view: ActorRunView) -> list[CompositeEpisode]:
    """Break payout down by the sources that have actually settled by now.

    Every amount is read from the income ledger with a timestamp, so a mid-shift reading
    contains only mid-shift money.  The day bonus settles at ``end_shift`` and therefore
    simply is not present earlier — no clamping, no estimate.  Cancellations appear as
    operational context and are never described as lost income.
    """
    # Anchor the recap to the observable end of the shift — the ``end_shift`` event — not
    # to the planned ``shift_end_min``.  The planned minute can fall after the last
    # transition the driver ever sees, which made the recap real in the data and absent
    # on screen.  The event is also when the day bonus settles, so the breakdown is
    # complete at exactly this minute without reaching forward for it.
    end_events = view.events_of("end_shift")
    if not end_events:
        # A planned boundary is not evidence that the shift ended.  Returning no recap is
        # preferable to summarising a censored/in-progress shift as if it were complete.
        return []
    at = float(end_events[-1].t_min)
    sources = view.income_sources_at(at)
    if len(sources) < int(PROBES["income_min_sources"]):
        return []
    entries = [e for e in view.income if float(e.t_min) <= at + 1e-6]
    signals = [ComponentSignal(
        signal_type=f"income_{entry.source}", at_min=float(entry.t_min),
        value=int(entry.amount_vnd), unit="VND_payout",
        source=f"sim.income_ledger.{entry.source}",
        source_ref=entry.ref or f"{entry.source}@{entry.t_min:.3f}",
        evidence_mode=EvidenceMode.OBSERVED, data_mode=view.data_mode) for entry in entries]
    cancels = view.events_of("order_cancelled_after_accept", until=at)
    signals.extend(_event_signal(view, "order_cancelled_after_accept", float(e.t_min),
                                 e.detail.get("order_id"), "order_id", e.ref)
                   for e in cancels)

    by_source: dict[str, list[str]] = {}
    for signal in signals:
        if signal.signal_type.startswith("income_"):
            by_source.setdefault(signal.signal_type[len("income_"):], []).append(signal.ref)
    numbers = [EpisodeNumber(f"payout_{source}_vnd", amount, "VND",
                             f"sim.income_ledger.{source}", tuple(by_source.get(source, ())))
               for source, amount in sources.items()]
    numbers.append(EpisodeNumber("payout_total_vnd", sum(sources.values()), "VND",
                                 "sim.income_ledger",
                                 tuple(r for refs in by_source.values() for r in refs)))
    numbers.append(EpisodeNumber("cancellation_count", len(cancels), "lần",
                                 "sim.event.order_cancelled_after_accept",
                                 tuple(s.ref for s in signals
                                       if s.signal_type == "order_cancelled_after_accept")))
    facts = [DerivedFact(
        key="settled_source_count", value=len(sources), unit="count",
        method="đếm nguồn tiền ĐÃ SETTLE tính tới thời điểm chốt; pending không được tính",
        from_signals=tuple(r for refs in by_source.values() for r in refs))]
    return [_build(
        view, episode_type="income_quality_not_amount",
        started_at=float(view.shift_start_min), detected_at=at,
        lifecycle_state="SUMMARISED", status=EpisodeStatus.CLOSED, surface=Surface.RECAP,
        interruption=InterruptionClass.DEFERRED, priority=90,
        authority=Authority.OBSERVATION, signals=signals, facts=facts, numbers=numbers,
        caveats=["Đây là driver payout đã ghi nhận, không phải doanh thu gộp và không phải thu nhập ròng.",
                 "Số lần huỷ sau nhận là bối cảnh vận hành; hệ thống không gọi đó là tiền bị mất.",
                 "Khoản chưa settle không được tính vào đây."],
        template_key=("episode.income_quality_not_amount" if cancels
                      else "episode.income_quality_no_cancellation"),
        params={"source_count": len(sources), "sources": dict(sources),
                "cancellation_count": len(cancels)},
        evidence_window_start=float(view.shift_start_min), confidence=0.95)]


# ----------------------------------------------------------- A7 infeasible bonus target
def detect_stop_chasing_infeasible_target(view: ActorRunView) -> list[CompositeEpisode]:
    """Report a bonus target the S1 solver itself declared unreachable.

    Authority matters more than frequency here.  The verdict is read from
    ``bonus_feasibility.solution.feasible`` on the persisted solver report linked to the
    checkpoint — it is never inferred from low payout, few trips or time remaining.

    **This is a state of a target, not a card of its own.**  Measurement showed A7 rescues
    0% of silent shifts — structurally, because every S1 verdict already *is* a READY
    checkpoint the driver has a card for.  A second card would be the same subject twice.
    The episode therefore carries ``supersedes_checkpoint_id``, and routing repaints that
    card instead of raising a new one.

    The target lifecycle is ``TARGET_ACTIVE → AT_RISK → INFEASIBLE (CLOSED) → TARGET_CLOSED``.
    Only the states the data can support are ever emitted: S1 persists exactly one record
    per driver per shift (0 of 270 actors have a second one), so ``AT_RISK`` requires a
    *later* S1 record and is currently unreachable by data rather than by code.  That is
    the honest outcome, and ``tests/test_episode_detectors.py`` pins it from both sides:
    the branch exists and fires on a two-record fixture, and never fires on real runs.
    A verdict must never be phrased as "the target has just become impossible".
    """
    episodes: list[CompositeEpisode] = []
    s1_records = sorted((c for c in view.checkpoints if "S1" in c.solver_set),
                        key=lambda c: float(c.at_min))
    for index, checkpoint in enumerate(s1_records):
        if checkpoint.feasible is not False:
            continue
        at = float(checkpoint.at_min)
        # AT_RISK is reachable only from an earlier feasible verdict by the same solver.
        # Nothing here reads payout, gap or time remaining to decide it.
        earlier_feasible = any(c.feasible is True for c in s1_records[:index])
        lifecycle = "AT_RISK" if earlier_feasible else "CLOSED_INFEASIBLE"
        signal = _checkpoint_signal(view, checkpoint, "bonus_feasibility_verdict",
                                    False, "bool")
        signals = [signal]
        if earlier_feasible:
            previous = next(c for c in reversed(s1_records[:index]) if c.feasible is True)
            signals.append(_checkpoint_signal(view, previous,
                                              "bonus_feasibility_verdict_previous",
                                              True, "bool"))
        numbers: list[EpisodeNumber] = []
        if checkpoint.gap_points is not None:
            numbers.append(EpisodeNumber("gap_points", checkpoint.gap_points, "điểm",
                                         "solver.S1.bonus_feasibility", (signal.ref,)))
        # The solver's `infeasible_reason` string carries numbers that are not anchored to
        # the number registry, so it is kept as a machine reason code only and never
        # rendered verbatim (same lesson as advisor/templates.py §C2 §1c).
        episodes.append(_build(
            view, episode_type="stop_chasing_infeasible_target", started_at=at,
            detected_at=at, lifecycle_state=lifecycle, status=EpisodeStatus.CLOSED,
            surface=Surface.PRIORITY_BOARD, interruption=InterruptionClass.PASSIVE,
            priority=30, authority=Authority.SOLVER, signals=signals,
            facts=[DerivedFact(
                key="target_feasible", value=False, unit="bool",
                method="đọc trực tiếp solution.feasible của solver report S1 đã persist",
                from_signals=(signal.ref,), evidence_mode=EvidenceMode.OBSERVED)],
            numbers=numbers,
            caveats=["Đây là kết luận của solver theo dữ kiện tại thời điểm đó, không phải dự báo.",
                     "Payout đã ghi nhận không thay đổi vì kết luận này.",
                     "Hệ thống không khuyến khích kéo dài ca để đuổi mốc."],
            template_key="episode.stop_chasing_infeasible_target",
            params={"verdict_at": clock(at),
                    "gap_points": checkpoint.gap_points,
                    "single_verdict_only": not earlier_feasible},
            evidence_window_start=at,
            valid_until=_live_validity(at, checkpoint.valid_until_min),
            solver_report_refs=((checkpoint.report_ref,) if checkpoint.report_ref else ()),
            supersedes_checkpoint_id=checkpoint.checkpoint_id,
            decision_key=(checkpoint.decision_key or
                          f"{view.driver_id}:bonus_eligibility:PROTECT_ELIGIBILITY"),
            confidence=float(checkpoint.confidence or 0.5)))
    return episodes


# --------------------------------------------------- A8 operational phase change
def detect_operational_phase_change(view: ActorRunView) -> list[CompositeEpisode]:
    """Compare two *adjacent past* windows of the driver's own recorded activity.

    The detector this replaces compared the first half of the shift with the second, which
    cannot be computed before the shift ends.  Making it past-only is the easy part; the
    hard part is that the obvious past-only version is also wrong.

    Measured noise floor: the median absolute change in utilisation between two adjacent
    windows is ~22 points at *every* window length up to 120 minutes, because the median
    60-minute window contains one completed trip.  An unguarded ±20pp rule therefore fires
    for 270 of 270 actors — a detector that fires for everyone has detected nothing.

    So two guards do the real work: a 120-minute window, and at least
    ``phase_min_trips_each_window`` completed trips in **each** side of the comparison.
    Shifts too short to hold two full windows are not evaluated at all rather than
    evaluated on a stub — they must be excluded from the denominator when reporting.

    The episode never names a place, never forecasts demand and never suggests moving:
    it reports the driver's own two numbers and stops.
    """
    window = PROBES["phase_window_min"]
    min_trips = int(PROBES["phase_min_trips_each_window"])
    delta_needed = PROBES["phase_material_delta_pp"]
    start, end = float(view.shift_start_min), float(view.shift_end_min)
    if end - start < 2 * window:
        return []

    def _utilisation(lo: float, hi: float) -> float:
        occupied = sum(max(0.0, min(float(b.t1), hi) - max(float(b.t0), lo))
                       for b in view.timeline if b.kind in ("on_trip", "enroute"))
        return 100.0 * occupied / (hi - lo)

    # Step by a whole window, not by a few minutes.  Two reasons, both measured:
    # a trip that straddles a window edge migrates between the two sides as the cursor
    # slides and manufactures a delta out of nothing; and a 10-minute cursor gives ~25
    # chances per shift to cross the threshold, which inflates firing on top of a noise
    # floor that is already ~22pp.  Non-overlapping windows give 2-3 honest comparisons.
    at = start + 2 * window
    while at <= end + 1e-6:
        previous_lo, split, recent_hi = at - 2 * window, at - window, at
        previous_trips = [e for e in view.events_of("dropoff", until=at)
                          if previous_lo < float(e.t_min) <= split]
        recent_trips = [e for e in view.events_of("dropoff", until=at)
                        if split < float(e.t_min) <= recent_hi]
        if len(previous_trips) >= min_trips and len(recent_trips) >= min_trips:
            previous_pct = _utilisation(previous_lo, split)
            recent_pct = _utilisation(split, recent_hi)
            delta = recent_pct - previous_pct
            if abs(delta) >= delta_needed:
                return [_phase_episode(view, previous_lo, at, previous_pct, recent_pct,
                                       previous_trips, recent_trips, window, delta)]
        at += window
    return []


def _phase_episode(view: ActorRunView, started_at: float, at: float,
                   previous_pct: float, recent_pct: float, previous_trips: list,
                   recent_trips: list, window: float, delta: float) -> CompositeEpisode:
    signals = [_event_signal(view, "dropoff", float(e.t_min), e.detail.get("order_id"),
                             "order_id", e.ref)
               for e in (*previous_trips, *recent_trips)]
    occupancy = ComponentSignal(
        signal_type="occupied_share_window", at_min=at,
        value=round(recent_pct, 1), unit="pct", source="sim.journey.timeline",
        source_ref=f"utilisation@{at:.3f}", evidence_mode=EvidenceMode.INFERRED,
        data_mode=view.data_mode, confidence=0.6)
    previous_occupancy = ComponentSignal(
        signal_type="occupied_share_window_previous", at_min=at - window,
        value=round(previous_pct, 1), unit="pct", source="sim.journey.timeline",
        source_ref=f"utilisation@{at - window:.3f}", evidence_mode=EvidenceMode.INFERRED,
        data_mode=view.data_mode, confidence=0.6)
    signals.extend((previous_occupancy, occupancy))
    refs = tuple(s.ref for s in signals)
    return _build(
        view, episode_type="operational_phase_change", started_at=started_at,
        detected_at=at, lifecycle_state="DETECTED", status=EpisodeStatus.OPEN,
        surface=Surface.TIMELINE, interruption=InterruptionClass.PASSIVE,
        priority=90, authority=Authority.DERIVED_RULE, signals=signals,
        facts=[DerivedFact(
            key="occupied_share_delta_pp", value=round(delta, 1), unit="pp",
            method=("so tỉ lệ phút có khách của hai cửa sổ liền kề ĐÃ QUA, mỗi cửa sổ phải "
                    "có tối thiểu số cuốc hoàn thành theo probe"),
            from_signals=refs, evidence_mode=EvidenceMode.INFERRED)],
        numbers=[
            EpisodeNumber("recent_pct", round(recent_pct, 1), "%",
                          "sim.journey.timeline", (occupancy.ref,)),
            EpisodeNumber("previous_pct", round(previous_pct, 1), "%",
                          "sim.journey.timeline", (previous_occupancy.ref,)),
            EpisodeNumber("recent_trips", len(recent_trips), "cuốc",
                          "sim.event.dropoff", refs),
            EpisodeNumber("previous_trips", len(previous_trips), "cuốc",
                          "sim.event.dropoff", refs),
            EpisodeNumber("window_minutes", round(window, 1), "phút",
                          "episode.probe", refs),
        ],
        caveats=["Đây là ghi nhận hoạt động của riêng bác, không phải nhu cầu thị trường.",
                 "Hệ thống không đề xuất di chuyển và không dự báo thu nhập.",
                 "Tỉ lệ có khách là số suy ra từ timeline."],
        template_key="episode.operational_phase_change",
        params={"recent_pct": round(recent_pct, 1), "previous_pct": round(previous_pct, 1),
                "recent_trips": len(recent_trips), "previous_trips": len(previous_trips),
                "window_minutes": round(window, 1)},
        evidence_window_start=started_at,
        safety_flags=("inferred_evidence",), confidence=0.5)


DETECTORS: dict[str, Callable[[ActorRunView], list[CompositeEpisode]]] = {
    "energy_pressure_building": detect_energy_pressure_building,
    "energy_episode_recovered": detect_energy_episode_recovered,
    "plan_slipping_not_driver_failing": detect_plan_slipping,
    "plan_stability": detect_plan_stability,
    "waiting_and_rest_record": detect_waiting_and_rest_record,
    "income_quality_not_amount": detect_income_quality,
    "stop_chasing_infeasible_target": detect_stop_chasing_infeasible_target,
    "operational_phase_change": detect_operational_phase_change,
}


# The signal types each detector must put behind a driver-facing revision.  This lives
# next to the detectors on purpose: the previous copy lived in ``checkpoint_intents`` and
# had drifted on six of eight entries, naming signals such as ``s1_infeasible`` for a
# detector that emits ``bonus_feasibility_verdict``.  A quality gate keyed on names
# nothing emits is not a lax gate — it is a gate that reports INSUFFICIENT for everything
# the moment anyone feeds it real observations, while reading like enforcement until then.
#
# A trailing ``*`` marks a family whose exact suffix is data-driven: income signals are
# named after the ledger source (``income_trip``, ``income_mission``, …), so the contract
# is "at least one income signal", not a fixed list.  Terminal revisions (EXPIRED) are
# projected away rather than rendered and carry only their closing signal, so they are not
# covered here — :func:`gsm_core.episodes.templates.present` never reaches them.
EPISODE_SIGNALS: dict[str, tuple[str, ...]] = {
    "energy_pressure_building": ("order_skipped_soc", "planned_swap", "soc_observed"),
    "energy_episode_recovered": ("order_skipped_soc", "go_swap", "swap_done",
                                 "plan_recomputed"),
    "plan_slipping_not_driver_failing": ("plan_before", "plan_after"),
    "plan_stability": ("plan_current", "plan_revision"),
    "waiting_and_rest_record": ("continuous_idle_elapsed", "rest_minutes_recorded",
                                "plan_current"),
    "income_quality_not_amount": ("income_*",),
    "stop_chasing_infeasible_target": ("bonus_feasibility_verdict",),
    "operational_phase_change": ("occupied_share_window",
                                 "occupied_share_window_previous"),
}


def _assert_signal_declarations(detectors: Mapping[str, object],
                                declarations: Mapping[str, tuple[str, ...]]) -> None:
    """Fail at import when a detector and its signal declaration drift apart.

    Taken as arguments rather than read from module scope so the gate itself is testable
    — a gate nobody can prove fires is the same class of decoration as the registry it
    replaces.
    """
    missing = sorted(set(detectors) - set(declarations))
    extra = sorted(set(declarations) - set(detectors))
    if missing or extra:
        raise RuntimeError(
            "EPISODE_SIGNALS lệch với DETECTORS — "
            f"thiếu khai báo: {missing}; khai báo thừa: {extra}")


_assert_signal_declarations(DETECTORS, EPISODE_SIGNALS)
