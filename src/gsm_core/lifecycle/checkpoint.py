"""Pure AdviceCheckpoint lifecycle primitives.

Checkpoint presentation lifecycle is deliberately separate from the legacy
``advice_lifecycle_event`` adherence projection.  The module has no simulator,
UI or database dependency so it can be used for shadow replay and contract tests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypedDict

from gsm_core.advisor.checkpoint_intents import (
    SignalObservation,
    enrich_candidate_intent,
)


CHECKPOINT_EVENT_TYPES = frozenset({
    "created", "queued", "ready", "generation_started", "generated",
    "generation_failed", "offered", "displayed", "accepted", "dismissed",
    "expanded", "expired", "superseded", "suppressed", "execution_observed",
})

PRESENTATION_TERMINAL_STATES = frozenset({
    "accepted", "dismissed", "expired", "superseded", "suppressed",
})

_ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"created"}),
    "created": frozenset({"queued", "ready", "suppressed", "expired", "superseded"}),
    "queued": frozenset({"ready", "expired", "superseded", "suppressed"}),
    # Ready to queued: the driver started moving after the checkpoint was ready. The moving gate
    # must leave a lifecycle trace rather than falling silent with no event at all.
    "ready": frozenset({"generation_started", "offered", "queued", "expired",
                         "superseded", "suppressed"}),
    "generation_started": frozenset({"generated", "generation_failed", "expired",
                                      "superseded"}),
    "generation_failed": frozenset({"generation_started", "offered", "expired",
                                     "superseded", "suppressed"}),
    "generated": frozenset({"offered", "expired", "superseded"}),
    "offered": frozenset({"displayed", "accepted", "dismissed", "expired", "superseded"}),
    "displayed": frozenset({"accepted", "dismissed", "expired", "superseded"}),
    "accepted": frozenset(),
    "dismissed": frozenset(),
    "expired": frozenset(),
    "superseded": frozenset(),
    "suppressed": frozenset(),
}


class CheckpointTransitionError(ValueError):
    """Raised when an immutable presentation lifecycle is advanced incorrectly."""


class RecommendationCandidate(TypedDict, total=False):
    checkpoint_id: str
    driver_id: str
    topic: str
    current_action: dict | None
    future_plan: list[dict]
    validity: dict
    fingerprint: str
    source_decision_id: str
    run_id: str | None
    solver_input_refs: list[str]
    solver_report_refs: list[str]


@dataclass(frozen=True)
class CheckpointPolicyResult:
    verdict: str
    reason: str | None
    candidate: dict | None
    superseded_checkpoint_ids: tuple[str, ...] = ()


CHECKPOINT_RECORD_FIELDS = frozenset({
    "checkpoint_id", "driver_id", "topic", "surface", "trigger_type",
    "current_action", "future_plan", "action_window", "validity",
    "urgency_band", "material_revision", "reason_code", "confidence_band",
    "numbers", "caveats", "fingerprint",
    "snapshot_ref", "solver_artifact_ref", "source_decision_id", "run_id",
    "solver_input_refs", "solver_report_refs", "solver_set", "data_mode",
    "source_episode_id", "episode_type", "decision_key", "output_intent",
    "evidence_quality", "evidence_refs", "is_mock", "created_at",
})


def checkpoint_record(candidate: dict) -> dict:
    """Strip policy-only fields and return the closed persisted checkpoint
    shape.

    The record carries the semantic number, the output intent, the decision identity and the state
    of the evidence directly, so presentation never has to infer meaning from a bare number id.
    """
    enriched = enrich_candidate_intent(candidate)
    record = {"schema_version": "1.3.0", **{
        key: enriched.get(key) for key in CHECKPOINT_RECORD_FIELDS
    }}
    record["numbers"] = list(record.get("numbers") or [])
    record["caveats"] = list(record.get("caveats") or [])
    return record


def _event_sort_key(event: dict) -> tuple[datetime, str]:
    try:
        occurred = datetime.fromisoformat(event["occurred_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CheckpointTransitionError(
            f"event thiếu occurred_at hợp lệ: {event.get('event_id')!r}") from exc
    return occurred, str(event.get("event_id", ""))


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _content_ref(value: dict) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(value).encode("utf-8")).hexdigest()


_ACTION_LABELS = {
    "PROTECT_ELIGIBILITY": "action.protect_eligibility",
    "ONLINE": "action.online",
    "REST": "action.rest",
    "SWAP": "action.swap",
    "END": "action.end",
    "EXTEND": "action.extend",
    "REPOSITION_SIM_ONLY": "action.reposition_sim_only",
    "NO_ACTION": "action.no_action",
}


_SOLVER_NUMBER_SEMANTICS: dict[str, tuple[tuple[str, str], ...]] = {
    # The solvers still expose their legacy positional ``numbers`` arrays.  This
    # adapter is the producer boundary where the exact solver/report type is known,
    # so it is the only safe place to attach meaning.  The browser must never infer
    # these labels from a unit or an array position by itself.
    "S1": (
        ("gap_points", "Số điểm còn thiếu"),
        ("next_tier_vnd", "Giá trị mốc thưởng kế tiếp"),
        ("points_per_hour", "Tốc độ điểm dùng trong phép tính"),
        ("hours_needed", "Thời gian ước tính để đạt mốc"),
        ("trips_needed", "Số cuốc ước tính để đạt mốc"),
    ),
    "S2": (
        ("expected_payout_vnd", "Payout kỳ vọng của kế hoạch"),
        ("baseline_payout_vnd", "Payout kỳ vọng của baseline"),
        ("delta_payout_vnd", "Chênh lệch payout kỳ vọng"),
        ("projected_points", "Điểm dự kiến cuối ca"),
        ("projected_bonus_tier_vnd", "Mốc thưởng dự kiến"),
        ("payout_per_order_vnd", "Payout tham chiếu mỗi cuốc"),
    ),
}


def _semantic_solver_numbers(solver: str, solver_report: dict) -> list[dict]:
    """Attach truthful keys at the typed solver boundary, preserving producer keys.

    S1's already-maxed branch has a different one-number shape, so treating its first
    value as a gap would be a semantic lie.  Unknown solver layouts remain explicitly
    generic through ``semantic_number`` later in the pipeline.
    """
    numbers = [dict(number) for number in (solver_report.get("numbers") or [])
               if isinstance(number, dict)]
    solution = solver_report.get("solution") or {}
    meanings = _SOLVER_NUMBER_SEMANTICS.get(solver, ())
    if solver == "S1" and solution.get("already_maxed"):
        meanings = (("current_tier_bonus_vnd", "Thưởng theo mốc hiện tại"),)
    for index, number in enumerate(numbers):
        if index >= len(meanings):
            break
        key, label = meanings[index]
        number.setdefault("key", key)
        number.setdefault("label", label)
    return numbers


def _canonical_action(code: str | None) -> dict | None:
    normalized = str(code or "NO_ACTION").upper()
    if normalized not in _ACTION_LABELS:
        normalized = "NO_ACTION"
    return {"code": normalized, "label_id": _ACTION_LABELS[normalized]}


def _topic_for_action(code: str, solver: str) -> str:
    if solver == "S1":
        return "bonus_eligibility"
    if solver == "S4":
        return "positioning_sim_only"
    if solver == "S7" or code == "REST":
        return "rest"
    if code == "SWAP":
        return "energy"
    if code == "END":
        return "shift_boundary"
    if code == "EXTEND":
        return "shift_boundary"
    return "shift_timing"


def _schedule_step_minutes(schedule: list[dict], bucket_end_hint: str | None,
                           first_bucket: str | None) -> float | None:
    """The bucket grid step, derived from the solver's own schedule labels
    rather than from an invented constant.

    It prefers the difference between two consecutive labels; for a single-element schedule it uses
    the boundary hint the producer supplies. When neither is available it returns nothing, leaving
    the window unset rather than inventing one."""
    labels = [item.get("bucket") for item in schedule
              if isinstance(item, dict) and item.get("bucket")]
    for left, right in zip(labels, labels[1:]):
        try:
            delta = (datetime.fromisoformat(right)
                     - datetime.fromisoformat(left)).total_seconds() / 60.0
        except (TypeError, ValueError):
            continue
        if delta > 0:
            return delta
    if bucket_end_hint and first_bucket:
        try:
            delta = (datetime.fromisoformat(bucket_end_hint)
                     - datetime.fromisoformat(first_bucket)).total_seconds() / 60.0
        except (TypeError, ValueError):
            return None
        if delta > 0:
            return delta
    return None


def _plan_window(bucket_label: str | None, step_min: float | None) -> dict | None:
    """The window of one bucket in the shift schedule, purely from solver data."""
    if not bucket_label or not step_min:
        return None
    try:
        start = datetime.fromisoformat(bucket_label)
    except (TypeError, ValueError):
        return None
    return {"start": start.isoformat(),
            "end": (start + timedelta(minutes=step_min)).isoformat()}


def normalize_solver_decision(
    solver_name: str,
    snapshot: dict,
    solver_input: dict,
    solver_report: dict,
    source_decision_id: str,
) -> RecommendationCandidate:
    """Normalize one actual solver result without recomputing solver output.

    The record keeps current action and future plan structurally separate and
    derives every trace reference from the exact values supplied by the caller.
    """
    solver = str(solver_name).upper()
    solution = solver_report.get("solution") or {}
    future: list[dict] = []
    action_window = solution.get("action_window")

    if solver == "S2":
        current_raw, future_raw = normalize_shift_plan(solution)
        current_code = (current_raw or {}).get("action")
        current = _canonical_action(current_code)
        step_min = _schedule_step_minutes(
            solution.get("schedule") or [], snapshot.get("bucket_end"),
            (current_raw or {}).get("bucket"))
        if action_window is None:
            action_window = _plan_window((current_raw or {}).get("bucket"), step_min)
        for step in future_raw:
            action = _canonical_action(step.get("action"))
            if action is not None:
                window = step.get("window") or _plan_window(step.get("bucket"), step_min)
                future.append({**action, "window": window})
    elif solver == "S1":
        current_code = ("NO_ACTION" if solution.get("already_maxed")
                        else "PROTECT_ELIGIBILITY")
        current = _canonical_action(current_code)
    elif solver == "S4":
        current_code = "REPOSITION_SIM_ONLY"
        current = _canonical_action(current_code)
    elif solver == "S7":
        current_code = "REST"
        current = _canonical_action(current_code)
    else:
        current_code = str(solution.get("action") or "NO_ACTION").upper()
        current = _canonical_action(current_code)

    freshness = snapshot.get("freshness_deadline") or solver_input.get("freshness_deadline")
    # The boundary hint may come from the solver input, on the historical path, or from the
    # producer's snapshot, on the simulation path, where the producer declares the real boundary
    # instead of a fabricated freshness offset masking every solver window.
    validity_until = normalize_validity(
        solver,
        shift_end=snapshot.get("shift_end"),
        policy_effective_boundary=solver_input.get("policy_effective_boundary"),
        solver_bucket_end=solver_input.get("bucket_end") or snapshot.get("bucket_end"),
        allocation_bucket_end=(solver_input.get("allocation_bucket_end")
                               or snapshot.get("allocation_bucket_end")),
        rest_window_end=(solver_input.get("rest_window_end")
                         or snapshot.get("rest_window_end")),
        freshness_deadline=freshness,
    )
    observed_at = snapshot.get("observed_at")
    confidence = float(solver_report.get("confidence") or 0.0)
    candidate: RecommendationCandidate = {
        "driver_id": str(snapshot.get("driver_id") or ""),
        "topic": _topic_for_action(current["code"] if current else "NO_ACTION", solver),
        "surface": snapshot.get("surface") or "nudge",
        "trigger_type": snapshot.get("trigger_type") or "solver_update",
        "current_action": current,
        "future_plan": future,
        "action_window": action_window,
        "validity": {
            "valid_from": observed_at,
            "valid_until": validity_until,
            "freshness_deadline": freshness,
        },
        "urgency_band": solver_report.get("urgency_band") or "medium",
        "material_revision": str(solver_report.get("material_revision") or "1"),
        "reason_code": str(solver_report.get("reason_code") or "solver_recommendation"),
        # Numbers and caveats are data the solver has already computed, typed and with a
        # source. They stay on the record so a card does not depend on an artifact lookup.
        "numbers": _semantic_solver_numbers(solver, solver_report),
        "caveats": [str(caveat) for caveat in (solver_report.get("caveats") or [])],
        "confidence": confidence,
        "confidence_band": "high" if confidence >= 0.8 else "medium" if confidence >= 0.5 else "low",
        "snapshot_ref": _content_ref(snapshot),
        "solver_artifact_ref": _content_ref({"input": solver_input, "report": solver_report}),
        "source_decision_id": source_decision_id,
        "run_id": snapshot.get("run_id"),
        "solver_input_refs": [_content_ref(solver_input)],
        "solver_report_refs": [_content_ref(solver_report)],
        "solver_set": [solver],
        "solver_status": str(solver_report.get("status") or "optimal").lower(),
        "maintenance": current is not None and current["code"] in {"ONLINE", "NO_ACTION"},
        "data_mode": snapshot.get("data_mode") or "synthetic",
        "is_mock": bool(snapshot.get("is_mock", True)),
        "created_at": observed_at,
    }
    available_signals = {"solver_action", "solver_report", "state_snapshot"}
    if validity_until is not None:
        available_signals.add("validity")
    if solver == "S1":
        available_signals.update(("s1_verdict", "policy_version"))
        if candidate["numbers"]:
            available_signals.add("gap_points")
    elif solver == "S2":
        available_signals.add("s2_current_action")
        if snapshot.get("soc_pct") is not None or (snapshot.get("runtime_state") or {}).get("soc_pct") is not None:
            available_signals.add("soc_state")
        if current_code == "SWAP" and action_window:
            available_signals.add("swap_window")
        if current_code == "REST" and action_window:
            available_signals.add("rest_window")
    candidate = enrich_candidate_intent(candidate, observations={
        signal_id: SignalObservation(signal_id) for signal_id in available_signals})
    fingerprint = checkpoint_fingerprint(candidate)
    candidate["fingerprint"] = fingerprint
    driver_id = candidate["driver_id"]
    identity = hashlib.sha256(f"{driver_id}{fingerprint}".encode("utf-8")).hexdigest()[:24]
    candidate["checkpoint_id"] = f"ckpt-{identity}"
    return candidate


def evaluate_checkpoint(
    candidate: dict,
    active_checkpoints: list[dict],
    cadence_memory: dict,
    now: str | datetime,
    is_driving: bool,
) -> CheckpointPolicyResult:
    """Apply the checkpoint policy in fail-closed order for one candidate."""
    candidate = enrich_candidate_intent(candidate)
    status = str(candidate.get("solver_status") or "optimal").lower()
    if candidate.get("missing_state") or status == "missing_state":
        return CheckpointPolicyResult("suppressed", "missing_state", None)
    if status in {"timeout", "infeasible", "stale", "error", "failed"}:
        return CheckpointPolicyResult("suppressed", status, None)

    validity = candidate.get("validity") or {}
    valid_until = validity.get("valid_until")
    if not valid_until:
        return CheckpointPolicyResult("suppressed", "missing_validity", None)

    action = candidate.get("current_action") or {}
    if action.get("code") == "NO_ACTION":
        return CheckpointPolicyResult("suppressed", "not_actionable", None)
    output_intent = candidate.get("output_intent") or {}
    if output_intent.get("mode") == "INTERNAL_ONLY":
        return CheckpointPolicyResult("suppressed", "no_decision_change", None)
    evidence_quality = candidate.get("evidence_quality") or {}
    quality_status = evidence_quality.get("status")
    if output_intent.get("mode") in {"ACTION", "SUMMARY"} and quality_status != "SUFFICIENT":
        reason = {
            "INSUFFICIENT": "insufficient_signals",
            "LOW_QUALITY": "low_quality_signals",
            "UNMEASURED": "unmeasured_signals",
        }.get(str(quality_status), "missing_evidence_quality")
        return CheckpointPolicyResult("suppressed", reason, None)

    fingerprint = candidate.get("fingerprint") or checkpoint_fingerprint(candidate)
    nonterminal = [c for c in active_checkpoints if c.get("state") not in PRESENTATION_TERMINAL_STATES]
    if any(c.get("fingerprint") == fingerprint for c in nonterminal):
        return CheckpointPolicyResult("suppressed", "duplicate", None)
    decision_key = candidate.get("decision_key")
    superseded = tuple(
        str(c["checkpoint_id"]) for c in nonterminal
        if c.get("fingerprint") != fingerprint and (
            (decision_key is not None and c.get("decision_key") == decision_key)
            # 1.1/1.2 records predate decision_key.  Keep their established topic-level
            # supersession behaviour while 1.3 records use the narrower decision identity.
            or (decision_key is None or c.get("decision_key") is None)
            and c.get("topic") == candidate.get("topic")
        ))

    now_dt = now if isinstance(now, datetime) else datetime.fromisoformat(now)
    try:
        if datetime.fromisoformat(valid_until) <= now_dt:
            return CheckpointPolicyResult("expired", "expired", None, superseded)
    except (TypeError, ValueError):
        return CheckpointPolicyResult("suppressed", "missing_validity", None, superseded)

    if is_driving:
        return CheckpointPolicyResult("queued", "unsafe_while_moving", candidate, superseded)

    topic = candidate.get("topic")
    if topic in set(cadence_memory.get("dismissed_topics") or ()):
        return CheckpointPolicyResult("suppressed", "dismissed_for_window", None, superseded)
    last = (cadence_memory.get("last_offered_by_topic") or {}).get(topic)
    if last is not None:
        last_dt = last if isinstance(last, datetime) else datetime.fromisoformat(last)
        if (now_dt - last_dt).total_seconds() < 20 * 60:
            return CheckpointPolicyResult("suppressed", "topic_cooldown", None, superseded)

    if candidate.get("maintenance"):
        return CheckpointPolicyResult("suppressed", "silent_maintenance", None, superseded)
    if float(candidate.get("confidence") or 0.0) <= 0.0:
        return CheckpointPolicyResult("suppressed", "low_evidence", None, superseded)
    return CheckpointPolicyResult("ready", None, candidate, superseded)


def checkpoint_fingerprint(candidate: dict) -> str:
    """Digest the material candidate fields, omitting metadata that does not
    change the advice.

    Poll time, the message, model and solver invocation identifiers and the presentation lease take
    no part in the fingerprint: two candidates with the same material content must deduplicate.
    """
    action = candidate.get("current_action")
    recommendation = candidate.get("recommendation") or {}
    if not isinstance(recommendation, dict):
        recommendation = {}
    if action is None:
        action = recommendation.get("action")
    future_plan = candidate.get("future_plan") or []
    head = future_plan[0] if future_plan and isinstance(future_plan[0], dict) else None
    material = {
        "topic": candidate.get("topic"),
        "surface": candidate.get("surface"),
        "current_action": action,
        "action_window": candidate.get("action_window"),
        # The next action, its code and its window, is material content for the driver: a plan
        # that moves the swap to another bucket is *different* advice and must not be deduplicated
        # against the old one. The rest of the schedule is excluded from the fingerprint so the
        # revision does not change on every poll.
        "future_head": ({"code": head.get("code"), "window": head.get("window")}
                        if head is not None else None),
        "urgency_band": candidate.get("urgency_band"),
        "material_revision": candidate.get("material_revision"),
        "reason_code": candidate.get("reason_code"),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def normalize_validity(solver: str, *, shift_end: str | None = None,
                       policy_effective_boundary: str | None = None,
                       solver_bucket_end: str | None = None,
                       allocation_bucket_end: str | None = None,
                       rest_window_end: str | None = None,
                       freshness_deadline: str | None = None) -> str | None:
    """Return the earliest known validity boundary for one solver family.

    The function intentionally does not invent a default window.  Missing all
    solver-specific/freshness boundaries returns ``None`` so policy can suppress
    the candidate instead of showing stale advice.
    """
    solver_name = str(solver).upper()
    if solver_name == "S1":
        values = (shift_end, policy_effective_boundary, freshness_deadline)
    elif solver_name == "S2":
        values = (solver_bucket_end, shift_end, freshness_deadline)
    elif solver_name == "S4":
        values = (allocation_bucket_end, freshness_deadline)
    elif solver_name == "S7":
        values = (rest_window_end, shift_end, freshness_deadline)
    elif solver_name == "RULE":
        values = (policy_effective_boundary, shift_end, freshness_deadline)
    else:
        return None
    known = [v for v in values if v is not None]
    if not known:
        return None
    try:
        return min(known, key=datetime.fromisoformat)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"validity boundary không phải ISO datetime: {known}") from exc


def normalize_shift_plan(solution: dict) -> tuple[dict | None, list[dict]]:
    """Normalize S2 output into current action and a future plan."""
    schedule = [item for item in (solution.get("schedule") or [])
                if isinstance(item, dict)]
    if not schedule:
        return solution.get("next_action"), []
    current = schedule[0]
    future = schedule[1:]
    next_action = solution.get("next_action")
    if next_action and (not future or future[0] != next_action):
        if next_action != current:
            future = [next_action, *future]
    return current, future


def deduplicate_candidates(candidates: list[dict]) -> list[dict]:
    """Keep the first material candidate for each stable fingerprint."""
    seen: set[str] = set()
    result: list[dict] = []
    for candidate in candidates:
        fingerprint = checkpoint_fingerprint(candidate)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(candidate)
    return result


def _priority_band(candidate: dict) -> int:
    topic = candidate.get("topic")
    urgency = candidate.get("urgency_band")
    if topic == "safety_reserved" or (topic == "energy" and urgency in {"high", "critical"}):
        return 0
    if urgency in {"high", "critical"}:
        return 1
    if topic in {"shift_boundary", "energy", "rest", "bonus_eligibility"}:
        return 1
    return 2


def select_primary_candidate(candidates: list[dict]) -> dict | None:
    """Select one primary candidate using a stable lexicographic policy."""
    if not candidates:
        return None

    def key(candidate: dict):
        valid_until = (candidate.get("valid_until")
                       or (candidate.get("validity") or {}).get("valid_until")
                       or "9999-12-31T23:59:59+00:00")
        created_at = candidate.get("created_at") or "9999-12-31T23:59:59+00:00"
        try:
            expiry = datetime.fromisoformat(valid_until).timestamp()
        except (TypeError, ValueError):
            expiry = float("inf")
        try:
            created = datetime.fromisoformat(created_at).timestamp()
        except (TypeError, ValueError):
            created = float("inf")
        return (_priority_band(candidate), expiry,
                -float(candidate.get("expected_impact") or 0.0),
                -float(candidate.get("confidence") or 0.0), created,
                str(candidate.get("topic") or ""))

    return min(candidates, key=key)


def _execution_link(event: dict) -> dict:
    payload = event.get("payload") or {}
    return {
        "event_id": event["event_id"],
        "segment_id": payload.get("segment_id"),
        "relation_type": event.get("relation_type"),
        "confidence": event.get("confidence"),
    }


def project_checkpoint_events(events: list[dict]) -> dict:
    """Replay events into one checkpoint state deterministically.

    ``execution_observed`` is deliberately side-channel data: it records a
    segment relation but never changes presentation state or implies acceptance.
    Duplicate event IDs are idempotent only when their content is identical.
    """
    unique: dict[str, dict] = {}
    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            raise CheckpointTransitionError("event thiếu event_id")
        old = unique.get(event_id)
        if old is not None:
            if old != event:
                raise CheckpointTransitionError(
                    f"event_id {event_id!r} xuất hiện với payload khác nhau")
            continue
        unique[event_id] = event

    ordered = sorted(unique.values(), key=_event_sort_key)
    if not ordered:
        raise CheckpointTransitionError("checkpoint phải có event created")

    checkpoint_id = ordered[0].get("checkpoint_id")
    driver_id = ordered[0].get("driver_id")
    state: str | None = None
    display_id = None
    execution_links: list[dict] = []
    expanded_event_ids: list[str] = []
    event_ids: list[str] = []

    for event in ordered:
        if event.get("checkpoint_id") != checkpoint_id:
            raise CheckpointTransitionError("các event không cùng checkpoint_id")
        if event.get("driver_id") != driver_id:
            raise CheckpointTransitionError("các event không cùng driver_id")
        event_type = event.get("event_type")
        if event_type not in CHECKPOINT_EVENT_TYPES:
            raise CheckpointTransitionError(f"event type không được phép: {event_type!r}")
        event_ids.append(event["event_id"])

        if event_type in {"execution_observed", "expanded"}:
            if state is None:
                raise CheckpointTransitionError(
                    f"{event_type} cần checkpoint created trước")
            if event_type == "execution_observed":
                execution_links.append(_execution_link(event))
            else:
                expanded_event_ids.append(event["event_id"])
            continue

        if state in PRESENTATION_TERMINAL_STATES:
            raise CheckpointTransitionError(
                f"checkpoint đã ở terminal state {state!r}, không được nhận {event_type!r}")
        allowed = _ALLOWED_TRANSITIONS[state]
        if event_type not in allowed:
            if state is None:
                raise CheckpointTransitionError(
                    f"event {event_type!r} cần event created trước")
            raise CheckpointTransitionError(
                f"transition {state!r} -> {event_type!r} không hợp lệ")
        state = event_type
        if event.get("display_id") is not None:
            display_id = event["display_id"]

    return {
        "checkpoint_id": checkpoint_id,
        "driver_id": driver_id,
        "state": state,
        "display_id": display_id,
        "execution_links": execution_links,
        "expanded_event_ids": expanded_event_ids,
        "event_ids": event_ids,
    }
