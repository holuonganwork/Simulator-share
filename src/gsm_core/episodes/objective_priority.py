"""Objective collision — a priority contract built from authority that already exists.

The capability was blocked on "the UI has no authority to choose between bonus, mission,
energy and rest".  That framing was wrong in a useful way: the *engine* already chooses,
deterministically, and logs every choice.  What was missing was a published record of the
choice, not the choice itself.

Where the order comes from — all of it observable, none of it invented here:

* ``world.py`` runs a committed rest (``rest_forced``) unconditionally;
* ``advice_bridge.should_defer_rest`` may defer a rest and says why, and the reasons are
  ``soc_low`` / ``fatigued`` / ``defer_cap`` / ``committed`` — i.e. an energy or health
  constraint outranks a rest plan;
* every outcome, including the non-defer one, is logged as ``advice_rest_veto``;
* the money channel passes through its own gate, logged as ``advice_bonus_gate``.

So the lexicographic order below is a *reading of the engine*, and
:func:`resolve_priority` refuses to produce a verdict it cannot attribute to a logged
event.  Three things it will never do: rank by a weighted score, choose when the evidence
is missing, or let a caller pass in an order of its own.

**Driver-facing is OFF and this module does not decide otherwise.**  ``fatigued`` is one
of the veto reasons, and fatigue is a latent variable the product may not surface
(``advisor-objective-model-v2`` §1.2b).  Whether a collision may be *shown*, and in what
words, is a Product Owner decision.  :func:`presentable_collision` therefore fails closed
on anything carrying a health reason, and the simulator showcase is the only consumer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from gsm_core.episodes.contract import Authority, DataMode


class ObjectivePriorityError(ValueError):
    """Raised when a priority verdict cannot be attributed to observed authority."""


class Objective(str, Enum):
    ENERGY = "energy"
    REST = "rest"
    BONUS = "bonus"
    MISSION = "mission"
    POSITIONING = "positioning"


# Lower is stronger.  This is a transcription of the engine's own gate order, not a
# preference: an energy constraint vetoes a rest, a committed rest overrides money, and
# money channels sit behind both.  Positioning is last because it is the only channel the
# engine treats as strictly optional.
LEXICOGRAPHIC_ORDER: tuple[Objective, ...] = (
    Objective.ENERGY, Objective.REST, Objective.BONUS,
    Objective.MISSION, Objective.POSITIONING,
)

# Veto reasons the engine actually emits, mapped to the objective that won.  A reason that
# is not in this map is not resolvable — see ``resolve_priority``.
VETO_REASON_WINNER: dict[str, Objective] = {
    "soc_low": Objective.ENERGY,
    "fatigued": Objective.REST,
    "defer_cap": Objective.REST,
    "committed": Objective.REST,
    "shift_budget_exhausted": Objective.POSITIONING,
    "cadence_suppressed": Objective.POSITIONING,
}

# Reasons whose *existence* is a health signal.  A collision resolved by one of these may
# be recorded and measured, but may not be rendered to a driver without an owner decision.
HEALTH_REASONS = frozenset({"fatigued"})


@dataclass(frozen=True)
class ObjectiveCollision:
    """One resolved collision, with the evidence that resolved it."""

    at_min: float
    primary_objective: Objective
    secondary_objectives: tuple[Objective, ...]
    conflicts: tuple[tuple[Objective, Objective], ...]
    override_reason: str
    authority: Authority
    valid_until_min: float | None
    source_event_refs: tuple[str, ...]
    source_report_refs: tuple[str, ...] = ()
    data_mode: DataMode = DataMode.SIMULATED
    driver_facing_allowed: bool = False
    blocked_reason: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "at_min": round(float(self.at_min), 3),
            "primary_objective": self.primary_objective.value,
            "secondary_objectives": [o.value for o in self.secondary_objectives],
            "conflicts": [[a.value, b.value] for a, b in self.conflicts],
            "override_reason": self.override_reason,
            "authority": self.authority.value,
            "valid_until_min": (None if self.valid_until_min is None
                                else round(float(self.valid_until_min), 3)),
            "source_event_refs": list(self.source_event_refs),
            "source_report_refs": list(self.source_report_refs),
            "data_mode": self.data_mode.value,
            "driver_facing_allowed": bool(self.driver_facing_allowed),
            "blocked_reason": self.blocked_reason,
            "notes": list(self.notes),
        }


def _rank(objective: Objective) -> int:
    return LEXICOGRAPHIC_ORDER.index(objective)


def resolve_priority(*, at_min: float, contenders: list[Objective], veto_reason: str,
                     source_event_refs: tuple[str, ...],
                     data_mode: DataMode = DataMode.SIMULATED,
                     valid_until_min: float | None = None,
                     source_report_refs: tuple[str, ...] = ()) -> ObjectiveCollision:
    """Resolve one collision, or refuse.

    Fails closed on all three ways the evidence can be inadequate: no reason, a reason the
    engine does not emit, and a reason whose winner is not among the contenders.  The last
    one matters most — it is what stops this module from quietly *inventing* a winner when
    the logged reason belongs to some other decision entirely.
    """
    if not contenders:
        raise ObjectivePriorityError("không có objective nào tranh chấp")
    if len(set(contenders)) < 2:
        raise ObjectivePriorityError(
            "chỉ có một objective — không phải xung đột, không phát hành verdict")
    if not source_event_refs:
        raise ObjectivePriorityError(
            "verdict phải truy được về event đã log; không có ref thì không phát hành")
    reason = str(veto_reason or "")
    if reason not in VETO_REASON_WINNER:
        raise ObjectivePriorityError(
            f"veto reason {reason!r} không nằm trong tập engine phát ra — "
            "không suy diễn người thắng")
    winner = VETO_REASON_WINNER[reason]
    if winner not in contenders:
        raise ObjectivePriorityError(
            f"reason {reason!r} chỉ về {winner.value} nhưng objective đó không tranh chấp")

    losers = tuple(sorted((o for o in set(contenders) if o != winner), key=_rank))
    health = reason in HEALTH_REASONS
    return ObjectiveCollision(
        at_min=float(at_min), primary_objective=winner, secondary_objectives=losers,
        conflicts=tuple((winner, loser) for loser in losers),
        override_reason=reason, authority=Authority.POLICY,
        valid_until_min=valid_until_min, source_event_refs=tuple(source_event_refs),
        source_report_refs=tuple(source_report_refs), data_mode=data_mode,
        driver_facing_allowed=False,
        blocked_reason=("health_reason_requires_owner_decision" if health
                        else "driver_facing_pending_owner_decision"),
        notes=("Thứ tự là bản đọc cổng của engine, không phải trọng số.",))


def presentable_collision(collision: ObjectiveCollision) -> bool:
    """Whether this collision could ever be rendered.  Currently always ``False``.

    Kept as a function rather than a constant so the owner decision has exactly one place
    to land, and so a test can assert that flipping the health case alone is not enough.
    """
    if collision.override_reason in HEALTH_REASONS:
        return False
    return bool(collision.driver_facing_allowed)


def collisions_from_events(events, *, data_mode: DataMode = DataMode.SIMULATED
                           ) -> list[ObjectiveCollision]:
    """Read collisions off a run's own veto events.  Unresolvable ones are skipped.

    Skipping rather than raising is deliberate here: a run contains many veto events that
    are not collisions at all, and one unrecognised reason must not take down the pass.
    Every skip is still visible, because the count of returned collisions can be compared
    against the count of veto events by the caller.
    """
    resolved: list[ObjectiveCollision] = []
    for index, event in enumerate(events):
        if getattr(event, "kind", "") != "advice_rest_veto":
            continue
        detail = dict(getattr(event, "detail", {}) or {})
        reason = str(detail.get("reason") or "")
        winner = VETO_REASON_WINNER.get(reason)
        if winner is None:
            continue
        contenders = [winner, Objective.REST] if winner is not Objective.REST else [
            Objective.REST, Objective.ENERGY]
        if winner is Objective.REST and reason == "committed":
            contenders = [Objective.REST, Objective.BONUS]
        try:
            resolved.append(resolve_priority(
                at_min=float(getattr(event, "t_min", 0.0)), contenders=contenders,
                veto_reason=reason, data_mode=data_mode,
                source_event_refs=(f"event#{index}",)))
        except ObjectivePriorityError:
            continue
    return resolved
