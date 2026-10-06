"""``ActorRunView`` — the read-only, plain-data input every episode detector sees.

The view is assembled once per (run, actor) by a projection that knows the simulator
(``gsm_sim.episode_view``).  Detectors depend only on this dataclass, so they can be
exercised from hand-built fixtures with no simulator import.

The view is also the place where the no-future-leak guarantee becomes testable rather
than reviewable: :meth:`ActorRunView.truncated` returns the same view as it would have
existed at minute ``t``.  A detector is leak free exactly when running it against
``view`` and against ``view.truncated(t)`` produces the same episode at ``t``.

``shift_end_min`` is deliberately *not* truncated.  It is the driver's declared plan for
the shift, already present in the solver snapshot and in checkpoint validity, so it is
known at every minute of the shift; it is not an observation of the future.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field, replace
from typing import Any

from gsm_core.episodes.contract import DataMode, EvidenceMode

# Timeline kinds during which text must not be pushed at the driver.
MOVING_KINDS = frozenset({"enroute", "on_trip", "relocate"})


@dataclass(frozen=True)
class RunEvent:
    t_min: float
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)
    ref: str = ""


@dataclass(frozen=True)
class Block:
    t0: float
    t1: float
    kind: str
    order_id: int | None = None
    payout_vnd: int | None = None
    ref: str = ""
    # ``True`` only on a view truncated through a block that is still in progress.  The
    # flag lets a detector say "30 minutes elapsed" without pretending it already knows
    # when that wait will end.
    is_open: bool = False

    @property
    def minutes(self) -> float:
        return float(self.t1) - float(self.t0)


@dataclass(frozen=True)
class Snapshot:
    t_min: float
    state: str
    soc_pct: float
    payout_vnd: int
    points: int
    trips_done: int
    online_min: float
    rest_min: float
    charge_min: float
    ref: str = ""


@dataclass(frozen=True)
class CheckpointView:
    """One persisted AdviceCheckpoint record, flattened to what detectors need."""

    checkpoint_id: str
    at_min: float
    valid_until_min: float | None
    solver_set: tuple[str, ...]
    state: str
    topic: str
    current_code: str | None
    future_code: str | None
    future_window: dict[str, str] | None
    action_window: dict[str, str] | None
    fingerprint: str
    report_ref: str
    snapshot_ref: str
    feasible: bool | None = None
    infeasible_reason: str | None = None
    gap_points: float | None = None
    confidence: float = 0.0
    numbers: tuple[dict[str, Any], ...] = ()
    decision_key: str | None = None

    @property
    def semantic(self) -> tuple[str | None, str | None, str | None]:
        """What the plan actually *says*: now, next, and when next starts."""
        start = (self.future_window or {}).get("start")
        return self.current_code, self.future_code, start


@dataclass(frozen=True)
class IncomeEntry:
    t_min: float
    amount_vnd: int
    source: str          # trip | day_bonus | mission | newbie
    ref: str = ""


@dataclass(frozen=True)
class ActorRunView:
    run_id: str
    seed: int
    actor_id: int
    driver_id: str
    archetype: str
    shift_start_min: float
    shift_end_min: float
    data_mode: DataMode
    is_mock: bool
    events: tuple[RunEvent, ...] = ()
    timeline: tuple[Block, ...] = ()
    snapshots: tuple[Snapshot, ...] = ()
    checkpoints: tuple[CheckpointView, ...] = ()
    income: tuple[IncomeEntry, ...] = ()
    segments: tuple[Block, ...] = ()
    tenure_days: int | None = None
    planned_rest_hour_from_previous_day: int | None = None

    # ------------------------------------------------------------------ access
    def truncated(self, at_min: float) -> "ActorRunView":
        """The view as it existed at ``at_min``.  Used to prove detectors are past-only.

        Blocks that merely *started* before ``at_min`` are clipped, not dropped: at minute
        t the driver knows they have been waiting since t0, but not when the wait will end.
        """
        limit = float(at_min) + 1e-6
        clipped: list[Block] = []
        for block in self.timeline:
            if float(block.t0) > limit:
                continue
            if float(block.t1) <= limit:
                clipped.append(block)
                continue
            clipped.append(replace(block, t1=float(at_min), payout_vnd=None, is_open=True))
        return replace(
            self,
            events=tuple(e for e in self.events if float(e.t_min) <= limit),
            timeline=tuple(clipped),
            snapshots=tuple(s for s in self.snapshots if float(s.t_min) <= limit),
            checkpoints=tuple(c for c in self.checkpoints if float(c.at_min) <= limit),
            income=tuple(i for i in self.income if float(i.t_min) <= limit),
            segments=tuple(s for s in self.segments if float(s.t0) <= limit),
        )

    def events_of(self, *kinds: str, until: float | None = None) -> list[RunEvent]:
        limit = float("inf") if until is None else float(until) + 1e-6
        wanted = frozenset(kinds)
        return [e for e in self.events if e.kind in wanted and float(e.t_min) <= limit]

    def block_at(self, at_min: float) -> Block | None:
        """The block covering ``at_min``, treating each block as half-open ``[t0, t1)``.

        The boundary matters: at the exact minute a trip ends, the driver has stopped.
        Treating ``t1`` as still inside the block marked them as riding at the very
        minute a queued card was due to resume, which made the card look unsafe forever.
        """
        for block in self.timeline:
            if float(block.t0) - 1e-6 <= float(at_min) < float(block.t1) - 1e-6:
                return block
        return None

    def is_moving(self, at_min: float) -> bool:
        block = self.block_at(at_min)
        return block is not None and block.kind in MOVING_KINDS

    def next_safe_min(self, at_min: float, horizon_min: float) -> float | None:
        """First minute at/after ``at_min`` where a text card may be shown.

        This models the ``queued`` gate: a card raised while riding resumes when the
        driver is stationary again.  Returns ``None`` when no safe minute exists inside
        the horizon.
        """
        if not self.is_moving(at_min):
            return float(at_min)
        deadline = float(at_min) + float(horizon_min)
        for block in self.timeline:
            if float(block.t0) < float(at_min) or block.kind in MOVING_KINDS:
                continue
            if float(block.t0) > deadline:
                return None
            return float(block.t0)
        return None

    def snapshot_at(self, at_min: float) -> Snapshot | None:
        """Latest snapshot at or before ``at_min``.  Never looks ahead."""
        if not self.snapshots:
            return None
        times = [float(s.t_min) for s in self.snapshots]
        index = bisect.bisect_right(times, float(at_min) + 1e-6) - 1
        return self.snapshots[index] if index >= 0 else None

    def soc_at(self, at_min: float) -> float | None:
        snapshot = self.snapshot_at(at_min)
        return None if snapshot is None else float(snapshot.soc_pct)

    def payout_at(self, at_min: float) -> int:
        """Driver payout settled up to ``at_min``.

        Built from the income ledger, not from end-of-shift totals, and deliberately
        *payout* rather than gross fare: the two are different money (CLAUDE.md §5).
        """
        return sum(int(entry.amount_vnd) for entry in self.income
                   if float(entry.t_min) <= float(at_min) + 1e-6)

    def income_sources_at(self, at_min: float) -> dict[str, int]:
        totals: dict[str, int] = {}
        for entry in self.income:
            if float(entry.t_min) > float(at_min) + 1e-6:
                continue
            totals[entry.source] = totals.get(entry.source, 0) + int(entry.amount_vnd)
        return {source: amount for source, amount in sorted(totals.items()) if amount}

    def checkpoints_until(self, at_min: float,
                          solver: str | None = None) -> list[CheckpointView]:
        rows = [c for c in self.checkpoints if float(c.at_min) <= float(at_min) + 1e-6
                and (solver is None or solver in c.solver_set)]
        return sorted(rows, key=lambda c: (float(c.at_min), c.checkpoint_id))

    def live_ready_at(self, at_min: float) -> list[CheckpointView]:
        """READY checkpoints whose validity still covers ``at_min``."""
        return [c for c in self.checkpoints
                if c.state == "ready" and float(c.at_min) <= float(at_min) + 1e-6
                and (c.valid_until_min is None
                     or float(c.valid_until_min) >= float(at_min) - 1e-6)]

    def plan_revisions_until(self, at_min: float) -> list[CheckpointView]:
        """S2 records where the *meaning* of the plan changed, newest last.

        Repeated polls that restate the same now/next pair are not revisions.  The first
        plan record counts as the baseline, not as a change.
        """
        revisions: list[CheckpointView] = []
        previous: tuple[str | None, str | None, str | None] | None = None
        seen: set[tuple[float, tuple]] = set()
        for checkpoint in self.checkpoints_until(at_min, solver="S2"):
            key = (float(checkpoint.at_min), checkpoint.semantic)
            if key in seen:
                continue
            seen.add(key)
            if previous is not None and checkpoint.semantic != previous:
                revisions.append(checkpoint)
            previous = checkpoint.semantic
        return revisions

    def latest_plan_at(self, at_min: float) -> CheckpointView | None:
        rows = self.checkpoints_until(at_min, solver="S2")
        return rows[-1] if rows else None

    def idle_blocks(self, min_minutes: float = 0.0) -> list[Block]:
        """Idle blocks of at least ``min_minutes``, compared inclusively.

        The epsilon is not cosmetic.  A detector that fires at ``t0 + 30`` is asking
        whether the wait has *reached* 30 minutes; re-deriving it from a view truncated
        at that same minute gives a block of 29.999999999999996, and a strict ``>=``
        would answer "no" to a question the data can answer.  That mismatch showed up as
        27 false future-leak reports before the boundary was made inclusive.
        """
        return [b for b in self.timeline
                if b.kind == "idle" and b.minutes >= float(min_minutes) - 1e-6]

    def minutes_of(self, kind: str, start: float | None = None,
                   end: float | None = None) -> float:
        total = 0.0
        for block in self.timeline:
            if block.kind != kind:
                continue
            low = float(block.t0) if start is None else max(float(block.t0), float(start))
            high = float(block.t1) if end is None else min(float(block.t1), float(end))
            total += max(0.0, high - low)
        return total

    def shift_fraction(self, at_min: float) -> float:
        span = max(1.0, float(self.shift_end_min) - float(self.shift_start_min))
        return (float(at_min) - float(self.shift_start_min)) / span

    @property
    def evidence_data_mode(self) -> DataMode:
        return self.data_mode

    @property
    def default_evidence_mode(self) -> EvidenceMode:
        return EvidenceMode.OBSERVED
