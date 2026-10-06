"""Tổng hợp event log + actor state → bảng metrics cơ bản (slice v0).

Metrics đầy đủ 3 tầng ở T-020. Ở đây: sanity đối chiếu spec.
"""

from __future__ import annotations

from statistics import median, mean

from .entities import Actor


def summarize(result) -> dict:
    actors: list[Actor] = result.actors
    orders = result.orders
    events = result.events

    n_orders = len(orders)
    n_completed = sum(a.orders_completed for a in actors)
    n_declined = sum(1 for e in events if e.kind == "order_declined")
    n_expired = sum(1 for e in events if e.kind == "order_expired")
    n_stranded = sum(a.stranded_count for a in actors)

    trips = [a.trips_done for a in actors]
    payouts = [a.payout_vnd for a in actors]
    ft = [a for a in actors if (a.shift_end_min - a.shift_start_min) >= 8 * 60]

    swap_waits = sorted(e.detail.get("wait_min", 0.0) for e in events if e.kind == "swap_done")
    pickup_etas = sorted(e.detail.get("eta_min", 0.0) for e in events if e.kind == "pickup")

    # utilization = occupied / thời gian THỰC SỰ tìm việc (online trừ nghỉ/sạc) — target FT 45-55%
    def _working(a):
        return max(1e-6, a.online_min - a.rest_min - a.charge_min)
    utils_ft = [a.occupied_min / _working(a) for a in ft]

    def _pctl(sorted_vals: list[float], q: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
        return sorted_vals[idx]

    return {
        "seed": result.seed,
        "orders_total": n_orders,
        "orders_completed": n_completed,
        "served_rate": round(n_completed / n_orders, 3) if n_orders else 0.0,
        "unserved_rate": round(1 - n_completed / n_orders, 3) if n_orders else 0.0,
        "orders_declined": n_declined,
        "orders_expired": n_expired,
        "battery_stranded": n_stranded,
        "trips_per_actor_median": median(trips) if trips else 0,
        "trips_per_actor_mean": round(mean(trips), 1) if trips else 0,
        "trips_fulltime_median": median([a.trips_done for a in ft]) if ft else 0,
        "payout_per_actor_median": int(median(payouts)) if payouts else 0,
        "payout_fulltime_median": int(median([a.payout_vnd for a in ft])) if ft else 0,
        "utilization_ft_median": round(median(utils_ft), 3) if utils_ft else 0.0,
        "pickup_eta_median": round(median(pickup_etas), 2) if pickup_etas else 0.0,
        "pickup_eta_p90": round(_pctl(pickup_etas, 0.9), 2),
        "swap_events": len(swap_waits),
        "swap_wait_median": round(median(swap_waits), 1) if swap_waits else 0.0,
        "swap_wait_p90": round(_pctl(swap_waits, 0.9), 1),
        "swap_wait_max": round(max(swap_waits), 1) if swap_waits else 0.0,
        "n_actors": len(actors),
        "n_fulltime": len(ft),
    }


def trips_by_hour(result) -> dict[int, int]:
    out: dict[int, int] = {}
    for e in result.events:
        if e.kind == "dropoff":
            h = int(e.t_min // 60) % 24
            out[h] = out.get(h, 0) + 1
    return dict(sorted(out.items()))
