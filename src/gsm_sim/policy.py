"""The simulation's mock policy bundle.

Every figure is read from configuration rather than hard-coded. It provides fares, payouts,
points per trip, daily bonus tiers and per-track costs. These are not real operator figures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config


@dataclass(frozen=True)
class PolicyBundle:
    base_fare_vnd: int
    base_km: float
    per_km_vnd: int
    driver_share: float
    point_peak: int
    point_normal: int
    point_window_hours: frozenset[int]
    point_peak_hours: frozenset[int]
    day_bonus_tiers: tuple[tuple[int, int], ...]  # (points, bonus) in ascending order
    bonus_min_acceptance: float
    bonus_min_completion: float
    version: str
    # Weekly quota and clawback. None means the real figures are unknown, and a solver must not
    # infer a threshold. Optional so that every older config still builds an identical bundle.
    weekly_quota: dict | None = None
    car_classes: dict = field(default_factory=dict)

    def to_core_record(self) -> dict:
        """The policy record in the shape the core layer reads.

        The simulation and the core each have their own bundle class with different accessors,
        and the shift solver lives in the core. This function is the single conversion point,
        used by the mock generator too, so that the two layers can never quote different policy
        figures.

        Identity fields belonging to the wider schema are added by the caller; only the policy
        content is produced here.
        """
        return {
            "version": self.version,
            "fare": {"base_vnd": int(self.base_fare_vnd), "base_km": float(self.base_km),
                     "per_km_vnd": int(self.per_km_vnd)},
            "driver_share": float(self.driver_share),
            "points": {"peak": int(self.point_peak), "normal": int(self.point_normal),
                       "peak_hours": sorted(int(h) for h in self.point_peak_hours),
                       "window_hours": sorted(int(h) for h in self.point_window_hours)},
            "day_bonus_tiers": [[int(p), int(v)] for p, v in self.day_bonus_tiers],
            "thresholds": {"bonus_min_acceptance": float(self.bonus_min_acceptance),
                           "bonus_min_completion": float(self.bonus_min_completion),
                           "forced_accept_below": 0.5},
            # The missing link. The schema carried the weekly quota, the solver knew how to
            # compute the clawback, and the read view mirrored the field, but no producer ever
            # wrote it, so the quota was always absent and the solver always reported it
            # unavailable. A complete solver was dead for want of one emit line here.
            #
            # This is the single conversion point, so emitting here makes the generator, the UI
            # and the advice bridge all see it at once. Costs are still dead in the same way:
            # schema, resolver, consumer and tests all exist, but this line does not.
            **({"weekly_quota": dict(self.weekly_quota)} if self.weekly_quota else {}),
            # Per-class tariff the simulation actually charges (`fare_for` / `driver_share_for`).
            # Without it the L0 bundle quoted only the flat fallback fare above.
            **({"car_classes": self._car_classes_record()} if self.car_classes else {}),
        }

    def _car_classes_record(self) -> dict:
        """Only the keys the L0 schema declares for `car_classes` (config keeps extra display keys)."""
        out: dict = {}
        for name, c in self.car_classes.items():
            rec = {
                "base_vnd": int(c.get("base_fare_vnd", c.get("base_vnd", self.base_fare_vnd))),
                "base_km": float(c.get("base_km", self.base_km)),
                "per_km_vnd": int(c.get("per_km_vnd", self.per_km_vnd)),
                "driver_share": float(c.get("driver_share", self.driver_share)),
            }
            if "battery_kwh" in c:
                rec["battery_kwh"] = float(c["battery_kwh"])
            if "kwh_per_100km" in c:
                rec["kwh_per_100km"] = float(c["kwh_per_100km"])
            out[str(name)] = rec
        return out

    @classmethod
    def from_config(cls, cfg: Config) -> "PolicyBundle":
        p = cfg.get("policy")
        tiers = tuple((int(pt), int(vnd)) for pt, vnd in p["day_bonus_tiers"])
        car_classes = {}
        car_sim = cfg.get("car_simulation", {})
        if car_sim and car_sim.get("classes"):
            car_classes = dict(car_sim["classes"])
        elif p.get("car_classes"):
            car_classes = dict(p["car_classes"])
        return cls(
            base_fare_vnd=int(p["base_fare_vnd"]),
            base_km=float(p["base_km"]),
            per_km_vnd=int(p["per_km_vnd"]),
            driver_share=float(p["driver_share"]),
            point_peak=int(p["point_peak"]),
            point_normal=int(p["point_normal"]),
            point_window_hours=frozenset(int(h) for h in p["point_window_hours"]),
            point_peak_hours=frozenset(int(h) for h in p["point_peak_hours"]),
            day_bonus_tiers=tiers,
            bonus_min_acceptance=float(p["bonus_min_acceptance"]),
            bonus_min_completion=float(p["bonus_min_completion"]),
            version=str(cfg.get("meta.policy_bundle_version", "sim-policy-v0")),
            # A lookup rather than an index: a config that declares no quota yields "unknown",
            # for which the solver has its own branch. Requiring the key here would break every
            # older config and every minimal test config for no gain.
            weekly_quota=p.get("weekly_quota") or None,
            car_classes=car_classes,
        )

    # --- Fare & payout ---

    def gross_fare(self, dist_km: float, vehicle_class: str | None = None) -> int:
        """Gross fare for a trip of the given distance, supporting vehicle class pricing."""
        if vehicle_class and self.car_classes and vehicle_class in self.car_classes:
            c = self.car_classes[vehicle_class]
            base_vnd = int(c.get("base_fare_vnd", c.get("base_vnd", self.base_fare_vnd)))
            base_km = float(c.get("base_km", self.base_km))
            per_km_vnd = int(c.get("per_km_vnd", self.per_km_vnd))
            extra = max(0.0, dist_km - base_km)
            return int(round(base_vnd + extra * per_km_vnd))
        extra = max(0.0, dist_km - self.base_km)
        return int(round(self.base_fare_vnd + extra * self.per_km_vnd))

    def driver_payout_from_gross(self, gross_vnd: int, vehicle_class: str | None = None) -> int:
        """The driver's share of the gross fare, before any bonus."""
        if vehicle_class and self.car_classes and vehicle_class in self.car_classes:
            share = float(self.car_classes[vehicle_class].get("driver_share", self.driver_share))
            return int(round(gross_vnd * share))
        return int(round(gross_vnd * self.driver_share))

    # --- Points and bonuses ---

    def trip_points(self, order_hour: int) -> int:
        """Points for a trip, by the hour the passenger ordered."""
        if order_hour not in self.point_window_hours:
            return 0
        if order_hour in self.point_peak_hours:
            return self.point_peak
        return self.point_normal

    def day_bonus(self, points: int, acceptance: float, completion: float) -> int:
        """The daily bonus: the highest tier reached, and only when the rate condition is met."""
        if acceptance < self.bonus_min_acceptance or completion < self.bonus_min_completion:
            return 0
        bonus = 0
        for tier_pts, tier_vnd in self.day_bonus_tiers:
            if points >= tier_pts:
                bonus = tier_vnd
        return bonus

    def next_tier_gap(self, points: int) -> tuple[int, int] | None:
        """Points short of the next tier and that tier's bonus, or None at the top tier."""
        for tier_pts, tier_vnd in self.day_bonus_tiers:
            if points < tier_pts:
                return (tier_pts - points, tier_vnd)
        return None
