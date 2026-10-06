"""The advice cadence policy: one rule deciding when the advisor speaks.

A deterministic component that lives outside the interface and is shared by the simulation and the
application, because three different notions of cadence had existed — the simulation counting
ticks, the application using fixed wall-clock hours, and the specification describing shift phases
— so paired comparisons were not measuring what the product would ship.

Given a topic, the current time, the shift phase, the memory of what has been said and whether the
driver is moving, it returns present, queue or suppress, with a typed reason and the minute at
which the topic next becomes eligible.

The approved baseline is in the config below: a minimum gap per topic, a budget of proactive
advice per shift, a priority order with safety first, anchoring by shift *phase* rather than by
wall-clock time, queueing rather than dropping while the driver is moving, and one compliance draw
per decision and material revision rather than a re-roll on every tick.

Why the keyed draw matters more than it looks. An earlier version drew sequentially from a random
stream on every tick until it succeeded, so a nominal compliance of three tenths was effectively
one, and every paired conclusion inherited that error. The keyed draw is a pure hash of the seed,
the decision id and the material revision: the same decision always gives the same answer however
many times it is asked, and only genuinely changed advice earns a new draw.

The boundary between simulation and product. Measuring the software's effect in simulation must not
depend on advice being silenced when a driver presses dismiss: the simulation compares a society of
drivers who follow advice against one working without the system at all.

So dismissal for the rest of a phase is a mechanism of the *real product*: a driver presses dismiss
and the advisor stays quiet for that phase. The simulation does not use that branch, having no
buttons; whether a driver listens is modelled by the keyed draw per archetype. Two tests lock that
boundary.

Cooldown and budget are the opposite: they apply to both, because the real product will have them
and a paired comparison must measure what ships. That is the reason this module exists.

It imports nothing from the simulation or the interface. Each caller builds the memory from its own
source and calls the same evaluation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

PRESENT = "PRESENT"
QUEUE = "QUEUE"
SUPPRESS = "SUPPRESS"

# Topics in the safety class. Text cards have no separate emergency modality yet, so safety is
# only prioritised ahead of budget and cooldown while the vehicle is stationary; while moving it
# queues like any other card.
SAFETY_TOPICS = frozenset({"safety"})

# The width of the bucket that identifies one decision, in minutes. This is the single source of
# truth: the world's decision id and the compliance draw must use the same number, or one decision
# id spans two draws and the re-rolling problem silently returns. A test caught exactly that once,
# when the draw used the cooldown's width while the decision id used this one.
DECISION_BUCKET_MIN = 30.0


def decision_bucket(now_min: float, bucket_min: float | None = None) -> int:
    """The index of the decision bucket containing the given minute."""
    return int(float(now_min) // float(bucket_min or DECISION_BUCKET_MIN))


@dataclass(frozen=True)
class CadenceConfig:
    """The approved baseline figures. The design states that the initial
    baseline is to be tuned from telemetry or a micro-randomised evaluation rather than by feel, so
    these must not be adjusted by intuition: a tighter cadence has to be an experimental arm."""
    min_gap_min_per_topic: float = 20.0
    max_proactive_per_shift: int = 6
    # The decision bucket width this channel uses; the shared constant by default. A channel with
    # a different decision cadence, such as the positioning planner, passes its own.
    decision_bucket_min: float = DECISION_BUCKET_MIN
    # Shift phase boundaries as *fractions* of the shift, which is a structural constant rather
    # than an economic one: every shift has a beginning, a middle and an end, and a short shift and
    # a long one are treated proportionately.
    phase_early_frac: float = 0.25
    phase_late_frac: float = 0.75

    @property
    def effective_gap_min(self) -> float:
        """The enforced cooldown: the larger of the configured cooldown and
        the decision bucket width.

        A cooldown shorter than the bucket width let the cadence permit speaking again *about the
        same decision* in the gap between them. The card reached a real driver, but its event id
        collided on the bucket key so the store deduplicated it: no event, and no charge against
        the per-shift budget. The budget cap was therefore unenforced for a third of the time, and
        the event denominator ran short.

        Fixed by a derived rule rather than a new constant: speaking again within one decision
        bucket is repeating oneself, not new advice. With the gap at least the bucket width, two
        utterances always fall in different buckets, which makes "one decision, at most one
        utterance" a fact rather than a promise.

        The configured minimum gap remains the approved baseline. It is a floor, not a final
        value; the ceiling is imposed by the channel's own decision cadence. This repository has
        already hit this same failure once on the draw side, and this is the other half.
        """
        return max(float(self.min_gap_min_per_topic), float(self.decision_bucket_min))


@dataclass
class CadenceMemory:
    """The history a cadence decision needs, built by each caller from its own
    source.

    It holds the minute each topic was last spoken, the phase in which the driver dismissed each
    topic, and how many proactive pieces of advice have been given this shift.

    The dismissal window is the rest of the current *phase*: not an arbitrary number of minutes,
    and not the whole shift, which would also silence later safety and lost-bonus warnings.
    """
    last_decided_min: dict[str, float] = field(default_factory=dict)
    dismissed_in_phase: dict[str, str] = field(default_factory=dict)
    proactive_count: int = 0


@dataclass(frozen=True)
class CadenceVerdict:
    verdict: str                      # present, queue or suppress
    reason: str | None = None         # a typed reason code matching the lifecycle schema
    next_eligible_min: float | None = None


def shift_phase(elapsed_min: float, shift_len_min: float,
                cfg: CadenceConfig | None = None) -> str:
    """The shift phase from elapsed time, replacing the application's fixed
    wall-clock hours.

    An empty or unknown shift yields the earliest phase, which avoids both a division by zero and a
    guess."""
    c = cfg or CadenceConfig()
    if shift_len_min <= 0:
        return "early"
    frac = max(0.0, float(elapsed_min)) / float(shift_len_min)
    if frac < c.phase_early_frac:
        return "early"
    if frac < c.phase_late_frac:
        return "mid"
    return "late"


def evaluate(topic: str, now_min: float, phase: str, memory: CadenceMemory,
             cfg: CadenceConfig | None = None, is_driving: bool = False) -> CadenceVerdict:
    """Whether to speak about this topic now. The order of the checks is the
    approved priority order.

    Safety comes before budget and cooldown while the vehicle is stationary. While the driver is
    moving, every text card including safety is queued — deferred, not lost, which is what
    distinguishes it from suppression. A topic dismissed in this phase stays quiet; an exhausted
    shift budget stays quiet; and a topic still inside its cooldown stays quiet, reporting the
    minute at which it next becomes eligible so the caller knows when to ask again.
    """
    c = cfg or CadenceConfig()
    if is_driving:
        return CadenceVerdict(QUEUE, "unsafe_while_moving")
    if topic in SAFETY_TOPICS:
        return CadenceVerdict(PRESENT)
    if memory.dismissed_in_phase.get(topic) == phase:
        return CadenceVerdict(SUPPRESS, "dismissed_for_window")
    if memory.proactive_count >= c.max_proactive_per_shift:
        return CadenceVerdict(SUPPRESS, "shift_budget_exhausted")
    last = memory.last_decided_min.get(topic)
    if last is not None:
        eligible = float(last) + c.effective_gap_min
        if now_min < eligible:
            return CadenceVerdict(SUPPRESS, "topic_cooldown", eligible)
    return CadenceVerdict(PRESENT)


def adherence_coin(seed: int, decision_id: str, material_revision: str) -> float:
    """The compliance draw, a pure function of the seed, the decision id and
    the material revision.

    It replaces a sequential random draw: asking again about one decision must give the same
    answer, which is what kills the re-rolling problem. It hashes rather than using the built-in,
    so the distribution is even and independent of the process hash seed — the same lesson as the
    run-id digest, where string hashing was not deterministic across processes.
    """
    key = f"{seed}\x1f{decision_id}\x1f{material_revision}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2 ** 64
