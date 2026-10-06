"""Parallel worlds: how much the advice helps, with a confidence interval.

The question is what happens when a driver works alone against when they follow the advice,
measured on that same driver.

Why paired differences rather than two independent groups: both arms run on the same seed and so
share the same orders, the same weather and the same congestion. The remaining difference is
therefore largely due to the advice rather than to today being busier. Comparing independently
would drown the effect in good-day, bad-day variance.

The statistics are therefore computed on the per-seed difference, averaged with a bootstrap
interval.

Reporting principle: an interval containing zero is a *result*, not a failure. Parameters are
never tuned to make a difference look better. If the advice does not help, that is what gets
said, and then the reason gets investigated.
"""

from __future__ import annotations

import copy
import statistics as st
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .journey import build_journey
from .metrics import summarize
from .runner import run_once

# Prebuilt channel sets for the ladder measurement, which is how value is attributed to a
# channel.
#
# The ladder must own the positioning override key as well as the real channels. Otherwise the
# "none" rung would silently inherit positioning from the product default and the ladder would
# lose its meaning: "none" has to be genuinely no intervention, invariant to whatever the product
# default happens to be.
CHANNEL_LADDER = {
    "s2_only": {"shift_plan": True, "accept_lift": False, "shift_extend": False,
                "positioning_overrides": "off"},
    "rest_window": {"shift_plan": True, "accept_lift": False, "shift_extend": False,
                    "rest_window": True, "positioning_overrides": "off"},
    "accept_lift": {"shift_plan": True, "accept_lift": True, "shift_extend": False,
                    "positioning_overrides": "off"},
    "positioning": {"shift_plan": False, "accept_lift": False, "shift_extend": False,
                    "rest_window": False, "positioning_overrides": "wait_only"},   # = B3w
    "all": {"shift_plan": True, "accept_lift": True, "shift_extend": True, "rest_window": True,
            "positioning_overrides": "wait_only"},
    "none": {"shift_plan": False, "accept_lift": False, "shift_extend": False,
             "rest_window": False, "positioning_overrides": "off"},
}


@dataclass
class PairResult:
    seed: int
    actor_id: int
    a: dict          # the target driver's metrics on the control arm
    b: dict          # and on the treatment arm
    system_a: dict   # system metrics for the guardrails
    system_b: dict
    # Adherence must appear in every paired result. The rule that every arm reports it and that
    # a deviation from nominal suspends the result existed only on paper: the paired pipeline
    # referenced adherence exactly zero times and no artifact carried the key. That is why one
    # channel reported perfect adherence against a real rate of about half across dozens of
    # artifacts with no gate firing.
    #
    # Both arms are recorded, the control included: a control arm has to be measured rather than
    # assumed clean.
    adherence_a: dict = field(default_factory=dict)
    adherence_b: dict = field(default_factory=dict)


def _cfg_with(cfg: Config, *, enabled: bool, actor_id: int | None,
              channels: dict | None, coverage: str = "single") -> Config:
    """A deep copy of the config with the advice flags applied.

    The copy is required: modifying in place would leave both arms sharing one dictionary, so the
    control arm would be overwritten by the treatment arm and every comparison would be
    meaningless.
    """
    c = Config(copy.deepcopy(cfg.data), cfg.root_dir)
    adv = c.data.setdefault("advice", {})
    adv["enabled"] = enabled
    # Coverage is no longer forced to a single driver. System guardrails measured on one driver
    # are meaningless: by design, one driver's effect on the market is near zero. Full coverage is
    # the mode to use when assessing systemic harm.
    adv["coverage"] = coverage
    adv["single_actor_id"] = actor_id if coverage == "single" else None
    if channels is not None:
        ch = dict(channels)
        # a pseudo-channel; separated from the real channel dictionary
        pos = ch.pop("positioning_overrides", None)
        adv["channels"] = ch
        if pos is not None:
            adv["positioning_overrides"] = pos
    return c


def pick_target(result, archetype: str = "P4") -> int:
    """The target driver: the one of the given archetype offered the most orders on the control
    arm.

    This is a biased diagnostic. Selecting the maximum picks the luckiest driver in the control
    world, so any perturbation at all — enabling any advice channel — regresses them toward the
    mean and makes the difference negative *whatever the intervention contains*. Demonstrated by a
    sign flip over five seeds with one intervention: selecting on the control arm gave a large
    negative, selecting on the treatment arm a large positive, and an unselected archetype mean a
    small positive. A whole series of conclusions that the advisor made drivers poorer was
    contaminated by this.

    Use it only to inspect one individual's journey. It must never be the primary criterion; that
    reads unselected cohort means. A test guards the warning label.

    The newcomer archetype is the requested baseline, and is not yet a real newcomer profile.
    """
    cands = [a for a in result.actors if a.archetype == archetype] or list(result.actors)
    return max(cands, key=lambda a: a.orders_offered).actor_id


def _cohort_metrics(result) -> dict:
    """The unbiased individual criterion: means over every driver, split by archetype.

    Nothing is selected on any statistic of either arm, so there is no regression to the mean.
    It lives inside the system dictionary so that the result and comparator shapes are unchanged.

    Net figures are added alongside the payout ones rather than replacing them. With costs at
    their default of zero, net equals payout until real costs are enabled."""
    by_arch_pay: dict[str, list[float]] = {}
    by_arch_net: dict[str, list[float]] = {}
    pays, trips, nets, costs = [], [], [], []
    for a in result.actors:
        net = float(a.payout_vnd) - float(a.cost_vnd)
        by_arch_pay.setdefault(a.archetype, []).append(float(a.payout_vnd))
        by_arch_net.setdefault(a.archetype, []).append(net)
        pays.append(float(a.payout_vnd))
        trips.append(float(a.trips_done))
        nets.append(net)
        costs.append(float(a.cost_vnd))
    out = {"payout_mean_all": round(st.mean(pays), 2) if pays else 0.0,
           "trips_mean_all": round(st.mean(trips), 3) if trips else 0.0,
           "net_mean_all": round(st.mean(nets), 2) if nets else 0.0,
           "cost_mean_all": round(st.mean(costs), 2) if costs else 0.0}
    for arch in sorted(by_arch_pay):
        out[f"payout_mean_{arch}"] = round(st.mean(by_arch_pay[arch]), 2)
        out[f"net_mean_{arch}"] = round(st.mean(by_arch_net[arch]), 2)
    # Battery metrics are reported per fleet, for two measured reasons. Fleet is entirely
    # confounded with archetype, so comparing the two fleets by a pooled mean compares personas
    # rather than battery technology. And charging time is strongly bimodal — a swap takes a
    # minute or two while charging at home takes hours — so a pooled mean is meaningless and
    # downtime is reported as percentiles per fleet.
    #
    # The keys carry their own prefix so as not to collide with the existing per-archetype
    # namespace.
    by_fleet: dict[str, dict[str, list[float]]] = {}
    for a in result.actors:
        f = by_fleet.setdefault(getattr(a.fleet, "value", str(a.fleet)),
                                {"pay": [], "net": [], "charge": []})
        f["pay"].append(float(a.payout_vnd))
        f["net"].append(float(a.payout_vnd) - float(a.cost_vnd))
        f["charge"].append(float(getattr(a, "charge_min", 0.0) or 0.0))
    for fl in sorted(by_fleet):
        v = by_fleet[fl]
        out[f"payout_mean_F_{fl}"] = round(st.mean(v["pay"]), 2)
        out[f"net_mean_F_{fl}"] = round(st.mean(v["net"]), 2)
        out[f"charge_min_p50_F_{fl}"] = round(float(np.percentile(v["charge"], 50)), 1)
        out[f"charge_min_p90_F_{fl}"] = round(float(np.percentile(v["charge"], 90)), 1)
    return out


def _driver_metrics(result, actor_id: int) -> dict:
    m = build_journey(result, actor_id).metrics
    return {k: m[k] for k in (
        "payout_vnd", "trip_payout_vnd", "day_bonus_vnd", "trips_completed",
        "acceptance_rate", "completion_rate", "utilization", "idle_min", "online_min",
        "offers", "declined", "points",
    )}


def _system_metrics(result, exclude_actor: int, health_actor_ids: set[int] | None = None) -> dict:
    """The guardrail: advice must not improve one person by making the system worse.

    Expanded from five fields to the four layers of the two-sided criterion — system, passenger,
    fairness and concentration. The five-field version had no inequality measure, no total payout
    and so no way to tell value created from value redistributed, no expired orders and no
    concentration index; the criterion could not even be stated.

    A caution when reading the baseline measured with this set: at thirty seeds every system-layer
    interval contains zero, and the statistically meaningful movement is at the individual layer.
    """
    from .sim_metrics import health_guardrail, system_guardrail
    s = summarize(result)
    others = [a for a in result.actors if a.actor_id != exclude_actor]
    swap_waits = [e.detail.get("wait_min", 0.0) for e in result.events
                  if e.kind == "swap_done" and "wait_min" in e.detail]
    g = system_guardrail(result)
    return {
        # an unbiased cohort estimator, replacing the earlier argmax
        **_cohort_metrics(result),
        "served_rate": s["served_rate"],
        "orders_completed": s["orders_completed"],
        "others_payout_vnd": sum(a.payout_vnd for a in others),
        "others_trips": sum(a.trips_done for a in others),
        "swap_wait_mean": round(st.mean(swap_waits), 3) if swap_waits else 0.0,
        # --- The four guardrail layers ---
        "total_payout_vnd": g["total_payout_vnd"],      # value created, or merely redistributed?
        "expired_n": g["expired_n"],                     # passengers left unserved
        "wait_median_min": g["wait_median_min"],         # passenger waiting
        "gini_payout": g["gini_payout"],                 # fairness
        "station_hhi": g["station_hhi"],                 # station concentration
        "supply_cell_hhi": g["supply_cell_hhi"],         # area concentration
        "starved_hours_n": g["starved_hours_n"],         # supply-starved hours
        # --- The health layer ---
        # This is the data source for the health aggregator, and it was once not wired at all:
        # the aggregator existed and the per-run function existed, but neither arm's system
        # metrics carried any of its keys, so every ladder run returned a suspended verdict
        # flagged as missing data. Measured, the data was always available and only this merge
        # was absent — the third instance of the same pattern within two days.
        #
        # The actor set narrows the health figures to those the intervention reached; absent, it
        # covers the whole cohort. The comparator routes these keys through the one-way branch, so
        # they never receive a two-sided significance verdict.
        **health_guardrail(result, actor_ids=health_actor_ids),
    }


def run_pair(cfg: Config, seed: int, channels: dict | None = None,
             actor_id: int | None = None, archetype: str = "P4",
             coverage: str = "single") -> PairResult:
    """Run the control and treatment worlds on the same seed.

    Full coverage advises the whole fleet and is the required mode when assessing systemic
    effects. Single coverage remains for attribution studies on one driver.
    """
    ra = run_once(_cfg_with(cfg, enabled=False, actor_id=None, channels=None), seed)
    aid = actor_id if actor_id is not None else pick_target(ra, archetype)
    rb = run_once(_cfg_with(cfg, enabled=True, actor_id=aid, channels=channels,
                            coverage=coverage), seed)
    from .sim_metrics import adherence_audit, touched_actors
    # The touched set comes from the treatment arm, since the control arm has no advice events
    # and would always be empty, and is applied to both. Because the arms are paired, the same
    # actor ids exist on each side, so the health layer compares the same group of people.
    touched = touched_actors(rb) or None
    return PairResult(seed=seed, actor_id=aid,
                      adherence_a=adherence_audit(ra), adherence_b=adherence_audit(rb),
                      a=_driver_metrics(ra, aid), b=_driver_metrics(rb, aid),
                      system_a=_system_metrics(ra, aid, health_actor_ids=touched),
                      system_b=_system_metrics(rb, aid, health_actor_ids=touched))


def _mean_dicts(rows: list[dict]) -> dict:
    """Average metric dictionaries key by key; a non-numeric key takes the first value.

    Used to collapse many days into one row per seed. Why collapse at all: the preregistration
    fixes that the bootstrap resamples by *seed* rather than by day, because days are not
    independent — the same actors carry memory across them. The bootstrap resamples the elements
    of the list it is given, so ensuring one element per seed makes it correct by construction,
    with no change to the bootstrap itself.
    """
    if not rows:
        return {}
    out: dict = {}
    for k in rows[0]:
        vals = [r[k] for r in rows if k in r]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        out[k] = round(st.mean(nums), 4) if len(nums) == len(vals) and nums else vals[0]
    return out


def _merge_adherence(rows: list[dict]) -> dict:
    """Merge several days' adherence audits into one, summing counts and concatenating flags.

    Summed rather than averaged: decisions and compliances are counts, and the statistical gate is
    designed for totals. Averaging would shrink the denominator by the number of days and strip
    the gate of its power.
    """
    by_ch: dict[str, dict] = {}
    by_arche: dict[str, dict] = {}
    flags: list[str] = []
    for r in rows:
        for ch, v in (r.get("by_channel") or {}).items():
            row = by_ch.setdefault(ch, {"decided": 0, "followed": 0, "dismissed": 0,
                                        "suppressed": 0, "event_decided": 0,
                                        "event_followed": 0})
            for k in row:
                row[k] += int(v.get(k) or 0)
        for key, v in (r.get("by_channel_archetype") or {}).items():
            row = by_arche.setdefault(key, {"decided": 0, "followed": 0})
            row["decided"] += int(v.get("decided") or 0)
            row["followed"] += int(v.get("followed") or 0)
        for f in r.get("flags") or []:
            if f not in flags:
                flags.append(f)
    return {"by_channel": by_ch, "by_channel_archetype": by_arche, "flags": flags}


def run_pair_multiday(cfg: Config, seed: int, *, days: int, channels_a: dict,
                      channels_b: dict, metric_days: list[int],
                      coverage: str = "all", archetype: str = "P4") -> PairResult:
    """A multi-day paired run, returning one result per seed.

    Why the single-day runner cannot be used: the intervention being measured only comes alive
    from the second day, because the planned rest hour is populated from the previous day's
    history. Measured over three days, the first produced no decisions at all. A one-day
    measurement would be measuring an inert channel.

    Three constraints from the preregistration are enforced here:

    1. the first day is excluded from the metrics. Without a driver memory the planned rest hour
       is absent, so including that day dilutes the very thing being measured;
    2. one result per seed, so the bootstrap resamples by seed;
    3. the channel set is declared explicitly for *both* arms.

    On that third point: the config helper writes the channel keys only when a set is supplied, so
    passing nothing for the control arm makes it inherit the product default silently. That would
    have been *accidentally* right, since the default matched what the preregistration wanted, but
    right for the wrong reason: the day someone changed the config, the control arm would change
    with it and no measurement would know. Requiring both puts each arm's baseline in the artifact
    where it can be read back.

    The prebuilt ladder entry must not be used here: it also enables the shift-plan channel, which
    was disabled as harmful, so measuring on it would mix two interventions.
    """
    from .multiday import run_multiday
    from .sim_metrics import adherence_audit, touched_actors

    if not metric_days:
        raise ValueError("`metric_days` rỗng — không có ngày nào để đo")
    if max(metric_days) >= days:
        raise ValueError(f"`metric_days`={metric_days} vượt quá `days`={days}")

    md_a = run_multiday(_cfg_with(cfg, enabled=True, actor_id=None,
                                  channels=channels_a, coverage=coverage), seed, days=days)
    md_b = run_multiday(_cfg_with(cfg, enabled=True, actor_id=None,
                                  channels=channels_b, coverage=coverage), seed, days=days)

    aid = pick_target(md_a.days[0], archetype)
    # The touched set comes from the treatment arm and is applied to both, unioned across the
    # measured days. The health layer must score on that set rather than on the whole cohort: a
    # channel reaching a tenth of drivers dilutes its effect by an order of magnitude on the
    # whole, below seed noise.
    touched: set[int] = set()
    for d in metric_days:
        touched |= touched_actors(md_b.days[d])
    touched = touched or None

    return PairResult(
        seed=seed, actor_id=aid,
        adherence_a=_merge_adherence([adherence_audit(md_a.days[d]) for d in metric_days]),
        adherence_b=_merge_adherence([adherence_audit(md_b.days[d]) for d in metric_days]),
        a=_mean_dicts([_driver_metrics(md_a.days[d], aid) for d in metric_days]),
        b=_mean_dicts([_driver_metrics(md_b.days[d], aid) for d in metric_days]),
        system_a=_mean_dicts([_system_metrics(md_a.days[d], aid, health_actor_ids=touched)
                              for d in metric_days]),
        system_b=_mean_dicts([_system_metrics(md_b.days[d], aid, health_actor_ids=touched)
                              for d in metric_days]))


def assert_crn(cfg: Config, seed: int, actor_id: int) -> bool:
    """The pairing must hold: one seed must give both arms the same list of orders.

    If the exogenous side diverges, every difference is worthless, so this is checked before any
    figure is trusted.
    """
    ra = run_once(_cfg_with(cfg, enabled=False, actor_id=None, channels=None), seed)
    rb = run_once(_cfg_with(cfg, enabled=True, actor_id=actor_id,
                            channels=CHANNEL_LADDER["all"]), seed)
    ka = [(o.order_id, round(o.t_min, 6), o.pickup_cell, o.gross_vnd) for o in ra.orders]
    kb = [(o.order_id, round(o.t_min, 6), o.pickup_cell, o.gross_vnd) for o in rb.orders]
    return ka == kb


def bootstrap_ci(diffs: list[float], n_boot: int = 5000, alpha: float = 0.05,
                 seed: int = 12345) -> tuple[float, float]:
    """A percentile bootstrap interval for the mean paired difference.

    A bootstrap rather than a t test, because the difference distribution is skewed and
    heavy-tailed; normality is not a safe assumption at a few dozen seeds.
    """
    if not diffs:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    arr = np.asarray(diffs, dtype=float)
    means = arr[rng.integers(0, len(arr), size=(n_boot, len(arr)))].mean(axis=1)
    return (float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


# Bootstrap intervals degenerate at small sample sizes: with one seed the interval has zero
# width and every nonzero difference reads as significant, and with two it is a coin flip. A
# distributional conclusion requires at least thirty seeds.
MIN_SEEDS_FOR_SIGNIFICANCE = 30

# That threshold is calibrated for advice against no advice. Comparing two *variants* of advice
# is a different problem needing far more seeds: a measured variant difference of a few thousand
# came with a per-seed standard deviation several times larger, implying a sample size in the
# hundreds.
#
# Recorded so that nobody repeats the mistake of running thirty seeds twice against two versions
# of the code and concluding the newer one is worse. Comparing variants requires pairing — the
# same control arm per seed against two treatment arms — and bootstrapping the difference of the
# differences.
MIN_SEEDS_FOR_VARIANT_COMPARISON = 100


# The health layer is a one-way gate: it reports deterioration only and deliberately offers no
# direction to be praised in, because optimising for a high veto count means driving people into
# fatigue or empty batteries in order to pass. Putting these keys into the two-sided significance
# table printed "more vetoes" as a system effect, which reads as advice improving the system —
# exactly the direction the layer exists to prevent. They travel through the health flags rather
# than through the significance helper.
HEALTH_KEYS_ONE_WAY = frozenset({
    "rest_min_total", "veto_calls_n", "veto_fired_n",
    "veto_soc_low_n", "veto_fatigued_n", "veto_defer_cap_n",
    "work_span_p50", "work_span_p90", "work_span_max",
    "drive_min_p50", "drive_min_p90", "drive_min_max",
})

# The explicit list above leaked twice: two key families were wired into the health layer after
# the set was frozen, fell through into the two-sided table, and were printed as system effects
# when vetoes rose. Matching by prefix stops future health keys repeating that; the explicit set
# remains for keys with no shared prefix.
_ONE_WAY_PREFIXES = ("veto_", "xveto_", "commit_")


def _is_one_way(key: str) -> bool:
    return key in HEALTH_KEYS_ONE_WAY or key.startswith(_ONE_WAY_PREFIXES)

# Denominators, not results. Kept apart from the one-way keys for a different reason: those are
# blocked so that rising vetoes do not read as an improvement, while these are blocked because
# significance on a denominator is semantically meaningless — its difference is always zero, both
# arms using the same set of actors, and "not significant" would be actively misleading.
SCOPE_KEYS = frozenset({"n_actors_scope"})


def _sig(lo: float, hi: float, n: int, min_seeds: int = MIN_SEEDS_FOR_SIGNIFICANCE) -> bool:
    """Significant when the interval excludes zero *and* there are enough seeds. The default
    minimum is the advice-against-no-advice standard.

    Comparing two *variants* of advice is a different problem needing several times as many seeds.
    This helper used to hard-code the lower figure, so every variant contrast passing through the
    comparator was marked significant below its own standard. Callers now pass the higher minimum
    for variant contrasts.
    """
    return bool(n >= min_seeds and (lo > 0 or hi < 0))


def compare(pairs: list[PairResult], min_seeds: int | None = None) -> dict:
    """The summary: paired differences per metric, bootstrap intervals, and system guardrails.

    Significance is only ever set once the seed count clears the minimum; below it the flag stays
    false and an insufficiency flag is set, so a consumer cannot read noise as an effect."""
    n = len(pairs)
    ms = min_seeds if min_seeds is not None else MIN_SEEDS_FOR_SIGNIFICANCE
    # The insufficiency flag compares against the *effective* threshold rather than a constant.
    # With a caller raising the minimum for a variant comparison, a fixed constant would report a
    # sample as sufficient while every significance verdict was being withheld at the higher bar:
    # two flags contradicting each other.
    out: dict = {"n_seeds": n, "n_insufficient": n < ms,
                 "driver": {}, "system": {}, "min_seeds_for_sig": ms}
    if not pairs:
        return out
    # The key set is the union across all pairs rather than the first pair's: a key absent from
    # the first seed would be dropped silently, and one absent from a later seed would raise. A
    # pair missing a key is skipped for that key and the pair count is declared explicitly, rather
    # than coercing a silent zero — zero is a value, and absent is absent.

    def _rows(get_a, get_b, keys, digits):
        rows = {}
        for key in sorted(keys):          # the union is a set; sorting keeps artifacts and command-line output stable under diff
            sub = [p for p in pairs if key in get_a(p) and key in get_b(p)]
            if not sub:
                continue
            va = [float(get_a(p)[key] or 0) for p in sub]
            vb = [float(get_b(p)[key] or 0) for p in sub]
            diffs = [b - a for a, b in zip(va, vb)]
            lo, hi = bootstrap_ci(diffs)
            row = {
                "mean_a": round(st.mean(va), digits),
                "mean_b": round(st.mean(vb), digits),
                "delta_mean": round(st.mean(diffs), digits),
                "ci95": (round(lo, digits), round(hi, digits)),
                "n_positive": sum(1 for d in diffs if d > 0),
                "_n_sub": len(sub), "_lo_hi": (lo, hi),
            }
            if len(sub) < n:
                row["n_pairs"] = len(sub)
            rows[key] = row
        return rows

    for key, row in _rows(lambda p: p.a, lambda p: p.b,
                          {k for p in pairs for k in p.a}, 2).items():
        n_sub, (lo, hi) = row.pop("_n_sub"), row.pop("_lo_hi")
        row["significant"] = _sig(lo, hi, n_sub, ms)
        out["driver"][key] = row
    for key, row in _rows(lambda p: p.system_a, lambda p: p.system_b,
                          {k for p in pairs for k in p.system_a}, 4).items():
        n_sub, (lo, hi) = row.pop("_n_sub"), row.pop("_lo_hi")
        if key in SCOPE_KEYS:
            row.pop("n_positive", None)
            row["role"] = "MẪU SỐ — số tài xế trong phạm vi chấm tầng 5, KHÔNG phải kết quả"
        elif _is_one_way(key):
            # The health layer has no two-sided significance; its gate is the one-way flag
            # function, which reports deterioration only. Assigning significance here would print
            # rising vetoes as a system improvement. Matching is by prefix, because two key
            # families previously slipped through an explicit list.
            row["one_way_gate"] = "sim_metrics.health_guardrail_flags (D-M3-05)"
        else:
            row["significant"] = _sig(lo, hi, n_sub, ms)
        out["system"][key] = row
    return out


def run_ladder(cfg: Config, seeds: list[int], archetype: str = "P4",
               steps: tuple[str, ...] = ("s2_only", "accept_lift", "all"),
               coverage: str = "single") -> dict:
    """The ladder measurement: one rung per channel set, so value can be attributed.

    The same target driver is used at every rung, so the rungs are comparable.

    Full coverage closes a gap an earlier study recorded about itself: it had never separated the
    channels' contributions fleet-wide, only on a single driver.

    Performance: the control arm runs once per seed and is reused at every rung, since it does not
    depend on the channel configuration. Rerunning it per rung would double the cost for an
    identical result.
    """
    from .sim_metrics import adherence_audit, touched_actors
    cfg_a = _cfg_with(cfg, enabled=False, actor_id=None, channels=None)
    ra0 = run_once(cfg_a, seeds[0])
    aid = pick_target(ra0, archetype)

    cache_a = {seeds[0]: ra0}
    out: dict = {}
    for name in steps:
        pairs = []
        for s in seeds:
            ra = cache_a.get(s) or run_once(cfg_a, s)
            cache_a[s] = ra
            rb = run_once(_cfg_with(cfg, enabled=True, actor_id=aid,
                                    channels=CHANNEL_LADDER[name], coverage=coverage), s)
            touched = touched_actors(rb) or None      # see the note in the pair runner
            pairs.append(PairResult(
                seed=s, actor_id=aid,
                a=_driver_metrics(ra, aid), b=_driver_metrics(rb, aid),
                system_a=_system_metrics(ra, aid, health_actor_ids=touched),
                system_b=_system_metrics(rb, aid, health_actor_ids=touched),
                adherence_a=adherence_audit(ra), adherence_b=adherence_audit(rb)))
        out[name] = compare(pairs)
        out[name]["adherence"] = aggregate_adherence(pairs, nominal=nominal_adherence(cfg))
        out[name]["health_guardrail"] = aggregate_health_guardrail(pairs)
    return out


def aggregate_health_guardrail(pairs: list[PairResult]) -> dict:
    """The health layer aggregated across seeds: a mean per key on each arm, with the one-way
    gate applied to those means.

    Without this step the layer would exist only on paper — the function present in the metrics
    module while no paired artifact carried it. A test guards that.
    """
    from .sim_metrics import health_guardrail_flags

    # A correction worth keeping: this list was once copied by hand and covered only one key
    # family. Measured, nine keys were pushed *out* of the two-sided table by the prefix rule and
    # into a gate that did not inspect them, and they reached no aggregate either — while the
    # artifact printed a claim beside them that they were guarded by that gate. A governance
    # declaration that was not true.
    #
    # It was the third instance of one mistake: the note above already recorded that the explicit
    # list had leaked twice, and that fix wired only the way *out* of the two-sided table,
    # forgetting the way *in* to the gate.
    #
    # It is now derived from the rail constants themselves, so adding a rail adds it here
    # automatically rather than by anyone remembering.
    from .sim_metrics import COMMIT_KEYS, EXTEND_RAILS, REST_RAILS

    keys = ("rest_min_total",
            "veto_calls_n", "veto_fired_n", *(f"veto_{r}_n" for r in REST_RAILS),
            "xveto_calls_n", "xveto_fired_n", *(f"xveto_{r}_n" for r in EXTEND_RAILS),
            *COMMIT_KEYS,
            "work_span_p50", "work_span_p90", "work_span_max",
            "drive_min_p50", "drive_min_p90", "drive_min_max",
            # The denominator has to appear in the artifact: the same passing verdict means
            # something entirely different scored over the whole fleet than over a tenth of it,
            # where the effect is diluted below noise. Without this key a reader cannot tell the
            # two cases apart.
            "n_actors_scope")

    def _mean(side: str) -> dict:
        rows = [getattr(pr, side) for pr in pairs]
        rows = [r for r in rows if r and "rest_min_total" in r]
        if not rows:
            return {}
        return {k: round(float(st.mean([float(r.get(k) or 0) for r in rows])), 2)
                for k in keys}

    a, b = _mean("system_a"), _mean("system_b")
    flags = health_guardrail_flags(a, b) if a and b else         ["tầng 5 THIẾU DỮ LIỆU (system_a/b không mang khoá sức khoẻ) — không được coi là sạch"]
    return {"a_mean": a, "b_mean": b, "flags": flags,
            "verdict": "TREO — sức khoẻ suy giảm" if flags else "OK"}


def nominal_adherence(cfg: Config) -> dict[str, float]:
    """This run's nominal adherence: exactly the numbers the bridge uses as its draw
    probabilities.

    A review caught the statistical gate using a hard-coded default while the bridge merged an
    override from the config — two sources of truth that happened to agree that day. That config
    key exists in order to be changed, for sensitivity sweeps and calibration; once changed, the
    gate would test against the old null, fire constantly, and be switched off. A test's null must
    be the parameters of the run being tested.
    """
    from .advice_bridge import DEFAULT_ADHERENCE
    adv = cfg.data.get("advice") or {}
    return {**DEFAULT_ADHERENCE, **(adv.get("adherence_by_archetype") or {})}


def aggregate_adherence(pairs: list[PairResult], nominal: dict[str, float] | None = None,
                        *, control_clean_channels: tuple[str, ...] | None = None,
                        gate_both_arms: bool = False) -> dict:
    """Pool the treatment arm's adherence across seeds and apply the impossibility gate.

    Why pooled rather than per seed: the original rule's tolerance is a tolerance for the total.
    At a few hundred decisions per seed the sampling error alone exceeds it, so a per-seed gate
    would fire constantly on noise and whoever investigated would widen the tolerance rather than
    fix the fault.

    The impossibility gate *can* be applied per seed, because it is not a statistical test: an
    adherence of exactly one or exactly zero over a reasonable denominator indicates a missing
    numerator or denominator.

    The nominal rates are the run's own; absent, they fall back to the defaults, which is only
    correct for a run that overrides nothing.

    Narrowing the control-arm cleanliness check. By default the control arm must have no decisions
    on *any* channel, which is right when advice is disabled there, as on every current path.

    Supplying a channel tuple requires it to be clean only on those channels. That is needed by a
    design whose control arm has positioning enabled and therefore always has positioning
    decisions. Under the default the gate would suspend every seed and the measurement could never
    run.

    This changes the meaning of a gate fixed in a preregistration, and was recorded there before a
    single seed was run. Done after seeing the numbers it would be indistinguishable from widening
    a gate for a nicer verdict. The reasoning: the cleanliness gate exists to keep the control arm
    free of *the intervention being measured*, and a baseline present identically on both arms
    cancels in the difference.

    Gating both arms. By default the statistical gate inspects the treatment arm only, as before.
    Enabled, it inspects both, which is what the rule to suspend on either arm requires.
    """
    from .advice_bridge import DEFAULT_ADHERENCE
    from .sim_metrics import adherence_stat_flags

    if nominal is None:
        nominal = DEFAULT_ADHERENCE

    tot: dict[str, dict] = {}
    tot_arche: dict[str, dict] = {}
    flags: list[str] = []
    for pr in pairs:
        for ch, r in (pr.adherence_b.get("by_channel") or {}).items():
            # Dismissals and suppressions were once omitted and so never reached the artifact,
            # which lost the distinction between a driver refusing and the cadence blocking the
            # advisor — two different outcomes. A violation by omission rather than by
            # miscalculation.
            row = tot.setdefault(ch, {"decided": 0, "followed": 0, "dismissed": 0,
                                      "suppressed": 0,
                                      "event_decided": 0, "event_followed": 0})
            for k in row:
                row[k] += int(r.get(k) or 0)
        for key, r in (pr.adherence_b.get("by_channel_archetype") or {}).items():
            row = tot_arche.setdefault(key, {"decided": 0, "followed": 0})
            row["decided"] += int(r.get("decided") or 0)
            row["followed"] += int(r.get("followed") or 0)
        for f in pr.adherence_b.get("flags") or []:
            msg = f"seed {pr.seed}: {f}"
            if msg not in flags:
                flags.append(msg)
    # A real gate for control-arm contamination. The control arm's adherence field existed with
    # a comment saying the control arm must be measured rather than assumed clean, and no gate
    # read it — a mechanism living in a comment and a field with no execution path. With advice
    # disabled, no channel may have decisions at all; if one does, the control arm is contaminated
    # and every difference is worthless.
    for pr in pairs:
        # A defensive lookup, because some test stubs carry only the treatment arm's adherence;
        # they exercise the statistical gate rather than this one. A real paired result always has
        # both, so the production path cannot slip through. The trade is stated: a stub without
        # the control arm's adherence is not checked by this gate.
        adh_a = getattr(pr, "adherence_a", None) or {}
        dirty = {ch: int(r.get("decided") or 0)
                 for ch, r in (adh_a.get("by_channel") or {}).items()
                 if int(r.get("decided") or 0) > 0
                 and (control_clean_channels is None or ch in control_clean_channels)}
        if dirty:
            pham_vi = ("mọi kênh" if control_clean_channels is None
                       else f"kênh đang đo {list(control_clean_channels)}")
            msg = (f"seed {pr.seed}: arm ĐỐI CHỨNG có quyết định {dirty} ({pham_vi}) — "
                   f"arm A KHÔNG SẠCH, mọi Δ của seed này là rác (DET-01)")
            if msg not in flags:
                flags.append(msg)

    # A gate on the gate: "clean" is not the same as "invisible".
    #
    # The contamination check reads the control arm's per-channel table. But a *soft* channel
    # deliberately stays out of the adherence denominator, so it never appears there — not even on
    # the treatment arm where it genuinely fires. Measured, the channel fired several times while
    # the table listed only one other channel, so the check found nothing and always passed.
    #
    # And the contamination it exists to catch was real in that very measurement: the treatment
    # arm drew more times than the control and the fingerprints diverged, while the gate reported
    # success. A gate that cannot fail is worse than no gate, because it manufactures confidence.
    #
    # So a channel that was asked for but does not appear on the arm that has it means the audit
    # is blind, and the verdict is suspended with that reason, sending the reader to find another
    # way to measure rather than trusting this one.
    if control_clean_channels:
        thay_o_B = {ch for pr in pairs
                    for ch in ((getattr(pr, "adherence_b", None) or {}).get("by_channel") or {})}
        mu = [ch for ch in control_clean_channels if ch not in thay_o_B]
        if mu:
            flags.append(
                f"cổng đối chứng MÙ: kênh {mu} không quan sát được trong `by_channel` của arm B "
                f"⇒ `dirty` luôn rỗng ⇒ STOP-B không thể fail. KHÔNG đọc thành 'arm A sạch'. "
                f"Kênh MỀM không vào mẫu số adherence — phải đếm bằng đường khác "
                f"(vd hook `should_defer_rest`, xem `research/audit/.../cycle10-*.py`).")

    for r in tot.values():
        r["decision_adherence"] = (r["followed"] / r["decided"]) if r["decided"] else None
        r["event_adherence"] = ((r["event_followed"] / r["event_decided"])
                                if r["event_decided"] else None)
    # The statistical gate applies across the pooled seeds, which is what its threshold was
    # derived for; per seed the standard error is far too coarse.
    for f in adherence_stat_flags(tot_arche, nominal):
        if f not in flags:
            flags.append(f)
    # The rule is to suspend on *either* arm. An earlier version pooled only the treatment arm,
    # leaving the control arm outside the statistical gate entirely. For an ordinary paired run
    # that is harmless, since the control arm has advice disabled and no decisions to score. But a
    # design with positioning enabled on the control arm gives that arm its own adherence
    # instrument, and a broken instrument there ruins the difference just as much.
    if gate_both_arms:
        tot_arche_a: dict[str, dict] = {}
        for pr in pairs:
            adh_a = getattr(pr, "adherence_a", None) or {}
            for key, r in (adh_a.get("by_channel_archetype") or {}).items():
                row = tot_arche_a.setdefault(key, {"decided": 0, "followed": 0})
                row["decided"] += int(r.get("decided") or 0)
                row["followed"] += int(r.get("followed") or 0)
        for f in adherence_stat_flags(tot_arche_a, nominal):
            msg = f"[arm A] {f}"
            if msg not in flags:
                flags.append(msg)
    return {"by_channel": tot, "flags_per_seed": flags,
            # Read this before trusting an arm's difference: a flag means that arm's instrument
            # is broken, which suspends the result rather than annotating it.
            "verdict": "TREO — thước đo hỏng" if flags else "OK"}
