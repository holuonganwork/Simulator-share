"""The bridge from advice to action.

Without it, "the driver follows the advice" would be words rather than something the simulation
can carry out.

The bridge maps one closed vocabulary onto another; it does no language understanding:

    shift solver  ->  online / rest / swap / end
                             |
                             v  (plus a compliance model)
    actor         ->  wait / rest / go and swap or charge / end shift

The pipeline is deterministic: a solver, with no live model call. The decision content lives in
the solver and the composer only phrases it, so taking the action straight from the solver is
faithful to the real advisor.

Two traps avoided, recorded so that nobody reopens them:

1. The solver's "next action" is not the immediate action. It is defined as the first action in
   the whole schedule that is not "online", which may be hours away. Acting on it makes drivers
   rest hours ahead of the plan. The immediate action is the current bucket's; the next action is
   only for explanation.

2. The advisor must not see the future. The demand forecast is built from the expected field in
   configuration times per-driver noise, which provably does not read the realised trace. It must
   never be built from the rest of the day's actual orders, which would produce a spurious
   positive result in every paired comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

# The simulation's base date. Every timestamp produced here carries a real date, not only a time.
_BASE_DATE = date(2026, 7, 1)
_MIN_PER_DAY = 1440


def _iso(minute: float) -> str:
    """An ISO timestamp from minutes since midnight on the base date, carrying the real date.

    A time defect worth recording: an earlier version wrapped the hour modulo twenty-four, which
    kept the hour and discarded the date, so a bucket past midnight was labelled as the previous
    morning and therefore appeared *earlier* than the current time. The solver groups by
    timestamp and sorts the grouped keys, which sorts ISO strings lexicographically, so the last
    bucket jumped to first position and the whole schedule was assigned the wrong hours. The worst
    consequence was that the immediate action came from a different bucket.

    It was reproducible with demand at midnight, but never occurred in real runs because the
    demand field covers daytime only, so the midnight hour produced no rows at all. It was
    therefore latent rather than live, and published figures were unaffected. It is fixed anyway,
    because adding night-time demand to a config would silently revive it.
    """
    m = int(minute)
    d = _BASE_DATE + timedelta(days=m // _MIN_PER_DAY)
    rem = m % _MIN_PER_DAY
    return f"{d.isoformat()}T{rem // 60:02d}:{rem % 60:02d}:00+07:00"

from gsm_core.lifecycle.cadence import (DECISION_BUCKET_MIN, PRESENT, SUPPRESS,
                                        CadenceConfig, CadenceMemory, adherence_coin,
                                        decision_bucket, evaluate, shift_phase)
from gsm_core.solvers import bonus_feasibility, idle_reduction, shift_dp

from .behavior import IdleAction
from .entities import Actor, FleetType

# Solver actions mapped to actor actions. Swapping splits by fleet: a driver who charges at
# home cannot swap a battery.
_ACTION_MAP = {
    # The online action once mapped to waiting, which was semantically wrong and measurably
    # harmful. The solver's online means "this window should be spent working"; it says nothing
    # about standing still or moving. In the simulation, waiting means standing still for an
    # order and relocating means moving to a busier cell, and both are ways of being online.
    # Mapping online to waiting turned "keep working" into "stand still" and overrode
    # relocation. Measured on one driver-day, trips fell by a fifth, payout by a quarter and
    # idle time rose by nearly half: the advice made the driver poorer through a translation
    # error, not through poor advice.
    #
    # Online therefore means no intervention: the advisor agrees that working is right and
    # leaves the instinct to choose between waiting and relocating. That also matches the
    # product boundary, under which the advisor does not name a cell.
    "ONLINE": None,
    "REST": IdleAction.REST,
    "END": IdleAction.END_SHIFT,
}

# The compliance model is a reasoned assumption with no real figures behind it yet: newcomers
# lack experience and comply most; experienced top earners have their own system and comply
# least; everyone else sits between. It needs calibrating against real adherence data, whether
# from a driver survey or a live experiment.
DEFAULT_ADHERENCE = {
    "P1": 0.55, "P2": 0.50, "P3": 0.30, "P4": 0.75, "P5": 0.30, "P6": 0.50, "P7": 0.50,
}
DEFAULT_ADHERENCE_FALLBACK = 0.50

# The bonus-day boundary. The daily bonus is computed per day, so minutes past this mark earn
# points toward the next day's tier and must not enter the calculation of how far today's tier
# still is.
PHUT_MOT_NGAY = 1440.0


def phut_sinh_diem(policy, t0: float, t1: float) -> float:
    """How many minutes in the interval a trip accepted then would actually earn bonus points.

    The policy accessor is the only source; the list of hours is never copied here, because two
    copies diverge the moment someone changes the config. The hour-of-day convention is taken
    from the place where points are actually credited.
    """
    if t1 <= t0:
        return 0.0
    tong, t = 0.0, t0
    while t < t1:
        bien = min((int(t // 60) + 1) * 60.0, t1)
        if policy.trip_points(int(t // 60) % 24) > 0:
            tong += bien - t
        t = bien
    return tong


def phut_toi_khi_khung_dong(policy, t0: float, tran: float) -> float:
    """Where the last point-earning minute within the allowance falls, measured from the start.

    Used to trim the safety margin: adding a margin is reasonable, but a margin lying past the
    close of the point window repeats the very defect this rail exists to fix, asking the driver
    for minutes we know earn nothing.
    """
    cuoi, t, het = 0.0, t0, t0 + tran
    while t < het:
        bien = min((int(t // 60) + 1) * 60.0, het)
        if policy.trip_points(int(t // 60) % 24) > 0:
            cuoi = bien - t0
        t = bien
    return cuoi


def phut_dong_ho_de_kiem_du(policy, t0: float, can_phut_sinh_diem: float,
                            tran: float) -> float | None:
    """How many clock minutes from the start are needed to accumulate the required number of
    point-earning minutes.

    Absent when the allowance can never be enough.

    When every minute falls inside the window the function returns exactly the requirement, so
    this is a generalisation of the earlier formula rather than a behaviour change, and a test
    pins that to the last decimal.
    """
    if can_phut_sinh_diem <= 0:
        return 0.0
    tich, t, het = 0.0, t0, t0 + tran
    while t < het:
        bien = min((int(t // 60) + 1) * 60.0, het)
        if policy.trip_points(int(t // 60) % 24) > 0:
            con = can_phut_sinh_diem - tich
            if bien - t >= con:
                return (t - t0) + con
            tich += bien - t
        t = bien
    return None


@dataclass(frozen=True)
class BonusGateAdvice:
    """The acceptance-lift channel: a warning that the acceptance rate is below the threshold
    for bonus eligibility.

    Why it is the most valuable channel, measured rather than assumed: the daily bonus pays
    nothing below the acceptance threshold, whatever the driver's points. The pilot config sets
    that threshold above the newcomer archetype's own baseline, so newcomers are excluded from
    the entire daily bonus. Measured, two archetypes received nothing while the rest received
    substantial bonuses.

    The advice is a statement of policy — the driver's rate is below the threshold and is
    therefore costing them the bonus — and it acts at the level of a rate, never on a specific
    order, which the product boundary forbids.
    """
    t_min: float
    acceptance_now: float
    threshold: float
    lift_applied: float
    followed: bool


@dataclass(frozen=True)
class BridgedAdvice:
    """One consultation with the advisor, and its outcome."""
    t_min: float
    solver_action: str            # the action for the current bucket
    mapped_action: IdleAction | None
    followed: bool
    adherence: str                # whether the driver complied
    plan_next_action: str | None  # the solver's next action, for explanation rather than immediate execution
    plan_next_bucket: str | None
    reason: str | None


class AdviceActionBridge:
    """Ask the shift solver what to do now, then decide whether the actor complies.

    It uses its own random stream. Sharing the world's stream would mean that enabling advice
    shifts every actor's draws, so the two arms would differ for reasons unrelated to advice and
    the paired-seed comparison would be worthless.
    """

    def __init__(self, cfg, policy, seed: int, *, checkpoint_trace=None,
                 run_id: str = ""):
        adv = cfg.get("advice", {}) or {}
        # The health policy lock: raise immediately if anyone sweeps or overrides the deferral
        # cap. The choke point is this bridge rather than the single-run entry point, because
        # multi-day runs build worlds directly and multi-day is the only path that populates the
        # planned rest hour. A guard at the single-run entry would be blind on exactly the path
        # that matters.
        from gsm_core.policy_locks import assert_policy_locks
        assert_policy_locks(cfg.get, where="AdviceActionBridge")
        self.enabled = bool(adv.get("enabled", False))
        self.coverage = str(adv.get("coverage", "single"))
        self.single_actor_id = adv.get("single_actor_id")
        self.interval_min = float(adv.get("interval_min", 30))
        self.adherence = {**DEFAULT_ADHERENCE, **(adv.get("adherence_by_archetype") or {})}
        # The shift solver lives in the core layer and uses a different bundle class. The
        # conversion goes through one accessor, shared with the mock generator, so the two
        # layers cannot drift apart on policy.
        from gsm_core.policy import PolicyBundle as CorePolicy
        self.sim_policy = policy
        rec = policy.to_core_record()
        # The validity window is read from config metadata where present. Absent means unknown
        # rather than valid, and the world logs when a policy is used outside its window.
        for k in ("policy_effective_from", "policy_effective_to"):
            v = cfg.get(f"meta.{k}", None)
            if v:
                rec[k.replace("policy_", "")] = str(v)
        self.policy = CorePolicy.from_record(rec)
        self.policy_valid_today = self.policy.is_valid_at(_BASE_DATE.isoformat())
        # This is the positioning planner's run period — how often it assigns in batches — and
        # no longer the grid that identifies decisions. That grid is now a single constant shared
        # by every channel.
        self.bucket_min = int(adv.get("bucket_min", 60))
        # What remains of the old grid is one constraint: the decision bucket must not be wider
        # than the channel's decision cadence, or two different assignments collapse into one id
        # and events are swallowed. A loud guard enforces it, in place of maintaining a third
        # grid.
        if self.bucket_min < DECISION_BUCKET_MIN:
            raise ValueError(
                f"advice.bucket_min={self.bucket_min}′ NGẮN HƠN lưới quyết định "
                f"{DECISION_BUCKET_MIN:.0f}′ ⇒ hai lần gán khác nhau sẽ gộp chung một "
                f"decision_id và event bị nuốt. Hạ lưới quyết định (DECISION_BUCKET_MIN) "
                f"hoặc giữ planner ≥ lưới — không chạy tiếp với mismatch.")
        # This world's own time boundary. The advisor may not plan, or defer a shift, past the
        # moment the world stops.
        self.world_end_min = float(cfg.get("time.end_min", 1440) or 1440)
        # A hypothesis the data refused; the flag is kept so the comparison can be rerun.
        #
        # The hypothesis: a rest recommendation means "do not spend this window earning", and
        # swapping or relocating are already not earning, so overriding them is redundant and
        # harmful. More than half of all interventions were of that kind, and the argument was
        # persuasive.
        #
        # Measured over fifteen seeds, enabling the guard made the advisor *worse*, not better,
        # roughly doubling the average loss with a confidence interval entirely below zero.
        #
        # The reason: heading for a swap costs a trip to the station plus a queue that fails
        # about a tenth of the time, and relocating is an empty run costing a seventh of all
        # time. Those overrides were rescuing drivers from expensive actions.
        #
        # Default off, keeping current behaviour. The flag stays for comparison, not for use.
        self.rest_only_overrides_wait = bool(adv.get("rest_only_overrides_wait", False))
        # The average trip distance of this world, taken from the same source the world
        # generates trips from, rather than a constant in the solver defaults.
        #
        # A correction worth keeping: the key used here once did not exist. Config lookups
        # return their default silently, so this line never read the config at all, and nobody
        # noticed because the default happened to equal the real value. Anyone sweeping trip
        # distance for a sensitivity study would have seen no effect and concluded wrongly. A
        # gate now blocks non-existent keys.
        self.avg_dist_km = float(cfg.get("demand.trip_km_median", 3.5) or 3.5)
        # Cash cost per kilometre, read from the same config key as the world's cost ledger.
        # Zero by default, which matches current policy.
        self.cash_cost_km = float(cfg.get("vehicle.cash_cost_vnd_per_km", 0.0) or 0.0)
        self.swap_fee_solver = float(cfg.get("vehicle.swap_fee_vnd", 0.0) or 0.0)  # C5
        # The population's completion prior is one minus this world's own
        # cancellation-after-acceptance rate. The key here was also once wrong, and also
        # harmless only because the default happened to match.
        self.completion_prior = round(
            1.0 - float(cfg.get("behavior.cancel_after_accept_rate", 0.05) or 0.05), 4)
        # The positioning channel, at three levels:
        #   off               the default; the planner does not run and the trace is identical
        #                     to a config without the flag;
        #   wait only         override only when the instinct is waiting. Conservative, after
        #                     overriding expensive actions was measured to make things worse;
        #   wait and relocate also change the destination of an instinctive relocation.
        self.positioning_overrides = str(adv.get("positioning_overrides", "off") or "off")
        # The positioning channel's trigger. The capacity trigger is the original path,
        # selecting drivers standing in cells with no headroom, unchanged to the character. The
        # wait trigger selects by a cell's median idle streak above a threshold with a minimum
        # count, plus the zone veto. An unknown value raises immediately: failing loudly beats a
        # hidden fallback.
        self.positioning_trigger = str(adv.get("positioning_trigger", "capacity") or "capacity")
        if self.positioning_trigger not in ("capacity", "wait"):
            raise ValueError(f"advice.positioning_trigger={self.positioning_trigger!r} "
                             f"— chỉ nhận capacity|wait")
        # The weight given to tier distance in the allocator's objective. Zero disables it and
        # restores the earlier behaviour through an explicit branch rather than relying on
        # floating-point arithmetic.
        # The upper bound raises rather than clamping silently: a large weight erases the charge
        # priority scale entirely, so an out-of-range value must fail rather than be quietly
        # bounded.
        self.positioning_tier_weight = float(adv.get("positioning_tier_weight", 0.0) or 0.0)
        if not (0.0 <= self.positioning_tier_weight <= 1.0):
            raise ValueError(
                f"advice.positioning_tier_weight={self.positioning_tier_weight!r} — phải trong "
                f"[0,1]. Giá trị lớn xoá ưu tiên PIN; xem lan can SOC_SAN_AN_TOAN_PCT.")
        # Choose the destination cell by value rather than by distance. The nearest mode is
        # the previous behaviour, reached through an explicit branch. An unknown value raises
        # rather than falling back silently: a silent fallback means an experimental arm runs
        # the wrong branch and nobody knows.
        self.positioning_target_mode = str(
            adv.get("positioning_target_mode", "nearest") or "nearest")
        if self.positioning_target_mode not in ("nearest", "value", "share"):
            raise ValueError(f"advice.positioning_target_mode="
                             f"{self.positioning_target_mode!r} — chỉ nhận nearest|value|share")
        self.positioning_km_penalty = float(adv.get("positioning_km_penalty", 0.8) or 0.0)
        if self.positioning_km_penalty < 0.0:
            raise ValueError(f"advice.positioning_km_penalty="
                             f"{self.positioning_km_penalty!r} — âm nghĩa là THƯỞNG cho việc đi "
                             f"xa; nếu muốn tắt phạt thì đặt 0.0")
        # The cost-benefit weight for a repositioning decision, off by default.
        #
        # Off is not general caution but two measured reasons:
        #   - a zero weight is the condition under which the allocator runs bit-identically, so
        #     the fingerprint cannot move and every published comparison keeps its meaning;
        #   - the benefit side rests on the demand rate, which uses a uniform population proxy.
        #     Measured over five seeds and a full day, almost every decision came out "worth
        #     going", with a median benefit-to-cost ratio near eight. That is a statement about
        #     the simulated world, not about the real city.
        #
        # The demo overlay is where it gets switched on.
        self.km_weight = float(adv.get("km_weight", 0.0) or 0.0)
        # A second, separate lever. Separating them was a fix rather than tidying.
        #
        # An earlier version tied both to one weight. Measured over five seeds and a full day,
        # they pull in opposite directions:
        #
        #   - the distance term in the allocator picks a nearer cell whenever a driver must be
        #     pushed off their preferred one, which *reduces* distance;
        #   - the cost-benefit gate, replacing the rail, judges almost everything worth going to
        #     and so removes the fallbacks, which *increases* distance markedly at the tail.
        #
        # One switch over two opposing levers means the stronger one wins and whoever enables it
        # cannot tell which they chose. The complaint that prompted this was that suggestions
        # were too far away, so combining them made exactly that complaint worse.
        self.cost_benefit_vi_tri = bool(adv.get("cost_benefit_vi_tri", False))
        self.positioning_max_extra_km = float(adv.get("positioning_max_extra_km", 2.0) or 0.0)
        if self.positioning_max_extra_km <= 0.0:
            raise ValueError(f"advice.positioning_max_extra_km="
                             f"{self.positioning_max_extra_km!r} — phải > 0. Giá trị 0 loại sạch "
                             f"mọi ô trừ ô gần nhất ⇒ `value` âm thầm thành `nearest`.")
        pw = adv.get("positioning_wait") or {}
        self.positioning_wait_threshold_min = float(pw.get("threshold_min", 30.0))
        self.positioning_wait_min_idle = int(pw.get("min_idle", 2))
        # Builds the absent scenario, where real data carries no per-cell supply. The solver
        # has to work at all three availability levels; with no supply it gives no positioning
        # advice rather than guessing.
        self.market_supply_available = bool(adv.get("market_supply_available", True))
        # each channel switches independently, so value can be attributed to a channel
        ch = adv.get("channels") or {}
        self.ch_shift_plan = bool(ch.get("shift_plan", True))
        self.ch_accept_lift = bool(ch.get("accept_lift", False))
        self.ch_shift_extend = bool(ch.get("shift_extend", False))
        self.ch_rest_window = bool(ch.get("rest_window", False))
        # A sub-channel of the shift plan that speaks only when the schedule says to finish.
        # The solver knows about bonus tiers, so its finish recommendation has already weighed
        # them. This isolates the value of early-finish advice from the full shift plan, which
        # was rejected as harmful. Off by default and enabled only for measurement.
        self.sp_end_only = bool(adv.get("shift_plan_end_only", False))
        # The early-swap channel: move a swap that has to happen anyway into a cheap moment —
        # a long idle stretch with an empty station — rather than a forced one mid-job. It is a
        # time channel, which is why it has any effect in a zero-cost simulation, where every
        # cost channel proved inert.
        self.ch_swap_early = bool(ch.get("swap_early", False))
        self.swap_early_band_pct = float(adv.get("swap_early_band_pct", 15.0))
        self.swap_early_idle_min = float(adv.get("swap_early_idle_min", 10.0))
        # suggest a swap station from live global state; a positioning-family channel
        self.ch_station_choice = bool(ch.get("station_choice", False))
        self.station_choice_min_gain_min = float(adv.get("station_choice_min_gain_min", 3.0))
        self.rest_defer_max_min = float(adv.get("rest_defer_max_min", 120))
        self.lift_step = float(adv.get("accept_lift_step", 0.10))
        self.lift_max = float(adv.get("accept_lift_max", 0.15))
        self.min_offers_before_lift = int(adv.get("min_offers_before_lift", 5))
        self.extend_max_min = float(adv.get("shift_extend_max_min", 60))
        # parameters of the feasibility condition
        self.max_realized_accept = float(adv.get("max_realized_accept", 0.93))
        self.min_online_min_for_rate = float(adv.get("min_online_min_for_rate", 30))
        self.trips_per_hour_est = float(adv.get("trips_per_hour_est", 1.5))
        # record refusals to advise, so that harmful advice avoided can be counted
        self.skipped_advice: list[tuple[float, int, str]] = []
        # Rolling multi-day history, attached after the world is built so the constructor
        # signature is unchanged and the single-day path is untouched. At attachment time the
        # memory holds only completed days, so nothing leaks from the future.
        self.memory: dict | None = None
        self.rng = np.random.default_rng(seed ^ 0xADD1CE)
        self._last_consult: dict[int, float] = {}
        # the speaking cadence follows the same rule the interface uses
        self.seed = int(seed)
        # The shadow trace sink receives data only after an existing solver call. It owns no
        # random stream, triggers no solver, and is off by default.
        self.checkpoint_trace = checkpoint_trace
        self.run_id = run_id
        cad = (adv.get("cadence") or {})
        self.cadence_cfg = CadenceConfig(
            min_gap_min_per_topic=float(cad.get("min_gap_min_per_topic", 20.0)),
            max_proactive_per_shift=int(cad.get("max_proactive_per_shift", 6)))
        self.cadence_enabled = bool(cad.get("enabled", True))
        # Positioning sits outside the budget by default, with reasons rather than for
        # convenience:
        # (a) it is the only channel with a demonstrated significant positive effect, and
        #     constraining it with an untested budget would destroy a measured result;
        # (b) a tighter cadence is an experimental arm, not a silent change to the baseline. To
        #     constrain it, enable this flag and measure.
        self.cadence_counts_positioning = bool(cad.get("count_positioning_in_budget", False))
        self._cadence_mem: dict[int, CadenceMemory] = {}
        self._suppressed_seen: set[tuple] = set()
        # a decision's effect is applied once
        self._effect_applied: set[str] = set()
        self._suppressed_out: list[tuple] = []
        # advice given and not followed: the adherence denominator for two of the channels
        self._spoken_outcome_seen: set[str] = set()
        self._spoken_outcome_out: list[tuple] = []
        self._share = float(adv.get("share", 0.0))
        self._covered_cache: dict[int, bool] = {}

    # ---------- who receives advice ----------

    def covers(self, actor: Actor) -> bool:
        if not self.enabled:
            return False
        hit = self._covered_cache.get(actor.actor_id)
        if hit is not None:
            return hit
        if self.coverage == "all":
            hit = True
        elif self.coverage == "single":
            hit = (self.single_actor_id is not None
                   and int(self.single_actor_id) == actor.actor_id)
        elif self.coverage == "share":
            # deterministic in the actor id, consuming none of the shared stream
            hit = float(np.random.default_rng(actor.actor_id ^ 0x5A4E).random()) < self._share
        else:                       # none, or an unknown value, means nobody
            hit = False
        self._covered_cache[actor.actor_id] = hit
        return hit

    # ---------- cadence and the keyed compliance draw ----------

    def _mem(self, actor_id: int) -> CadenceMemory:
        m = self._cadence_mem.get(actor_id)
        if m is None:
            m = CadenceMemory()
            self._cadence_mem[actor_id] = m
        return m

    def _phase(self, actor: Actor, now_min: float) -> str:
        """This driver's own shift phase, since shifts differ by archetype, in place of wall-clock
        time."""
        return shift_phase(now_min - actor.shift_start_min,
                           actor.shift_end_min - actor.shift_start_min, self.cadence_cfg)

    def cadence_allows(self, actor: Actor, topic: str, now_min: float) -> bool:
        """Consult the shared cadence rule before speaking. A suppression records a typed reason
        once per actor, topic and bucket, so the world can log it without repeating every
        tick."""
        if not self.cadence_enabled:
            return True
        v = evaluate(topic, now_min, self._phase(actor, now_min),
                     self._mem(actor.actor_id), self.cadence_cfg)
        if v.verdict == PRESENT:
            return True
        if v.verdict == SUPPRESS:
            key = (actor.actor_id, topic, v.reason,
                   int(now_min // self.cadence_cfg.min_gap_min_per_topic))
            if key not in self._suppressed_seen:
                self._suppressed_seen.add(key)
                self._suppressed_out.append((now_min, actor.actor_id, topic, v.reason))
        return False

    def cadence_note_spoken(self, actor: Actor, topic: str, now_min: float) -> None:
        """Something was actually said, so the cooldown and the shift budget both advance."""
        if not self.cadence_enabled:
            return
        m = self._mem(actor.actor_id)
        m.last_decided_min[topic] = now_min
        if topic != "positioning" or self.cadence_counts_positioning:
            m.proactive_count += 1

    def _claim_effect(self, actor: Actor, topic: str, now_min: float,
                      ) -> bool:
        """Claim the right to apply a decision's effect, returning true exactly once.

        The key matches the compliance draw's key: one decision means one draw and one
        application. Without it, re-asking means re-applying, and the dose of the intervention
        would depend on how often the advisor was asked — which is the very cadence being
        measured. The invariant is that the number of applications equals the number of decisions
        followed, not the number of events written.
        """
        key = f"{actor.actor_id}-{topic}-{decision_bucket(now_min)}"
        if key in self._effect_applied:
            return False
        self._effect_applied.add(key)
        return True

    # Which channels need this gate and which do not, recorded so nobody "fixes" it wrongly:
    #   acceptance lift  needs it. The lift is a one-off effect, and re-asking must not add it
    #                    again.
    #   shift extension  needs it, for the same reason.
    #   rest window      does not. Its accumulator advances on every deferring tick, and that is
    #                    not a one-off effect applied repeatedly but a real further two minutes
    #                    of deferred rest. Gating it would make the simulation believe a driver
    #                    deferred rest by two minutes for the whole shift.
    #   shift plan and positioning
    #                    do not. They return an action, or assign a cell, for the world to carry
    #                    out, and the world executes one action per tick, so nothing
    #                    accumulates.

    def drain_suppressed(self) -> list[tuple]:
        """The world collects suppressions so it can log them with their typed reason."""
        out, self._suppressed_out = self._suppressed_out, []
        return out

    def note_spoken_outcome(self, actor: Actor, topic: str, now_min: float,
                            material_revision: str, *, followed: bool, reason: str,
                            ) -> None:
        """The advisor spoke and this is the outcome, for branches that do not log events
        themselves.

        Two branches need it, and both belong in the denominator:
          - not followed: the driver heard the advice and did otherwise;
          - followed but infeasible: the driver agreed and the world could not carry it out, for
            instance an extension past the end of the simulated day. That is still a decision
            that was followed, with zero magnitude. Omitting it from the numerator understates
            adherence, measured at about four tenths against a true five.

        A consumer measuring the *size* of an intervention must read the magnitude, not count
        events: these events exist to preserve the denominator, not to assert that something
        happened.

        Two channels previously wrote events only where the driver complied, returning silently
        otherwise, so their adherence denominators contained only compliers and the rate was one
        by construction. Measured over three seeds, one of them reported a perfect rate against a
        true rate of about a third.

        Deduplication uses the compliance draw's own key — same bucket, same material revision —
        because re-asking one decision yields the same draw and must yield one event rather than
        one per tick.
        """
        key = self._outcome_key(actor, topic, now_min, material_revision)
        if key in self._spoken_outcome_seen:
            return
        self._spoken_outcome_seen.add(key)
        self._spoken_outcome_out.append((now_min, actor.actor_id, topic, followed, reason))

    def _outcome_key(self, actor: Actor, topic: str, now_min: float,
                     material_revision: str) -> str:
        """The outcome deduplication key, identical to the compliance draw's key."""
        return (f"{actor.actor_id}-{topic}-{decision_bucket(now_min)}"
                f"-{material_revision}")

    def mark_outcome_logged(self, actor: Actor, topic: str, now_min: float,
                            material_revision: str) -> None:
        """This decision's outcome is logged directly by the world, on the successful branch, so
        re-asks within the same bucket must not produce further outcome events.

        The suite caught this: a decision already applied pushed the shift end up against the
        world's end, so re-asks in the same bucket fell into the infeasible branch and noted an
        infeasible outcome for a decision that had already been applied. An earlier ordering
        happened to suppress it. The reordering was behaviour-neutral but not event-log neutral,
        and the behaviour measurement was blind to that because all its metrics are at decision
        level."""
        self._spoken_outcome_seen.add(
            self._outcome_key(actor, topic, now_min, material_revision))

    def drain_spoken_outcomes(self) -> list[tuple]:
        """The world collects outcomes to log, which is what keeps the adherence denominator right.

        The time in each record is when the advice was spoken, not when it was collected: the
        decision id has to derive from that to match the followed branch of the same decision.
        """
        out, self._spoken_outcome_out = self._spoken_outcome_out, []
        return out

    def coin_follows(self, actor: Actor, topic: str, now_min: float,
                     material_revision: str, bucket_min: float | None = None) -> bool:
        """Whether the driver follows this particular advice: one draw per decision.

        The key is the seed, the actor, the topic, the bucket and the material revision, so
        re-asking one decision gives the same answer. That is what kills the re-rolling problem,
        where drawing afresh every couple of minutes until the answer was "follow" turned a
        nominal adherence of three tenths into an effective one.
        """
        p = float(self.adherence.get(actor.archetype, DEFAULT_ADHERENCE_FALLBACK))
        # The keyed compliance draw is unconditional.
        #
        # An earlier version fell back to an unkeyed draw when the cadence was disabled, meaning
        # "with the flag off, behave as before". That was a serious measurement error: it tied
        # the *draw mechanism* to the same flag as the *speaking cadence*, so the cadence-off arm
        # of every ablation both had no cadence and reverted to re-rolling. Measured, that arm
        # showed adherence well above nominal while the cadence-on arm barely moved, meaning the
        # control arm had noticeably more compliant drivers for reasons unrelated to cadence.
        # Every "price of cadence" difference computed from it was inflated.
        #
        # The keyed draw is simply the correct way to draw, not a feature that accompanies
        # cadence; the cadence flag now controls only the gate.
        #
        # A useful consequence: the shared stream is no longer consumed by compliance draws, so
        # enabling one channel does not shift another channel's coverage stream, and the two arms
        # pair properly.
        #
        # The bucket must match the world's decision id, using the same constant. Otherwise one
        # decision spans two draws and the re-rolling problem returns; a test catches that.
        key = f"{actor.actor_id}-{topic}-{decision_bucket(now_min)}"
        return adherence_coin(self.seed, key, material_revision) < p

    def due(self, actor: Actor, now_min: float) -> bool:
        """Consult only when due. Consulting every tick is both expensive and unrealistic: drivers
        do not open the app twice a minute."""
        last = self._last_consult.get(actor.actor_id)
        return last is None or (now_min - last) >= self.interval_min

    # ---------- build the solver's input, with nothing from the future ----------

    def build_shift_plan_input(self, actor: Actor, now_min: float, demand_hint_fn,
                               horizon_min: float) -> dict:
        """The shift-plan input, built from the actor's current state.

        The demand hint is the actor's own belief, per cell and hour. It never receives the
        world's actual orders.
        """
        b = self.bucket_min
        # Phantom buckets. The world stops at its end time, but the shift-extension channel can
        # push a driver's finish past it. Planning over time the world does not have inflates the
        # remaining-bucket count, which feeds directly into the required-rest calculation, so the
        # advisor forces rest because of an hour that does not exist. Measured, it happened dozens
        # of times in a single run, each time by exactly one bucket.
        horizon = min(float(horizon_min), self.world_end_min)
        starts = [s for s in range(int(now_min) - int(now_min) % b, int(horizon), b)
                  if s + b > now_min]
        forecast = []
        for s in starts:
            hour = (s // 60) % 24          # personal beliefs are looked up by hour of day
            hint = demand_hint_fn(actor, hour) or {}
            for cell, exp in sorted(hint.items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
                forecast.append({
                    "bucket": _iso(s),      # the label carries a real date and does not wrap
                    "cell_cluster": cell,
                    "expected_orders": round(float(exp), 3),
                })
        return {
            # This producer emits the two rest fields, so it declares the version whose shape
            # it produces. The historical producer emits neither and keeps the older version. The
            # two versions coexist and the registry routes per record.
            "schema_version": "1.1.0", "driver_id": f"d-{actor.actor_id}",
            "t_now": _iso(now_min),
            "buckets_remaining": len(starts),
            "soc_pct": round(actor.soc_pct, 1),   # the simulation has battery telemetry, where the real data does not
            "points_now": int(actor.points),
            "demand_forecast": forecast,
            # The solver has to see the rest already taken. Without it the rest requirement is
            # re-applied at every consultation, which measurably inflated total rest and produced
            # repeated advice to rest immediately after resting.
            # The actor's rest total covers both instinctive and advised rest, which also
            # resolves the two-physiologies problem: the solver subtracts exactly the rest the
            # instinct already took.
            "rest_taken_min": round(float(actor.rest_min), 1),
            "shift_elapsed_min": round(max(0.0, now_min - actor.shift_start_min), 1),
            "policy_bundle_version": self.policy.version,
            "view_version": "sim-3", "source": "MOCK",
        }

    # ---------- the solver's real parameters ----------

    def _acc_estimate(self, actor: Actor) -> float:
        """The acceptance rate as of now: past data only, with nothing from the future.

        Without enough of today's data it uses the driver's own rolling multi-day history, and
        failing that the archetype baseline, which stands in for the historical rate a real
        system reads from its own statistics. It never uses the raw property before any offer,
        because that returns one for zero over zero and turns "not yet known" into "perfect".

        The sufficiency check counts decisions, the same quantity it is guarding. Counting offers
        instead would treat a driver blocked by charge on four turns in five as having enough
        data when they had in fact been asked once.
        """
        if actor.orders_decided < self.min_offers_before_lift:
            mem = (self.memory or {}).get(actor.actor_id)
            return float(mem.acceptance_avg if mem is not None and mem.acceptance_avg is not None
                         else actor.accept_base)
        return float(actor.acceptance_rate)

    def _comp_estimate(self, actor: Actor) -> float:
        """The same, for the completion rate.

        Before any trip is accepted the raw property returns one, the same zero-over-zero problem
        as acceptance. It is replaced by the driver's own multi-day history, and failing that a
        population prior of one minus this world's cancellation-after-acceptance rate — the same
        kind of substitute already used for acceptance.

        The bonus threshold itself is deliberately not used as a fallback: it equals the
        threshold exactly, and the eligibility test is inclusive, so it would *pass* the gate.
        That sounds conservative and is in fact permissive.
        """
        if actor.orders_accepted <= 0:
            mem = (self.memory or {}).get(actor.actor_id)
            if mem is not None and mem.completion_avg is not None:
                return float(mem.completion_avg)
            return self.completion_prior
        return float(actor.completion_rate)

    def solver_params(self, actor: Actor) -> dict:
        """The real parameters passed to the shift solver.

        The consultation used to call the solver without parameters, so it fell back to defaults:

        - a bucket length half what the bridge builds, which made the solver believe charge
          lasted twice as long and required half the rest, pushing solutions strongly toward
          staying online. Measured, most drivers' schedules changed;
        - an assumed acceptance probability and average distance, where the defaults' own
          docstring says the caller should supply real figures;
        - no acceptance or completion rate, so the eligibility check reported that it had nothing
          to judge by, and the solver promised bonuses to drivers the policy would not pay.
        """
        return {
            "bucket_min": self.bucket_min,
            "p_accept": self._acc_estimate(actor),
            "avg_dist_km": self.avg_dist_km,
            "acceptance_rate": self._acc_estimate(actor),
            "completion_rate": self._comp_estimate(actor),
            # The same value as the world's cost ledger: one source of truth for both the
            # world and the optimiser. Zero by default, so the baseline is bit-identical.
            # A later step moves the source to the policy bundle's costs, keyed by track and
            # date.
            "cash_cost_vnd_per_km": self.cash_cost_km,
            # the fee for one swap, from the same config key the world uses; zero by default
            "swap_fee_vnd": self.swap_fee_solver,
        }

    # ---------- consulting the advisor ----------

    def consult(self, actor: Actor, now_min: float, demand_hint_fn,
                horizon_min: float) -> BridgedAdvice | None:
        """Return bridged advice when there is something applicable, and nothing otherwise."""
        if not (self.ch_shift_plan and self.covers(actor)) or not self.due(actor, now_min):
            return None
        if not self.cadence_allows(actor, "shift_plan", now_min):
            return None
        self._last_consult[actor.actor_id] = now_min

        spi = self.build_shift_plan_input(actor, now_min, demand_hint_fn, horizon_min)
        if spi["buckets_remaining"] <= 0:
            return None
        report = shift_dp.solve(spi, self.policy, self.solver_params(actor))
        sol = report.get("solution") or {}
        schedule = sol.get("schedule") or []
        if not schedule:
            return None
        # the current bucket, on the same grid the plan input is built with
        b = self.bucket_min
        bucket_end_min = int(now_min) - int(now_min) % b + b
        # the immediate action is the current bucket's, not the next action
        solver_action = str(schedule[0].get("action") or "ONLINE").upper()
        na = sol.get("next_action") or {}

        # In end-only mode, a schedule that does not say to finish means there is nothing to
        # say. This check comes before the compliance draw and before recording that something
        # was spoken, so neither a draw nor a budget slot is spent on advice that does not
        # exist.
        if self.sp_end_only and str(solver_action).upper() != "END":
            return None
        # Capture only after the channel-specific eligibility gate.  Previously an
        # end-only channel persisted ONLINE/REST checkpoints it had explicitly decided
        # not to speak, inflating checkpoint counts with records that had no output path.
        self._capture_checkpoint("S2", actor, now_min, spi, report, "shift_plan",
                                 validity_hints={"bucket_end_min": float(bucket_end_min)})
        mapped = _map_action(solver_action, actor)
        # a material revision means the advice itself changed, which warrants a new draw
        followed = self.coin_follows(actor, "shift_plan", now_min, solver_action)
        self.cadence_note_spoken(actor, "shift_plan", now_min)
        return BridgedAdvice(
            t_min=now_min, solver_action=solver_action,
            mapped_action=mapped if followed else None,
            followed=followed, adherence="follow" if followed else "ignore",
            plan_next_action=na.get("action"), plan_next_bucket=na.get("bucket"),
            reason=na.get("reason"),
        )


    # ---------- compliance for the positioning channel ----------

    def standby_follow_draw(self, actor: Actor, now_min: float = 0.0,
                            target_cell: str = "") -> bool:
        """One compliance draw per standby assignment, taken when the planner assigns rather than
        on each poll.

        Drawing on each poll would mean re-rolling every couple of minutes until the answer was
        "follow". The draw is a keyed hash and consumes nothing from the shared random stream, so
        enabling the channel does not shift any actor's draws.

        A correction worth keeping: an earlier note described a dedicated random stream here.
        That predates the keyed draw; the stream now serves only channel coverage."""
        return self.coin_follows(actor, "positioning", now_min, f"cell{target_cell}")

    # ---------- the early swap, while idle and the station is empty ----------

    def check_swap_early(self, actor: Actor, now_min: float, nearest_queue_len: int,
                         nearest_has_ready: bool, soc_threshold: float) -> tuple[bool, str]:
        """Whether to advise swapping now, with a reason.

        The principle: this advice changes the *timing* of something inevitable. A charge level
        just above the swap threshold means the next swap is certain, and doing it during a long
        idle stretch, when the opportunity cost is near zero, at a station with a short queue and
        a battery ready, so that no queue is created, is cheaper than doing it when the battery
        runs down mid-job with the risk of being stranded or of a failed swap.

        It forecasts nothing: every condition is read from currently observable state.

        Every branch returns a reason, so the world can count the denominator if needed.
        """
        if not (self.ch_swap_early and self.covers(actor)):
            return False, "channel_off"
        if actor.fleet != FleetType.SWAP:
            return False, "not_swap_fleet"    # the hardware cannot be changed
        if actor.soc_pct <= soc_threshold:
            return False, "below_threshold"   # the instinct already goes; no advice needed
        if actor.soc_pct > soc_threshold + self.swap_early_band_pct:
            return False, "soc_high"          # not yet unavoidable; advising would waste a swap
        if actor.idle_streak_min < self.swap_early_idle_min:
            return False, "not_idle_long"     # busy or newly idle: the moment is not cheap
        if nearest_queue_len > 1 or not nearest_has_ready:
            return False, "station_busy"      # the station is not cheap: advising would create the queue
        if not self.cadence_allows(actor, "swap_early", now_min):
            return False, "cadence"
        self.cadence_note_spoken(actor, "swap_early", now_min)
        rev = "swap_now"
        if not self.coin_follows(actor, "swap_early", now_min, rev):
            self.note_spoken_outcome(actor, "swap_early", now_min, rev,
                                     followed=False, reason="not_followed")
            return False, "not_followed"
        return True, rev

    # ---------- suggesting a station from live global state ----------

    @staticmethod
    def station_eta_min(station, now_min: float, travel_min: float,
                        avg_swap_min: float = 1.5) -> float:
        """The total time until a full battery is in hand at one station, from observable state
        only.

        It is the travel time, plus one swap per person already queueing, plus, when no battery
        is ready, the wait for the earliest one to finish charging. Nothing is forecast."""
        wait = station.queue_len * avg_swap_min
        if station.available_full(now_min) <= 0:
            readies = [b.ready_at_min for b in station.batteries]
            if not readies:
                return float("inf")           # a station with no batteries is unusable
            wait += max(0.0, min(readies) - now_min)
        return travel_min + wait

    def pick_station(self, actor: Actor, stations, now_min: float, travel_min_fn,
                     instinct_station) -> tuple[object | None, str]:
        """The station to go to, with a reason; nothing means say nothing.

        The instinct picks the nearest station and steps around a long queue exactly once, blind
        to which batteries are ready and to the real route. The advisor scans every station by
        estimated total time and speaks only when the saving clears a minimum: a trivial
        difference is nothing to say. It is deterministic, with ties broken by station id, and
        takes no random draws; compliance is decided by the keyed draw."""
        if not (self.ch_station_choice and self.covers(actor)):
            return None, "channel_off"
        if instinct_station is None or not stations:
            return None, "no_station"
        best, best_eta = None, float("inf")
        for s in sorted(stations, key=lambda x: x.node_id):
            eta = self.station_eta_min(s, now_min, travel_min_fn(s))
            if eta < best_eta:
                best, best_eta = s, eta
        eta_instinct = self.station_eta_min(instinct_station, now_min,
                                            travel_min_fn(instinct_station))
        if best is None or best.node_id == instinct_station.node_id:
            return None, "instinct_optimal"
        if eta_instinct - best_eta < self.station_choice_min_gain_min:
            return None, "not_material"
        if not self.cadence_allows(actor, "station_choice", now_min):
            return None, "cadence"
        self.cadence_note_spoken(actor, "station_choice", now_min)
        rev = f"st{best.node_id}"
        if not self.coin_follows(actor, "station_choice", now_min, rev):
            self.note_spoken_outcome(actor, "station_choice", now_min, rev,
                                     followed=False, reason="not_followed")
            return None, "not_followed"
        return best, rev

    # ---------- warning that the acceptance rate is below the bonus threshold ----------

    def check_bonus_gate(self, actor: Actor, now_min: float) -> BonusGateAdvice | None:
        """Advise raising the acceptance rate when it is below the bonus threshold.

        Only while it is still recoverable: enough offers have been seen and the shift is not
        over. Nothing is said once the threshold is met, or once the lift has reached its cap.
        """
        if not (self.ch_accept_lift and self.covers(actor)):
            return None
        if now_min >= actor.shift_end_min:
            return None                       # the shift is over; advice is useless
        thr = float(self.policy.bonus_min_acceptance)

        # A measured finding: the acceptance rate accumulates over the whole day, so refusals
        # early in a shift cannot be recovered. A reactive version that only advised after a
        # minimum number of offers moved one driver's rate a few points and still left it below
        # the threshold, so the bonus stayed at zero. The advice has to be preventive, from the
        # start of the shift.
        #
        # Before there is enough of today's data, the rate is estimated from history: the
        # driver's own rolling multi-day record where available, and the archetype's baseline
        # otherwise. Both are past data and leak nothing from the future.
        acc = self._acc_estimate(actor)   # the same source as the solver parameters

        # --- Advise only where the advice actually helps ---
        # A correction worth keeping: an earlier note here claimed a measured cliff effect
        # costing tens of thousands. That claim was withdrawn: remeasured over thirty seeds, the
        # group that never reached the threshold came out *positive*, and the large loss was one
        # atypical seed. The gate built on that reasoning was itself measured to be inert. This
        # comment was a copy of one in the config, and two copies of a withdrawn claim are how a
        # later analysis came to rest on it.
        #
        # The reasoning that still stands: a threshold bonus is all or nothing, so half-measures
        # — raising a rate that will certainly not reach the threshold — amount to saying
        # something unusable.
        ok, why = self._advice_would_help(actor, now_min, thr, acc_est=acc)
        if not ok:
            self.skipped_advice.append((now_min, actor.actor_id, why))
            return None
        if acc >= thr or actor.accept_lift >= self.lift_max:
            return None
        # The cadence is consulted here rather than at the top of the function. At the top,
        # every tick with nothing to say — the threshold already met, or the usefulness check
        # returning no — still recorded a suppression event. Measured, most calls and nearly half
        # the suppression events were phantoms. The consequence was that "which channel is
        # starved of budget", the basis of a diagnosis, was systematically wrong, and the
        # suppression count reported to stakeholders was inflated.
        #
        # Behaviour is unchanged: the cadence evaluation is pure, consumes no draws, and the
        # memory does not change within one call, so the speak-or-not conclusion is the same and
        # only the recording differs.
        if not self.cadence_allows(actor, "accept_lift", now_min):
            return None                       # out of cooldown or budget: stay silent
        # A material revision is the qualitative content of the advice, not the gap figure
        # inching along each tick. The same advice keeps the same compliance draw.
        followed = self.coin_follows(actor, "accept_lift", now_min, "lift")
        self.cadence_note_spoken(actor, "accept_lift", now_min)
        applied = 0.0
        # One decision means one application of its effect. The lift used to be added every
        # time this function ran with compliance true, and because the keyed draw returns the
        # same answer on re-asking, a run without a cooldown applied the same decision twice or
        # more per bucket. The control arm therefore received a stronger dose, saturated the cap
        # sooner, and its difference was no longer "the price of cadence". This is a correctness
        # error rather than only a measurement one: advice followed once raises the rate once.
        if followed and self._claim_effect(actor, "accept_lift", now_min):
            applied = min(self.lift_step, self.lift_max - actor.accept_lift)
            actor.accept_lift += applied
        return BonusGateAdvice(t_min=now_min, acceptance_now=round(acc, 4), threshold=thr,
                               lift_applied=round(applied, 4), followed=followed)

    # ---------- the rest-window channel: concentrate rest into quiet hours ----------

    def build_idle_reduction_input(self, actor: Actor, now_min: float, demand_hint_fn) -> dict:
        """The idle-reduction input, built from idle time accumulated so far.

        Demand per hour is an index normalised against the day's peak, taken from the actor's own
        belief rather than from future orders. The solver treats the lower half of that index as
        off-peak.
        """
        segs, total, longest = [], 0.0, 0.0
        for h, mins in sorted(actor.idle_by_hour.items()):
            segs.append({"hour": int(h), "duration_seconds": float(mins) * 60.0})
            total += float(mins)
            longest = max(longest, float(mins))

        raw = {h: sum((demand_hint_fn(actor, h) or {}).values()) for h in range(24)}
        peak = max(raw.values()) or 1.0
        demand_by_hour = {str(h): round(v / peak, 4) for h, v in raw.items()}

        return {
            "schema_version": "1.0.0", "driver_id": f"d-{actor.actor_id}",
            "t_now": _iso(now_min),
            "session_date": _iso(now_min)[:10],
            "idle_segments": segs,
            "total_idle_min": round(total, 2),
            "longest_idle_min": round(longest, 2),
            "online_hours": round(max(0.0, actor.online_min) / 60.0, 3),
            "demand_by_hour": demand_by_hour,
            "active_reposition": None,      # the simulation does not model mission-driven repositioning
            "view_version": "sim-3", "source": "MOCK",
        }

    def rest_window_hour(self, actor: Actor, now_min: float, demand_hint_fn) -> int | None:
        """The hour the idle-reduction solver recommends concentrating rest into, or nothing.

        It calls the real solver rather than reimplementing the reasoning, which is how the
        two-sources-of-truth problem is avoided.
        """
        if not (self.ch_rest_window and self.covers(actor)):
            return None
        # Prefer the window planned yesterday. It is the only way retrospective advice can act
        # at all: within a day the window it names is always already behind, which made the
        # channel entirely inert. Known a day ahead, the window is still in front.
        if actor.planned_rest_hour is not None:
            return int(actor.planned_rest_hour)
        ii = self.build_idle_reduction_input(actor, now_min, demand_hint_fn)
        rep = idle_reduction.solve(ii)
        sol = rep.get("solution") or {}
        if not sol.get("notable"):
            return None                      # no invented problem when the driver is not waiting much
        w = sol.get("worst_window")
        if w:
            # the solver's window is an hour of the day; it ends at the top of the next hour
            self._capture_checkpoint(
                "S7", actor, now_min, ii, rep, "rest_window",
                validity_hints={"rest_window_end_min": (int(w["hour"]) + 1) * 60.0})
        return int(w["hour"]) if w else None

    def should_defer_rest(self, actor: Actor, now_min: float, hour: int,
                          demand_hint_fn, soc_threshold: float,
                          alt_action_fn=None) -> tuple[bool, str, tuple | None]:
        """Whether to defer rest into a quiet hour, with the reason and an alternative action.

        Deferring is a commitment, not a veto. The veto version turned most rest into empty
        waiting and produced no extra orders; a single assignment of "wait" was the entire
        mechanism. Two consequences:

        - the alternative-action probe supplies something useful to do while waiting for the
          promised hour. Waiting means there is nothing useful, in which case rest is not
          deferred at all: no work is not the same as no deferral. It is checked before the
          cadence and the compliance draw, because advice that does not exist must not be
          recorded as suppressed.
        - when the draw says the driver complies, the commitment is recorded as an absolute
          minute of the day, so it survives shifts crossing midnight, and the deferred total is
          charged once with the real interval. The world forces rest at the next decision point
          inside that hour; if the driver is busy for the whole hour the commitment breaks and
          the right to rest returns, after which no further veto is allowed until rest actually
          happens.

        The rails, in a deliberate order — health outranks the commitment, so a genuinely tired
        driver rests even mid-commitment:
          1. low charge: never defer, since deferring a swap risks being stranded;
          2. genuine fatigue past the threshold: never defer;
          3. the right to rest already returned: never defer until rest happens;
          4. the deferral cap: rest cannot be pushed back indefinitely.
        """
        # The alternative-action probe must be deterministic. It runs before the cadence and
        # the compliance draw, so a probe that drew would shift the treatment arm's stream on
        # every rejected decision while the control arm, exiting earlier, would not. The world
        # now passes a draw-free probe, and the default is to wait when the caller passes none.
        #
        # The advised path no longer draws a movement probability: whether the driver complies
        # is already modelled by the compliance draw, and drawing again counts the same question
        # twice. That probability belongs to the un-advised instinct, not to the advised path.
        alt_action_fn = alt_action_fn or (lambda _a: (IdleAction.WAIT, None))
        if actor.soc_pct <= soc_threshold:
            return False, "soc_low", None
        if actor.online_min > actor.fatigue_threshold_min:
            return False, "fatigued", None
        # With a commitment open the decision is already made: keep avoiding rest through
        # useful work, without re-drawing compliance and without adding to the quota. When there
        # is no useful work left, rest happens early and the world's rest branch clears the
        # commitment.
        if actor.rest_commit_due_min is not None:
            alt = alt_action_fn(actor)
            if alt is None or alt[0] == IdleAction.WAIT:
                return False, "commit_rest_early", None
            return True, "committed", alt
        if actor.rest_commit_broken:
            return False, "commit_broken", None
        if actor.rest_deferred_min >= self.rest_defer_max_min:
            return False, "defer_cap", None
        target = self.rest_window_hour(actor, now_min, demand_hint_fn)
        if target is None or target == hour:
            return False, "no_window" if target is None else "at_window", None
        # defer only if that window is still ahead in the shift
        minutes_to = ((target - hour) % 24) * 60
        if minutes_to <= 0 or now_min + minutes_to > actor.shift_end_min:
            return False, "window_past", None
        if actor.rest_deferred_min + minutes_to > self.rest_defer_max_min:
            return False, "defer_cap", None
        # the fall-through must not be waiting; checked before the cadence and the draw
        alt = alt_action_fn(actor)
        if alt is None or alt[0] == IdleAction.WAIT:
            return False, "no_alt_action", None
        # Consult the cadence only once there is genuinely a window to defer into. The rails
        # above, the two window checks and the alternative-action check are all cases of having
        # nothing to say, and suppressing there suppresses advice that does not exist.
        if not self.cadence_allows(actor, "rest_window", now_min):
            return False, "", None            # the shared cadence decides
        self.cadence_note_spoken(actor, "rest_window", now_min)
        # This was once the only channel that took no compliance draw, which pinned its
        # effective adherence at one: every driver always followed advice to defer rest. It now
        # obeys both the cadence and the draw like every other channel. It is soft advice, so its
        # events stay out of the adherence denominator, but the draw still models whether the
        # driver listens.
        # A material revision is the qualitative content — which hour rest is deferred to — not a
        # figure inching along each tick. A different target hour warrants a new draw; the same
        # hour keeps the same one.
        rev = f"defer_to_{target:02d}h"
        if not self.coin_follows(actor, "rest_window", now_min, rev):
            self.note_spoken_outcome(actor, "rest_window", now_min, rev,
                                     followed=False, reason="not_followed")
            return False, "not_followed", None
        # Book the commitment once, in the right quantity: the start of the target hour as an
        # absolute minute of the day, since the offset is measured between hour boundaries.
        actor.rest_commit_due_min = (now_min - now_min % 60.0) + minutes_to
        # The quota is charged the real deferral, which is the time until the commitment falls
        # due. Charging the whole hour-to-hour offset overstated it by up to an hour each time
        # and exhausted the policy-locked cap prematurely.
        actor.rest_deferred_min += float(max(0.0, minutes_to - now_min % 60.0))
        return True, rev, alt

    # ---------- when raising the acceptance rate is actually feasible ----------

    def _acceptance_recoverable(self, actor: Actor, now_min: float, thr: float) -> bool:
        """Whether the day-cumulative acceptance rate can still be recovered before the shift ends.

        Given the offers seen, those accepted, the offers still expected and the highest rate
        actually achievable at the lift cap, the final rate is the accepted plus the achievable
        share of the remainder, over the total. It is feasible when that clears the threshold.

        The remaining offers are estimated from this driver's own offer rate so far, times the
        time left. Only data up to now is used.

        With no offers yet, nothing has been refused and nothing lost, so it is always feasible —
        which is right, because preventive advice at the start of a shift is the most valuable
        kind.
        """
        o, a = actor.orders_offered, actor.orders_accepted
        if o == 0:
            return True
        remaining_min = max(0.0, actor.shift_end_min - now_min)
        elapsed = max(1.0, actor.online_min)
        R = (o / elapsed) * remaining_min          # the offers still expected
        p = self.max_realized_accept
        if o + R <= 0:
            return False
        return (a + p * R) / (o + R) >= thr

    def build_bonus_gap_input(self, actor: Actor, now_min: float) -> dict:
        """The bonus-gap input, from the actor's current state.

        Historical points per hour is estimated from this driver's own shift so far. Without
        enough history it is left empty so the solver uses its own theoretical fallback, rather
        than inventing a parallel estimate here.
        """
        tiers = [[int(p), int(v)] for p, v in self.policy.day_bonus_tiers
                 if int(p) > int(actor.points)]
        # Prefer the rolling multi-day history, which is what the feasibility solver was
        # designed to receive and the main value of multi-day runs. Without it — the first day,
        # or single-day mode — fall back to the within-day estimate.
        hist = {}
        mem = (self.memory or {}).get(actor.actor_id)
        # Test for presence, not truthiness: a history of zero points per hour is valid data —
        # a driver who historically earned no points, which the solver must see and call
        # infeasible — and not a missing history.
        if mem is not None and mem.points_per_hour_avg is not None:
            # a whole-day history mixes peak and off-peak, so it fills both buckets
            hist = {"peak": mem.points_per_hour_avg, "offpeak": mem.points_per_hour_avg}
        elif actor.online_min >= self.min_online_min_for_rate and actor.points > 0:
            rate = actor.points / (actor.online_min / 60.0)
            hour = int(now_min // 60) % 24
            hist = {("peak" if self.policy.is_peak(hour) else "offpeak"): round(rate, 3)}
        return {
            "schema_version": "1.0.0", "driver_id": f"d-{actor.actor_id}",
            "t_now": _iso(now_min),
            "points_now": int(actor.points),
            "next_tiers": tiers,
            "historical_points_per_hour": hist,
            "hours_budget_remaining": round(max(0.0, actor.shift_end_min - now_min) / 60.0, 3),
            "acceptance_rate": round(actor.acceptance_rate, 4),
            "completion_rate": round(actor.completion_rate, 4),
            "policy_bundle_version": self.policy.version,
            "view_version": "sim-3", "source": "MOCK",
        }

    def _advice_would_help(self, actor: Actor, now_min: float, thr: float,
                           acc_est: float | None = None) -> tuple[bool, str]:
        """Whether advice to raise the acceptance rate would help.

        A clear boundary between the solver and the simulation. The bridge used to copy the
        advisor's reasoning, which is the two-sources-of-truth problem the other layers had to
        undo.

        - The feasibility solver decides: is there a tier left to reach, are there enough hours,
          and are the acceptance *and* completion constraints satisfiable. The bridge must not
          recompute any of that.
        - The simulation adds exactly one thing the solver cannot answer: the acceptance rate
          accumulates over the whole day, so the question is whether it can still be recovered
          from here. The solver's check is static and cannot replace that.
        """
        solver_input = self.build_bonus_gap_input(actor, now_min)
        rep = bonus_feasibility.solve(solver_input, self.policy)
        sol = rep.get("solution") or {}
        # Stay silent only when the top tier is reached *and* the bonus is genuinely safe.
        # At the top tier with a rate below threshold the policy pays nothing, and silence there
        # abandons the driver at the one moment it is still recoverable. Fall through and let
        # the recoverability check decide whether there is time.
        if sol.get("already_maxed") and sol.get("feasible"):
            return False, "already_maxed"        # at the top tier and safe: further advice is redundant

        if not sol.get("feasible"):
            # The solver reports infeasible. This channel can only move the acceptance rate,
            # so it is worth advising only when that is the *sole* binding constraint. If hours
            # or the completion rate also bind, raising acceptance addresses the wrong thing and
            # the driver takes on more cheap trips without earning the bonus.
            # The constraints are read as typed booleans rather than by parsing the solver's
            # prose, because a change of wording would silently render this gate deaf.
            # Fail closed: a missing key is treated as some other constraint binding, and no
            # advice is given.
            cons = sol.get("constraints") or {}
            blocked_elsewhere = (not cons.get("enough_hours", False)
                                 or not cons.get("ok_completion", False))
            acceptance_is_blocker = not cons.get("ok_acceptance", True)
            if blocked_elsewhere or not acceptance_is_blocker:
                return False, "blocked_elsewhere"
        # Comparing the raw acceptance property was wrong: it returns one when no offer has
        # been made yet, so the start of a shift was always mistaken for "threshold already met",
        # killing exactly the preventive advice that is worth most. Use the same estimate the
        # bonus gate chose — history or baseline — rather than the degenerate value.
        elif (acc_est if acc_est is not None
              else actor.acceptance_rate) >= float(self.policy.bonus_min_acceptance):
            return False, "already_qualified"

        if not self._acceptance_recoverable(actor, now_min, thr):
            return False, "acceptance_unrecoverable"
        self._capture_checkpoint("S1", actor, now_min, solver_input, rep, "accept_lift")
        return True, ""

    # ---------- deferring the end of a shift when close to a tier ----------

    def check_shift_extend(self, actor: Actor, now_min: float,
                           soc_threshold: float) -> tuple[float, str]:
        """Return the minutes by which to defer the end of the shift, with a reason; zero means no
        deferral.

        Deferral happens only when the next points tier is close. Otherwise it only tires the
        driver without earning anything, and there is a cap on the total deferral so that it
        cannot become an indefinite extension.

        The three health rails.

        This function previously had none, while the rest-deferral channel — which the policy
        locks classify in the *same family*, that of lengthening working time for money — had
        three. Measured, this one did read the driver's time online, but as the denominator of a
        productivity rate rather than as a fatigue gate, so a tired but productive driver was
        still advised to work longer.

        Why this is a boundary rather than a refinement: the argument that a rest-adherence
        figure will come to be seen as something to improve applies verbatim here, with the sign
        reversed — "improving" this rate means more drivers agreeing to work longer. The
        repository also contradicted itself: the guardrail layer reports a rise in the working
        span as a problem, while the adherence view counted the rate that causes it as an
        achievement.

          1. low charge: below the swap threshold, do not advise more driving. It advises
             something that may not be possible and raises the risk of being stranded.
          2. already fatigued: time online is past the threshold.
          3. would become fatigued: this advice itself would push them past it.

        Why both the second and the third, with separate reasons: deferring rest only moves rest
        in time, while extending a shift adds working hours. For this channel the harm lies in
        the advice *pushing* the driver past the threshold, not only in their already being past
        it. With the second rail alone, the very case most worth blocking slips through: just
        under the threshold now, and well past it after the extension. The two reasons are kept
        apart so the guardrail layer can distinguish "already tired" from "made tired by advice".

        Time online is a *proxy* for fatigue and not real driving minutes: it includes rest. The
        rest-deferral channel uses the same proxy, so keeping them symmetric is right, but these
        rails should not be read as measuring real fatigue. Outstanding: separate driving time
        from time online for both channels at once, since fixing one alone creates a new
        asymmetry.

        Every branch returns a reason, including those that are not rails, because the world uses
        it to count the veto denominator. Reading a raw veto count without its denominator is a
        misreading.
        """
        if not (self.ch_shift_extend and self.covers(actor)):
            return 0.0, "channel_off"
        # --- The first two rails come before everything else, including the cap and the
        # cadence: these are cases of having nothing to say, and suppressing advice that does not
        # exist is wrong.
        if actor.soc_pct <= soc_threshold:
            return 0.0, "soc_low"
        if actor.online_min > actor.fatigue_threshold_min:
            return 0.0, "fatigued"
        if actor.shift_extended_min >= self.extend_max_min:
            return 0.0, "extend_cap"
        gap = self.policy.next_tier_gap(int(actor.points))
        if not gap:
            return 0.0, "no_tier"             # already at the top tier: nothing to reach for
        gap_points = int(gap[0])
        # points per hour estimated from this driver alone, with no view of the future
        online_h = max(0.5, actor.online_min / 60.0)
        rate = actor.points / online_h
        if rate <= 0:
            return 0.0, "no_rate"
        # The requirement is measured in *point-earning* minutes, not clock minutes. Treating
        # every minute alike was wrong, because trips earn no points outside the point window, so
        # "another N minutes reaches the tier" was factually false in more than a third of cases.
        # The driver still decides, but was deciding on a false premise we supplied: a truthfulness
        # error rather than an optimisation one.
        need_min = gap_points / rate * 60.0
        # --- Compare against the time left in the shift. Without that comparison the channel
        # extended shifts even when the tier was reachable within the shift, lengthening the
        # working day for no extra bonus, and granted the full requirement when only part of it
        # was needed. A tier within reach of the shift means there is nothing to say.
        # The comparison is against *point-earning* minutes remaining, not clock minutes: a shift
        # ending late may have an hour and a half on the clock but only ten earning minutes, and
        # the clock version would promise the tier while the driver ends far short.
        remaining_min = max(0.0, actor.shift_end_min - now_min)
        earn_remaining = phut_sinh_diem(self.policy, now_min, actor.shift_end_min)
        if need_min <= earn_remaining:
            return 0.0, "reachable_in_shift"
        extend_left = self.extend_max_min - actor.shift_extended_min
        # --- A truthfulness rail: is this tier reachable at all today?
        #
        # The horizon is the moment the point window closes within the day, not the extension cap
        # and not "the next twenty-four hours". Bonus tiers reset daily, so a minute after
        # midnight earns points toward tomorrow's tier, and counting it here would invent a
        # source of points that does not exist. Running to the end of the day is general enough:
        # once the window closes no minute earns, so the result equals "run to the close" while
        # also holding when the window is not contiguous.
        #
        # Deliberately before the fatigue rail, which does not contradict the "health before
        # economic cap" principle below: this belongs to the other family — nothing to say at all
        # — rather than to the family of harmful advice. A health rail exists to block harmful
        # advice; where the channel is already silent there is nothing to block, and counting it
        # would inflate the health ledger by claiming credit for rescuing someone the channel
        # never spoke to.
        #
        # The horizon must not be the remaining extension allowance. An earlier version used it
        # and broke the very principle the block below protects: a case that deserved a fatigue
        # verdict was downgraded to a cap verdict because the fatigue projection was clamped.
        need_extra_min = phut_dong_ho_de_kiem_du(
            self.policy, actor.shift_end_min, need_min - earn_remaining,
            max(0.0, PHUT_MOT_NGAY - actor.shift_end_min))
        if need_extra_min is None:
            return 0.0, "outside_point_window"   # the window closes before the tier is reachable: stay silent
        # --- The fatigue rail, measured against the projected end of the *extended* shift:
        # time online, plus what remains of the shift, plus the intended extension. Online time
        # accrues by the clock, so by the time an extension takes effect it already includes the
        # rest of the shift. Comparing time online plus the requirement at the moment of advice
        # both omitted the shift still to run and used the requirement in place of the real
        # extension.
        # Deliberately before the cap check: when advice would both exceed the economic cap and
        # push the driver past the fatigue threshold, the reason reported must be the health one.
        # Otherwise the veto ledger reports "out of allowance" for exactly the cases where the
        # health rail is what stopped it.
        # It uses the pre-cap extension, since the cap only shrinks it and an over-cap case is
        # caught immediately below.
        if (actor.online_min + remaining_min + need_extra_min * 1.15
                > actor.fatigue_threshold_min):
            return 0.0, "would_exceed_fatigue"
        if need_extra_min > extend_left:
            return 0.0, "cap_unreachable"     # the extra needed is beyond the permitted cap
        if not self.cadence_allows(actor, "shift_extend", now_min):
            return 0.0, "cadence"             # out of cooldown or budget: stay silent
        rule_input = {
            "driver_id": f"d-{actor.actor_id}", "t_now": _iso(now_min),
            "points_now": int(actor.points), "gap_points": gap_points,
            # The requirement is in point-earning minutes while the extension is in clock
            # minutes. The two differ once the window reaches its edge, so the remaining earning
            # minutes travel alongside and a reader can see the discrepancy rather than having to
            # derive it.
            "points_per_hour": rate, "need_earning_min": need_min,
            # two fields: what remains of the shift, and the extra needed beyond it
            "shift_remaining_min": remaining_min,
            "shift_earning_remaining_min": earn_remaining,
            "need_extra_min": need_extra_min,
            "extend_remaining_min": extend_left,
        }
        rule_report = {
            "status": "optimal", "confidence": 1.0,
            "reason_code": "bonus_tier_within_extension_cap",
            "solution": {
                "action": "EXTEND",
                "action_window": {
                    "start": _iso(now_min),
                    "end": _iso(min(self.world_end_min,
                                    actor.shift_end_min + need_extra_min * 1.15)),
                },
            },
        }
        self._capture_checkpoint(
            "RULE", actor, now_min, rule_input, rule_report, "shift_extend")
        # Recording that something was spoken must happen unconditionally: the budget is the
        # listener's attention, and the advisor speaking spends it whether or not the driver
        # complies. Recording it after the compliance draw meant ignored advice cost nothing, so
        # this channel could be re-asked every bucket without limit while another channel could
        # not. The two then used different units of "spoken", and every per-channel budget figure
        # was skewed.
        self.cadence_note_spoken(actor, "shift_extend", now_min)
        if not self.coin_follows(actor, "shift_extend", now_min, "extend"):
            # Returning silently left no trace, so this channel's events existed only where
            # the advice was followed and the adherence denominator lost the non-compliant half
            # entirely, reporting a perfect rate against a real one of about half. Note that the
            # per-decision and per-consultation rates are different units and must not be
            # mixed.
            self.note_spoken_outcome(actor, "shift_extend", now_min, "extend",
                                     followed=False, reason="not_followed")
            return 0.0, "not_followed"
        # Grant only the extra needed beyond the shift, not the whole requirement: the part
        # inside the shift will elapse anyway, and granting it all lengthens the working day for
        # nothing.
        # The safety margin must not push the tail past the close of the point window. Without
        # trimming, a shift ending near the close asks for a minute we know earns nothing — the
        # same defect this rail exists to fix, only smaller. The trim never falls below the
        # requirement, because the requirement ends inside an earning block within the
        # allowance.
        add = min(need_extra_min * 1.15, extend_left,
                  phut_toi_khi_khung_dong(self.policy, actor.shift_end_min, extend_left))
        # Never defer past the moment the world stops. Extending a shift beyond the end of the
        # simulated day is advice that cannot be carried out: it produces no extra trips while
        # still spending the extension budget and still writing an event, so a paired comparison
        # reads an intervention where nothing happened. Measured, it was nearly a fifth of all
        # deferrals. Clamped here rather than at each consumer, so no consumer has to remember.
        add = min(add, max(0.0, self.world_end_min - actor.shift_end_min))
        if add <= 0.0:
            # The driver agreed but the world could not carry it out. That is still a decision
            # that was followed, with zero magnitude, so it belongs in both the numerator and the
            # denominator. Returning silently dropped a fifth of the numerator and understated
            # adherence. Clamping the magnitude here is right; clamping away the measurement
            # trace as well would be another denominator error.
            self.note_spoken_outcome(actor, "shift_extend", now_min, "extend",
                                     followed=True, reason="infeasible_world_end")
            return 0.0, "infeasible_world_end"
        # The one-application claim must come after the feasibility clamp. Before it, advice
        # that could not be carried out still burnt the token, and with the token gone every
        # re-ask within the same bucket returned nothing, so the decision disappeared entirely —
        # more than a quarter of followed decisions were lost that way. The rule is unchanged:
        # one decision, one application; the token is spent when the effect is applied, not when
        # the advice is spoken.
        if not self._claim_effect(actor, "shift_extend", now_min):
            return 0.0, "claimed"             # one decision, one extension
        # The world logs this decision's outcome directly, with its magnitude. Marking it stops
        # re-asks in the same bucket from producing phantom infeasible events once the shift end
        # has already reached the cap.
        self.mark_outcome_logged(actor, "shift_extend", now_min, "extend")
        actor.shift_extended_min += add
        actor.shift_end_min += add
        return add, ""

    def _capture_checkpoint(self, solver_name: str, actor: Actor, now_min: float,
                            solver_input: dict, solver_report: dict,
                            channel: str,
                            validity_hints: dict[str, float] | None = None) -> str | None:
        if self.checkpoint_trace is None:
            return None
        source_decision_id = (
            f"slth-{self.run_id}-{actor.actor_id}-{channel}-{decision_bucket(now_min)}")
        # Real freshness is this channel's own consultation period rather than a fixed offset
        # invented in the trace. The solver-specific boundary is declared at the call site.
        hints = {"freshness_deadline_min": now_min + self.interval_min}
        hints.update(validity_hints or {})
        return self.checkpoint_trace.capture(
            solver_name, actor, now_min, solver_input, solver_report,
            source_decision_id, validity_hints=hints)


def _map_action(solver_action: str, actor: Actor) -> IdleAction | None:
    if solver_action == "SWAP":
        return IdleAction.GO_SWAP if actor.fleet == FleetType.SWAP else IdleAction.GO_CHARGE
    return _ACTION_MAP.get(solver_action)
