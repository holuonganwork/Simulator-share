"""Adapter: an interrupting episode → an ``AdviceCheckpoint`` candidate.

Only episodes that genuinely claim the driver's attention cross this boundary, so the
checkpoint population keeps its existing meaning: one record = one decision to interrupt.
Everything else stays in the episode projection and is read directly by the client.

The adapter *translates*; it never decides.  Surface, action, window, validity and
numbers all come from the episode, which in turn copied them from a solver record.
Nothing here recomputes a plan, invents a window or upgrades a confidence band.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib

from gsm_core.advisor.checkpoint_intents import (
    SignalObservation,
    enrich_candidate_intent,
)
from gsm_core.episodes.contract import CompositeEpisode, Surface
from gsm_core.lifecycle.checkpoint import checkpoint_fingerprint

_BASE_DATE = date(2026, 7, 1)
_TZ = "+07:00"

# Episode type → the existing checkpoint topic vocabulary.  Reusing the vocabulary keeps
# cadence memory, dedup and dismissal behaviour identical to the S1/S2 path.
TOPIC_OF: dict[str, str] = {
    "energy_pressure_building": "energy",
    "energy_episode_recovered": "energy",
    "plan_stability": "shift_timing",
    "plan_slipping_not_driver_failing": "shift_timing",
    "waiting_and_rest_record": "rest",
    "income_quality_not_amount": "shift_boundary",
    "stop_chasing_infeasible_target": "bonus_eligibility",
    "operational_phase_change": "shift_timing",
}

SURFACE_OF: dict[Surface, str] = {
    Surface.NUDGE: "nudge",
    Surface.RECAP: "recap",
    Surface.BRIEF: "brief",
}


class EpisodeAdapterError(ValueError):
    """Raised when an episode cannot be expressed as a checkpoint without inventing data."""


def iso_for_minute(minute: float) -> str:
    """Minutes from the run base day → the ISO stamp the checkpoint contract expects."""
    stamp = datetime.combine(_BASE_DATE, datetime.min.time()) + timedelta(
        minutes=float(minute))
    return stamp.isoformat() + _TZ


def episode_to_candidate(episode: CompositeEpisode, *, shown_at_min: float | None = None
                         ) -> dict:
    """Build the checkpoint candidate for one interrupting episode.

    ``shown_at_min`` is the minute the funnel decided the card may appear, which for a
    queued card is later than detection.  Validity is anchored to that minute, because
    presenting a card whose window already closed while it waited is the failure the
    moving gate exists to avoid.
    """
    surface = SURFACE_OF.get(episode.recommended_surface)
    if surface is None:
        raise EpisodeAdapterError(
            f"surface {episode.recommended_surface.value} không có tương ứng trong "
            "advice_checkpoint.surface (brief|nudge|recap) — episode này phải ở "
            "projection, không phải checkpoint")
    if episode.current_action is None:
        raise EpisodeAdapterError(
            f"episode {episode.episode_type} không có canonical action ⇒ không thể là "
            "một card hành động")

    valid_from = float(shown_at_min if shown_at_min is not None else episode.detected_at)
    valid_until = episode.valid_until
    if valid_until is None:
        raise EpisodeAdapterError(
            f"episode {episode.episode_type} không có valid_until ⇒ fail closed thay vì "
            "bịa một cửa sổ hiệu lực")
    if float(valid_until) <= valid_from:
        raise EpisodeAdapterError(
            f"episode {episode.episode_type} hết hiệu lực trước khi hiển thị được")

    candidate = {
        "driver_id": episode.driver_id,
        "topic": TOPIC_OF.get(episode.episode_type, "shift_timing"),
        "surface": surface,
        "trigger_type": "state_change",
        "current_action": {"code": episode.current_action.code,
                            "label_id": episode.current_action.label_id},
        "future_plan": [{"code": step.code, "label_id": step.label_id,
                          "window": step.window} for step in episode.future_plan],
        "action_window": episode.action_window,
        "validity": {
            "valid_from": iso_for_minute(valid_from),
            "valid_until": iso_for_minute(float(valid_until)),
            "freshness_deadline": iso_for_minute(float(valid_until)),
        },
        "urgency_band": "high" if episode.priority <= 10 else "medium",
        "material_revision": str(episode.material_revision),
        "reason_code": f"episode.{episode.episode_type}",
        "numbers": [{"key": number.key, "value": number.value,
                     "unit": number.unit, "source": number.source}
                     for number in episode.numbers],
        "caveats": list(episode.caveats),
        "confidence": float(episode.confidence),
        "confidence_band": ("high" if episode.confidence >= 0.8
                             else "medium" if episode.confidence >= 0.5 else "low"),
        "snapshot_ref": (episode.source_snapshot_refs[0]
                          if episode.source_snapshot_refs else episode.episode_id),
        "solver_artifact_ref": episode.material_fingerprint,
        "source_decision_id": episode.episode_id,
        "run_id": episode.run_id,
        "solver_input_refs": list(episode.source_checkpoint_refs),
        "solver_report_refs": list(episode.source_solver_report_refs),
        "solver_set": ["EPISODE"],
        "solver_status": "optimal",
        "maintenance": False,
        "data_mode": episode.data_mode.value.lower(),
        "is_mock": bool(episode.is_mock),
        "created_at": iso_for_minute(valid_from),
        "episode_id": episode.episode_id,
        "source_episode_id": episode.episode_id,
        "episode_type": episode.episode_type,
        "decision_key": episode.decision_key,
        "evidence_refs": list(episode.evidence_refs),
    }
    observations: dict[str, SignalObservation] = {}
    for signal in episode.component_signals:
        age = max(0.0, valid_from - float(signal.at_min))
        fresh = signal.freshness_min is None or age <= float(signal.freshness_min) + 1e-6
        previous = observations.get(signal.signal_type)
        observation = SignalObservation(
            signal.signal_type, fresh=fresh,
            trusted=bool(signal.source), provenance=bool(signal.source_ref), grain_ok=True)
        if previous is not None:
            observation = SignalObservation(
                signal.signal_type, fresh=previous.fresh and observation.fresh,
                trusted=previous.trusted and observation.trusted,
                provenance=previous.provenance and observation.provenance,
                grain_ok=previous.grain_ok and observation.grain_ok)
        observations[signal.signal_type] = observation
    candidate = enrich_candidate_intent(candidate, observations=observations)
    candidate["fingerprint"] = checkpoint_fingerprint(candidate)
    # A checkpoint record is immutable, so a material revision needs a new record id.
    # ``decision_key`` above remains stable and lets the lifecycle/UI repaint the same
    # driver decision instead of stacking two visible cards.
    identity = hashlib.sha256(
        f"{episode.episode_id}:{candidate['fingerprint']}".encode("utf-8")
    ).hexdigest()[:24]
    candidate["checkpoint_id"] = f"ckpt-{identity}"
    return candidate
