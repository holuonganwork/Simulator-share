"""Spatiotemporal congestion, derived from the order distribution plus event route effects.

The modelling assumption is that the order distribution stands in for congestion: a cell busy
with orders at peak hour moves more slowly than an empty one.

Congestion combines with rain as a survival product in the world speed model:

    speed = base * (1 - r_rain) * (1 - r_cong)

r_cong lies in [0, cap] per cell and hour. An event's route effect adds locally around the
venue, also as a survival term rather than a multiplier on demand, which would double-count
against the event's own arrival rate.

Switchable off: disabling the field or setting the cap to zero yields r_cong = 0 and leaves the
baseline bit-identical.
"""

from __future__ import annotations

import math

from .config import Config
from .geo import grid_distance


class CongestionField:
    def __init__(self, orders: list, cfg: Config, env=None):
        cong = cfg.get("congestion", {}) or {}
        self.enabled = bool(cong.get("enabled", True))
        self.cap = float(cong.get("cap", 0.35))
        self.normalize = str(cong.get("normalize", "global_peak"))  # global_peak | hour_peak
        self.env = env

        # pickup counts per hour and cell: the basis for local density
        self._field: dict[int, dict[str, float]] = {}
        for o in orders:
            h = int(o.t_min // 60) % 24
            cells = self._field.setdefault(h, {})
            cells[o.pickup_cell] = cells.get(o.pickup_cell, 0.0) + 1.0
        self._global_peak = max((v for cells in self._field.values() for v in cells.values()), default=1.0)
        self._hour_peak = {h: (max(cells.values()) if cells else 1.0) for h, cells in self._field.items()}

    def _base_r(self, cell: str, hour: int) -> float:
        """Baseline congestion from local order density, normalised into [0, cap]."""
        if not self.enabled or self.cap <= 0.0:
            return 0.0
        dens = self._field.get(hour, {}).get(cell, 0.0)
        if self.normalize == "hour_peak":
            denom = self._hour_peak.get(hour, 1.0)
        else:
            denom = self._global_peak
        return self.cap * (dens / denom) if denom > 0 else 0.0

    def _route_r(self, cell: str, hour: int) -> float:
        """An event's route effect: a local closure or slowdown around the venue during its window.

        The term is (1 - speed_mult) weighted by a Gaussian in grid distance; several events
        combine as a survival product."""
        if self.env is None or not getattr(self.env, "events", None):
            return 0.0
        t = hour * 60 + 30
        surv = 1.0
        for ev in self.env.events:
            mult = getattr(ev, "route_speed_mult", 1.0)
            if mult >= 1.0:
                continue
            # the event window that affects routes, covering both ingress and egress
            t_lo = ev.t_start_min - ev.ramp_in_min
            t_hi = ev.t_end_min + ev.egress_min
            if not (t_lo <= t <= t_hi):
                continue
            gd = grid_distance(cell, ev.venue_cell)
            sig = getattr(ev, "route_sigma_cells", 1.5)
            if gd < 0 or gd > 4 * sig:
                continue
            g = math.exp(-(gd ** 2) / (2 * sig ** 2))
            r_ev = (1.0 - mult) * g
            surv *= (1.0 - r_ev)
        return 1.0 - surv

    def r(self, cell: str, hour: int) -> float:
        """Total congestion in [0, 0.95]: baseline and route effects combined as a survival product.

        Disabling the field returns zero for everything, including event route effects. The
        route effect belongs to the speed model, so it follows this switch; anything else would
        break the promise that turning the field off restores the baseline exactly."""
        if not self.enabled:
            return 0.0
        base = self._base_r(cell, hour)
        route = self._route_r(cell, hour)
        r = 1.0 - (1.0 - base) * (1.0 - route)
        return min(0.95, max(0.0, r))
