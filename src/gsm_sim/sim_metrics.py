"""The full metric set, in three mandatory groups:

  - system: served and expired rates, passenger waiting time, and supply-demand density per cell
    and hour;
  - driver: acceptance, completion, cancellation, utilisation, idle time, trips, payout and
    points;
  - advisor comparison: delegated to the paired-run comparator rather than reimplemented here.

The governing principle is that no fact has two numbers. That was the most expensive lesson of
this simulation's first cycle, when the data said one acceptance rate and the simulation behaved
as another. Consequently:

- driver metrics aggregate from the journey builder rather than recomputing from events;
- system metrics keep the existing summary's definitions for what it already covered and only add
  what was missing;
- a test proves the two paths agree.
"""

from __future__ import annotations

import statistics as st

from .journey import build_journey
from .metrics import summarize


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return float(s[k])


def customer_wait(result) -> dict:
    """How long a passenger waits between ordering and being matched, in minutes.

    Only matched orders count: an expired order has no finite wait, and folding them in would
    distort the median and hide the very problem of unmatched orders, which has its own rate.
    """
    t_of = {o.order_id: o.t_min for o in result.orders}
    waits = [e.t_min - t_of[e.detail["order_id"]]
             for e in result.events
             if e.kind == "order_matched" and e.detail.get("order_id") in t_of]
    if not waits:
        return {"matched_n": 0, "wait_median_min": 0.0, "wait_p90_min": 0.0, "wait_max_min": 0.0}
    return {
        "matched_n": len(waits),
        "wait_median_min": round(st.median(waits), 3),
        "wait_p90_min": round(_pct(waits, 0.90), 3),
        "wait_max_min": round(max(waits), 3),
    }


def supply_demand_density(result) -> dict:
    """Supply and demand density per cell and hour, and per hour.

    This is the measurement that found an early defect where a whole hour had no drivers at all
    against dozens of orders. It was a throwaway script then; it is a tested function now, so it
    need not be rebuilt whenever the question arises.

    Supply is the number of drivers on shift at the middle of the hour, approximated from shift
    windows rather than tracked per minute — enough to reveal an empty window, which is the
    point.
    """
    demand_hour: dict[int, int] = {}
    demand_cell_hour: dict[tuple[str, int], int] = {}
    for o in result.orders:
        h = int(o.t_min // 60) % 24
        demand_hour[h] = demand_hour.get(h, 0) + 1
        key = (o.pickup_cell, h)
        demand_cell_hour[key] = demand_cell_hour.get(key, 0) + 1

    expired_hour: dict[int, int] = {}
    t_of = {o.order_id: o.t_min for o in result.orders}
    for e in result.events:
        if e.kind == "order_expired":
            t = t_of.get(e.detail.get("order_id"))
            if t is not None:
                h = int(t // 60) % 24
                expired_hour[h] = expired_hour.get(h, 0) + 1

    supply_hour: dict[int, int] = {}
    for a in result.actors:
        for h in range(24):
            mid = h * 60 + 30
            if a.shift_start_min <= mid <= a.shift_end_min:
                supply_hour[h] = supply_hour.get(h, 0) + 1

    per_hour = {}
    for h in sorted(demand_hour):
        sup = supply_hour.get(h, 0)
        per_hour[h] = {
            "demand": demand_hour[h],
            "supply_drivers": sup,
            "orders_per_driver": round(demand_hour[h] / sup, 3) if sup else None,
            "expired_rate": round(expired_hour.get(h, 0) / demand_hour[h], 4),
        }
    starved = [h for h, v in per_hour.items() if v["expired_rate"] > 0.40]
    return {"per_hour": per_hour, "starved_hours": sorted(starved),
            "top_cells": sorted(demand_cell_hour.items(), key=lambda kv: -kv[1])[:10]}


def driver_metrics(result) -> dict:
    """Per-driver metric distributions, aggregated from the journey builder.

    Deliberately not recomputed from events: the journey is already the per-driver source of
    truth and has its own conservation tests. Recomputing here would create a second source.
    """
    rows = [build_journey(result, a.actor_id).metrics for a in result.actors]
    active = [m for m in rows if m["offers"] > 0]

    def dist(key: str, src: list[dict]) -> dict:
        vals = [float(m[key]) for m in src if m.get(key) is not None]
        if not vals:
            return {"n": 0}
        return {"n": len(vals), "median": round(st.median(vals), 4),
                "mean": round(st.mean(vals), 4),
                "p10": round(_pct(vals, 0.10), 4), "p90": round(_pct(vals, 0.90), 4)}

    by_arch: dict[str, list[float]] = {}
    for a in result.actors:
        m = build_journey(result, a.actor_id).metrics
        if m["acceptance_rate"] is not None:
            by_arch.setdefault(a.archetype, []).append(m["acceptance_rate"])

    return {
        "n_drivers": len(rows), "n_active": len(active),
        "acceptance_rate": dist("acceptance_rate", active),
        "completion_rate": dist("completion_rate", active),
        "utilization": dist("utilization", active),
        "idle_min": dist("idle_min", active),
        "trips_completed": dist("trips_completed", active),
        "payout_vnd": dist("payout_vnd", active),
        "day_bonus_vnd": dist("day_bonus_vnd", active),
        "points": dist("points", active),
        "online_min": dist("online_min", active),
        "cancel_after_accept": sum(m["cancelled_after_accept"] for m in rows),
        "skipped_soc": sum(m["skipped_soc"] for m in rows),
        "acceptance_by_archetype": {k: round(st.median(v), 4) for k, v in sorted(by_arch.items())},
    }


def system_metrics(result) -> dict:
    """System metrics: the existing summary plus what the specification still lacked."""
    base = summarize(result)
    return {**base, "customer_wait": customer_wait(result),
            "density": supply_demand_density(result)}


def cost_summary(result) -> dict:
    """Read the cost ledger, which until now no metric read at all, leaving it dead.

    It reads only and never subtracts from payout: revenue, payout and net are kept separate, so
    the net figure is a new key and no actor's payout is touched.

    With costs at their default of zero, the total is zero and net equals mean payout until real
    costs are enabled."""
    pays = [float(a.payout_vnd) for a in result.actors]
    costs = [float(a.cost_vnd) for a in result.actors]
    nets = [p - c for p, c in zip(pays, costs)]
    return {
        "cost_total_vnd": int(round(sum(costs))),
        "cost_mean_vnd": round(st.mean(costs), 2) if costs else 0.0,
        "net_mean_vnd": round(st.mean(nets), 2) if nets else 0.0,
    }


def full_report(result) -> dict:
    """The full metric set for one run. Paired comparison lives in the comparator, not here."""
    return {"seed": result.seed, "system": system_metrics(result),
            "drivers": driver_metrics(result), "cost": cost_summary(result), "source": "MOCK"}


# ---------------------------------------------------------------------------
# Fairness, concentration and the passenger's side.
#
# Why they exist: fleet-wide advice was measured to lower the served rate on most seeds and to add
# expired orders every day, while the five guardrail fields then in place caught none of it. These
# are the precondition for a two-sided acceptance criterion: an advice channel is only valuable when
# individual payout rises *and* the system, the passenger and fairness do not get worse.
#
# Every function reads directly from the run result; none reimplements a rule or touches the
# engine.


def gini(values: list[float]) -> float:
    """The Gini coefficient of a distribution: zero is perfectly equal, one is everything held
    by one person.

    It is scale-invariant — doubling every income leaves it unchanged — which is what makes two
    worlds with different total payouts comparable.
    """
    vals = sorted(float(v) for v in values)
    n = len(vals)
    total = sum(vals)
    if n == 0 or total <= 0:
        return 0.0
    cum = sum((2 * i - n + 1) * v for i, v in enumerate(vals))
    return round(cum / (n * total), 4)


def hhi(shares: list[float]) -> float:
    """A normalised concentration index: zero is perfectly spread, one is entirely in one
    place."""
    vals = [float(v) for v in shares if v > 0]
    n = len(vals)
    total = sum(vals)
    if n <= 1 or total <= 0:
        return 1.0 if n == 1 else 0.0
    raw = sum((v / total) ** 2 for v in vals)
    return round((raw - 1.0 / n) / (1.0 - 1.0 / n), 4)


def fairness_metrics(result) -> dict:
    """How income is distributed across drivers, and the total payout.

    The total is the most important field here: without it there is no way to tell advice that
    creates value from advice that merely redistributes it.
    """
    pay = [a.payout_vnd for a in result.actors]
    if not pay:
        return {"n_actors": 0, "gini_payout": 0.0, "total_payout_vnd": 0,
                "payout_p10": 0, "payout_median": 0, "payout_p90": 0}
    return {
        "n_actors": len(pay),
        "gini_payout": gini(pay),
        "total_payout_vnd": int(sum(pay)),
        "payout_p10": int(_pct(pay, 0.10)),
        "payout_median": int(st.median(pay)),
        "payout_p90": int(_pct(pay, 0.90)),
    }


def concentration_metrics(result) -> dict:
    """Concentration: load across stations, supply across cells, and simultaneous peaks by hour.

    This is the anti-herding guardrail: advice that raises these figures is an advisor destroying
    its own value.
    """
    from collections import Counter

    import h3

    st_load = Counter(e.detail.get("station") for e in result.events
                      if e.kind in ("swap_done", "swap_failed") and e.detail.get("station"))

    # Supply per cell is a driver's minutes of presence, attributed to the cell of the segment's
    # *destination*.
    #
    # The destination is a deliberate proxy: the herding signal to catch is where drivers
    # converge. For resting and charging the two ends coincide, so nothing is lost; for travel
    # and relocation the destination is exactly where advice pushed them. Splitting between the
    # two ends would dilute the very signal being measured.
    #
    # The segment carries no cell field, and an early draft read one that did not exist and
    # returned zero silently; a test now guards that.
    res = result.grid.res
    cell_minutes: Counter = Counter()
    for sgm in result.segments:
        dt = max(0.0, float(sgm["t1"]) - float(sgm["t0"]))
        lat, lon = sgm.get("to_lat"), sgm.get("to_lon")
        if dt <= 0.0 or lat is None or lon is None:
            continue
        cell_minutes[h3.latlng_to_cell(float(lat), float(lon), res)] += dt

    swap_hour = Counter(int(e.t_min // 60) for e in result.events if e.kind == "go_swap")
    rest_hour = Counter(int(e.t_min // 60) for e in result.events if e.kind == "rest")
    return {
        "station_hhi": hhi(list(st_load.values())),
        "supply_cell_hhi": hhi(list(cell_minutes.values())),
        "n_supply_cells": len(cell_minutes),
        "supply_minutes_total": round(sum(cell_minutes.values()), 1),
        "peak_swap_per_hour": max(swap_hour.values()) if swap_hour else 0,
        "peak_rest_per_hour": max(rest_hour.values()) if rest_hour else 0,
        "n_stations_used": len(st_load),
    }


def customer_impact(result) -> dict:
    """The effect on passengers, including the orders nobody took, which the waiting-time metric
    deliberately excludes.

    That metric covers matched orders only, which is statistically correct but hides the largest
    harm. Here the two are separated: the wait for orders that were served, and the ways an order
    went unserved.
    """
    from collections import Counter
    w = customer_wait(result)
    kinds = Counter(e.kind for e in result.events)
    total = len(result.orders)
    expired = kinds.get("order_expired", 0)
    return {
        "orders_total": total,
        "expired_n": expired,
        "expired_rate": round(expired / total, 4) if total else 0.0,
        "wait_median_min": w["wait_median_min"],
        "wait_p90_min": w["wait_p90_min"],
        "unserved_breakdown": {
            "expired": expired,
            "censored": kinds.get("order_censored", 0),
            "cancelled_after_accept": kinds.get("order_cancelled_after_accept", 0),
        },
    }


def system_guardrail(result) -> dict:
    """The five guardrail layers: system, passenger, fairness, concentration, and health — the
    last asking whether the rails are still intact."""
    s = summarize(result)
    out = {"served_rate": s["served_rate"], "orders_completed": s["orders_completed"]}
    out.update(fairness_metrics(result))
    out.update(customer_impact(result))
    out.update(concentration_metrics(result))
    # The supply-demand density function existed with no consumer. This extracts one comparable
    # scalar from it: the number of supply-starved hours, where a large share of that hour's
    # orders expired. It calls the original rather than redefining it.
    out["starved_hours_n"] = len(supply_demand_density(result)["starved_hours"])
    out.update(health_guardrail(result))
    return out


# ---------------------------------------------------------------------------
# The fifth guardrail layer: are the health rails still intact?
#
# Why it exists: an adversarial review measured that deleting the fatigue rail took its vetoes
# from most drivers to none while every test stayed green, the headline mean *rose*, and none of
# the four existing guardrail layers reported anything — the textbook case of buying a difference
# by removing a rail.
#
# The discipline: this layer emits counts and minutes only. No coefficients, no money, and no
# entry into the acceptance formula — any constant multiplying these by money would be a price on
# a health rail. It is a one-way gate that reports only deterioration, which is what stops anyone
# being rewarded for a *high* veto count by making drivers hit fatigue more often.
#
# Two definitions of overwork are measured in parallel, because choosing one is blind to half the
# picture — they disagree by more than a factor of two on how many drivers exceed a long day:
#   the working span, from rest to rest, including waiting for passengers;
#   real driving minutes within that span.
# ---------------------------------------------------------------------------

REST_RAILS = ("soc_low", "fatigued", "defer_cap")
# The shift-extension channel's rails. The extension cap is deliberately excluded: it is an
# economic constraint rather than a health rail, and folding it in would inflate the veto count
# with blocks that have nothing to do with health.
EXTEND_RAILS = ("soc_low", "fatigued", "would_exceed_fatigue")
_DRIVE_KINDS = frozenset({"enroute", "on_trip", "relocate"})
_RECOVERY_KINDS = frozenset({"rest", "charge"})
# The break threshold is derived from the world's own rest distribution, so every genuine rest
# resets the streak while a one- or two-minute swap does not, since a swap is not recovery.
# Changing that distribution without recalibrating turns a test red.
DRIVE_BREAK_MIN = 20.0
RAIL_ALIVE_MIN_N = 20           # below this, a rail being alive or dead is indistinguishable from noise
REST_TOTAL_DROP_TOL = 0.02      # a drop in total rest beyond this between arms is reported
SPAN_P90_RISE_TOL = 0.10        # a rise in the overload tail beyond this is reported; the tail, not the maximum, which is
                                # far noisier

# The rest-deferral channel's commitment ledger. Not a rail: it is the evidence that a promise
# was actually kept. Invariant: commitments made are at least those kept, broken and cleared.
COMMIT_KEYS = ("commit_made_n", "commit_kept_n", "commit_broken_n", "commit_cleared_n")

# ---------------------------------------------------------------------------
# Rails declared inert, explicitly.
#
# The flags now also report a case the old gate was blind to: a rail that was *never* alive, zero
# on both arms even though the gate ran. The old form only caught alive-then-dead, so an
# unreachable rail could never trip it — measured, one rail was zero on both arms across thirty
# seeds.
#
# A rail that is deliberately inert is declared here with its reason and the condition under
# which it should be reopened.
# ---------------------------------------------------------------------------
RAIL_KHAI_TRO: dict[str, str] = {
    "veto_soc_low_n":
        "BẤT KHẢ ĐẠT theo cấu trúc sau `D-M3-04-FIX`: `world.py` chỉ vào nhánh hoãn-nghỉ khi "
        "`action == REST`, mà `behavior.choose_idle_action` bước 1 đã trả GO_SWAP/GO_CHARGE với "
        "CÙNG ngưỡng `swap_soc_threshold_pct` được truyền xuống ⇒ tập rỗng. Tính AN TOÀN vẫn "
        "đúng (chặn ở thượng nguồn); đây là lỗ hổng HIỂN THỊ. "
        "MỞ LẠI khi: nhánh hoãn-nghỉ nhận thêm action ngoài REST, hoặc hai ngưỡng tách nhau.",
    "veto_defer_cap_n":
        "Trơ ở config hiện hành — đã có test khai trơ `test_t3_defer_cap_TRO_o_config_hien_hanh` "
        "(ĐỎ = TIN TỐT). MỞ LẠI khi kênh hoãn-nghỉ nói đủ nhiều để chạm trần "
        "`rest_defer_max_min`.",
}


def rest_rails_audit(result) -> dict:
    """Count rail vetoes from the log-only veto events, which the world writes for every
    non-deferral outcome. The call count travels with them by necessity: advice keeps drivers
    online longer, so the veto denominator differs between arms, and reading a raw count without
    it is a misreading."""
    out = {f"veto_{r}_n": 0 for r in REST_RAILS}
    calls = 0
    for e in result.events:
        if e.kind != "advice_rest_veto":
            continue
        calls += 1
        r = e.detail.get("reason")
        if r in REST_RAILS:
            out[f"veto_{r}_n"] += 1
    out["veto_calls_n"] = calls
    out["veto_fired_n"] = sum(out[f"veto_{r}_n"] for r in REST_RAILS)
    # The commitment ledger. Deferring rest is a promise with an outcome, and the outcome has to
    # be countable:
    #   made     a commitment was recorded, logged once when the compliance draw agreed
    #   kept     the promised hour arrived and rest was enforced
    #   broken   the driver was busy for the whole hour, so the right to rest returned
    #   cleared  rest happened voluntarily before the promised hour
    # Invariant: those made are at least those kept, broken and cleared; the remainder is the
    # commitments still open at the end of the day.
    #
    # Without this ledger, "deferral is a commitment" would be a mechanism that exists only on
    # paper, with nobody able to show the promise was kept.
    #
    # The event kind is shared with the not-followed record from the drain path — same kind,
    # different followed flag, because separating the kind would separate the denominator.
    # Counting commitments without filtering on that flag counts *refused* advice as commitments,
    # which was caught when five events yielded only two real commitments.
    kinds = {"advice_rest_commit_kept": "commit_kept_n",
             "advice_rest_commit_broken": "commit_broken_n",
             "advice_rest_commit_cleared": "commit_cleared_n"}
    out["commit_made_n"] = 0
    for k in kinds.values():
        out[k] = 0
    for e in result.events:
        if e.kind == "advice_rest_window" and e.detail.get("followed") is True:
            out["commit_made_n"] += 1
        else:
            k = kinds.get(e.kind)
            if k:
                out[k] += 1
    return out


def fingerprint_actors(result) -> str:
    """A per-actor digest of trajectory, money and rest: the single source for every determinism
    gate.

    It was moved here from a script, where a preregistered stop condition depended on a function
    outside the library — not importable from the package, and easy for someone to reimplement
    slightly differently.

    It replaces an earlier check that compared only the *orders*, which are generated outside the
    world and therefore matched even when every actor trajectory had diverged. This one catches
    contamination of the random streams.
    """
    import hashlib
    import json as _json

    segs: dict[int, list] = {}
    for s in result.segments:
        segs.setdefault(s["actor_id"], []).append(
            (s["kind"], round(float(s["t0"]), 3), round(float(s["t1"]), 3)))
    rows = []
    for a in sorted(result.actors, key=lambda x: x.actor_id):
        rows.append((a.actor_id, sorted(segs.get(a.actor_id, [])),
                     round(float(a.payout_vnd), 6), int(a.trips_done),
                     round(float(a.rest_min), 6)))
    return hashlib.sha256(_json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]


def extend_rails_audit(result) -> dict:
    """The extension channel's counterpart to the rest-rail audit.

    Why it is audited separately rather than merged: the two channels block in opposite
    directions. One counts how often the advisor did not dare defer rest; the other how often it
    did not dare suggest working longer. One combined number would cancel them and lose exactly
    the information worth reading.

    Its keys carry their own prefix so the two counters can live side by side in one report
    without overwriting each other.

    Its call count is deliberately excluded from the scope keys, and that must not be "fixed".
    Scope keys exclude quantities whose difference is always zero because both arms use the same
    set of actors, so significance would be meaningless for them. This call count genuinely
    differs between arms — advice keeps drivers online longer, so they are polled more often — and
    a significant difference here is information rather than noise. It is a denominator *for
    reading the fired count*, not a denominator in the scope-keys sense.
    """
    out = {f"xveto_{r}_n": 0 for r in EXTEND_RAILS}
    calls = 0
    for e in result.events:
        if e.kind != "advice_extend_veto":
            continue
        calls += 1
        r = e.detail.get("reason")
        if r in EXTEND_RAILS:
            out[f"xveto_{r}_n"] += 1
    out["xveto_calls_n"] = calls
    out["xveto_fired_n"] = sum(out[f"xveto_{r}_n"] for r in EXTEND_RAILS)
    return out


def continuous_work(result, break_min: float = DRIVE_BREAK_MIN) -> dict:
    """Per actor and then aggregated: the working span from rest to rest, and real driving
    minutes. The streak resets on a rest or charge segment long enough to count as a break; an
    idle gap spent waiting for passengers neither resets the span nor adds to driving."""
    import collections
    by_actor: dict[int, list] = collections.defaultdict(list)
    for s in result.segments:
        by_actor[s["actor_id"]].append(s)
    spans, drives = [], []
    for segs in by_actor.values():
        segs.sort(key=lambda s: (s["t0"], s["t1"]))
        span0 = span1 = None
        drive = 0.0
        best_span = best_drive = 0.0
        for s in segs:
            if s["kind"] in _RECOVERY_KINDS and (s["t1"] - s["t0"]) >= break_min:
                if span0 is not None:
                    best_span = max(best_span, span1 - span0)
                    best_drive = max(best_drive, drive)
                span0, drive = None, 0.0
                continue
            if s["kind"] not in _DRIVE_KINDS:
                continue
            if span0 is None:
                span0 = s["t0"]
            span1 = s["t1"] if span1 is None else max(span1, s["t1"])
            drive += s["t1"] - s["t0"]
        if span0 is not None:
            best_span = max(best_span, span1 - span0)
            best_drive = max(best_drive, drive)
        spans.append(best_span)
        drives.append(best_drive)
    import numpy as np
    sp, dr = np.asarray(spans or [0.0]), np.asarray(drives or [0.0])
    return {"work_span_p50": round(float(np.percentile(sp, 50)), 1),
            "work_span_p90": round(float(np.percentile(sp, 90)), 1),
            "work_span_max": round(float(sp.max()), 1),
            "drive_min_p50": round(float(np.percentile(dr, 50)), 1),
            "drive_min_p90": round(float(np.percentile(dr, 90)), 1),
            "drive_min_max": round(float(dr.max()), 1)}


def tier_achievement(result, day_bonus_tiers) -> dict[int, int]:
    """How many drivers reached each points tier.

    This exists to make the *losing* side visible. The optimal selection only takes headroom that
    costs nobody a tier, but the real mechanism displaces whoever loses the assignment, and that
    person may be sitting just below a tier. Uncounted, the harm is invisible and we would report
    the gain while hiding the loss.

    It counts points rather than money, for two reasons: the acceptance gate means reaching a tier
    is not the same as being paid for it, and merging two different questions into one figure
    loses information; and counting points keeps this function outside the money scope.
    """
    pts = [int(getattr(a, "points", 0)) for a in result.actors]
    return {int(t): sum(1 for p in pts if p >= int(t)) for t, _v in day_bonus_tiers}


def touched_actors(result, channel: str | None = None) -> set[int]:
    """The actors the intervention actually reached.

    Why it is needed: a guardrail computed over the whole cohort is only sensitive when the
    intervention touches nearly everyone. With every channel enabled it touches all of them and
    the effect is far above tolerance, so the gate works. But a sparse channel speaking a handful
    of times a day across ninety drivers reaches about a tenth of them, which dilutes the effect
    by an order of magnitude, below seed noise. The gate would then be watching noise, verdicts
    would depend on the seed, and whoever investigated would widen the tolerance rather than fix
    the denominator.
    """
    ids: set[int] = set()
    for e in result.events:
        if not str(e.kind).startswith("advice_") or int(e.actor_id) < 0:
            continue
        if channel is not None and (e.detail or {}).get("channel") != channel:
            continue
        ids.add(int(e.actor_id))
    return ids


def health_guardrail(result, actor_ids: set[int] | None = None) -> dict:
    """The health layer: counts and minutes only, never money.

    Restricting to a set of actors narrows the rest total to those the intervention reached.
    Omitted, it covers the whole cohort, which is the earlier behaviour and is kept for existing
    consumers.
    """
    acts = (result.actors if actor_ids is None
            else [a for a in result.actors if int(a.actor_id) in actor_ids])
    out = {"rest_min_total": round(sum(a.rest_min for a in acts), 1),
           "n_actors_scope": len(acts)}
    out.update(rest_rails_audit(result))
    # The extension channel's rails belong in the same guardrail table. Without this wiring the
    # audit function would have no caller — the shape where a layer has an aggregator but no data
    # source and suspends every comparison for reasons nobody can see.
    out.update(extend_rails_audit(result))
    out.update(continuous_work(result))
    return out


def health_guardrail_flags(a: dict, b: dict) -> list[str]:
    """A one-way gate between the control and treatment arms, reporting health regressions.

    It reports a rail collapsing to zero — alive on the control arm with a sufficient sample and
    dead on the treatment arm, which is the removed-rail scenario; total rest falling beyond
    tolerance; and the tail of either overwork definition rising beyond tolerance.

    There is no direction to be praised in: more vetoes or more rest earn nothing.
    """
    flags: list[str] = []
    # Both rail families are inspected. Iterating only the rest rails left three extension keys
    # labelled as guarded by this gate in the artifact while the gate never looked at them.
    for tien_to, rails in (("veto", REST_RAILS), ("xveto", EXTEND_RAILS)):
        for r in rails:
            k = f"{tien_to}_{r}_n"
            va, vb = int(a.get(k) or 0), int(b.get(k) or 0)
            if va >= RAIL_ALIVE_MIN_N and vb == 0:
                flags.append(f"lan can `{k}` SỤP VỀ 0 (A={va}, B=0) — nghi xoá lan can, "
                             f"TREO mọi Δ cho tới khi giải thích được")
                continue
            # A rail that was never alive. The older form was blind to this because it required
            # a minimum count on the control arm.
            #
            # A correction worth keeping: a first attempt required both arms to have run the
            # gate, which killed this branch for exactly the rail family it had just been
            # extended to. On the control arm the channel is off and returns before any rail is
            # reached, so its call count is always zero — a dead branch of precisely the kind
            # this gate exists to catch.
            #
            # The correct condition is "on the arm where the gate did run, this rail never
            # fired", anchored to the treatment arm. The control arm participates only when it
            # also ran, and if it ran with the rail firing, that is the collapse case already
            # handled above.
            calls_a, calls_b = int(a.get(f"{tien_to}_calls_n") or 0), \
                int(b.get(f"{tien_to}_calls_n") or 0)
            if (calls_b > 0 and vb == 0 and (calls_a == 0 or va == 0)
                    and k not in RAIL_KHAI_TRO):
                flags.append(
                    f"lan can `{k}` CHƯA TỪNG BẮN dù cổng chạy {calls_a}/{calls_b} lượt — nó "
                    f"đang được ĐẾM như một lan can trong khi không bảo vệ được gì. Khai vào "
                    f"`RAIL_KHAI_TRO` kèm lý do + điều kiện mở lại, hoặc sửa cho nó đạt tới được")
    # The commitment ledger. Only commitments *kept* is a quantity whose collapse to zero means
    # a health regression: the advisor promised to defer rest and stopped keeping its word.
    # Commitments made, broken and cleared are not inspected individually: a fall in those made
    # only means the channel spoke less, and a fall in those broken is good news. Reporting them
    # would create a direction to optimise toward, which is what this layer exists to prevent.
    ka, kb = int(a.get("commit_kept_n") or 0), int(b.get("commit_kept_n") or 0)
    if ka >= RAIL_ALIVE_MIN_N and kb == 0:
        flags.append(f"`commit_kept_n` SỤP VỀ 0 (A={ka}, B=0) — lời hứa hoãn nghỉ không còn được "
                     f"thi hành; TREO mọi Δ cho tới khi giải thích được")
    made_b, giu_b = int(b.get("commit_made_n") or 0), (
        kb + int(b.get("commit_broken_n") or 0) + int(b.get("commit_cleared_n") or 0))
    if made_b >= RAIL_ALIVE_MIN_N and giu_b == 0:
        flags.append(f"sổ CAM KẾT hở: arm B ghi {made_b} cam kết nhưng KHÔNG cam kết nào có kết "
                     f"cục (kept/broken/cleared đều 0) — lời hứa hoãn nghỉ đang sống trên giấy")
    if made_b and giu_b > made_b:
        flags.append(f"sổ CAM KẾT vỡ bảo toàn: kept+broken+cleared = {giu_b} > made = {made_b}")
    ra, rb = float(a.get("rest_min_total") or 0), float(b.get("rest_min_total") or 0)
    if ra > 0 and (ra - rb) / ra > REST_TOTAL_DROP_TOL:
        flags.append(f"rest_min_total giảm {(ra - rb) / ra:.1%} (A={ra:.0f}′ → B={rb:.0f}′) "
                     f"— advice đang ăn vào nghỉ")
    for k in ("work_span_p90", "drive_min_p90"):
        ka, kb = float(a.get(k) or 0), float(b.get(k) or 0)
        if ka > 0 and (kb - ka) / ka > SPAN_P90_RISE_TOL:
            flags.append(f"{k} tăng {(kb - ka) / ka:.1%} (A={ka:.0f}′ → B={kb:.0f}′) "
                         f"— advice kéo dài chuỗi làm việc liên tục")
    return flags


# ---------------------------------------------------------------------------
# The validity gate for every paired run: adherence must be inside the artifact.
#
# The rule had long been documented — every arm reports decision adherence per archetype against
# nominal, and a deviation beyond a small tolerance suspends the result. Measured, the three
# modules that produce artifacts referenced adherence exactly zero times, and no artifact carried
# an adherence key at all. The gate existed only on paper.
#
# That is the direct reason a denominator defect survived dozens of artifacts: one channel
# reported perfect adherence against a real rate of about half, dozens of times, with no gate
# firing.
# ---------------------------------------------------------------------------

# Two kinds of gate, different in nature; do not merge them.
#
#   Impossibility, per seed: a state that cannot be right however noisy the run. An adherence of
#   exactly one or exactly zero over a large enough denominator indicates a missing numerator or
#   denominator rather than statistical luck. Had this gate existed, it would have caught the
#   denominator defect in the very first artifact.
#
#   Statistical, in aggregate: the measured rate deviating from nominal beyond a tolerance.
#   The original rule's tolerance cannot be applied per seed: with a few hundred decisions the
#   sampling error alone exceeds it, so a per-seed gate would fire constantly on noise. It is
#   meaningful only across many seeds. Stated here so that nobody "fixes" it by widening the
#   tolerance.
IMPOSSIBLE_ADHERENCE_MIN_DENOM = 20     # below this, a perfect or zero rate could genuinely be luck


def adherence_audit(result, run_id: str | None = None) -> dict:
    """Decisions, compliance and adherence per channel and per channel-and-archetype.

    It reads the shared adherence view rather than reimplementing the counting, which is how the
    two-sources-of-truth problem is avoided.

    An empty flag list means no impossible state was found.
    """
    from gsm_core.lifecycle import projections as P

    lifecycle = (P.sim_events_to_lifecycle(result.events, run_id) if run_id
                 else P.sim_events_to_lifecycle(result.events))
    view = P.adherence_view(lifecycle)
    # The adherence view drops decisions by skipping them, which leaves no trace, and the gate
    # below inspects only keys that are present, so it cannot see a key that is *absent*. If a
    # producer emits an unregistered topic, or mixes topics, the denominator falls with nobody
    # knowing — the same shape as the defect that survived dozens of artifacts because no
    # mechanism complained.
    drops = P.adherence_drops(lifecycle)
    arche = {str(a.actor_id): a.archetype for a in result.actors}

    by_ch: dict[str, dict] = {}
    by_ch_ar: dict[str, dict] = {}
    for (_run, drv, topic), v in view.items():
        if topic is None:
            continue
        for bucket, key in ((by_ch, topic),
                            (by_ch_ar, f"{topic}|{arche.get(str(drv), '?')}")):
            row = bucket.setdefault(key, {"decided": 0, "followed": 0, "dismissed": 0,
                                          "suppressed": 0, "event_decided": 0,
                                          "event_followed": 0})
            for k in row:
                row[k] += int(v.get(k) or 0)

    for bucket in (by_ch, by_ch_ar):
        for row in bucket.values():
            row["decision_adherence"] = (row["followed"] / row["decided"]
                                         if row["decided"] else None)
            row["event_adherence"] = (row["event_followed"] / row["event_decided"]
                                      if row["event_decided"] else None)

    # Event-level adherence is a structurally biased estimator on any channel that re-asks: the
    # frequency of re-asking depends on the outcome itself. A driver who does not comply is asked
    # again every tick, inflating the event denominator, while one who complies is not. Measured,
    # a re-asking channel showed nearly twenty percentage points lower at event level than at
    # decision level, while three non-re-asking channels showed no gap at all.
    #
    # It cannot be "fixed", because it measures something else, but it must be labelled, or
    # someone will compare event-level rates *between* channels, where re-asking channels always
    # look worse, or quote it as the real adherence. For a re-asking channel it is a lower
    # bound.
    for bucket in (by_ch, by_ch_ar):
        for row in bucket.values():
            row["event_repeat_ratio"] = (row["event_decided"] / row["decided"]
                                         if row["decided"] else None)
            row["event_adherence_is_lower_bound"] = bool(
                row["event_decided"] > row["decided"])

    # Complying and being able to act are two questions. The followed flag is the outcome of the
    # compliance draw at assignment time; the execution rate is separated out here. It does not
    # enter the adherence gate, which measures the integrity of the instrument rather than the
    # dynamics, but it is a finding in its own right: advice agreed to and not executable is
    # advice going to waste.
    exec_n = sum(1 for e in result.events if e.kind == "standby_followed")
    coin_n = sum(len(e.detail.get("coin_follow_ids") or [])
                 for e in result.events if e.kind == "standby_alloc")
    execution = {"positioning": {"coin_true_n": coin_n, "executed_n": exec_n,
                                 "execution_rate": (exec_n / coin_n) if coin_n else None}}

    # The impossibility gate has to know the run's nominal rate. On a perfect-compliance arm —
    # the very idea of comparing a world without an advisor against one followed completely — a
    # measured adherence of one is correct rather than impossible. The earlier form would have
    # suspended every channel on every seed, blocking the most valid arm permanently, and the gate
    # would then be switched off. The bounds are computed per channel from the archetypes that
    # channel actually reaches.
    from .advice_bridge import DEFAULT_ADHERENCE
    adv = (getattr(result, "config", None) and result.config.data.get("advice")) or {}
    nominal = {**DEFAULT_ADHERENCE, **(adv.get("adherence_by_archetype") or {})}
    bounds: dict[str, tuple[float, float]] = {}
    for key in by_ch_ar:
        ch, _, ar = key.partition("|")
        p = nominal.get(ar)
        if p is None:
            continue
        lo, hi = bounds.get(ch, (1.0, 0.0))
        bounds[ch] = (min(lo, float(p)), max(hi, float(p)))

    return {"by_channel": by_ch, "by_channel_archetype": by_ch_ar,
            "execution": execution, "drops": drops,
            "flags": adherence_flags(by_ch, channel_nominal_bounds=bounds, drops=drops)}


def adherence_flags(by_channel: dict,
                    channel_nominal_bounds: dict[str, tuple[float, float]] | None = None,
                    drops: dict | None = None) -> list[str]:
    """The impossibility gate: states that cannot be right however noisy the run.

    Each flag says what is wrong and why it is impossible, so a reader of the artifact does not
    have to look it up. A flag suspends that arm's result rather than being a footnote.

    The nominal bounds give, per channel, the range of nominal rates across the archetypes that
    channel reaches. A perfect rate is impossible only when the maximum nominal is below one, so
    that the draw is still random; a zero rate likewise only when the minimum is above zero.
    Without bounds it assumes a genuinely random draw, which is right for any configuration that
    does not set an extreme nominal.
    """
    bounds = channel_nominal_bounds or {}
    out: list[str] = []
    for ch, r in sorted(by_channel.items()):
        d, adh = r["decided"], r["decision_adherence"]
        if d == 0:
            out.append(f"{ch}: decided=0 — kênh có event nhưng KHÔNG có quyết định nào "
                       f"vào mẫu số (mẫu số RỖNG, không phải 'thiếu')")
            continue
        if d < IMPOSSIBLE_ADHERENCE_MIN_DENOM:
            continue                      # too small a denominator: a perfect or zero rate could genuinely be luck
        min_p, max_p = bounds.get(ch, (0.5, 0.5))   # no bounds available: treat the draw as random
        if adh is not None and adh >= 1.0 and max_p < 1.0:
            out.append(f"{ch}: decision_adherence = 1,000 trên {d} quyết định — BẤT KHẢ với "
                       f"coin ngẫu nhiên (nominal ≤ {max_p:.2f}); dấu hiệu mẫu số chỉ chứa "
                       f"người ĐÃ THEO (D-M3-01)")
        if adh is not None and adh <= 0.0 and min_p > 0.0:
            out.append(f"{ch}: decision_adherence = 0,000 trên {d} quyết định — BẤT KHẢ "
                       f"(nominal ≥ {min_p:.2f}); dấu hiệu tử số không được ghi")
        if r["event_decided"] == 0 and r["decided"] > 0:
            out.append(f"{ch}: event_decided=0 trong khi decided={d} — một nửa bộ đo "
                       f"hai-đơn-vị chết im lặng (event_adherence sẽ là None)")

    # A gate for what is *not* present in the per-channel table.
    #
    # Every check above inspects keys that exist. But the adherence view drops decisions with an
    # unregistered or mixed topic, so they are absent, and no loop above can reach an absent key.
    # That is exactly the earlier defect's shape: a missing denominator with every other figure
    # looking normal. This gate therefore reads the drop record rather than the table.
    #
    # An unregistered topic suspends the result rather than merely raising a flag, because this
    # repository has a history of warnings being ignored: the earlier defect survived dozens of
    # artifacts precisely because nothing blocked. Same principle as the adherence gate: with an
    # incomplete denominator, every difference is suspect.
    if drops:
        if drops.get("unknown"):
            out.append(
                f"TREO: {drops['unknown']} quyết định bị loại khỏi mẫu số vì topic CHƯA PHÂN LOẠI "
                f"{drops.get('topics_unknown') or ''} — mẫu số KHÔNG đầy đủ nên mọi Δ tính trên nó "
                f"đều đáng ngờ. Khai topic vào `gsm_core.lifecycle.advice_topics` "
                f"(MEASURED_TOPICS nếu là lời khuyên kinh tế, SOFT_TOPICS nếu là khuyên mềm) rồi "
                f"đo lại. ĐỪNG đọc Δ này.")
        if drops.get("mixed"):
            out.append(
                f"TREO: {drops['mixed']} quyết định mang CẢ topic được-đo lẫn topic bị-loại trên "
                f"cùng một `decision_id` — đây là LỖI PRODUCER (không đường ghi nào hôm nay tạo "
                f"được). Cả cụm bị loại (đánh đổi khai ở `projections.adherence_view`), nên mẫu số "
                f"tụt. Tìm producer đang trộn topic trước khi tin bất kỳ số nào.")
        # Soft-advice drops are not flagged: that boundary is working correctly rather than
        # behaving anomalously. Flagging it would teach readers to ignore flags, which is the
        # fastest way to kill a gate.
    return out


# ---------------------------------------------------------------------------
# The statistical gate: an analytic Poisson-binomial z test, not a bootstrap, suspending the
# result beyond four standard deviations.
#
# Why not a bootstrap: the null distribution is known exactly. Each decision is a Bernoulli trial
# whose probability is the nominal adherence of that particular driver's archetype. A bootstrap
# resamples the *observed* decisions and so treats them as exchangeable; the closed form does not,
# and it handles the archetype mix by construction — a channel that only reaches the least
# compliant archetypes has their rate as its null, not the fleet average.
#
# Why four standard deviations, derived rather than chosen:
#   - the real defect, a perfect rate over a hundred decisions against a nominal half, scores ten,
#     leaving a factor of two and a half of margin;
#   - the old rule's fixed absolute tolerance is well under one standard deviation at a hundred
#     decisions and nearly three at five thousand, so no fixed absolute threshold can be right at
#     both ends, which is why that rule died;
#   - family-wise over the channel-by-archetype grid, three standard deviations gives a false
#     alarm rate of several percent, which is noisy enough that the gate would be switched off;
#     four gives under two in a thousand; four and a half is tighter than needed.
#
# Do not widen this threshold when it fires. Its false-alarm rate is under two in a thousand runs,
# so a firing almost certainly means the instrument is broken rather than that the run was noisy.
# ---------------------------------------------------------------------------

ADHERENCE_Z_MAX = 4.0
STAT_GATE_MIN_DENOM = 20      # below this the variance estimate is too coarse; skip the cell


def poisson_binomial_z(followed: int, ps: list[float]) -> float:
    """The z statistic for the compliance count against the Poisson-binomial null.

    Each probability is the nominal compliance rate for that decision, taken from the archetype
    of the driver who made it. The closed form requires the decisions to be independent, which
    holds for the keyed hash draw: the seed is part of the key, so draws are independent both
    across keys and across seeds.
    """
    n = len(ps)
    if n == 0:
        return 0.0
    mu = sum(ps)
    var = sum(p * (1.0 - p) for p in ps)
    if var <= 0.0:                # a nominal rate of exactly zero or one makes the statistic meaningless
        return 0.0
    return (float(followed) - mu) / (var ** 0.5)


def adherence_stat_flags(by_channel_archetype: dict, nominal: dict[str, float]) -> list[str]:
    """The statistical gate over the channel-by-archetype cells, and aggregated per channel.

    It takes the per-cell table from the adherence audit and the nominal rates per archetype, and
    returns the suspension messages. An empty list means no cell or channel departs from the null
    beyond the threshold.
    """
    out: list[str] = []
    per_channel: dict[str, tuple[int, list[float]]] = {}
    for key, row in sorted(by_channel_archetype.items()):
        ch, _, arche = key.partition("|")
        p = nominal.get(arche)
        d, f = int(row.get("decided") or 0), int(row.get("followed") or 0)
        if p is None or d == 0:
            continue                      # an unknown archetype has no null to compare against; the impossibility gate covers the
                                          # denominator
        fol, ps = per_channel.get(ch, (0, []))
        per_channel[ch] = (fol + f, ps + [p] * d)
        if d < STAT_GATE_MIN_DENOM:
            continue
        z = poisson_binomial_z(f, [p] * d)
        if abs(z) > ADHERENCE_Z_MAX:
            out.append(f"{key}: adherence {f}/{d} = {f / d:.3f} vs danh nghĩa {p:.2f} — "
                       f"z = {z:+.1f} (|z| > {ADHERENCE_Z_MAX:.0f}) ⇒ TREO: thước đo lệch "
                       f"null quá mức nhiễu cho phép")
    for ch, (fol, ps) in sorted(per_channel.items()):
        if len(ps) < STAT_GATE_MIN_DENOM:
            continue
        z = poisson_binomial_z(fol, ps)
        if abs(z) > ADHERENCE_Z_MAX:
            mu = sum(ps) / len(ps)
            out.append(f"{ch} (gộp hỗn hợp archetype): adherence {fol}/{len(ps)} = "
                       f"{fol / len(ps):.3f} vs null Poisson-binomial mu={mu:.3f} — "
                       f"z = {z:+.1f} ⇒ TREO")
    return out
