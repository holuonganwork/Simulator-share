"""The demand generator: a deterministic exogenous trace of orders, given a seed.

The exogenous trace is the list of orders — time, pickup cell, dropoff cell, distance and gross
fare — generated before the simulation runs, and shared by every arm. That sharing is what makes
paired comparisons possible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import Config
from .geo import Grid, cell_distance_km, grid_disk, haversine_km, sample_point_in_cell
from .policy import PolicyBundle


@dataclass(frozen=True)
class Order:
    order_id: int
    t_min: float  # minutes since midnight
    pickup_cell: str
    drop_cell: str
    dist_km: float
    gross_vnd: int
    patience_min: float = 5.0  # the passenger cancels if unmatched after this many minutes; exogenous and stream-safe
    # the continuous street address inside the cell, used for movement and display
    pickup_lat: float = 0.0
    pickup_lon: float = 0.0
    drop_lat: float = 0.0
    drop_lon: float = 0.0
    vehicle_class: str = "A_COMPACT"


def _cell_weights(grid: Grid, cfg: Config) -> dict[str, float]:
    """Demand weight per core cell, a linear combination of a population proxy and a
    points-of-interest score, normalised to sum to one.

    The population proxy is uniform across core cells, for want of per-cell population data.
    The interest score is the total weight of the landmarks falling in the cell.
    """
    a = float(cfg.get("demand.zone_weight_pop_coeff"))
    b = float(cfg.get("demand.zone_weight_poi_coeff"))
    type_w = cfg.get("demand.poi_type_weights")
    default_w = float(type_w.get("default", 1.0))

    poi_score: dict[str, float] = {c: 0.0 for c in grid.core_cells}
    for p in grid.pois:
        if p.cell in poi_score:
            poi_score[p.cell] += float(type_w.get(p.kind, default_w))

    n = len(grid.core_cells)
    pop_each = 1.0 / n if n else 0.0
    total_poi = sum(poi_score.values()) or 1.0

    raw: dict[str, float] = {}
    for c in grid.core_cells:
        raw[c] = a * pop_each + b * (poi_score[c] / total_poi)
    s = sum(raw.values()) or 1.0
    return {c: w / s for c, w in raw.items()}


def _hour_intensity(cfg: Config) -> dict[int, float]:
    """Relative hourly weights, restricted to the run window and normalised to sum to one."""
    weights = cfg.get("demand.hour_weights")
    start_min = int(cfg.get("time.start_min"))
    end_min = int(cfg.get("time.end_min"))
    start_h, end_h = start_min // 60, end_min // 60
    kept = {int(h): float(w) for h, w in weights.items() if start_h <= int(h) < end_h}
    s = sum(kept.values()) or 1.0
    return {h: w / s for h, w in kept.items()}


def expected_demand_field(grid: Grid, cfg: Config) -> dict[int, dict[str, float]]:
    """Expected orders per hour and cell, taken from configuration rather than the realised
    trace.

    The rate is orders per day times the hour's share times the cell's weight.

    This is a legitimate source for an actor's beliefs, standing in for an earlier field built
    from the run's own orders, which leaked information from the future. Deterministic in the
    configuration and independent of the seed."""
    orders_per_day = float(cfg.get("demand.orders_per_day"))
    hour_share = _hour_intensity(cfg)
    cell_w = _cell_weights(grid, cfg)
    return {h: {c: orders_per_day * share * w for c, w in cell_w.items()}
            for h, share in hour_share.items()}


def generate_orders(grid: Grid, cfg: Config, policy: PolicyBundle, seed: int, env=None,
                    road=None) -> list[Order]:
    """Generate the exogenous trace deterministically from the seed.

    Orders per hour are Poisson in the daily total times the hour share times the environment's
    demand factor, plus any event contribution. Each order takes its pickup cell from the zone
    weights plus the event addend, its dropoff by distance decay, its distance from a lognormal
    draw, and its gross fare from policy. With no environment there is no environmental
    variation, which is the baseline.
    """
    rng = np.random.default_rng(seed)
    # A separate random stream for point coordinates, kept apart from the exogenous trace, so
    # that adding coordinates does not disturb the existing trace and the baseline metrics stay
    # unchanged.
    rng_pt = np.random.default_rng(seed ^ 0xC0FFEE)
    # Hỗ trợ cấu hình động từ demand_profile (gsm_sim/event)
    if env is not None and getattr(env, "demand_profile", None) is not None:
        orders_per_day = float(env.demand_profile.effective_orders_per_day)
        hour_share = dict(env.demand_profile.hourly_shares)
    else:
        orders_per_day = float(cfg.get("demand.orders_per_day"))
        hour_share = _hour_intensity(cfg)
    cell_w = _cell_weights(grid, cfg)
    cells = list(cell_w.keys())
    base_probs = np.array([cell_w[c] for c in cells], dtype=float)

    med = float(cfg.get("demand.trip_km_median"))
    sigma = float(cfg.get("demand.trip_km_sigma"))
    km_max = float(cfg.get("demand.trip_km_max"))
    buffer_k = int(cfg.get("world.buffer_ring_k"))
    km_per_cell = float(cfg.get("demand.drop_km_per_cell", 0.35))
    softness = float(cfg.get("demand.drop_softness_km", 0.5))
    # how far the dropoff follows demand; zero is pure distance and reproduces the old trace
    drop_alpha = float(cfg.get("demand.drop_demand_alpha", 0.0) or 0.0)
    mu = math.log(med)  # lognormal median = exp(mu)

    pat_med = float(cfg.get("dispatcher.patience_median_min", 3.0))
    pat_sigma = float(cfg.get("dispatcher.patience_sigma", 0.5))
    pat_max = float(cfg.get("dispatcher.patience_max_min", 10.0))
    pat_mu = math.log(pat_med)

    detour_f = float(cfg.get("demand.detour_factor", 1.3))   # fallback when the matrix lacks the pair
    # An hourly-interpolation flag here was dead: read and never used, while the config
    # declared it true with a comment promising interpolation, which misled anyone reading the
    # config. Both sides were removed. Interpolating intensity between hours, if wanted, is a
    # calibration change requiring a thirty-seed regate, not a flag flip.
    hours_sorted = sorted(hour_share.keys())

    car_sim = cfg.get("car_simulation", {})
    is_car_mode = bool(car_sim.get("enabled", False) and car_sim.get("primary_mode", False))
    rng_cls = np.random.default_rng(seed ^ 0xCA8)
    car_classes = ["A_COMPACT", "B_SEDAN", "C_SUV", "D_LUXURY"]
    car_probs = [0.55, 0.20, 0.15, 0.07]
    car_probs = [p / sum(car_probs) for p in car_probs]  # no Limo in the fleet: renormalise

    orders: list[Order] = []
    oid = 0
    for hour in hours_sorted:
        share = hour_share[hour]
        # the environment's demand factor at mid-hour, clamped
        env_f = env.demand_factor(hour * 60 + 30) if env is not None else 1.0
        lam = orders_per_day * share * env_f
        n_h = int(rng.poisson(lam))
        for _ in range(n_h):
            t_min = hour * 60 + rng.uniform(0, 60)
            # times are spread uniformly within the hour; intensity is not interpolated between hours
            pickup = _sample_pickup(cells, base_probs, grid, env, t_min, rng)
            target_km = float(min(km_max, math.exp(rng.normal(mu, sigma))))
            drop = _sample_drop(grid, pickup, target_km, buffer_k, km_per_cell, softness, rng,
                                demand_w=cell_w, alpha=drop_alpha)
            # Lộ trình bị ngập sâu cấm xe tuyệt đối (thiên tai/ngập lụt) -> bỏ qua đơn này
            if env is not None and hasattr(env, "is_route_passable"):
                if not env.is_route_passable(t_min, pickup, drop):
                    continue
            patience = float(min(pat_max, math.exp(rng.normal(pat_mu, pat_sigma))))
            p_lat, p_lon = sample_point_in_cell(grid, pickup, rng_pt)
            d_lat, d_lon = sample_point_in_cell(grid, drop, rng_pt)
            # The distance contract: distance is the straight line between the two chosen
            # endpoints. The lognormal draw only selects the dropoff cell, and the shape of the
            # distribution survives through the distance decay.
            # Fare, time and charge all derive from that distance times the detour, so there is
            # one source of distance.
            dist_km = haversine_km(p_lat, p_lon, d_lat, d_lon)
            # The fare is charged on the real driven distance, as the operator charges by
            # route kilometres rather than straight line. With no road matrix the old
            # straight-line path is kept.
            actual_detour = env.get_route_detour_factor(t_min, pickup, drop, detour_f) if (env is not None and hasattr(env, "get_route_detour_factor")) else detour_f
            fare_km = dist_km * road.factor(pickup, drop, actual_detour) if road else dist_km
            v_class = str(rng_cls.choice(car_classes, p=car_probs)) if is_car_mode else "A_COMPACT"
            gross = policy.gross_fare(fare_km, vehicle_class=v_class if is_car_mode else None)
            orders.append(Order(oid, t_min, pickup, drop, round(dist_km, 3), gross, round(patience, 2),
                                p_lat, p_lon, d_lat, d_lon, vehicle_class=v_class))
            oid += 1

    # events add trips around the venue: added, never multiplied
    if env is not None and env.events:
        oid = _add_event_orders(orders, oid, grid, cells, env, policy, mu, sigma, km_max,
                                buffer_k, km_per_cell, softness, pat_mu, pat_sigma, pat_max, rng, rng_pt,
                                road=road, detour_f=detour_f,
                                demand_w=cell_w, drop_alpha=drop_alpha)

    orders.sort(key=lambda o: (o.t_min, o.order_id))
    # renumber orders in time order, for determinism and stability
    return [Order(i, o.t_min, o.pickup_cell, o.drop_cell, o.dist_km, o.gross_vnd, o.patience_min,
                  o.pickup_lat, o.pickup_lon, o.drop_lat, o.drop_lon, vehicle_class=o.vehicle_class)
            for i, o in enumerate(orders)]


def _sample_pickup(cells, base_probs, grid, env, t_min, rng) -> str:
    """Choose the pickup cell from the zone weights, plus any event addend."""
    if env is None or not env.events:
        return cells[rng.choice(len(cells), p=base_probs)]
    add = np.array([env.event_addend(c, t_min) for c in cells], dtype=float)
    if add.sum() <= 0:
        return cells[rng.choice(len(cells), p=base_probs)]
    w = base_probs + add / max(1.0, add.sum())
    w = w / w.sum()
    return cells[rng.choice(len(cells), p=w)]


def _add_event_orders(orders, oid, grid, cells, env, policy, mu, sigma, km_max,
                      buffer_k, km_per_cell, softness, pat_mu, pat_sigma, pat_max, rng, rng_pt,
                      road=None, detour_f=1.3, demand_w=None, drop_alpha=0.0) -> int:
    """Generate the additional trips an event produces, timed within its window and located
    around the venue."""
    for ev in env.events:
        # the event's expected trips, spread over its time profile
        n_event = int(ev.attendance * ev.capture_rate)
        # spread across the ramp-up, the event and the egress
        t_lo = ev.t_start_min - ev.ramp_in_min
        t_hi = ev.t_end_min + ev.egress_min
        for _ in range(n_event):
            # sample the time from the profile by simple rejection
            for _try in range(5):
                t = rng.uniform(t_lo, t_hi)
                if rng.random() < env._event_time_profile(ev, t) * 60.0:
                    break
            cell = _event_pickup_cell(grid, cells, ev, rng)
            target_km = float(min(km_max, math.exp(rng.normal(mu, sigma))))
            drop = _sample_drop(grid, cell, target_km, buffer_k, km_per_cell, softness, rng,
                                demand_w=demand_w, alpha=drop_alpha)
            patience = float(min(pat_max, math.exp(rng.normal(pat_mu, pat_sigma))))
            p_lat, p_lon = sample_point_in_cell(grid, cell, rng_pt)
            d_lat, d_lon = sample_point_in_cell(grid, drop, rng_pt)
            dist_km = haversine_km(p_lat, p_lon, d_lat, d_lon)  # M0-9 contract
            fare_km = dist_km * road.factor(cell, drop, detour_f) if road else dist_km
            gross = policy.gross_fare(fare_km, vehicle_class="A_COMPACT")
            orders.append(Order(oid, t, cell, drop, round(dist_km, 3), gross,
                                round(patience, 2), p_lat, p_lon, d_lat, d_lon, vehicle_class="A_COMPACT"))
            oid += 1
    return oid


def _event_pickup_cell(grid, cells, ev, rng) -> str:
    from .geo import grid_disk
    disk = [c for c in grid_disk(ev.venue_cell, int(2 * ev.sigma_cells)) if grid.is_core(c)]
    return rng.choice(disk) if disk else ev.venue_cell


_ALLOWED_DROP_CACHE: dict[int, frozenset[str]] = {}


def _allowed_drop_cells(grid: Grid, buffer_k: int) -> frozenset[str]:
    """The origin-destination boundary: a dropoff must lie in the core or in the buffer ring
    around it. Precomputed once per grid."""
    key = id(grid)
    cached = _ALLOWED_DROP_CACHE.get(key)
    if cached is not None:
        return cached
    allowed: set[str] = set()
    for c in grid.core_cells:
        allowed.update(grid_disk(c, buffer_k))
    out = frozenset(allowed)
    _ALLOWED_DROP_CACHE.clear()  # hold one grid, to avoid leaking across grids
    _ALLOWED_DROP_CACHE[key] = out
    return out


def _sample_drop(grid: Grid, pickup: str, dist_km: float, buffer_k: int,
                 km_per_cell: float, softness: float, rng,
                 demand_w: dict[str, float] | None = None,
                 alpha: float = 0.0) -> str:
    """Choose the dropoff cell from a disc around the pickup, widened to reach the target
    distance.

    The disc is not capped at a fixed ring, as it once was: that cap blocked every trip beyond
    roughly two and a half kilometres while the fare was still computed from the lognormal draw,
    which broke the distance contract. The destination is constrained to the core or its buffer,
    and distance decay favours cells nearest the target.

    An honest consequence: the pilot area is only a few kilometres across, so the realised
    distance distribution is cut by geography. A median below the target is a physical limit of
    the pilot, labelled a calibration gap rather than papered over with an invented figure.

    Dropoffs follow demand. A purely distance-based version produced a world where dropoff
    location was *anti*-correlated with demand: the busiest cells received a third of pickups but
    a fiftieth of dropoffs, and four in five trips ended outside the core, costing a tenth of all
    time in deadheading. The mechanism was that the permitted dropoff area is several times the
    size of the core and distance decay pushes outwards, while real passengers travel *to* busy
    places — homes, offices, malls — which are where demand comes from.

    The correction multiplies by a demand factor blended linearly, `1 + alpha * (w / mean - 1)`,
    floored at zero and normalised by the mean over the disc, so each decision is scale-free.

    - At alpha zero the factor is exactly one in floating point, so the old trace is reproduced
      bit for bit and the branch below is skipped entirely. That is what keeps the old baseline
      comparable.
    - A power form was deliberately rejected: buffer cells have zero weight, and a power kills
      them completely at any positive alpha, putting every dropoff inside the core — an
      overcorrection in the opposite direction. The linear blend keeps buffer cells alive.
    """
    k = max(1, int(round(dist_km / km_per_cell)) + 2)
    allowed = _allowed_drop_cells(grid, buffer_k)
    disk = [c for c in grid_disk(pickup, k) if c in allowed]
    if len(disk) <= 1:
        return pickup
    dists = np.array([abs(cell_distance_km(grid, pickup, c) - dist_km) for c in disk])
    weights = np.exp(-dists / softness)
    if alpha > 0.0 and demand_w:
        dw = np.array([float(demand_w.get(c, 0.0)) for c in disk])
        mean = dw.mean()
        if mean > 0.0:
            mult = np.maximum(0.0, 1.0 + alpha * (dw / mean - 1.0))
            if mult.sum() > 0.0:            # all zero, which cannot happen with a positive mean; defensive
                weights = weights * mult
    weights = weights / weights.sum()
    return disk[rng.choice(len(disk), p=weights)]
