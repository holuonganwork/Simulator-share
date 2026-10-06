"""The instinct behaviour model: what a driver does with no advice.

Deterministic given the random stream passed in. It decides whether to accept an order, what to
do when idle, and which station to swap at.

The model approximates a utility model closely enough for actors to behave plausibly — working
the peaks, resting around midday, swapping when charge runs low, and finishing near their
habitual hour. Parameters are set during calibration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .entities import Actor, ActorState, FleetType, Station
from .geo import Grid, cell_distance_km


class IdleAction(str, Enum):
    WAIT = "wait"
    RELOCATE = "relocate"
    GO_SWAP = "go_swap"
    GO_CHARGE = "go_charge"
    REST = "rest"
    END_SHIFT = "end_shift"


def _logistic(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


# The weight of economics in the accept decision. Deliberately below one so that the
# archetype's own acceptance baseline stays dominant: in practice drivers refuse because a trip
# is far, because they are tired, or because the shift is nearly over, not purely on the money
# in one trip. An assumption, calibrated by a gate over at least thirty seeds.
ECON_WEIGHT = 0.7


@dataclass(frozen=True)
class AcceptDecision:
    """An accept decision carrying enough grounds to explain it afterwards.

    Returning only a boolean left the decline event unable to say why the driver refused, which
    is exactly the question the journey view has to answer for every offer.
    """
    accepted: bool
    p_accept: float       # the acceptance probability at that moment
    net_vnd: float        # gross less the cost of the pickup leg
    z: float              # net against the market median, standardised and clamped
    reason: str           # forced, base_behavior or economics


def decide_accept(actor: Actor, gross_vnd: int, pickup_dist_km: float, forced: bool, rng,
                  pickup_disutility_vnd_per_km: float = 3000.0, logit_center_vnd: float = 6000.0,
                  logit_scale_vnd: float = 8000.0) -> AcceptDecision:
    """The accept decision with its grounds. This is where the real model lives.

    The model. An earlier form added a raw economic term to the logit of the baseline, and that
    term dominated: an ordinary trip scored well over one logit unit, so an archetype with a
    baseline of 0.80 actually accepted 94% and one at 0.98 accepted 99.5%. The archetype had
    become almost meaningless, with fleet acceptance at 96% against a real range of 0.74 to 0.97.

    In the current form the baseline *is* the archetype's mean acceptance and economics only
    modulate around it: the logit of the baseline plus a small weight times a standardised net,
    clamped to two standard deviations either way. When net is near the centre the driver
    accepts at exactly the baseline, and only far or cheap trips are refused more often. The
    centre therefore means the market's median net, not a break-even point, and must be set from
    the real trip distribution.

    Exactly one random draw, as before. Consuming more or fewer would shift the stream, changing
    results and breaking the common random numbers that paired worlds depend on.

    The reason recorded on a refusal:
      - economics: the trip is below the market median and the probability fell accordingly, so
        the driver judged it far or cheap. This is where advice has room to act.
      - base_behavior: the trip was ordinary or good, and the refusal came from the archetype's
        own baseline — tiredness, the end of a shift, a choosy temperament. Not about money.
    """
    if forced:
        return AcceptDecision(True, 1.0, float(gross_vnd), 0.0, "forced")
    # This parameter is perceived disutility — the effort, time and risk of the pickup leg —
    # and not a cash cost. Real cash costs are more than an order of magnitude smaller. Two
    # concepts, two names: the money ledger lives on the actor, not here. The value and the
    # behaviour are unchanged from before the rename, bit for bit.
    net = gross_vnd - pickup_dist_km * pickup_disutility_vnd_per_km
    z = (net - logit_center_vnd) / max(1e-6, logit_scale_vnd)
    z = max(-2.0, min(2.0, z))
    # The effective baseline includes any capped lift from advice. With no advice the lift is
    # zero and the value is identical to before, so the control arm is unchanged.
    base = min(0.999, max(0.001, actor.effective_accept_base))
    x = math.log(base / (1 - base)) + ECON_WEIGHT * z
    p = _logistic(x)
    accepted = rng.random() < p          # exactly one draw
    reason = "accepted" if accepted else ("economics" if z < 0 else "base_behavior")
    return AcceptDecision(accepted, p, float(net), float(z), reason)


def accept_order(actor: Actor, gross_vnd: int, pickup_dist_km: float, forced: bool, rng,
                 pickup_disutility_vnd_per_km: float = 3000.0, logit_center_vnd: float = 6000.0,
                 logit_scale_vnd: float = 8000.0) -> bool:
    """The accept decision as a plain boolean; forcing always accepts.

    The signature is kept for existing callers and tests. The real model lives in
    `decide_accept`, whose docstring explains why the baseline is the mean and economics only
    modulate around it.
    """
    return decide_accept(actor, gross_vnd, pickup_dist_km, forced, rng,
                         pickup_disutility_vnd_per_km, logit_center_vnd, logit_scale_vnd).accepted


def soc_range_km(actor: Actor, cfg_vehicle: dict) -> float:
    """Remaining range in kilometres from state of charge: the single source of this formula.

    The pilot config once hard-coded two range constants that no code read and that differed
    from the effective values here by several percent. Both keys were removed and the config
    comment now points at this function; change the formula and that comment must follow.

    The function currently has no caller, because the swap decision uses the charge percentage
    directly. It is kept because it is the only place that states the relation between charge
    and distance, and a config gate stops derived numbers from creeping back into configuration.
    """
    if actor.fleet == FleetType.SWAP:
        return actor.soc_pct / max(1e-6, float(cfg_vehicle["swap_consume_pct_per_km"]))
    return actor.soc_pct / max(1e-6, float(cfg_vehicle["charge_consume_pct_per_km"]))


def choose_idle_action(
    actor: Actor,
    now_min: float,
    grid: Grid,
    cfg_vehicle: dict,
    hour: int,
    demand_hint: dict[str, float] | None,
    rng,
    cfg_behavior: dict | None = None,
) -> tuple[IdleAction, str | None]:
    """Choose what to do while idle, returning the action and a target cell when relocating.

    The demand hint is the driver's own expected orders per cell; absent, it is treated as
    uniform.
    """
    swap_threshold = float(cfg_vehicle["swap_soc_threshold_pct"])

    # 1. Low charge: go and swap or charge. Highest priority.
    if actor.soc_pct <= swap_threshold:
        return (IdleAction.GO_SWAP if actor.fleet == FleetType.SWAP else IdleAction.GO_CHARGE, None)

    # 2. Past the habitual finish time: end the shift.
    if now_min >= actor.shift_end_min:
        return (IdleAction.END_SHIFT, None)

    # 3. The habitual meal hour, after driving long enough: rest. Once a day.
    cfg_behavior = cfg_behavior or {}
    fatigue = actor.online_min / max(1.0, actor.fatigue_threshold_min)
    if (hour == actor.meal_hour and actor.meals_taken == 0
            and fatigue > 0.35 and rng.random() < 0.5):
        actor.meals_taken += 1
        return (IdleAction.REST, None)

    # 4. High fatigue: a short rest, with probability rising in fatigue.
    if fatigue > 1.0 and rng.random() < 0.3:
        return (IdleAction.REST, None)

    # 5. Consider relocating. Extracted into its own function because it doubles as the
    # behaviour tree with rest suppressed: the fall-through branch of a deferred rest calls it.
    return consider_relocate(actor, grid, hour, demand_hint, rng, cfg_behavior)


def consider_relocate(
    actor: Actor,
    grid: Grid,
    hour: int,
    demand_hint: dict[str, float] | None,
    rng,
    cfg_behavior: dict | None = None,
) -> tuple[IdleAction, str | None]:
    """Step five of the idle tree: relocate to a neighbouring cell with clearly higher expected
    orders, otherwise wait.

    A pure extraction from the idle chooser: the order of random draws is preserved draw for
    draw, because the five-seed behaviour-neutral fingerprint gate must come out identical.

    It doubles as the "useful alternative action" probe used by the deferred-rest path. Steps one
    and two — charge and end of shift — have already been checked by the time rest is chosen, so
    the only real alternative is relocating. Returning wait means there is nothing useful to do,
    and the caller must not defer: no work is not the same as no deferral.

    Impatience. The demand hint is a prior cached per actor and hour, never updated from
    experience. If the current cell is a local maximum then the best cell stays the current one
    for the whole hour, and the driver sits still for hours with no orders — four hours, measured.
    Real people do not do that: a long idle spell is evidence of being in the wrong place, so
    they travel further and grow less choosy.

    This is instinct, not advice: it widens the radius and lowers the bar, and supplies no
    information about demand. The advisor's value remains *informed* positioning.
    """
    best_cell, p_move = best_relocate_target(actor, grid, demand_hint, cfg_behavior)
    if best_cell is not None and rng.random() < p_move:
        return (IdleAction.RELOCATE, best_cell)
    return (IdleAction.WAIT, None)


def best_relocate_target(
    actor: Actor,
    grid: Grid,
    demand_hint: dict[str, float] | None,
    cfg_behavior: dict | None = None,
) -> tuple[str | None, float]:
    """Phase one, deterministic: whether any cell is worth moving to, and with what probability.
    Consumes no random draws.

    Returns the best cell, or nothing when there is no useful action.

    Why it is separate. The deferred-rest gate uses the relocation logic as a probe: it only
    needs to know whether there is useful work. The combined function drew a random number while
    answering, and that gate runs before the cadence check, so every *rejected* decision still
    advanced the treatment arm's random stream while the control arm, exiting earlier, did not.

    Measured over three days and two seeds with adherence at zero, so that every decision was
    rejected: the treatment arm drew twenty-one more times than the control, and the actor
    fingerprints diverged.

    It reproduces only in multi-day runs, because on a single day the gate never reaches that
    call: the planned rest hour is only populated from the second day onwards.

    Mixing "is there work" with "do we go" in one function was the whole bug. Separated, the
    probe cannot draw by accident: it does not take a generator at all, and a test pins that
    through the signature.
    """
    cfg_behavior = cfg_behavior or {}
    ring, bar, p_move, give_up = 1, 1.25, 0.5, False
    if bool(cfg_behavior.get("idle_impatience_enabled", True)):
        step = float(cfg_behavior.get("idle_impatience_step_min", 30.0))
        n = int(actor.idle_streak_min // step) if step > 0 else 0
        n = min(n, int(cfg_behavior.get("idle_impatience_max_steps", 2)))
        ring += n                                   # widening the search ring as impatience grows
        bar -= 0.10 * n                             # the bar falls: less choosy
        p_move = min(0.95, p_move + 0.20 * n)       # and the move cost matters less
        give_up = n >= int(cfg_behavior.get("idle_impatience_max_steps", 2))

    if demand_hint is not None:
        here = demand_hint.get(actor.cell, 0.0)
        best_cell, best_val = actor.cell, here
        # The bar is anchored to the current cell and does not move during the loop. Comparing
        # against a running best instead meant each later candidate had to beat the previous
        # winner by the full margin again, making the choice a greedy chain that depended on
        # traversal order — and that order is the sorted cell index, which is arbitrary
        # geographically. The visible symptom was a clearly better neighbouring cell that was
        # never suggested.
        nguong = here * bar          # move only on a clear improvement; the bar falls with impatience
        for nb in _neighbors(actor.cell, grid, ring):
            v = demand_hint.get(nb, 0.0)
            # less the cost of moving
            v_adj = v - 0.15 * cell_distance_km(grid, actor.cell, nb)
            if v_adj > nguong and v_adj > best_val:
                best_cell, best_val = nb, v_adj
        # At maximum impatience — idle for the full span with nobody offering work — the driver
        # stops believing their own prior. Reality has refuted it: an hour here and no orders. A
        # real person tries somewhere else at that point, including somewhere they believe is
        # worse.
        # No threshold can express this, because under that prior the current cell is still the
        # local maximum. The comparison has to be dropped, not lowered.
        if best_cell == actor.cell and give_up:
            nbs = _neighbors(actor.cell, grid, ring)
            if nbs:
                best_cell = max(nbs, key=lambda c: (demand_hint.get(c, 0.0), c))
        if best_cell != actor.cell:
            return (best_cell, p_move)

    return (None, p_move)


def _neighbors(cell: str, grid: Grid, ring: int = 1) -> list[str]:
    """Neighbouring cells within the given ring, restricted to the core. Sorted for
    determinism."""
    from .geo import grid_disk

    return sorted(c for c in grid_disk(cell, ring) if c != cell and grid.is_core(c))


def choose_station(actor: Actor, grid: Grid, stations: list[Station], now_min: float, rng) -> Station | None:
    """Choose a swap station: the nearest by cell distance, stepping once to the next nearest if
    the closest has a long queue.

    A correction worth keeping: an earlier docstring described a habitual-station rule with a
    probability attached. No such logic exists in the body, and the function draws no random
    numbers at all — the generator is accepted for call-site compatibility and never used.

    The consequence matters: calling this does not shift the random stream, so the early-swap
    channel is safer for paired runs than a comment elsewhere assumed. Nothing about the
    behaviour changed here; only the documentation, which was wrong inside the source of truth
    and led later readers to reason from it.
    """
    if not stations:
        return None
    ranked = sorted(stations, key=lambda s: cell_distance_km(grid, actor.cell, s.cell))
    nearest = ranked[0]
    if nearest.queue_len > 3 and len(ranked) > 1:
        return ranked[1]
    return nearest
