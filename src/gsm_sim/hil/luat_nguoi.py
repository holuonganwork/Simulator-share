"""Rules that apply only to the human driver: the second layer of separating the interactive
path from the simulation.

Nothing here runs when there is no human actor, and the fingerprint gate runs in exactly that
mode.

Why the module exists. The separation already held mechanically: the world contains four seams
guarded by a comparison against the human actor's id, and since the fingerprint gate runs without
one, every line inside those branches was already outside the measurement.

What was missing was a *readable* boundary. To know whether a line touched the fingerprint you had
to trace backwards for the enclosing condition. Nobody does that while making a quick fix, so
everything was labelled blocked to be safe, and four tasks stayed wrongly blocked for weeks under a
constraint that did not actually apply to them.

Gathering the rules into one named place makes "does this touch the fingerprint" answerable by file
path rather than by reading backwards.

The boundary: the world may *call* into this module and must *contain* none of it. The world keeps
its four seams, each a single call, and two gates enforce that — one checking the world contains no
such rules, the other that no simulation module imports this file.

Why not fork the whole simulation package instead: two copies drift apart within weeks and nobody
knows which is right. More importantly, the solvers must be one copy, because if the interactive
path ran its own the simulation would no longer prove anything about what ships. The split is by
layer with a gate, not by directory.
"""

from __future__ import annotations

from ..geo import cell_distance_km

# The tolerance for distance comparisons, named rather than scattered, so every comparison uses
# the same one.
_SAI_SO_KM = 1e-9


# ══════════════════════════════════════════════════════════════════════════════════════════
# Costing a repositioning decision
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# The question was whether repositioning still pays once its time and energy costs are counted,
# and the honest answer was that no layer compared the two at all.
#
# The most important fact about this block: under current policy both the swap fee and the cash
# cost per kilometre are zero, so the cash cost of an empty kilometre is exactly zero. Modelling
# the battery cost in money would evaluate to zero and silently drop the whole term — a change
# that passes its tests while doing nothing.
#
# In this world time is the only currency, and charge converts into time: each kilometre of charge
# brings the next swap forward, and a swap costs the trip to the station plus the queue.
#
# Why every constant is read from config: a money figure written into the code starts lying the
# moment policy changes, and no gate fires. Every piece below comes from the running world's own
# policy and configuration.

# Below this, earnings per minute are noise rather than a measurement: early in a shift one lucky
# trip yields a rate an order of magnitude above the truth.
_ONLINE_TOI_THIEU_PHUT = 30.0

# The safety factor: how far the benefit must exceed the cost before advising a move. Above one,
# because the benefit is an expectation that may not materialise while the cost is certain once
# the journey is made. That asymmetry belongs in the formula rather than in the explanation.
_HE_SO_AN_TOAN = 1.25


def _so(x, mac_dinh=None):
    """A float, or the default. A missing or malformed value never becomes a silent zero."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return mac_dinh
    return v if v == v else mac_dinh          # not a number falls back to the default


def gia_mot_cuoc_vnd(world) -> float | None:
    """What the driver receives for an average trip in this world.

    Payout rather than gross revenue: the two are kept separate, and using revenue here would
    inflate the benefit side by around a third, recommending journeys well beyond what is
    warranted.
    """
    cfg = getattr(world, "cfg", None)
    if cfg is None:
        return None
    base = _so(cfg.get("policy.base_fare_vnd"))
    per_km = _so(cfg.get("policy.per_km_vnd"))
    km = _so(cfg.get("demand.trip_km_median"))
    share = _so(cfg.get("policy.driver_share"))
    if getattr(world, "car_sim_enabled", False):
        car_classes = getattr(world, "car_classes", {})
        cinfo = car_classes.get("A_COMPACT", {})
        if cinfo:
            base = float(cinfo.get("base_fare_vnd", base or 20000))
            per_km = float(cinfo.get("per_km_vnd", per_km or 13000))
            share = float(cinfo.get("driver_share", share or 0.75))
    if None in (base, per_km, km, share) or share <= 0:
        return None
    return (base + per_km * km) * share


def doanh_thu_moi_phut_vnd(world, actor) -> float | None:
    """The value of one minute spent driving empty: the lower of two bounds.

    The measured rate is this driver's own earnings over their time online. The ceiling is what a
    perfectly busy driver would achieve, from the average trip value and the throughput per
    bucket.

    Taking the lower is caution in the direction of *not* silencing the channel: an underestimated
    cost makes the threshold easier to clear. That is the right safe direction, because this is
    the only channel with a measured effect on earnings.

    With little time online the ceiling is used rather than the measurement: a few minutes is not
    a measurement, and early in a shift it produces absurd figures.
    """
    gia = gia_mot_cuoc_vnd(world)
    if gia is None:
        return None
    cfg = getattr(world, "cfg", None)
    bucket = _so(getattr(getattr(world, "market", None), "bucket_min", None), 60.0) or 60.0
    # Service throughput, from the same source the market state uses to convert demand into
    # capacity. Read from the market object rather than from config again: two routes to one
    # number eventually diverge.
    tph = _so(getattr(getattr(world, "market", None), "trips_per_hour_est", None), 1.0) or 1.0
    tpd = tph * bucket / 60.0
    tran = gia * tpd / bucket

    online = _so(getattr(actor, "online_min", None), 0.0) or 0.0
    payout = _so(getattr(actor, "payout_vnd", None), 0.0) or 0.0
    if online < _ONLINE_TOI_THIEU_PHUT or payout <= 0:
        return tran
    return min(tran, payout / online)


def phut_pin_som_hon(world, actor, km: float) -> float:
    """The battery cost expressed in minutes: how much sooner these kilometres bring the next
    swap.

    This is the term a money-denominated model drops entirely, because the swap fee is zero.

    The derivation: the distance consumes a share of the pack, a full pack covers a known
    distance, and that distance buys a known number of driving minutes. The charge consumed here
    therefore converts directly into a fraction of one swap's cost.

    A driver who charges at home does not swap mid-shift, so this term is zero for them. Assigning
    them a figure anyway would count a cost that does not exist.
    """
    from ..entities import FleetType

    if getattr(actor, "fleet", None) is not FleetType.SWAP:
        return 0.0
    pct_km = _so(getattr(world, "_pct_per_km", lambda a: None)(actor))
    if not pct_km or pct_km <= 0:
        return 0.0
    cfg = getattr(world, "cfg", None)
    # A swap takes the service time at the cabinet, taken at the upper end of the range:
    # underestimating the battery cost means advising more travel than is warranted, and running
    # flat mid-shift is worse than an extra kilometre.
    phut_moi_lan = (_so(cfg.get("station.swap_time_s_max") if cfg else None, 120.0)
                    or 120.0) / 60.0
    return (km * pct_km / 100.0) * phut_moi_lan


def phat_rui_ro_soc_vnd(world, actor, km: float, dt_moi_phut: float) -> float:
    """A non-linear penalty for a journey that pushes the charge below the swap threshold.

    A kilometre at a comfortable charge is nearly free. The same kilometre near the threshold is
    quite different: it turns a smooth shift into one with an unplanned detour to a station,
    costing the trip and the queue, at a moment the driver did not choose.

    Linearising this erases exactly what makes it dangerous.
    """
    from ..entities import FleetType

    if getattr(actor, "fleet", None) is not FleetType.SWAP:
        return 0.0
    soc = _so(getattr(actor, "soc_pct", None))
    pct_km = _so(getattr(world, "_pct_per_km", lambda a: None)(actor))
    cfg = getattr(world, "cfg", None)
    nguong = _so(cfg.get("vehicle.swap_soc_threshold_pct") if cfg else None, 20.0) or 20.0
    if soc is None or not pct_km or pct_km <= 0:
        return 0.0
    sau = soc - km * pct_km
    if sau >= nguong:
        return 0.0                            # above the threshold: no penalty
    # Already below the threshold before setting off, this journey is not what pushed them
    # there, and charging it would charge twice for one event.
    if soc < nguong:
        return 0.0
    # The cost is one unplanned detour to swap, priced through the value of a minute. An
    # unplanned detour costs the trip to the station and the queue, not only the time at the
    # cabinet.
    phut_re = ((_so(cfg.get("station.swap_time_s_max") if cfg else None, 120.0) or 120.0) / 60.0
               + (_so(cfg.get("station.wait_cap_min") if cfg else None, 60.0) or 60.0) * 0.25)
    return phut_re * float(dt_moi_phut or 0.0)


def loi_ich_doi_o(world, actor, o_dich: str, now: float) -> dict | None:
    """The benefit and the cost, in money, of leaving the current cell for another. Absent when
    there is not enough data.

    The benefit comes from the real queue: the supply-demand ratio is expected demand over
    effective supply, which is trips per driver per bucket. The difference between two cells is
    exactly how many more trips standing there would bring. It is the figure the advisor already
    ranks by, so no new source of numbers is introduced.

    It is discounted by travel time: the ratio covers a whole bucket, and a journey of several
    minutes leaves only the remainder of that bucket to benefit from. Taking it whole would credit
    the driver for time spent on the road.

    The cost has four terms, and the largest is not the one people expect. Measured on the current
    configuration for a journey of a few kilometres, travel time dominates in the ordinary case,
    the battery term is negligible, the cash term is zero under current policy, and the
    charge-risk term is zero at a comfortable charge but becomes the largest of all near the swap
    threshold.

    So in this world what makes a reposition expensive is the *risk* of running flat, not the
    charge consumed. Linearising that erases the dangerous part.
    """
    market = getattr(world, "market", None)
    grid = getattr(world, "grid", None)
    if market is None or grid is None or not o_dich:
        return None
    try:
        view = market.view(now) or {}
    except Exception:  # noqa: BLE001 - an unreadable market is not guessed at
        return None
    cells = view.get("cells") or {}
    o_dang = getattr(actor, "cell", None)
    # An absent ratio means the supply figure is *unavailable*, not that supply is zero. Guessing
    # here would build an entire economic model on a blank.
    if _so((cells.get(o_dich) or {}).get("sd_ratio")) is None:
        return None

    # The marginal-driver correction. Without it the benefit side is inflated.
    #
    # A cell's ratio is demand over the supply *currently there*. But on arriving, the driver is
    # one more competitor, so their real share is demand over supply plus one.
    #
    # Measured, this matters: with effective supply at zero in every cell, the ratio equalled the
    # whole hour's demand and the difference reached several trips' worth of money. With the
    # correction, an empty cell still looks good, but the figure is what the driver would actually
    # receive.
    #
    # In the cell they are *already standing in*, they are already part of the supply, so the
    # denominator is the supply itself with a floor of one, not one more. Adding on both sides
    # would penalise staying and tilt the balance toward moving.
    def _phan_cua_bac(o: str | None, dang_o_do: bool) -> float:
        c = cells.get(o) or {}
        cau = _so(c.get("expected_demand"), 0.0) or 0.0
        cung = _so(c.get("supply_effective"), 0.0) or 0.0
        mau = max(1.0, cung) if dang_o_do else (cung + 1.0)
        return cau / mau

    sd_dich = _phan_cua_bac(o_dich, dang_o_do=False)
    sd_dang = _phan_cua_bac(o_dang, dang_o_do=True)

    dt_phut = doanh_thu_moi_phut_vnd(world, actor)
    gia_cuoc = gia_mot_cuoc_vnd(world)
    if dt_phut is None or gia_cuoc is None:
        return None

    km = _so(cell_distance_km(grid, o_dang, o_dich), 0.0) or 0.0
    gio = int(now // 60) % 24
    try:
        phut_di = float(world._travel_min(km, gio, o_dang))
    except Exception:  # noqa: BLE001
        return None

    bucket = _so(getattr(market, "bucket_min", None), 60.0) or 60.0
    chiet_khau = max(0.0, bucket - phut_di) / bucket

    loi = max(0.0, sd_dich - sd_dang) * chiet_khau * gia_cuoc

    cfg = getattr(world, "cfg", None)
    tien_mat_km = _so(cfg.get("vehicle.cash_cost_vnd_per_km") if cfg else None, 0.0) or 0.0
    chi = (phut_di * dt_phut
           + phut_pin_som_hon(world, actor, km) * dt_phut
           + km * tien_mat_km
           + phat_rui_ro_soc_vnd(world, actor, km, dt_phut))

    return {"km": round(km, 3), "phut_di": round(phut_di, 2),
            "chiet_khau": round(chiet_khau, 3),
            "loi_vnd": round(loi, 1), "chi_vnd": round(chi, 1),
            "bo": loi >= chi * _HE_SO_AN_TOAN}


# Distinguishes a decided cost-benefit from one that lacked the data to decide. Using absence for
# both would swallow the advice whenever the market was short of figures — silent for want of
# knowledge, while a reader would take it for silent because it was not worth saying.
_KHONG_QUYET = object()


class LuatNguoiChoi:
    """The human driver's rule set; one instance lives alongside one world.

    It takes the world rather than its parts: these rules read the grid, the market state and the
    advice configuration, and passing those three separately would lengthen the signature without
    decoupling anything real.
    """

    def __init__(self, world) -> None:
        self.w = world

    # ------------------------------------------------------------------ seam: target cell
    def o_dich_hop_le(self, actor, o_dich: str | None, now: float) -> str | None:
        """Enforce the distance rail on the cell the advisor recommends.

        Returns a valid cell, possibly a nearer one, or nothing when no cell is worth
        recommending.

        Why it is needed: the config declares a hard rail — how many kilometres further than the
        nearest candidate a driver may be sent — but it lived only in the choice of the preferred
        cell. The allocator then redistributes whatever is contested and receives no distance at
        all; its inputs are a target, a charge priority and a tier gap. Once it pushes a driver off
        their preferred cell, no distance constraint remains.

        Measured on one actor over dozens of assignments, the chosen cell was typically a couple
        of kilometres away while the nearest candidate at the same moment was a few hundred
        metres, and it was further in nearly every case — beyond the declared rail, in an area
        barely two kilometres across.

        Why it applies to the human driver only: changing the allocator would change the behaviour
        of every autonomous actor, which is a different measurement baseline requiring a fresh
        paired run, not a patch.

        Why it falls back to a nearer cell rather than going silent: silence costs the one advisor
        channel with a measured effect on earnings. The nearest candidate has still passed the
        supply-and-demand filter; it is simply not the best cell under a global allocation. For one
        person, near and good beats far and best.

        Why the *furthest* cell still inside the rail rather than the nearest: the rail is a
        ceiling, not a target, and jumping to the nearest discards the benefit the rail permits.
        """
        if o_dich is None:
            return None

        # ══════════════════════════════════════════════════════════════════════════════════
        # Filtering by cost and benefit, inside the distance rail
        # ══════════════════════════════════════════════════════════════════════════════════
        #
        # The order is deliberate and cannot be reversed:
        #
        #   1. cost and benefit choose among the cells worth going to, which is the real
        #      comparison;
        #   2. the distance rail remains the hard outer bound, because a wrong economic model can
        #      recommend a journey across the district, and the rail is what stops that.
        #
        # Removing the rail because a model now exists would place all trust in a new formula
        # whose benefit side rests on a demand rate built from a uniform population proxy.
        #
        # Measured over five seeds and a full day: the gate said "worth going" in about
        # ninety-eight percent of decisions, with a median benefit-to-cost ratio near eight.
        #
        # So when profit and loss are genuinely compared, the model says repositioning does pay,
        # and the distance rail is blocking advice that is economically correct. But that is a
        # statement about the *simulated* world rather than about the real city.
        #
        # And this gate makes the recommended distances *longer*: the tail rose by more than half,
        # because it removes every fallback. A gate that says go almost every time barely exists,
        # and it is bought with that extra distance. It is therefore off by default, behind its own
        # key rather than sharing one with the distance weight, which pulls the other way.
        if self._bat_cost_benefit():
            ra = self._loc_theo_loi_ich(actor, o_dich, now)
            if ra is not _KHONG_QUYET:
                return ra

        tran_them = float(getattr(self.w.advice, "positioning_max_extra_km", 0.0) or 0.0)
        if tran_them <= 0:
            return o_dich
        ranked = list((self.w.market.view(now) or {}).get("ranked_cells") or [])
        if not ranked:
            return o_dich

        def cd(c: str) -> float:
            return cell_distance_km(self.w.grid, actor.cell, c)

        gan_nhat = min(ranked, key=lambda c: (cd(c), c))
        han = cd(gan_nhat) + tran_them + _SAI_SO_KM
        if cd(o_dich) <= han:
            return o_dich
        trong_han = [c for c in ranked if cd(c) <= han]
        if not trong_han:
            return None
        return max(trong_han, key=lambda c: (cd(c), c))

    # ------------------------------------------------------------- 6.4: cost/benefit
    def _bat_cost_benefit(self) -> bool:
        """The cost-benefit filter runs only behind its own flag, which is off by default.

        The flag is separate from the allocator's distance weight, because measurement showed the
        two pull in opposite directions: the allocator term shortens distances by choosing a
        nearer cell when a driver must be moved, while this gate lengthens them, judging almost
        everything worth going to and so removing every fallback. Sharing one flag would leave
        whoever enabled it unable to tell which they had chosen.

        Off by default for two reasons rather than general caution: the benefit side rests on a
        demand rate built from a uniform population proxy, so "almost always worth going" is a
        statement about the simulated world; and enabling it changes the demonstration's
        behaviour, which is the owner's decision.
        """
        return bool(getattr(self.w.advice, "cost_benefit_vi_tri", False))

    def _dem(self, khoa: str) -> None:
        """Counters for the diagnostics table."""
        d = getattr(self.w, "_dem_cost_benefit", None)
        if d is None:
            d = self.w._dem_cost_benefit = {}
        d[khoa] = d.get(khoa, 0) + 1

    def _loc_theo_loi_ich(self, actor, o_dich: str, now: float):
        """Whether the target cell is worth going to; if not, fall back to the furthest that is.

        It returns the undecided marker when there is not enough data, in which case the caller
        falls back to the distance rail rather than guessing. Unavailable supply is not zero
        supply.
        """
        self._dem("xet")
        r = loi_ich_doi_o(self.w, actor, o_dich, now)
        if r is None:
            self._dem("thieu_du_lieu")
            return _KHONG_QUYET
        if r["bo"]:
            self._dem("giu")
            return o_dich

        ranked = list((self.w.market.view(now) or {}).get("ranked_cells") or [])
        # Fall back to the furthest cell still worth going to. The rail is a ceiling rather than
        # a target, and jumping straight to the nearest cell discards the benefit still
        # permitted.
        ung_vien = []
        for c in ranked:
            if c == o_dich:
                continue
            rc = loi_ich_doi_o(self.w, actor, c, now)
            if rc is not None and rc["bo"]:
                ung_vien.append((rc["km"], c))
        if not ung_vien:
            self._dem("im")
            return None
        self._dem("lui")
        return max(ung_vien)[1]

    # ------------------------------------------------------------------ seam: standby
    def cho_phep_ghi_de_standby(self) -> bool:
        """May the standby plan override a human driver's action?

        No. The human actor heard the recommendation at the stop and decided for themselves.
        Overriding after they have pressed something is the system acting on the driver's behalf,
        which the product boundary forbids.

        It returns a named constant rather than a bare false in the world, so that whoever wants
        to change it reads why first.
        """
        return False
