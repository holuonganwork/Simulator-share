"""Two-stage dispatcher, run on each dispatch tick.

    Stage 1, candidate retrieval: the hex disc around the pickup at the operating resolution.
    Stage 2, batched bipartite matching: cost is the pickup arrival time, solved optimally, and
             only pairs within the arrival-time limit are accepted.

Why greedy matching is no longer used. Stage 2 was deferred once and never built, and greedy
left two measurable defects:

1. Ranking by straight-line distance picks the driver who is geometrically nearer but has the
   slower route; when that driver's arrival time fails the limit, the order is dropped outright,
   on the reasoning that arrival time is monotone in distance. It is not: the road factor varies
   by more than a factor of three. Measured, several hundred of a few thousand dispatches were
   dropped that should not have been.
2. Starvation by order id: considering orders in ascending id lets an older order take a driver
   that a newer order needs more, sometimes its only feasible candidate, so the newer order dies
   or accepts someone far away.

Optimal matching handles both, because it minimises the total over all feasible pairs at once.

Greedy remains available through configuration: the specification requires keeping it as a
comparison baseline, and it is the fallback when the matrix grows too large.

Deterministic: the matrix is built in sorted order on both axes, with a stable tie-break.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from .entities import Actor, ActorState
from .geo import Grid, grid_disk, haversine_km

# cost of an infeasible pair: larger than any real arrival time, but finite, as the solver
# requires finite costs
INFEASIBLE = 1e6
# ceiling on the assignment problem size; beyond it the greedy fallback bounds the tick time
MAX_PAIRS = 200_000


@dataclass
class Assignment:
    order_id: int
    actor_id: int
    pickup_dist_km: float
    eta_min: float


def _speed_kmh(hour: int, speed_cfg: dict) -> float:
    def _val(x):
        if isinstance(x, (list, tuple)):
            return float(sum(x) / len(x))
        return float(x)

    if hour in set(speed_cfg["peak_hours"]):
        return _val(speed_cfg["peak"])
    if hour in set(speed_cfg["night_hours"]):
        return _val(speed_cfg["night"])
    return _val(speed_cfg["offpeak"])


def match_batch(
    open_orders: list,          # the orders still open
    idle_actors: list[Actor],
    grid: Grid,
    hour: int,
    speed_cfg: dict,
    disp_cfg: dict,
    speed_fn=None,              # effective km/h per cell and hour; None uses the hourly base
    detour: float = 1.0,        # detour factor, used when no per-pair factor is available
    factor_fn=None,             # the real road factor for a cell pair
) -> list[Assignment]:
    """Assign orders to drivers for one tick, returned in ascending order id.

    Stage 1 builds candidates from the grid; stage 2 minimises total arrival time. A driver
    takes at most one order per tick, and a pair beyond the arrival-time limit is never
    assigned.
    """
    if not open_orders or not idle_actors:
        return []

    k_max = int(disp_cfg["candidate_ring_k_max"])
    eta_max = float(disp_cfg["eta_max_min"])
    mode = str(disp_cfg.get("matching", "batch")).lower()

    orders = sorted(open_orders, key=lambda o: o.order_id)
    actors = sorted(idle_actors, key=lambda a: a.actor_id)
    idx_of = {a.actor_id: j for j, a in enumerate(actors)}

    by_cell: dict[str, list[Actor]] = {}
    for a in actors:
        by_cell.setdefault(a.cell, []).append(a)

    speed = {}

    def _eta(order, a) -> float:
        """Real pickup arrival time: distance times the pair's own road factor over the effective
        speed.

        Using the per-pair factor rather than bare straight-line distance is the whole point:
        the bare version is where the wrong-driver defect comes from.
        """
        h = speed.get(order.pickup_cell)
        if h is None:
            h = speed_fn(order.pickup_cell, hour) if speed_fn is not None                 else _speed_kmh(hour, speed_cfg)
            speed[order.pickup_cell] = h
        d = haversine_km(a.lat, a.lon, order.pickup_lat, order.pickup_lon)
        fac = factor_fn(a.cell, order.pickup_cell) if factor_fn is not None else detour
        return d * fac / h * 60.0, d

    # --- Stage 1: candidates from the hex grid, with arrival time and distance ---
    cand: list[list[tuple[int, float, float]]] = []   # in the order of `orders`
    for order in orders:
        row: list[tuple[int, float, float]] = []
        for cell in grid_disk(order.pickup_cell, k_max):
            for a in by_cell.get(cell, []):
                eta, d = _eta(order, a)
                if eta <= eta_max:
                    row.append((idx_of[a.actor_id], eta, d))
        cand.append(row)

    n_o, n_d = len(orders), len(actors)
    use_batch = mode != "greedy" and n_o > 1 and n_d > 1 and n_o * n_d <= MAX_PAIRS

    if use_batch:
        # --- Stage 2: bipartite matching on arrival time; infeasible pairs cost the sentinel ---
        cost = np.full((n_o, n_d), INFEASIBLE, dtype=float)
        dist = np.zeros((n_o, n_d), dtype=float)
        for i, row in enumerate(cand):
            for j, eta, d in row:
                if eta < cost[i, j]:          # a pair may appear once; keep the smallest
                    cost[i, j] = eta
                    dist[i, j] = d
        rows, cols = linear_sum_assignment(cost)
        out = [Assignment(orders[i].order_id, actors[j].actor_id,
                          round(float(dist[i, j]), 3), round(float(cost[i, j]), 2))
               for i, j in zip(rows, cols) if cost[i, j] < INFEASIBLE]
        return sorted(out, key=lambda x: x.order_id)

    # --- Fallback: greedy on arrival time, the comparison baseline and the large-problem path ---
    taken: set[int] = set()
    out = []
    for i, order in enumerate(orders):
        best = None
        for j, eta, d in sorted(cand[i], key=lambda t: (t[1], actors[t[0]].actor_id)):
            if j in taken:
                continue
            best = (j, eta, d)
            break
        if best is None:
            continue
        j, eta, d = best
        taken.add(j)
        out.append(Assignment(order.order_id, actors[j].actor_id, round(d, 3), round(eta, 2)))
    return out
