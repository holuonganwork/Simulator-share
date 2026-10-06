"""Canonical, post-run projection used by the Web demo.

The simulator remains the only component that mutates actor/order state.  This module only
reads a completed ``RunResult`` and turns existing events, observer snapshots, segments and
checkpoint records into a deterministic actor timeline.  It must not import SimPy or draw
randomness.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from gsm_core.lifecycle.checkpoint import project_checkpoint_events, select_primary_candidate


_BASE_DATE = date(2026, 7, 1)
_TIME_EPSILON = 1e-6
_VISIBLE_EVENT_KINDS = frozenset({
    "go_online", "order_matched", "order_declined", "order_skipped_soc",
    "order_cancelled_after_accept", "pickup", "dropoff", "rest",
    "charge_home_start", "charge_home_end", "go_swap", "swap_done", "swap_failed",
    "relocate", "end_shift", "censored_end_of_run", "advice_given",
    "advice_suppressed", "advice_bonus_gate", "advice_shift_extend",
    "advice_rest_window", "standby_followed",
})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:24]


def minute_from_iso(value: str | None) -> float | None:
    """Convert a simulator trace ISO timestamp back to minutes from the base day."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    start = datetime.combine(_BASE_DATE, datetime.min.time(), tzinfo=parsed.tzinfo)
    return (parsed - start).total_seconds() / 60.0


def _driver_snapshot(raw: dict[str, Any], run_id: str, seed: int) -> dict[str, Any]:
    return {
        "driver_id": f"d-{raw['actor_id']}",
        "actor_id": int(raw["actor_id"]),
        "state": raw.get("state"),
        "simulation_time_min": float(raw.get("t_min", 0.0)),
        "position": {
            "lat": float(raw.get("lat", 0.0)),
            "lng": float(raw.get("lon", 0.0)),
            "cell": raw.get("cell"),
        },
        "soc_pct": float(raw.get("soc_pct", 0.0)),
        "payout_vnd": int(raw.get("payout_vnd", 0)),
        "gross_vnd": int(raw.get("gross_vnd", 0)),
        "points": int(raw.get("points", 0)),
        "trips_done": int(raw.get("trips_done", 0)),
        "orders_offered": int(raw.get("orders_offered", 0)),
        "orders_accepted": int(raw.get("orders_accepted", 0)),
        "orders_completed": int(raw.get("orders_completed", 0)),
        "orders_cancelled": int(raw.get("orders_cancelled", 0)),
        "online_min": float(raw.get("online_min", 0.0)),
        "rest_min": float(raw.get("rest_min", 0.0)),
        "charge_min": float(raw.get("charge_min", 0.0)),
        "shift_start_min": raw.get("shift_start_min"),
        "shift_end_min": raw.get("shift_end_min"),
        "provenance": {
            "run_id": run_id, "seed": seed, "data_mode": "sim-engine", "is_mock": True,
        },
    }


def _order_map(result: Any) -> dict[int, Any]:
    return {int(order.order_id): order for order in getattr(result, "orders", [])}


def _trip(order: Any, state: str) -> dict[str, Any]:
    return {
        "trip_id": f"trip-{int(order.order_id)}",
        "order_id": int(order.order_id),
        "state": state,
        "pickup": {"lat": float(order.pickup_lat), "lon": float(order.pickup_lon),
                    "cell": order.pickup_cell},
        "destination": {"lat": float(order.drop_lat), "lon": float(order.drop_lon),
                         "cell": order.drop_cell},
        "dist_km": float(order.dist_km),
        "gross_vnd": int(order.gross_vnd),
    }


def _state_for_event(kind: str) -> str | None:
    return {
        "order_matched": "MATCHED", "order_declined": "DECLINED",
        "order_skipped_soc": "SKIPPED_SOC", "order_cancelled_after_accept": "CANCELLED_AFTER_ACCEPT",
        "pickup": "PICKED_UP", "dropoff": "COMPLETED",
    }.get(kind)


def _segment_for(segments: list[dict], actor_id: int, t_min: float,
                 order_id: int | None) -> dict | None:
    candidates = [s for s in segments
                  if int(s.get("actor_id", -1)) == actor_id
                  and (order_id is None or s.get("order_id") == order_id)]
    if not candidates:
        return None
    exact_start = [s for s in candidates if abs(float(s.get("t0", -1)) - t_min) < 1e-6]
    if exact_start:
        # On a pickup boundary, prefer the passenger leg over the just-finished pickup leg.
        exact_start.sort(key=lambda s: (s.get("kind") != "on_trip", s.get("segment_id", "")))
        return dict(exact_start[0])
    exact_end = [s for s in candidates if abs(float(s.get("t1", -1)) - t_min) < 1e-6]
    if exact_end:
        exact_end.sort(key=lambda s: (s.get("kind") != "on_trip", s.get("segment_id", "")))
        return dict(exact_end[0])
    covering = [s for s in candidates
                if float(s.get("t0", -1)) <= t_min <= float(s.get("t1", -1))]
    return dict(sorted(covering, key=lambda s: (s.get("t0", 0), s.get("segment_id", "")))[0]) \
        if covering else None


def _checkpoint_minute(checkpoint: dict) -> float | None:
    refs = checkpoint.get("validity") or {}
    value = refs.get("valid_from") or checkpoint.get("created_at")
    return minute_from_iso(value)


def _checkpoint_for(checkpoints: list[dict], driver_id: str, t_min: float) -> dict | None:
    candidates = []
    for checkpoint in checkpoints:
        if checkpoint.get("driver_id") != driver_id:
            continue
        observed = _checkpoint_minute(checkpoint)
        if observed is not None and abs(observed - t_min) < _TIME_EPSILON:
            candidates.append(checkpoint)
    if not candidates:
        return None
    return dict(select_primary_candidate(candidates) or candidates[0])


def _checkpoint_states(checkpoints: list[dict], lifecycle_events: list[dict]) -> dict[str, str]:
    events_by_checkpoint: dict[str, list[dict]] = defaultdict(list)
    for event in lifecycle_events:
        checkpoint_id = event.get("checkpoint_id")
        if checkpoint_id:
            events_by_checkpoint[str(checkpoint_id)].append(event)
    states: dict[str, str] = {}
    for checkpoint in checkpoints:
        checkpoint_id = str(checkpoint.get("checkpoint_id"))
        events = events_by_checkpoint.get(checkpoint_id, [])
        if not events and checkpoint.get("state") is None:
            # Legacy hand-built fixtures predate exported lifecycle events.  The runtime
            # trace always carries `created` + policy event; retaining this compatibility
            # inference keeps those fixtures displayable without changing production state.
            states[checkpoint_id] = "ready"
            continue
        try:
            states[checkpoint_id] = str(project_checkpoint_events(events)["state"])
        except Exception:
            # Hand-built fixtures may omit lifecycle events.  Do not invent READY; an
            # absent state is audited and therefore cannot silently become a card.
            states[checkpoint_id] = str(checkpoint.get("state") or "unknown")
    return states


def _prepare_checkpoint_attachments(
        checkpoints: list[dict], lifecycle_events: list[dict], events: list[Any],
        snapshots_by_event: dict[int, dict], driver_id: str
        ) -> tuple[dict[int, dict], list[tuple[dict, dict, float]], list[dict]]:
    """Assign READY checkpoints to one visible event or one explicit extra transition.

    Checkpoint timestamps are minute-bucket timestamps while event times can be fractional.
    The first visible event at/after the bucket owns the checkpoint.  Same-time candidates
    use the lifecycle primary selector; all other candidates receive an audit reason.
    """
    actor_id = int(str(driver_id).removeprefix("d-"))
    states = _checkpoint_states(checkpoints, lifecycle_events)
    audit: list[dict] = []
    ready: list[tuple[float, dict]] = []
    for checkpoint in checkpoints:
        if checkpoint.get("driver_id") != driver_id:
            continue
        checkpoint_id = str(checkpoint.get("checkpoint_id"))
        state = states.get(checkpoint_id, "unknown")
        if state != "ready":
            audit.append({"checkpoint_id": checkpoint_id,
                          "state": state, "reason": f"not_ready:{state}"})
            continue
        t_min = _checkpoint_minute(checkpoint)
        if t_min is None:
            audit.append({"checkpoint_id": checkpoint_id,
                          "state": state, "reason": "missing_alignment"})
            continue
        ready.append((t_min, checkpoint))

    grouped: list[list[tuple[float, dict]]] = []
    for item in sorted(ready, key=lambda pair: (pair[0], str(pair[1].get("checkpoint_id")))):
        if not grouped or abs(grouped[-1][0][0] - item[0]) >= _TIME_EPSILON:
            grouped.append([item])
        else:
            grouped[-1].append(item)

    assignments: dict[int, dict] = {}
    extras: list[tuple[dict, dict, float]] = []
    used_event_indices: set[int] = set()
    visible = [
        (index, event) for index, event in enumerate(events)
        if int(getattr(event, "actor_id", -1)) == actor_id
        and str(getattr(event, "kind", "")) in _VISIBLE_EVENT_KINDS
        and index in snapshots_by_event
    ]

    for group in grouped:
        candidates = [checkpoint for _, checkpoint in group]
        primary = select_primary_candidate(candidates)
        for _, checkpoint in group:
            checkpoint_id = str(checkpoint.get("checkpoint_id"))
            if primary is None or checkpoint_id != primary.get("checkpoint_id"):
                audit.append({"checkpoint_id": checkpoint_id, "state": "ready",
                              "reason": "non_primary_same_time"})
                continue
            t_min = _checkpoint_minute(checkpoint)
            assert t_min is not None
            # UPDATE-147: một checkpoint READY phải được gắn vào transition mà nó CÒN
            # HIỆU LỰC và tài xế ĐỌC ĐƯỢC (không enroute/on_trip). Trước đây gắn mù vào
            # event đầu tiên sau bucket ⇒ 34 card chết `expired` + 12 card mất vĩnh viễn
            # vì moving-at-attach (funnel seed 1000, UPDATE-146 §2.3).
            valid_until_min = minute_from_iso(
                (checkpoint.get("validity") or {}).get("valid_until"))
            after = [
                (index, event) for index, event in visible
                if index not in used_event_indices
                and float(getattr(event, "t_min", -1.0)) >= t_min - _TIME_EPSILON
            ]
            within = [
                (index, event) for index, event in after
                if valid_until_min is None
                or float(getattr(event, "t_min", -1.0)) <= valid_until_min + _TIME_EPSILON
            ]
            safe = [
                (index, event) for index, event in within
                if str((snapshots_by_event.get(index) or {}).get("state"))
                not in {"enroute", "on_trip"}
            ]
            # Ưu tiên transition an toàn trong validity; nếu tài xế di chuyển suốt
            # validity thì vẫn gắn vào transition đầu tiên để moving-gate ghi `queued`
            # (dấu vết lifecycle) thay vì card biến mất không vết.
            event_choice = (safe or within or [None])[0]
            if event_choice is not None:
                index, _event = event_choice
                assignments[index] = checkpoint
                used_event_indices.add(index)
                continue
            if after:
                # có transition sau bucket nhưng toàn bộ nằm ngoài validity — checkpoint
                # hết hạn trước khi tài xế có cơ hội thấy; audit thay vì gắn card chết.
                audit.append({"checkpoint_id": checkpoint_id, "state": "ready",
                              "reason": "expired_before_transition"})
                continue
            previous = [snapshot for snapshot in snapshots_by_event.values()
                        if float(snapshot.get("t_min", -1.0)) <= t_min + _TIME_EPSILON]
            if previous:
                extras.append((checkpoint, dict(previous[-1]), t_min))
            else:
                audit.append({"checkpoint_id": checkpoint_id, "state": "ready",
                              "reason": "missing_snapshot"})
    return assignments, extras, audit


def _checkpoint_transitions(extras: list[tuple[dict, dict, float]],
                            run_id: str, seed: int) -> list[dict]:
    extra: list[dict] = []
    for checkpoint, raw, t_min in extras:
        driver = _driver_snapshot({**raw, "t_min": t_min}, run_id, seed)
        transition_id = "transition-" + _digest({
            "run_id": run_id, "actor_id": raw["actor_id"], "kind": "advice_checkpoint",
            "t_min": t_min, "checkpoint_id": checkpoint["checkpoint_id"],
        })
        extra.append({
            "transition_id": transition_id, "event_index": None, "t_min": t_min,
            "kind": "advice_checkpoint", "driver": driver, "state_delta": {},
            "trip": None, "segment": None, "checkpoint": checkpoint,
            "timeline_event": {"kind": "advice_checkpoint",
                                "checkpoint_id": checkpoint["checkpoint_id"]},
        })
    return extra


def _episode_transitions(result: Any, actor_id: int, raw_snapshots: list[dict],
                         run_id: str, seed: int,
                         occupied_times: set[float]) -> list[dict]:
    """Materialise proactive episode boundaries in the replay timeline.

    Simulator traces are event-driven: a long idle block may have no event at the exact
    minute-30 threshold where A5 becomes actionable.  If the replay only contains simulator
    events, fast-forward jumps over that card before the next event and the driver never sees
    it.  These are presentation-only transition markers derived from the already-completed
    episode funnel; they do not mutate the run or invent a solver decision.
    """
    from gsm_core.episodes.contract import EpisodeStatus, Surface
    from gsm_core.episodes.engine import run_episodes
    from gsm_sim.episode_view import build_actor_run_view

    # Episode boundaries are an optional presentation projection.  A few legacy/demo
    # fixtures intentionally model only the event stream and therefore do not carry the
    # simulator config required to assemble a full ``DriverJourney``.  Keep those traces
    # event-only instead of making actor selection fail; production RunResult objects always
    # include this field and still take the proactive path below.
    config = getattr(result, "config", None)
    if not hasattr(config, "get") or config.get("time.end_min") is None:
        return []

    view = build_actor_run_view(result, actor_id)
    outcome = run_episodes(view)
    eligible_surfaces = {
        Surface.NUDGE, Surface.BANNER, Surface.PLAN_STRIP,
        Surface.PRIORITY_BOARD, Surface.RECAP, Surface.TIMELINE,
    }
    times: dict[float, list[dict]] = defaultdict(list)
    for row in outcome.shown:
        episode = row.episode
        if episode.status is EpisodeStatus.EXPIRED:
            continue
        if episode.recommended_surface not in eligible_surfaces or row.shown_at is None:
            continue
        at = float(row.shown_at)
        if any(abs(at - existing) < _TIME_EPSILON for existing in occupied_times):
            continue
        times.setdefault(at, []).append({
            "episode_id": episode.episode_id,
            "episode_type": episode.episode_type,
            "surface": episode.recommended_surface.value,
            "material_revision": int(episode.material_revision),
        })

    snapshots = sorted(raw_snapshots, key=lambda item: float(item.get("t_min", 0.0)))
    extras: list[dict] = []
    for at, episodes in sorted(times.items()):
        prior = [row for row in snapshots if float(row.get("t_min", -1.0)) <= at + _TIME_EPSILON]
        raw = dict(prior[-1] if prior else (snapshots[0] if snapshots else {
            "actor_id": actor_id, "state": "idle", "soc_pct": 0.0,
            "payout_vnd": 0, "gross_vnd": 0, "points": 0, "trips_done": 0,
            "orders_offered": 0, "orders_accepted": 0, "orders_completed": 0,
            "orders_cancelled": 0, "online_min": 0.0, "rest_min": 0.0,
            "charge_min": 0.0, "lat": 0.0, "lon": 0.0,
        }))
        raw["t_min"] = at
        driver = _driver_snapshot(raw, run_id, seed)
        transition_id = "transition-" + _digest({
            "run_id": run_id, "actor_id": actor_id, "kind": "advice_episode",
            "t_min": at, "episodes": episodes,
        })
        extras.append({
            "transition_id": transition_id, "event_index": None, "t_min": at,
            "kind": "advice_episode", "driver": driver, "state_delta": {},
            "trip": None, "segment": None, "checkpoint": None,
            "timeline_event": {"kind": "advice_episode", "t_min": at,
                                "episodes": episodes},
        })
    return extras


def build_demo_trace(result: Any, actor_id: int, *,
                     include_episode_transitions: bool = False) -> dict[str, Any]:
    """Build the immutable timeline consumed by ``DemoSessionService``.

    Episode-boundary markers are opt-in because a run contains many actors and the replay
    only needs them for the actor the reviewer selected.  The service enables the option at
    selection time; direct trace consumers keep the inexpensive event-only projection.
    """
    actor = next((item for item in getattr(result, "actors", [])
                  if int(item.actor_id) == int(actor_id)), None)
    if actor is None:
        raise ValueError(f"actor {actor_id} không tồn tại trong run")

    run_id = str(getattr(result, "run_id", ""))
    seed = int(getattr(result, "seed", 0))
    raw_snapshots = [dict(item) for item in getattr(result, "trace_snapshots", [])
                     if int(item.get("actor_id", -1)) == int(actor_id)]
    snapshots_by_event = {int(item["event_index"]): item for item in raw_snapshots
                          if item.get("event_index") is not None}
    order_by_id = _order_map(result)
    segments = [dict(item) for item in getattr(result, "segments", [])]
    checkpoints = [dict(item) for item in getattr(result, "advice_checkpoints", [])]
    events = list(getattr(result, "events", []))
    lifecycle_events = [dict(item) for item in
                        getattr(result, "advice_checkpoint_events", [])]
    checkpoint_assignments, checkpoint_extras, checkpoint_audit = (
        _prepare_checkpoint_attachments(
            checkpoints, lifecycle_events, events, snapshots_by_event, f"d-{actor_id}"))
    lifecycle: dict[int, str] = {}
    previous_driver: dict[str, Any] | None = None
    transitions: list[dict[str, Any]] = []
    event_times: set[float] = set()

    for event_index, event in enumerate(events):
        if int(getattr(event, "actor_id", -1)) != int(actor_id):
            continue
        kind = str(getattr(event, "kind", ""))
        if kind not in _VISIBLE_EVENT_KINDS:
            continue
        raw = snapshots_by_event.get(event_index)
        if raw is None:
            # Existing hand-built tests/runs can omit observer snapshots.  Keep the projection
            # explicit and fail-safe instead of fabricating dynamic state.
            continue
        detail = dict(getattr(event, "detail", {}) or {})
        order_id = detail.get("order_id")
        if order_id is not None:
            order_id = int(order_id)
            state = _state_for_event(kind)
            if state is not None:
                lifecycle[order_id] = state
        driver = _driver_snapshot(raw, run_id, seed)
        state_delta = {}
        if previous_driver is not None:
            for key in ("state", "soc_pct", "payout_vnd", "gross_vnd", "points",
                        "trips_done", "orders_offered", "orders_accepted",
                        "orders_completed", "orders_cancelled", "online_min",
                        "rest_min", "charge_min"):
                if previous_driver.get(key) != driver.get(key):
                    state_delta[key] = driver[key]
        previous_driver = driver
        order = order_by_id.get(order_id) if order_id is not None else None
        trip = _trip(order, lifecycle[order_id]) if order is not None and order_id in lifecycle else None
        segment = _segment_for(segments, int(actor_id), float(event.t_min), order_id)
        checkpoint = checkpoint_assignments.get(event_index)
        transition_id = "transition-" + _digest({
            "run_id": run_id, "actor_id": actor_id, "event_index": event_index,
            "kind": kind, "t_min": float(event.t_min), "detail": detail,
        })
        transitions.append({
            "transition_id": transition_id, "event_index": event_index,
            "t_min": float(event.t_min), "kind": kind, "driver": driver,
            "state_delta": state_delta, "trip": trip, "segment": segment,
            "checkpoint": checkpoint,
            "timeline_event": {"event_index": event_index, "kind": kind,
                                "t_min": float(event.t_min), "detail": detail},
        })
        event_times.add(float(event.t_min))

    transitions.extend(_checkpoint_transitions(checkpoint_extras, run_id, seed))
    if include_episode_transitions:
        transitions.extend(_episode_transitions(
            result, actor_id, raw_snapshots, run_id, seed, event_times))
    transitions.sort(key=lambda item: (float(item["t_min"]), item["event_index"] is None,
                                       item["event_index"] if item["event_index"] is not None else -1,
                                       item["transition_id"]))
    for index, transition in enumerate(transitions):
        transition["sequence"] = index
    return {
        "run_id": run_id, "seed": seed, "actor_id": int(actor_id),
        "driver_id": f"d-{actor_id}", "archetype": getattr(actor, "archetype", None),
        "fleet": getattr(getattr(actor, "fleet", None), "value", getattr(actor, "fleet", None)),
        "transitions": transitions,
        "checkpoints": checkpoints,
        "checkpoint_audit": checkpoint_audit,
        "provenance": {"run_id": run_id, "seed": seed,
                        "data_mode": "sim-engine", "is_mock": True},
    }
