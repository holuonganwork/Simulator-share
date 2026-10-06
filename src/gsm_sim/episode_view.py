"""Projection: completed ``RunResult`` → :class:`ActorRunView` for the episode engine.

This is the only module that knows both the simulator's shape and the episode contract.
It reads a finished run and rewrites it into plain data; it never mutates the run, never
re-simulates anything and never draws randomness — the same discipline ``demo_trace.py``
follows for the replay timeline.

Two things are resolved here rather than inside detectors, because both need simulator
knowledge that the pure core should not carry:

* the lifecycle **state** of each checkpoint, replayed from its event stream;
* the S1 **feasibility verdict**, read from the persisted ``bonus_feasibility`` solver
  report that the checkpoint points at.  Detectors receive the verdict as a field and can
  therefore never be tempted to re-derive it from payout or trip count.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from gsm_core.episodes.contract import DataMode
from gsm_core.episodes.view import (
    ActorRunView,
    Block,
    CheckpointView,
    IncomeEntry,
    RunEvent,
    Snapshot,
)
from gsm_core.lifecycle.checkpoint import project_checkpoint_events
from gsm_sim.demo_trace import minute_from_iso
from gsm_sim.journey import build_journey, income_entries


def _checkpoint_minute(checkpoint: dict) -> float | None:
    value = (checkpoint.get("validity") or {}).get("valid_from") or checkpoint.get("created_at")
    return minute_from_iso(value)


def _future_step(checkpoint: dict) -> tuple[str | None, dict | None]:
    for step in checkpoint.get("future_plan") or []:
        if isinstance(step, dict) and step.get("code"):
            return str(step["code"]), step.get("window")
    return None, None


def _solver_reports(result: Any) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    for artifact in getattr(result, "advice_artifacts", []) or []:
        if artifact.get("artifact_type") != "solver_report":
            continue
        reports[str(artifact.get("artifact_id"))] = artifact.get("payload") or {}
    return reports


def _checkpoint_states(result: Any) -> dict[str, str]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in getattr(result, "advice_checkpoint_events", []) or []:
        grouped[str(event["checkpoint_id"])].append(dict(event))
    states: dict[str, str] = {}
    for checkpoint in getattr(result, "advice_checkpoints", []) or []:
        cid = str(checkpoint["checkpoint_id"])
        events = grouped.get(cid, [])
        if not events:
            states[cid] = str(checkpoint.get("state") or "unknown")
            continue
        try:
            states[cid] = str(project_checkpoint_events(events)["state"])
        except Exception:
            # An unreplayable stream is reported as unknown, never promoted to ready.
            states[cid] = "unknown"
    return states


def build_actor_run_view(result: Any, actor_id: int, *,
                         data_mode: DataMode = DataMode.SIMULATED,
                         states: dict[str, str] | None = None,
                         reports: dict[str, dict] | None = None) -> ActorRunView:
    """Assemble one actor's view.  ``states``/``reports`` may be shared across actors."""
    actor = next((item for item in getattr(result, "actors", [])
                  if int(item.actor_id) == int(actor_id)), None)
    if actor is None:
        raise ValueError(f"actor {actor_id} không tồn tại trong run")

    run_id = str(getattr(result, "run_id", ""))
    seed = int(getattr(result, "seed", 0))
    driver_id = f"d-{int(actor_id)}"
    states = _checkpoint_states(result) if states is None else states
    reports = _solver_reports(result) if reports is None else reports

    events: list[RunEvent] = []
    raw_events = list(getattr(result, "events", []))
    for index, event in enumerate(raw_events):
        if int(getattr(event, "actor_id", -1)) != int(actor_id):
            continue
        events.append(RunEvent(
            t_min=float(event.t_min), kind=str(event.kind),
            detail=dict(getattr(event, "detail", {}) or {}),
            ref=f"event#{index}"))
    events.sort(key=lambda item: (item.t_min, item.kind, item.ref))

    journey = build_journey(result, int(actor_id))
    timeline = tuple(Block(
        t0=float(block.t0), t1=float(block.t1), kind=str(block.kind),
        order_id=block.order_id, payout_vnd=block.payout_vnd,
        ref=f"block:{block.kind}@{block.t0:.3f}") for block in journey.timeline)

    snapshots = tuple(sorted((Snapshot(
        t_min=float(row.get("t_min", 0.0)), state=str(row.get("state", "")),
        soc_pct=float(row.get("soc_pct", 0.0)), payout_vnd=int(row.get("payout_vnd", 0)),
        points=int(row.get("points", 0)), trips_done=int(row.get("trips_done", 0)),
        online_min=float(row.get("online_min", 0.0)),
        rest_min=float(row.get("rest_min", 0.0)),
        charge_min=float(row.get("charge_min", 0.0)),
        ref=f"snapshot#{row.get('event_index')}@{float(row.get('t_min', 0.0)):.3f}")
        for row in getattr(result, "trace_snapshots", []) or []
        if int(row.get("actor_id", -1)) == int(actor_id)),
        key=lambda item: item.t_min))

    checkpoints: list[CheckpointView] = []
    for checkpoint in getattr(result, "advice_checkpoints", []) or []:
        if str(checkpoint.get("driver_id")) != driver_id:
            continue
        at = _checkpoint_minute(checkpoint)
        if at is None:
            continue
        refs = checkpoint.get("solver_report_refs") or []
        report = reports.get(str(refs[0])) if refs else None
        solution = (report or {}).get("solution") or {}
        future_code, future_window = _future_step(checkpoint)
        cid = str(checkpoint["checkpoint_id"])
        checkpoints.append(CheckpointView(
            checkpoint_id=cid, at_min=float(at),
            valid_until_min=minute_from_iso(
                (checkpoint.get("validity") or {}).get("valid_until")),
            solver_set=tuple(str(name) for name in (checkpoint.get("solver_set") or ())),
            state=states.get(cid, "unknown"), topic=str(checkpoint.get("topic") or ""),
            current_code=((checkpoint.get("current_action") or {}).get("code")),
            future_code=future_code, future_window=future_window,
            action_window=checkpoint.get("action_window"),
            fingerprint=str(checkpoint.get("fingerprint") or ""),
            report_ref=str(refs[0]) if refs else "",
            snapshot_ref=str(checkpoint.get("snapshot_ref") or ""),
            feasible=solution.get("feasible") if "feasible" in solution else None,
            infeasible_reason=(report or {}).get("infeasible_reason"),
            gap_points=solution.get("gap_points"),
            confidence=float((report or {}).get("confidence") or 0.0),
            numbers=tuple(dict(number) for number in (checkpoint.get("numbers") or [])
                          if isinstance(number, dict)),
            decision_key=(str(checkpoint["decision_key"])
                          if checkpoint.get("decision_key") else None)))
    checkpoints.sort(key=lambda item: (item.at_min, item.checkpoint_id))

    ledger = income_entries(journey.timeline, result.events, int(actor_id))
    income = tuple(IncomeEntry(t_min=float(t), amount_vnd=int(amount), source=str(source),
                               ref=f"income:{source}@{float(t):.3f}")
                   for t, amount, source in ledger)

    segments = tuple(Block(
        t0=float(segment.get("t0", 0.0)), t1=float(segment.get("t1", 0.0)),
        kind=str(segment.get("kind", "")), order_id=segment.get("order_id"),
        ref=str(segment.get("segment_id", "")))
        for segment in getattr(result, "segments", []) or []
        if int(segment.get("actor_id", -1)) == int(actor_id))

    return ActorRunView(
        run_id=run_id, seed=seed, actor_id=int(actor_id), driver_id=driver_id,
        archetype=str(getattr(actor, "archetype", "")),
        shift_start_min=float(actor.shift_start_min),
        shift_end_min=float(actor.shift_end_min),
        data_mode=data_mode, is_mock=True,
        events=tuple(events), timeline=timeline, snapshots=snapshots,
        checkpoints=tuple(checkpoints), income=income, segments=segments,
        # Tenure is copied, never derived.  The field was declared on the view but never
        # populated, which meant the newbie capability was blocked by *absence* — a weak
        # guard, because the next detector that wants tenure has nothing stopping it from
        # reaching for archetype or payout as a proxy.  Carrying the declared value with
        # its data mode moves the guard into ``eligibility.profile_from_simulator``, which
        # refuses outright for REAL/LIVE.
        tenure_days=(int(actor.tenure_days)
                     if getattr(actor, "tenure_days", None) is not None else None),
        planned_rest_hour_from_previous_day=getattr(actor, "planned_rest_hour", None))


def build_all_views(result: Any, *, data_mode: DataMode = DataMode.SIMULATED
                    ) -> dict[int, ActorRunView]:
    """Build every actor's view once, sharing the expensive per-run lookups."""
    states = _checkpoint_states(result)
    reports = _solver_reports(result)
    return {int(actor.actor_id): build_actor_run_view(
        result, int(actor.actor_id), data_mode=data_mode, states=states, reports=reports)
        for actor in getattr(result, "actors", [])}
