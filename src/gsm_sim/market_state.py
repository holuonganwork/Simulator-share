"""The market-state producer for the simulation: counting supply from the live world.

The computation itself lives in the core feature module as a pure function. This file does one
thing: translate the world's live state into supply figures and call that function. Caps and
ratios are deliberately not recomputed here, because two places computing one rule is a familiar
defect.

Three kinds of supply, and why they are distinguished:

  supply now              idle actors, per cell: the people who can receive an order immediately;
  incoming, travelling    actors with a movement target: about to become supply at that cell;
  incoming, advised       advice already issued and not yet acted on.

That third term is what distinguishes this from an operator's heat map. Without it, within one
bucket every driver who asks later sees the best cell still empty and is sent there — the fallacy
of composition, which has been measured here.

Labelled limits of this version:

- drivers resting or swapping will return to work but are not yet modelled as future supply;
- a driver carrying a passenger is not counted as supply arriving at the dropoff cell, the horizon
  being long and uncertain;
- expected demand is a proxy: the configured rate, not observed demand.
"""

from __future__ import annotations

from gsm_core.features.market_state import build_market_state

from .entities import ActorState

# Hour to ISO label. The simulation runs within one day, so only the hour varies; the base date
# is kept so the labels match the advice bridge's.
_BASE_DATE = "2026-07-01"


def count_supply(actors, pending_targets: dict[int, str] | None = None
                 ) -> tuple[dict[str, int], dict[str, int]]:
    """Count supply, present and incoming, per cell.

    The pending targets are positioning advice already issued and not yet acted on.

    The rule against double counting: an actor contributes at most one unit of supply to each
    ledger. An actor already moving is trusted by their movement target and their pending
    instruction is ignored — an instruction may be stale, whereas a position does not lie.

    Read this carefully before "fixing" it: an idle actor with a pending instruction appears in
    *both* ledgers deliberately. They are real supply where they stand, because the dispatcher can
    still match them there until they leave, *and* they are incoming supply at the target, which is
    what prevents crowding. The two ledgers mean different things and are not one total; a consumer
    summing them is being conservative, double-subtracting from the cap, and that is the chosen
    trade. A review once read the at-most-one rule as an invariant over the *sum* and proposed
    removing such actors from the present ledger, which would have made it lie about matchable
    supply. A test pins both ledgers and the conservation of each.
    """
    pending = pending_targets or {}
    now: dict[str, int] = {}
    inc: dict[str, int] = {}
    for a in actors:
        if a.state == ActorState.OFFLINE:
            continue
        target = getattr(a, "enroute_cell", None)
        if target:
            inc[target] = inc.get(target, 0) + 1
            continue
        if a.state == ActorState.IDLE and a.cell:
            now[a.cell] = now.get(a.cell, 0) + 1
        pend = pending.get(a.actor_id)
        if pend:
            inc[pend] = inc.get(pend, 0) + 1
    return now, inc


def count_idle_wait(actors) -> dict[str, tuple[int, float]]:
    """The idle count and median idle streak per cell.

    The sample is every idle actor: the platform observes all drivers, and coverage decides only
    who *receives* advice, which is filtered at the candidate stage rather than here.

    The idle streak is strictly read-only here. It feeds the impatience instinct, so any tidying
    of its semantics would change behaviour.
    """
    from statistics import median
    by_cell: dict[str, list[float]] = {}
    for a in actors:
        if a.state == ActorState.IDLE and a.cell:
            by_cell.setdefault(a.cell, []).append(float(a.idle_streak_min))
    return {c: (len(v), float(median(v))) for c, v in by_cell.items()}


def wait_fired_cells(stats: dict[str, tuple[int, float]],
                     threshold_min: float, min_idle: int) -> set[str]:
    """The preregistered trigger rule, on an absolute threshold rather than a percentile: a cell
    fires when it has enough idle drivers and their median streak exceeds the threshold. The
    minimum count is what stops a per-driver rule sneaking in through the back door, since with one
    driver the cell median is simply that person's own wait."""
    return {c for c, (n, med) in stats.items() if n >= min_idle and med > threshold_min}


def diem_phan_cau(ranked: list[str], cells: dict[str, dict], *,
                  khoang_cach, tam_voi_km: float,
                  o_hien_tai: str | None = None) -> dict[str, float]:
    """The share of expected demand a driver would win by standing in each candidate cell.

    Why this measure, after two earlier ones failed. Ranking by nearest cell was the baseline. A
    supply-demand ratio measured negative and its mechanism was never understood. Pooling by the
    dispatch ring died before it ran: at the configured ring size the candidate disc already covers
    most of the pilot area, so the pooled figure is nearly constant.

    All three ignored a measured fact: the dispatcher's candidate ring covers the great majority of
    the area, so a driver anywhere is a candidate for almost every order. What decides is the
    *ranking by arrival time*, since the dispatcher minimises exactly that.

    The score of a cell is therefore the sum over every demand point of that demand divided by one
    plus the effective supply nearer to it than the candidate cell.

    One source of data: the cell list already covers every cell with demand and already carries
    both expected demand and effective supply. No second source is built from the actor list,
    because two places computing one rule is a familiar defect.

    Competitors are counted by *effective* supply, present plus incoming, not by present supply
    alone. Incoming supply includes drivers already advised to go there, which is the mechanism
    that prevents crowding; counting only the present would let every driver in one bucket see the
    good cell as empty and all be sent to it.

    Demand is summed over *every* cell that has any, not only the ranked ones: a driver still
    receives orders from neighbouring cells, and restricting the sum would omit most of the demand.

    Two defects of the first version, caught by failing tests and recorded:

    A reach radius was missing entirely. Without it, a driver ten kilometres from the demand scored
    the same as one a hundred metres away whenever there was no competition. The dispatcher only
    draws candidates within its ring, and beyond it the demand is unreachable. The radius is taken
    from the dispatcher's own configuration so it cannot drift from the real rule.

    Competitors were counted with a strict comparison, which missed drivers standing in the target
    cell itself: they are the same distance from the demand as the arriving driver, so they are
    direct and equal competitors, and omitting them made a crowded cell look empty.

    A known simplification, stated rather than hidden: treating each order as an independent
    lottery. In reality the assignment is global — a driver near five orders still receives only
    one, and a driver ranked second on many can still receive one. The measure therefore
    overstates cells near *many* orders. It is a first-order proxy rather than a correct model,
    but it is the first measure to know about geometric competition at all, and it explains why the
    ratio measure failed.

    The function is pure: no random draws, and no state read beyond its parameters.

    A self-counting defect, since fixed: the driver counted themselves among their own
    competitors, because the supply set included the cell they were standing in — which they were
    about to leave. The bias had a direction, favouring staying near where they already were: a
    destination further than the current position was self-penalised fully while standing still was
    self-penalised by half, leaving a systematic margin against moving. The fix subtracts exactly
    one unit of supply at the current cell, floored at zero.

    A published result from before the fix is therefore a conservative estimate.

    A related case is still unfixed: effective supply counts an idle driver holding a pending
    instruction in both ledgers, so one person becomes two competitors in two cells. That is
    deliberate for the capacity cap and wrong for counting competitors. The window is narrow, from
    the instruction being issued until they move, so the suspected frequency is low; it is on the
    list to be instrumented.

    Performance, measured and acceptable: the cost is the ranked cells times demand cells times
    supply cells, per candidate, which comes to seconds per seed. A memoised static distance matrix
    was planned and turned out not to be needed; the inner loop is independent of the candidate and
    could still be hoisted for a further several-fold gain if required.
    """
    co_cau = [(X, float(v["expected_demand"]), int(v.get("supply_effective") or 0))
              for X, v in sorted(cells.items())
              if _co_cau(X, v)]

    # The driver being scored is not their own competitor: exactly one unit of supply is
    # subtracted at the cell they stand in, floored at zero, while colleagues in that cell still
    # count.
    # When their cell is absent from the demand map — a small share of turns, standing somewhere
    # with no demand — they were never counted in the first place, so subtracting nothing is the
    # correct semantics. The parameter defaults to the earlier behaviour, but the cell chooser
    # always forwards it, and a test guards against forgetting to.
    co_cung = []
    for Y, v in sorted(cells.items()):
        s = int(v.get("supply_effective") or 0)
        if Y == o_hien_tai:
            s = max(0, s - 1)
        if s > 0:
            co_cung.append((Y, s))

    diem: dict[str, float] = {}
    for C in ranked:
        tong = 0.0
        for X, lam, _s in co_cau:
            dC = float(khoang_cach(C, X))
            if dC > tam_voi_km:
                continue                       # beyond the candidate ring: this demand is out of reach
            # Someone strictly nearer counts fully; someone at equal distance counts half.
            # A strict comparison alone misses drivers standing in the target cell itself, since
            # they are the same distance from the demand as we would be, making a crowded cell
            # look empty. An inclusive comparison alone scores standing on the demand the same as
            # standing a kilometre away, because the same crowd is counted for both, and the
            # measure loses its "nearer wins" dimension.
            gan = 0.0
            for Y, s in co_cung:
                dY = float(khoang_cach(Y, X))
                if dY < dC:
                    gan += s
                elif dY == dC:
                    gan += 0.5 * s
            tong += lam / (1.0 + gan)          # the denominator is at least one, so never a division by zero
        diem[C] = tong
    return diem


def _co_cau(cell: str, v: dict) -> bool:
    if "expected_demand" not in v:
        # Absent is not zero. Treating it as zero silently removes that cell's demand from the
        # calculation.
        raise KeyError(f"ô {cell} thiếu `expected_demand` — thiếu dữ liệu, KHÔNG được coi là cầu 0")
    return float(v["expected_demand"] or 0.0) > 0.0


def chon_o_dich(o_hien_tai: str, ranked: list[str], cells: dict[str, dict], grid,
                *, khoang_cach, che_do: str, phat_km: float, tran_km_them: float,
                tam_voi_km: float | None = None) -> str:
    """Choose a destination cell for one driver among the cells that still have headroom.

    Why the function exists: the core feature module ranks cells by supply-demand ratio, but the
    world then chose the *nearest* of them, discarding that ranking. The ratio was only filtering
    which cells were eligible and had no say in the choice.

    Measured before writing this, over three seeds and several hundred candidate turns, the nearest
    cell differed from the best-ranked one in nearly nine cases out of ten, and choosing by value
    lifted the median ratio by about forty percent.

    Two modes. The nearest mode branches explicitly to the original expression; it is deliberately
    not implemented as an infinite distance penalty, because the ratio can be infinite or undefined
    in degenerate cases and the arithmetic would silently destroy the tie-break. The value mode
    maximises the ratio less a distance penalty among the cells within the rail, breaking ties by
    cell id.

    The distance rail: left free, the ninetieth percentile of extra distance was measured at
    several kilometres, and nobody should be sent that far empty on the strength of a proxy — the
    expected demand is a configured rate rather than observed demand.

    The rail measures kilometres *beyond the nearest candidate* rather than an absolute distance. A
    driver at the edge of the area may have every destination far away, and an absolute cap would
    block them all and leave the channel inert — the same failure as an over-tight rail elsewhere,
    where the channel could say nothing at all.

    The function is pure: it takes no generator, consumes no randomness and reads no state beyond
    its parameters, so the two arms cannot diverge in their random streams. The distance function
    is injected so that unit tests need no grid library.
    """
    if not ranked:
        raise ValueError("`ranked` rỗng — caller phải chặn trước (world.py đã có `if not cands`)")

    km = {c: float(khoang_cach(o_hien_tai, c)) for c in ranked}
    gan_nhat = min(ranked, key=lambda c: (km[c], c))

    if che_do == "nearest":
        return gan_nhat
    if che_do not in ("value", "share"):
        raise ValueError(
            f"`positioning_target_mode` không hợp lệ: {che_do!r} (chỉ nhận "
            f"'nearest'|'value'|'share'). Im lặng rơi về mặc định là cách một arm thí nghiệm chạy "
            f"nhầm nhánh mà không ai biết — xem `D-M3-20`.")

    hop_le = [c for c in ranked if km[c] - km[gan_nhat] <= tran_km_them]
    if not hop_le:                      # a negative or malformed cap cannot happen, but raising beats silence
        raise ValueError(f"`tran_km_them`={tran_km_them} loại SẠCH mọi ô đích")

    if che_do == "share":
        # No free parameter: the distance penalty plays no part here. An earlier cycle had to
        # sweep two of its levels; with a single arm and no tuning there is no room to sweep and
        # then pick the result that looks best.
        if tam_voi_km is None:
            # No silent default: without a reach radius the measure loses its distance dimension
            # entirely, and a driver ten kilometres from the demand scores the same as one a
            # hundred metres away whenever there is no competition.
            raise ValueError("`che_do='share'` đòi `tam_voi_km` — suy từ "
                             "`dispatcher.candidate_ring_k_max` để không trôi khỏi luật dispatcher")
        d = diem_phan_cau(hop_le, cells, khoang_cach=khoang_cach, tam_voi_km=tam_voi_km,
                          o_hien_tai=o_hien_tai)
        return max(hop_le, key=lambda c: (d[c], c))

    def _sd(c: str) -> float:
        v = cells.get(c, {})
        if "sd_ratio" not in v:
            # Absent is not zero. Treating it as zero silently removes the cell from every
        # choice.
            raise KeyError(f"ô {c} thiếu `sd_ratio` — thiếu dữ liệu, KHÔNG được coi là cầu = 0")
        return float(v["sd_ratio"])

    return max(hop_le, key=lambda c: (_sd(c) - phat_km * km[c], c))


class MarketStateProducer:
    """Build the view per bucket, with a cache.

    The cache is a performance necessity: the idle loop asks every couple of minutes for the whole
    fleet across a full day. But it must not outlive its bucket — deciding from an hour-old picture
    of supply is the "stale figure looking fresh" failure that a concentration metric once had.
    """

    def __init__(self, world, bucket_min: int = 60, supply_available: bool = True):
        self.world = world
        self.bucket_min = int(bucket_min)
        # A flag for reconstructing the absent scenario, where real data carries no per-cell
        # supply. The solver has to work at all three availability levels.
        self.supply_available = bool(supply_available)
        adv = (world.cfg.get("advice", {}) or {}) if hasattr(world, "cfg") else {}
        self.trips_per_hour_est = float(adv.get("trips_per_hour_est", 1.5) or 1.5)
        # The advisor's own demand belief, used for fictitious play. Only this producer reads
        # it; the driver's instinct must never do so. If the instinct also consumed the iterated
        # belief, the loop would be changing the *world* rather than the advisor, and a test
        # guards that by disabling the planner and requiring an identical trace with and without
        # the override. The data aggregates a *different* run, so it is cross-run learning rather
        # than a leak from within this one.
        ov = adv.get("market_demand_override") or None
        self.demand_override: dict[int, dict[str, float]] | None = (
            {int(h): {str(c): float(v) for c, v in cells.items()}
             for h, cells in ov.items()} if ov else None)
        # The demand source is a parameter of the producer. The oracle mode is the original path,
        # bit for bit; the realised mode estimates from trips already picked up, through a narrow
        # reader that holds only events and scalars rather than the world. An unknown value raises
        # immediately: failing loudly beats a hidden fallback.
        src = str(adv.get("market_demand_source") or "oracle")
        if src not in ("oracle", "realized"):
            raise ValueError(f"advice.market_demand_source={src!r} — chỉ nhận oracle|realized")
        self.demand_source = None
        if src == "realized":
            if self.demand_override is not None:
                # the two beliefs replace one another; mixing them is meaningless
                raise ValueError("market_demand_source=realized không đi cùng "
                                 "market_demand_override: hai belief thay thế nhau")
            if self.bucket_min != 60:
                # the measurement is pinned to one bucket length, from which every anchor and
                # magnitude is derived; another length requires rederiving them and unpinning
                # deliberately rather than running on quietly
                raise ValueError(f"market_demand_source=realized đòi bucket_min=60 "
                                 f"(đang {self.bucket_min}) — spec e10 §3.2")
            from .demand_estimator import RealizedDemandEstimator
            rd = adv.get("realized_demand") or {}
            self.demand_source = RealizedDemandEstimator(
                world.events, start_min=int(world.cfg.get("time.start_min")),
                bucket_min=self.bucket_min,
                window_buckets=int(rd.get("window_buckets", 3)),
                min_pickups=int(rd.get("min_pickups", 5)))
        # Caps may be pooled by parent cell at a coarser resolution rather than per cell. Absent,
        # the original path is bit-identical. Parent cells form a disjoint partition, so demand
        # cannot be double-counted, whereas overlapping discs would. An unknown value raises, under
        # the same fail-loud discipline as the demand source above.
        pr = adv.get("slots_pool_res")
        self.slots_pool_res: int | None = None
        if pr is not None:
            pr = int(pr)
            res = int(world.cfg.get("world.h3_res"))
            if not 0 <= pr < res:
                raise ValueError(f"advice.slots_pool_res={pr} phải trong [0, {res}) — "
                                 f"phải THÔ hơn lưới vận hành res-{res} thì mới gộp được")
            self.slots_pool_res = pr
        self._parent: dict[str, str] = {}      # remember the cell-to-parent mapping; the view runs each bucket
        self._cache: dict[int, dict] = {}
        self.pending_targets: dict[int, str] = {}

    def _demand(self, hour: int, idx: int) -> dict[str, float]:
        if self.demand_source is not None:
            # The realised estimate is per planner bucket. While cold it returns nothing and the
            # advisor stays silent; it must never fall back to the configured field or the
            # override.
            return self.demand_source.estimate(idx)
        if self.demand_override is not None:
            # It replaces rather than merges: fictitious play needs control of the whole belief,
            # and a partial merge would mix two generations of belief and converge nowhere.
            return dict(self.demand_override.get(hour, {}) or {})
        return dict(self.world.demand_field.get(hour, {}) or {})

    def view(self, now_min: float) -> dict:
        idx = int(now_min) // self.bucket_min
        hit = self._cache.get(idx)
        if hit is not None:
            return hit
        self._cache.clear()               # keep only the current bucket, so no stale figure survives
        hour = (idx * self.bucket_min // 60) % 24
        acts = self.world.actors
        acts = list(acts.values()) if hasattr(acts, "values") else list(acts)
        if self.supply_available:
            now, inc = count_supply(acts, self.pending_targets)
        else:
            now, inc = None, None
        start = idx * self.bucket_min
        dem = self._demand(hour, idx)
        # The producer is what knows the geometry, so it builds the grouping and injects it. The
        # core builder deliberately imports no grid library and stays a pure function, which keeps
        # the partition replaceable and testable rather than hard-wired to one resolution.
        grp = None
        if self.slots_pool_res is not None and dem:
            import h3
            for c in dem:
                if c not in self._parent:
                    self._parent[c] = h3.cell_to_parent(c, self.slots_pool_res)
            grp = {c: self._parent[c] for c in dem}
        v = build_market_state(
            t_now=f"{_BASE_DATE}T{(start // 60) % 24:02d}:{start % 60:02d}:00+07:00",
            bucket_min=self.bucket_min,
            demand_by_cell=dem,
            supply_now_by_cell=now,
            supply_incoming_by_cell=inc,
            trips_per_driver_per_bucket=self.trips_per_hour_est * self.bucket_min / 60.0,
            source="MOCK",
            cell_group=grp,
        )
        if self.demand_source is not None:
            # Logged once per bucket, on this cache-miss branch, as diagnostics and to count the
            # share of silent buckets. The oracle mode logs nothing at all, for neutrality, and a
            # test guards that.
            # The cache is a contract that there is one snapshot per bucket: clearing it mid-bucket
            # would not change the demand estimate, which fixes its window at the bucket boundary,
            # but it would resample the supply.
            est = self.demand_source
            if dem:
                self.world.log(-1, "demand_est", "", idx=idx,
                               total=est.last_total, n_buckets=est.last_n_buckets,
                               n_cells=len(dem),
                               cells={c: [(int(now.get(c, 0)) + int(inc.get(c, 0))
                                           if now is not None else None),
                                          round(lam, 6)]
                                      for c, lam in sorted(dem.items())})
            else:
                self.world.log(-1, "demand_est_cold", "", idx=idx,
                               total=est.last_total, n_buckets=est.last_n_buckets)
        self._cache[idx] = v
        return v
