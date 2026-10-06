"""The simulated world: everything assembled to run one day for one arm.

A discrete-event simulation of the trip and swap lifecycles, with a dispatch tick. Events are
appended to a log.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import h3
import numpy as np
import simpy

from .behavior import (IdleAction, choose_idle_action, choose_station, consider_relocate,
                       decide_accept)
from .config import Config
from .dispatcher import _speed_kmh, match_batch
from .entities import Actor, ActorState, BatteryInStation, FleetType, Station
from .geo import Grid, cell_distance_km, diem_quen, grid_disk
from .policy import PolicyBundle
# One source for the shift-extension rails. Copying the list into the world would create a second
# source of truth, which is the defect where a client computed range with its own formula and
# diverged from the engine.
from .sim_metrics import EXTEND_RAILS

# Event kinds for the branches that did not log themselves, on the two channels that were
# missing a denominator: advice given but not followed, and advice followed that the world could
# not carry out.
# They share a kind with the followed-and-executed branch, differing only in the followed flag
# and the reason. The projections group denominators by kind, so a separate kind would separate
# the denominator from the numerator.
# Events on these branches carry a zero magnitude, so a consumer measuring the *size* of an
# intervention must read the magnitude and not count events.
_SPOKEN_OUTCOME_KIND = {
    "shift_extend": "advice_shift_extend",
    "rest_window": "advice_rest_window",
    "swap_early": "advice_swap_early",   # E4/E-03 (UPDATE-156)
    "station_choice": "advice_station_choice",   # E4/E-01 (UPDATE-157)
}


# The instinct's short-rest distribution, promoted from a literal to a constant so that the
# guardrail derives its break threshold from the same source. Changing the distribution without
# recalibrating the threshold now turns a test red instead of silently changing meaning.
REST_MIN_MINUTES, REST_MAX_MINUTES = 20.0, 45.0


def rest_commit_gate(actor: Actor, now_min: float) -> str | None:
    """The gate that carries out a rest commitment, called at an idle decision point after the
    instinct has run.

    It reports that the commitment was kept, when the promised hour has arrived and rest is
    forced; that it was broken, when the driver was busy for the whole hour, in which case the
    right to rest is returned and no further veto is allowed until rest actually happens; or
    nothing.

    A plain module function over the actor, so it can be unit-tested without building a world.
    It takes no draws and touches no money.
    """
    due = actor.rest_commit_due_min
    if due is None:
        return None
    if now_min >= due + 60.0:                 # the promised hour has passed in full
        actor.rest_commit_due_min = None
        actor.rest_commit_broken = True
        return "broken"
    if now_min >= due:                        # inside the promised hour: the next decision point forces it
        actor.rest_commit_due_min = None
        return "kept"
    return None


@dataclass
class Event:
    t_min: float
    actor_id: int
    kind: str
    cell: str = ""
    detail: dict = field(default_factory=dict)
    # The run's deterministic identity. It lets paired worlds on the same seed be told apart
    # and lets lifecycle events be exported and joined. Empty means a world built by hand
    # outside the runner.
    run_id: str = ""


class World:
    def __init__(self, grid: Grid, cfg: Config, policy: PolicyBundle, orders: list, actors: list[Actor],
                 seed: int, environment=None, congestion=None, run_id: str = "",
                 human_actor_id: int | None = None, hil_gate=None):
        # --- Human in the loop -------------------------------------------------------
        # When a human actor is set, that actor does not run the instinct model: the two seams
        # below, at the accept decision and at the idle-action decision, ask the gate instead of
        # the behaviour module.
        #
        # Why the seams must sit at exactly those two points and no earlier: the world holds one
        # sequential random stream shared by the whole fleet. Cutting away a draw earlier would
        # shift the stream for every other actor. A fingerprint gate over five seeds guards
        # against that drift, running with no human actor.
        #
        # The gate is an object with one method per decision, injected by the session layer.
        # Leaving it unset while naming a human actor is a configuration error and fails loudly
        # below rather than silently running the instinct.
        self.human_actor_id = human_actor_id
        self.hil_gate = hil_gate
        if human_actor_id is not None and hil_gate is None:
            raise ValueError(
                f"human_actor_id={human_actor_id} nhưng hil_gate=None — actor người sẽ âm thầm "
                f"chạy bản năng. Tiêm gate hoặc bỏ human_actor_id (bẫy #12: cấu hình quảng cáo "
                f"mà không có đường chạy).")
        # The rules governing the human driver live in their own module, not in this file.
        #
        # This file may call into them and must contain none of them. That boundary is what makes
        # "does this line touch the fingerprint" answerable by file path rather than by tracing
        # backwards for the enclosing condition — the old way left four tasks wrongly blocked for
        # weeks. Two gates enforce it.
        #
        # Absent when there is no human player, so every call below sits inside the human-actor
        # branch and never runs while the fingerprint is being measured.
        self.luat_nguoi = None
        if human_actor_id is not None:
            from .hil.luat_nguoi import LuatNguoiChoi

            self.luat_nguoi = LuatNguoiChoi(self)
        self.grid = grid
        self.cfg = cfg
        self.run_id = run_id  # the run's deterministic identity, stamped on every event
        self.policy = policy
        self.orders = orders
        self.actors = {a.actor_id: a for a in actors}
        self.seed = seed
        self.envctx = environment  # EnvironmentContext | None
        self.congestion = congestion  # CongestionField | None (spatiotemporal)
        # waypoints at every transition, for fallback rendering and debugging
        self.traj: list[tuple] = []
        # Observable replay snapshots.  This is append-only diagnostic state: it is captured
        # from the actor immediately after an existing event is logged and never drives the
        # SimPy world or consumes an RNG draw.  The demo projection uses it instead of trying
        # to reconstruct SOC/payout from a final actor object.
        self.trace_snapshots: list[dict] = []
        # Segments record each activity with exact start, end and endpoints, which is what the
        # timeline and trip layers draw from. Idle time is the gap between segments.
        self.segments: list[dict] = []
        self.env = simpy.Environment()
        self.events: list[Event] = []
        self.speed_cfg = cfg.get("speed_kmh")
        self.disp_cfg = cfg.get("dispatcher")
        self.veh = cfg.get("vehicle")
        self.car_sim_cfg = cfg.get("car_simulation", {}) or {}
        self.car_sim_enabled = bool(self.car_sim_cfg.get("enabled", False) and self.car_sim_cfg.get("primary_mode", False))
        self.car_classes = dict(self.car_sim_cfg.get("classes", {}) or {})
        self.car_charging = dict(self.car_sim_cfg.get("charging", {}) or {})
        # The habitual waiting spot: a jitter radius around the cell centre, applied to every
        # centroid placement. Deliberately no default, so a missing key raises rather than
        # silently substituting: the config is the single source of truth.
        # The ceiling derives from the cell's inradius rather than a hard-coded number, so it
        # survives a resolution change. Too large a radius breaks the invariant that jitter never
        # moves a point into another cell.
        from .geo import _cell_inradius_km
        self._spot_jitter_km = float(cfg.get("world.driver_spot_jitter_km"))
        _tran = 0.5 * _cell_inradius_km(grid.res)
        if not (0.0 <= self._spot_jitter_km <= _tran):
            raise ValueError(
                f"world.driver_spot_jitter_km = {self._spot_jitter_km} ngoài dải [0; {_tran:.4f}] "
                f"(0,5 × inradius res {grid.res}) — fail-loud, không clamp im lặng")
        self.detour = float(cfg.get("demand.detour_factor", 1.3))  # A4
        # Real road factors per cell pair, from the offline matrix. Absent when routing is
        # disabled or the file is missing, in which case everything falls back to the constant
        # detour and the behaviour is identical to before the matrix existed.
        from .geo import load_road_matrix
        self.road = load_road_matrix(cfg, grid)
        bcfg = cfg.get("behavior", {})
        # The former name suggested money. This is perceived disutility; the behaviour module
        # was renamed first and the config and reader now agree.
        self.pickup_disutility_km = float(bcfg.get("pickup_disutility_vnd_per_km", 3000.0))
        self.accept_center = float(bcfg.get("accept_logit_center_vnd", 6000.0))
        self.accept_scale = float(bcfg.get("accept_logit_scale_vnd", 8000.0))
        # Cancellation after acceptance: a no-show, a passenger cancelling, an incident. The
        # simulation completed almost every accepted trip, which is far cleaner than reality.
        # It happens while the driver is on the way to the pickup, so it costs time and charge
        # and earns nothing.
        self.cancel_after_accept = float(bcfg.get("cancel_after_accept_rate", 0.05))

        # a random stream for actor behaviour; the exogenous side already lives in the orders
        self.rng = np.random.default_rng(seed ^ 0xBEEF)
        # A separate stream for ratings: drawing them from the behaviour stream would shift it
        # and move every calibrated behaviour figure with it.
        self.rng_rating = np.random.default_rng(seed ^ 0x5A7E5)
        rcfg2 = cfg.get("rating", {}) or {}
        self.rating_p = float(rcfg2.get("p_rated", 0.75))
        self.rating_p5 = dict(rcfg2.get("p5_by_archetype", {}) or {})
        self.rating_p4s = float(rcfg2.get("p4_star", 0.75))
        # newcomer and mission programmes; the figures are labelled proxies in the config
        ncfg = cfg.get("newbie_program", {}) or {}
        self.newbie = ncfg if ncfg.get("enabled", False) else None
        mcfg = cfg.get("missions", {}) or {}
        self.mission_catalog = list(mcfg.get("daily_catalog", []) or [])             if mcfg.get("enabled", False) else []

        # the bridge from advice to action, off by default so the control arm is unchanged
        from .advice_bridge import AdviceActionBridge
        from .checkpoint_trace import CheckpointTraceSink
        self.checkpoint_trace = CheckpointTraceSink(
            enabled=bool(cfg.get("checkpoint_shadow.enabled", False)),
            run_id=run_id,
        )
        self.advice = AdviceActionBridge(
            cfg, policy, seed, checkpoint_trace=self.checkpoint_trace, run_id=run_id)
        # Cost parameters, zero by default, so the cost ledger stays at zero and behaviour is
        # unchanged. The ranges for sweeps come from the driver cost research note.
        self.swap_fee_vnd = int(self.veh.get("swap_fee_vnd", 0) or 0)
        self.cash_cost_km = float(self.veh.get("cash_cost_vnd_per_km", 0) or 0)
        # a policy may carry an expiry; warn loudly rather than silently
        if self.advice.policy_valid_today is False:
            self.log(-1, "policy_outside_validity", "",
                     effective_from=self.advice.policy.effective_from,
                     effective_to=self.advice.policy.effective_to)

        # open orders in time order, walked with a cursor
        self.orders_sorted = sorted(orders, key=lambda o: (o.t_min, o.order_id))
        self._order_ptr = 0
        self.open_orders: dict[int, object] = {}     # order_id -> Order
        self.order_open_since: dict[int, float] = {}
        # The order lifecycle: every order reaches exactly one terminal state.
        # created, open, matched, picked up, then completed, expired or censored at end of run.
        self.order_states: dict[int, tuple[str, float]] = {
            o.order_id: ("CREATED", o.t_min) for o in orders}
        # when each order was last offered to each driver, for the cooldown
        self.offer_history: dict[tuple[int, int], float] = {}
        self.offer_cooldown = float(cfg.get("dispatcher.offer_cooldown_min", 10.0))
        # accumulated online minutes per actor, flushed when the day is censored
        self._last_accrual: dict[int, float] = {}

        # stations
        self.stations: list[Station] = self._build_stations()
        self.station_by_id = {s.node_id: s for s in self.stations}

        self.metrics_start = float(cfg.get("time.start_min")) + float(cfg.get("time.warmup_min"))
        self.end_min = float(cfg.get("time.end_min"))

        # The expected demand field, taken from configuration rather than the run's realised
        # trace, which would leak information from the future. It is the basis of an actor's
        # personal experience.
        from .demand import expected_demand_field
        self.demand_field: dict[int, dict[str, float]] = expected_demand_field(grid, cfg)
        # A belief cache per actor and hour: the noise is sampled once and held for the day
        # rather than resampled at every idle check. The key is deterministic and does not
        # depend on set iteration order, which was shown to shift metrics between processes.
        self._belief_cache: dict[tuple[int, int], dict[str, float]] = {}

        # The positioning channel, built only when enabled: with the flag off the trace must be
        # bit-identical to a config without the flag, and a test guards that. The standby plan is
        # the current assignment of actors to cells, written by the planner each bucket; the idle
        # loop only reads it.
        self.standby_plan: dict[int, str] = {}
        # The assignment's decision id, generated once at assignment time and reused when it is
        # followed, so the two events join even across buckets.
        self.standby_decision: dict[int, str] = {}
        self.market = None
        if self.advice.enabled and self.advice.positioning_overrides != "off":
            from .market_state import MarketStateProducer
            self.market = MarketStateProducer(
                self, bucket_min=self.advice.bucket_min,
                supply_available=self.advice.market_supply_available)

    def _build_stations(self) -> list[Station]:
        slots = int(self.cfg.get("station.slots"))
        ready = float(self.cfg.get("station.ready_soc_pct"))
        out = []
        for s in self.grid.stations:
            batteries = [BatteryInStation(soc_pct=100.0, ready_at_min=0.0) for _ in range(slots - 1)]
            out.append(Station(node_id=s.node_id, cell=s.cell, lat=s.lat, lon=s.lon,
                               slots=slots, ready_soc_pct=ready, batteries=batteries))
        return out

    def log(self, actor_id: int, kind: str, cell: str = "", **detail):
        self.events.append(Event(round(self.env.now, 3), actor_id, kind, cell, detail,
                                 self.run_id))
        actor = self.actors.get(actor_id)
        if actor is None:
            return
        # Keep this snapshot deliberately data-only.  No helper below is allowed to mutate
        # actor/world state; adding the observer must remain behavior-neutral.
        self.trace_snapshots.append({
            "event_index": len(self.events) - 1,
            "t_min": round(self.env.now, 3),
            "actor_id": actor_id,
            "event_kind": kind,
            "event_detail": dict(detail),
            "state": actor.state.value,
            "cell": actor.cell,
            "lat": float(actor.lat),
            "lon": float(actor.lon),
            "vehicle_class": getattr(actor, "vehicle_class", "A_COMPACT"),
            "soc_pct": float(actor.soc_pct),
            "payout_vnd": int(actor.payout_vnd),
            "gross_vnd": int(actor.gross_vnd),
            "points": int(actor.points),
            "trips_done": int(actor.trips_done),
            "orders_offered": int(actor.orders_offered),
            "orders_accepted": int(actor.orders_accepted),
            "orders_completed": int(actor.orders_completed),
            "orders_cancelled": int(actor.orders_cancelled),
            "online_min": round(float(actor.online_min), 3),
            "rest_min": round(float(actor.rest_min), 3),
            "charge_min": round(float(actor.charge_min), 3),
            "shift_start_min": float(actor.shift_start_min),
            "shift_end_min": float(actor.shift_end_min),
        })

    def _decision_id(self, actor_id: int, channel: str, now: float) -> str:
        """The simulation's deterministic decision id: driver, channel and bucket, as the
        adherence specification defines it.

        Advice given, followed and suppressed within one tick fall in one bucket and so share a
        decision, which is what makes them joinable. Re-checks within the same bucket collapse
        into one logical decision.

        One number for every channel, positioning included. Positioning previously used its own
        grid, giving three grids for one concept, with a cooldown shorter than either.

        The remaining constraint, which is why the separate grid existed, must be kept: a bucket
        must not be wider than the channel's own decision cadence, or two genuinely different
        assignments collapse into one id and events are swallowed. That constraint is now
        enforced by a loud guard in the advice bridge rather than by a third grid."""
        from gsm_core.lifecycle.cadence import decision_bucket
        return f"slth-{self.run_id}-{actor_id}-{channel}-{decision_bucket(now)}"

    def _order_transition(self, oid: int, state: str) -> None:
        """Record an order state transition; a terminal state is written once and never
    overwritten."""
        cur = self.order_states.get(oid, ("CREATED", 0.0))[0]
        if cur in ("COMPLETED", "EXPIRED", "CENSORED_END_OF_RUN", "CANCELLED_AFTER_ACCEPT"):
            return  # a terminal state is an invariant
        self.order_states[oid] = (state, round(self.env.now, 3))

    def _tam_voi_km(self) -> float:
        """The radius within which a driver can still be reached by demand, derived from the
        dispatcher's own rule rather than typed as a constant.

        It is the candidate ring count times twice the cell apothem. Deriving it means it cannot
        drift when someone changes the ring limit — the place where another mismatch between the
        ring limit and the arrival-time limit went unnoticed."""
        if getattr(self, "_tam_voi_cache", None) is None:
            import h3 as _h3
            k = int(self.cfg.get("dispatcher.candidate_ring_k_max"))
            canh = float(_h3.average_hexagon_edge_length(self.grid.res, unit="km"))
            self._tam_voi_cache = k * canh * (3 ** 0.5)
        return self._tam_voi_cache

    def _cell_point(self, cell: str) -> tuple[float, float]:
        """The cell's representative point, used as the endpoint for relocating, charging and
    going online."""
        return self.grid.cell_centroid.get(cell) or (0.0, 0.0)

    def _set_pos(self, actor: Actor, lat: float, lon: float) -> None:
        """Atomic movement: position and cell are updated together, and callers no longer assign
        the cell themselves, so there is one source of truth.

        Placement onto the exact cell centre is replaced by the driver's habitual spot, a
        deterministic point within a configured jitter radius. Two properties of the measured
        mechanism are kept deliberately:

        (a) bit-equality with the centroid is the heuristic that recognises a centroid
            placement. It is safe because all four centroid call sites take the very tuple from
            the grid through one accessor with no floating-point round trip, while pickups,
            dropoffs and stations are sampled or map-derived points whose chance of matching bit
            for bit is negligible.
        (b) segment chains break by up to the jitter radius: a segment records the raw cell
            centre as its end while the next segment starts from the jittered point. That is a
            property of the measured version, not a defect.

        The jitter must be applied before the assignments, so the cell is re-derived from the
        jittered position; the radius stays below half the inradius, so the cell never
        changes."""
        if self._spot_jitter_km > 0.0:
            cell = h3.latlng_to_cell(lat, lon, self.grid.res)
            c = self.grid.cell_centroid.get(cell)
            if c is not None and abs(lat - c[0]) < 1e-9 and abs(lon - c[1]) < 1e-9:
                lat, lon = diem_quen(self.seed, actor.actor_id, cell, c[0], c[1],
                                     self._spot_jitter_km)
        actor.lat, actor.lon = lat, lon
        actor.cell = h3.latlng_to_cell(lat, lon, self.grid.res)
        self.traj.append((round(self.env.now, 3), actor.actor_id, lat, lon, actor.state.value))

    def _seg(self, actor_id: int, t0: float, t1: float, kind: str,
             frm: tuple, to: tuple, **meta) -> None:
        """Record one activity segment, from one position to another."""
        self.segments.append({
            "actor_id": actor_id, "t0": round(t0, 3), "t1": round(t1, 3), "kind": kind,
            "from_lat": frm[0], "from_lon": frm[1], "to_lat": to[0], "to_lon": to[1], **meta,
        })

    def _alt_action_probe(self, hour: int, hint):
        """A probe for "is there anything useful to do instead of resting", deterministic and
        taking no draws.

        The deferral logic calls it before the cadence and compliance checks. Were the probe to
        draw, every rejected decision would still shift the random stream and contaminate the
        control arm.

        It returns a relocation with a cell when one is worth going to, and waiting otherwise.
        It does not draw the movement probability: whether the driver complies is already
        answered elsewhere, and asking again would count it twice.
        """
        from .behavior import best_relocate_target

        cfg_b = self.cfg.get("behavior", {}) or {}

        def _probe(a):
            cell, _p = best_relocate_target(a, self.grid, hint, cfg_b)
            return (IdleAction.WAIT, None) if cell is None else (IdleAction.RELOCATE, cell)

        return _probe

    def _congestion_r(self, cell: str | None, hour: int) -> float:
        if self.congestion is None or cell is None:
            return 0.0
        return self.congestion.r(cell, hour)

    def _is_arterial_jammed(self, hour: int) -> bool:
        """Kiểm tra đường vành đai / trục chính có bị tắc ngẫu nhiên theo giờ không (xác suất 1% mỗi giờ).
        Sử dụng pseudo-random hash tất định theo seed và simulation hour để không làm lệch
        chuỗi RNG chính của mô phỏng."""
        import hashlib
        sim_hour = int(self.env.now // 60) if self.env.now > 0 else hour
        h_str = f"{self.seed}:arterial_jam:{sim_hour}"
        val = int(hashlib.md5(h_str.encode()).hexdigest()[:8], 16)
        prob = float(self.speed_cfg.get("arterial_jam_prob", 0.01))
        return (val % 10000) < int(prob * 10000)

    def _eff_speed(self, hour: int, cell: str | None = None, dest_cell: str | None = None) -> float:
        """Vận tốc hiệu dụng theo phân cấp đường và điều kiện giao thông:
        - EXPRESSWAY (Cao tốc): Luôn giữ nguyên tốc độ cao tốc (mặc định 80 km/h hoặc theo OSRM), không bị ảnh hưởng bởi tắc đường.
        - ARTERIAL (Đường vành đai / Trục chính): Tốc độ cơ sở đường vành đai/trục chính (~60 km/h hoặc theo OSRM).
          Không áp dụng tắc đường nội đô r_cong, nhưng có xác suất 1% mỗi giờ bị tắc ngẫu nhiên -> khi tắc giảm 1/2 tốc độ (50%).
        - LOCAL_STREET (Đường thường / Nội đô): Tốc độ theo khung giờ đô thị (peak 30, offpeak 40, night 50),
          chịu tác động của quy tắc tắc đường r_cong từ mật độ đơn hàng và thời tiết.
        """
        tier = "LOCAL_STREET"
        osrm_spd = None
        if self.road is not None and cell is not None and dest_cell is not None:
            tier = self.road.road_tier(cell, dest_cell)
            osrm_spd = self.road.inferred_speed(cell, dest_cell)

        # 1. Đường cao tốc: Luôn giữ nguyên tốc độ
        if tier == "EXPRESSWAY":
            base_exp = float(osrm_spd) if (osrm_spd is not None and osrm_spd >= 70.0) else float(self.speed_cfg.get("expressway_base", 80.0))
            if self.envctx is not None and self.envctx.v_floor <= 0.0:
                return 0.0
            return base_exp

        # 2. Đường vành đai / trục chính: Random tắc theo giờ với xác suất 1%, giảm 1/2 tốc độ
        if tier == "ARTERIAL":
            base_art = float(osrm_spd) if (osrm_spd is not None and 50.0 <= osrm_spd < 70.0) else float(self.speed_cfg.get("arterial_base", 60.0))
            if self.envctx is not None and self.envctx.v_floor <= 0.0:
                return 0.0
            if self._is_arterial_jammed(hour):
                return base_art * 0.5  # Giảm 1 nửa tốc độ khi xảy ra tắc đường vành đai
            return base_art

        # 3. Đường thường / nội đô: Áp dụng quy tắc tắc đường r_cong và thời tiết
        base = _speed_kmh(hour, self.speed_cfg)
        r_cong = self._congestion_r(cell, hour)
        t_now = self.env.now if self.env.now > 0 else float(hour * 60 + 30)
        if self.envctx is not None:
            # env.speed_factor = (1-r_rain)·(1-r_cong)·weather_factor survival product
            v = base * self.envctx.speed_factor(t_now, r_cong, cell=cell)
            if v <= 0.0:
                return 0.0
            return max(self.envctx.v_floor, v)
        if r_cong <= 0.0:
            return base
        return max(7.0, base * (1.0 - min(0.95, r_cong)))

    def _dfac(self, a: str | None, b: str | None) -> float:
        """The road factor for a cell pair, from the matrix if available and the constant detour
    otherwise."""
        if self.road is None or a is None or b is None:
            base_detour = self.detour
        else:
            base_detour = self.road.factor(a, b, self.detour)
        t_now = self.env.now if self.env.now > 0 else 720.0
        if self.envctx is not None and hasattr(self.envctx, "get_route_detour_factor") and a and b:
            return self.envctx.get_route_detour_factor(t_now, a, b, base_detour)
        return base_detour

    def _travel_min(self, dist_km: float, hour: int, cell: str | None = None,
                    fac: float | None = None, dest_cell: str | None = None) -> float:
        """Travel time: the real distance, scaled by the road factor, over the effective speed.

        The factor is per cell pair; absent, the constant detour applies.
        """
        f = self.detour if fac is None else fac
        eff_v = self._eff_speed(hour, cell, dest_cell=dest_cell)
        if eff_v <= 0.0:
            return 9999.0  # Ô ngập sâu cấm đường tuyệt đối
        return (dist_km * f) / eff_v * 60.0

    def _pct_per_km(self, actor: Actor) -> float:
        """Charge consumed per kilometre, adjusted for temperature: less range means more
    consumption. Uses the car EV class specifications (Taxi Co Huu)."""
        if self.car_sim_enabled and hasattr(actor, "vehicle_class") and actor.vehicle_class in self.car_classes:
            c = self.car_classes[actor.vehicle_class]
            battery_kwh = float(c.get("battery_kwh", 37.23))
            kwh_100km = float(c.get("kwh_per_100km", 14.5))
            base = kwh_100km / max(1.0, battery_kwh)
        else:
            base = float(self.veh["swap_consume_pct_per_km"] if actor.fleet == FleetType.SWAP
                         else self.veh["charge_consume_pct_per_km"])
        if self.envctx is None:
            return base
        return base / max(0.5, self.envctx.range_factor(self.env.now))

    # --- Processes ---

    def start_processes(self):
        """Spawn every simulation process, kept separate from the run loop for the human-in-the-loop
        path.

        The order in which processes are spawned is part of the deterministic identity: the
        scheduler breaks ties by time, priority and creation order, and creation order is exactly
        the order of the lines below. Reordering them changes how two events at the same instant
        interleave, which moves every calibrated figure. The five-seed fingerprint gate is what
        catches a reordering."""
        self.env.process(self._dispatcher_proc())
        self.env.process(self._order_expiry_proc())
        if self.market is not None:
            self.env.process(self._standby_planner())
        if bool(self.cfg.get("probe.wait_stats", False)):
            self.env.process(self._wait_stats_probe())
        for a in self.actors.values():
            self.env.process(self._actor_proc(a))

    def run(self):
        self.start_processes()
        self.env.run(until=self.end_min)
        self._settle_end_of_run()
        return self.events

    def _wait_stats_probe(self):
        """A log-only observer for control runs: idle counts and median streaks per cell, plus the
        streak per actor. That streak appears in no event, and reconstructing it from the
        timeline would be a second, error-prone recomputation.

        It takes no draws and writes no state, so the fingerprint must be identical with the flag
        on or off. Share and precision figures are computed afterwards from the event log rather
        than here."""
        from .market_state import count_idle_wait
        b = float(self.advice.bucket_min)
        while self.env.now < self.end_min:
            stats = count_idle_wait(self.actors.values())
            self.log(-1, "probe_wait_stats", "",
                     cells={c: [n, round(med, 3)] for c, (n, med) in sorted(stats.items())},
                     streaks={str(a.actor_id): [a.cell, round(float(a.idle_streak_min), 1)]
                              for a in self.actors.values()
                              if a.state == ActorState.IDLE})
            yield self.env.timeout(b)

    # --- Batch tick: assign idle drivers to cells with headroom, once per bucket ---

    def _standby_planner(self):
        """Batch assignment, matching the solver's nature, rather than greedy decrement. That keeps
        the assignment optimal and comparable against centralised allocation.

        The headroom comes from the market-state view, which has already subtracted supply in
        place, supply on its way and advice just issued. This function only carries it out;
        there is no second place computing headroom.

        The compliance draw is taken once at assignment time and never re-rolled on each poll.
        A driver who does not comply is not added to the plan and is not reminded again within
        that bucket.
        """
        from gsm_core.features.allocation import derive_allocation_input
        from gsm_core.solvers import capacity_alloc
        from .advice_bridge import _iso
        from .market_state import chon_o_dich

        b = float(self.advice.bucket_min)
        while self.env.now < self.end_min:
            now = self.env.now
            view = self.market.view(now)
            if not (view["positioning_allowed"] and view["ranked_cells"]):
                yield self.env.timeout(b)
                continue
            ranked = view["ranked_cells"]
            cap_left = {c: int(view["cells"][c]["capacity_left"] or 0) for c in ranked}

            # The wait trigger selects candidates by a cell's waiting time rather than by its
            # remaining headroom. The default capacity branch follows the old path exactly:
            # nothing fires and the effective ranking is the ranking itself.
            wait_mode = self.advice.positioning_trigger == "wait"
            if wait_mode:
                from .market_state import count_idle_wait, wait_fired_cells
                wait_T = self.advice.positioning_wait_threshold_min
                fired = wait_fired_cells(count_idle_wait(self.actors.values()),
                                         wait_T, self.advice.positioning_wait_min_idle)
                # Part of the trigger's definition rather than an option: a cell that fired is
                # removed from the batch's destination set. Since candidates only come from
                # fired cells, a driver's own cell is never a destination, so the assignment
                # cannot leave them where they stand; and a fired cell takes nobody in, which
                # closes the overlap between sources and destinations within one batch.
                ranked_eff = [c for c in ranked if c not in fired]
                if not ranked_eff:
                    # with no valid destination left they are not a candidate at all: no draw
                    # is taken and no decision is counted
                    yield self.env.timeout(b)
                    continue
            else:
                fired, ranked_eff = set(), ranked

            cands = []
            for a in self.actors.values():
                if a.state != ActorState.IDLE or not self.advice.covers(a):
                    continue
                if a.actor_id in self.standby_plan:
                    continue                     # already holds a live assignment
                if wait_mode:
                    if a.cell not in fired:
                        continue                 # sources are only cells that fired
                    if a.idle_streak_min < wait_T:
                        continue                 # the individual gate: do not move someone
                                                 # who has only just arrived
                elif cap_left.get(a.cell, 0) > 0:
                    continue                     # standing in a cell with headroom: moving them is churn
                # Positioning sits outside the cadence by default, being the one channel with
                # a significant positive effect. It only comes under the cooldown and budget
                # when the measurement flag is set. The default branch must follow the old path
                # exactly, or every paired result already measured with positioning would change
                # silently.
                if (self.advice.cadence_counts_positioning
                        and not self.advice.cadence_allows(a, "positioning", now)):
                    continue
                # The preferred cell for this driver; the assignment solver staggers whatever
                # is contested.
                #
                # This line once picked the *nearest* cell, discarding the supply-demand ranking
                # that the market-state feature had just computed. That ranking was then only
                # filtering which cells were eligible and had no say in the choice.
                # Measured over three seeds and several hundred assignments, the nearest cell
                # differed from the best-ranked one nine times in ten, and switching lifted the
                # median ratio by about forty percent.
                # The nearest mode remains selectable and branches explicitly to the old
                # expression.
                pref = chon_o_dich(
                    a.cell, ranked_eff, view["cells"], self.grid,
                    khoang_cach=lambda x, y: cell_distance_km(self.grid, x, y),
                    che_do=self.advice.positioning_target_mode,
                    phat_km=self.advice.positioning_km_penalty,
                    tran_km_them=self.advice.positioning_max_extra_km,
                    tam_voi_km=self._tam_voi_km())
                # The solver orders by ascending priority, so charge is inverted: drivers with
                # more charge go first, since they can travel further, and drivers low on charge
                # should not be sent on an empty run.
                cand = {"driver_id": f"d-{a.actor_id}", "advice_kind": "standby_zone",
                        "target": pref, "priority_soc": round(100.0 - a.soc_pct, 1)}
                # Distance to the next bonus tier, in points. The policy accessor is the only
                # source; the tier table is never copied.
                # At the top tier the field is omitted rather than set to zero: "no tier left to
                # reach" is an absence, while a gap of zero means standing exactly on a tier.
                gap = self.policy.next_tier_gap(int(a.points))
                if gap:
                    cand["tier_gap_points"] = int(gap[0])
                # Distance to the destination cell, which the assignment solver never used to
                # receive.
                #
                # The allocator scored cost as a charge penalty plus a flat constant for "wrong
                # cell", so when it pushed a driver off their preferred cell it chose the
                # replacement without knowing which was near. Measured on one actor over dozens
                # of assignments, the chosen cell was typically several kilometres away while the
                # nearest candidate was a few hundred metres, and it was further in almost every
                # case.
                #
                # Attached only when the distance weight is positive. Without the field the
                # solver falls back to the old penalty, and a test proves over four hundred
                # matrices that the solution is identical, so the fingerprint cannot move.
                #
                # It is the distance to *every* destination, not to the preferred one. A single
                # scalar would enter the matrix as a constant along the row and could not change
                # the solution: over four hundred contested matrices the assignment shuffled but
                # the assigned distance stayed put, even at a large weight.
                if self.advice.km_weight > 0.0:
                    cand["km_to_zones"] = {
                        z: round(cell_distance_km(self.grid, a.cell, z), 3)
                        for z in ranked_eff}
                cands.append(cand)
            if not cands:
                yield self.env.timeout(b)
                continue

            zones = [{"zone": c, "capacity": cap_left[c]} for c in ranked_eff]
            ai = derive_allocation_input(_iso(now), cands, [], zones,
                                         bucket_min=int(b),
                                         tier_weight=self.advice.positioning_tier_weight,
                                         km_weight=self.advice.km_weight)
            report = capacity_alloc.solve(ai)
            sol = report["solution"]
            # The veto invariant: nobody is assigned into a cell that fired. Structurally the
            # solver has no slot there, and the assertion makes a breach loud rather than
            # silent.
            assert all(al["assigned_target"] not in fired for al in sol["allocations"]),                 "zone-veto thủng: có người bị gán vào ô fired"

            n_follow = 0
            per_cell: dict[str, int] = {}
            flags_by_cell: dict[str, list] = {}
            # The denominator for the positioning channel is everyone assigned, including
            # those who do not comply. Without recording it here the lifecycle only sees the
            # followed events and adherence reads as a perfect hundred percent, where the truth
            # was under half. It goes into the detail of an existing event rather than a new
            # kind, so consumers that count by kind see unchanged figures.
            assigned_by_cell: dict[str, list] = {}
            dids_by_cell: dict[str, dict] = {}
            # The instrument was wrong: the outcome of the compliance draw has to be
            # observable independently of whether the world could execute it. Previously the
            # followed event was the only evidence, so every case of "willing but not
            # executable" — already in the right cell, busy until the end of the shift, the
            # instinct choosing something else — counted as non-compliance, biasing adherence by
            # a couple of percentage points and suspending the significance gate.
            coin_true_by_cell: dict[str, list] = {}
            for al in sol["allocations"]:
                cell = al["assigned_target"]
                per_cell[cell] = per_cell.get(cell, 0) + 1
                flags_by_cell.setdefault(cell, al.get("safety_flags") or [])
                aid = int(al["driver_id"][2:])
                did = self._decision_id(aid, "positioning", now)
                self.checkpoint_trace.capture(
                    "S4", self.actors[aid], now, ai, report, did,
                    # The planner runs each bucket, so the real freshness horizon is the next
                    # bucket boundary rather than a fixed offset invented in the trace.
                    validity_hints={
                        "freshness_deadline_min": now + float(self.advice.bucket_min),
                        "allocation_bucket_end_min": now + float(self.advice.bucket_min),
                    })
                assigned_by_cell.setdefault(cell, []).append(aid)
                dids_by_cell.setdefault(cell, {})[str(aid)] = did
                # "Said" means the advisor spoke, regardless of whether the driver complied:
                # the budget is a budget of the listener's attention. Recorded only when the
                # measurement flag is on.
                if self.advice.cadence_counts_positioning:
                    self.advice.cadence_note_spoken(self.actors[aid], "positioning", now)
                if self.advice.standby_follow_draw(self.actors[aid], now, cell):
                    coin_true_by_cell.setdefault(cell, []).append(aid)
                    self.standby_plan[aid] = cell
                    # the decision is created at assignment time and reused when it is followed
                    self.standby_decision[aid] = did
                    self.market.pending_targets[aid] = cell   # deduct the headroom now, for whoever asks next
                    n_follow += 1
            for cell in sorted(per_cell):
                self.log(-1, "standby_alloc", cell, n_assigned=per_cell[cell],
                         capacity_left=cap_left[cell], safety_flags=flags_by_cell[cell],
                         assigned_ids=sorted(assigned_by_cell.get(cell, [])),
                         coin_follow_ids=sorted(coin_true_by_cell.get(cell, [])),
                         decision_ids=dids_by_cell.get(cell, {}))
            self.log(-1, "standby_planner", "",
                     n_candidates=len(cands), n_assigned=sol["n_assigned"],
                     n_unassigned=len(sol["unassigned"]), n_follow=n_follow,
                     herding_avoided=sol["herding_avoided"],
                     # Only the wait mode carries the extra keys; the capacity mode keeps its
                     # detail untouched, bit for bit. The fired set is recorded so that volume
                     # diffs and the self-check that nobody was assigned into a fired cell can
                     # be read from the event log.
                     **({"n_fired_cells": len(fired), "fired_cells": sorted(fired)}
                        if wait_mode else {}))
            yield self.env.timeout(b)

    def _settle_end_of_run(self):
        """Close the day: flush time for actors still busy, censor in-flight orders, and only then
        compute the daily bonus. The scheduler drops pending timeouts, so this has to reconcile
        explicitly."""
        # settle the kilometre cost once at the end of the day; the default rate is zero
        if self.cash_cost_km:
            for a in self.actors.values():
                a.cost_vnd += int(round(a.km_driven * self.cash_cost_km))
        # 1. Actors still online: add the final stretch to their online minutes.
        for a in self.actors.values():
            if a.state == ActorState.OFFLINE:
                continue
            last = self._last_accrual.get(a.actor_id)
            if last is not None and self.end_min > last:
                a.online_min += self.end_min - last
                self._last_accrual[a.actor_id] = self.end_min
            # an actor mid-activity is marked censored so metrics and the UI can tell
            if a.state in (ActorState.ENROUTE, ActorState.ON_TRIP, ActorState.CHARGING, ActorState.REST):
                self.log(a.actor_id, "censored_end_of_run", a.cell, state=a.state.value)
        # 2. Orders that never reached a terminal state are censored.
        for oid, (state, _t) in list(self.order_states.items()):
            if state in ("COMPLETED", "EXPIRED"):
                continue
            if state in ("MATCHED", "PICKED_UP"):
                self.order_states[oid] = ("CENSORED_END_OF_RUN", self.end_min)
                self.log(-1, "order_censored", order_id=oid, last_state=state)
            elif state in ("CREATED", "OPEN"):
                # an order never matched by the end of the day has expired, operationally
                self.order_states[oid] = ("EXPIRED", self.end_min)
                self.log(-1, "order_expired", order_id=oid, reason="end_of_run")
        # 3. The daily bonus for actors still online at the end of the run.
        for a in self.actors.values():
            if a.state != ActorState.OFFLINE:
                self._newbie_settle(a)     # settle the newcomer top-up before closing the books
                bonus = self.policy.day_bonus(a.points, a.acceptance_rate, a.completion_rate)
                a.payout_vnd += bonus
                self.log(a.actor_id, "day_end_settle", a.cell,
                         trips=a.trips_done, payout=a.payout_vnd, points=a.points, day_bonus=bonus)


    def _newbie_settle(self, actor: Actor) -> None:
        """Settle the newcomer programme at the end of the day, called both when a shift ends and
        when the day is censored.

        The structure follows the operator's published programme; the amounts are labelled
        proxies in the config. Two layers:

        1. A daily revenue guarantee during the first months: if gross falls below the floor and
           the driver was online enough, the shortfall is topped up. The online condition is what
           stops the guarantee being claimed without working.
        2. A cumulative trip milestone in the first week, read from completed days in the
           driver's memory plus today, and paid once.
        """
        if not self.newbie or actor.tenure_days > int(self.newbie["tenure_newbie_max_days"]):
            return
        # 1) the daily revenue guarantee
        if actor.tenure_days <= int(self.newbie["guarantee_days"]):
            floor = int(self.newbie["guarantee_gross_floor_vnd"])
            min_online = float(self.newbie["guarantee_min_online_h"]) * 60.0
            if actor.online_min >= min_online and actor.gross_vnd < floor:
                topup = int(round((floor - actor.gross_vnd)
                                  * self.policy.driver_share))   # top up the driver's share of the shortfall
                actor.newbie_topup_vnd += topup
                actor.payout_vnd += topup
                self.log(actor.actor_id, "newbie_guarantee_topup", actor.cell,
                         tenure_days=actor.tenure_days, gross_day=actor.gross_vnd,
                         floor_vnd=floor, topup_vnd=topup)
        # 2) the first-week trip milestone, which needs history; a single day sees only today
        if actor.tenure_days <= 7:
            mem = (self.advice.memory or {}).get(actor.actor_id)
            prior = sum(mem.trips_hist[-(actor.tenure_days - 1):]) if (
                mem and actor.tenure_days > 1) else 0
            already = bool(getattr(mem, "newbie_week1_paid", False)) if mem else False
            total7 = prior + actor.trips_done
            if not already and total7 >= int(self.newbie["first_week_trip_target"]):
                bonus = int(self.newbie["first_week_bonus_vnd"])
                actor.payout_vnd += bonus
                self.log(actor.actor_id, "newbie_week1_bonus", actor.cell,
                         tenure_days=actor.tenure_days, trips_7d=total7, bonus_vnd=bonus)
                if mem:
                    mem.newbie_week1_paid = True   # the event is written at settlement, so nothing leaks from the future

    def _inject_orders(self):
        """Move orders that have reached the current time into the open pool."""
        now = self.env.now
        while self._order_ptr < len(self.orders_sorted) and self.orders_sorted[self._order_ptr].t_min <= now:
            o = self.orders_sorted[self._order_ptr]
            self.open_orders[o.order_id] = o
            self.order_open_since[o.order_id] = now
            self._order_transition(o.order_id, "OPEN")
            self._order_ptr += 1

    def _order_expiry_proc(self):
        """The passenger cancels once their patience runs out unmatched. Per order and exogenous,
    so it is safe for paired runs."""
        while True:
            yield self.env.timeout(0.5)  # swept twice a simulated minute
            now = self.env.now
            expired = [oid for oid, t0 in self.order_open_since.items()
                       if oid in self.open_orders
                       and (now - t0) > self.open_orders[oid].patience_min]
            for oid in expired:
                self.open_orders.pop(oid, None)
                self.order_open_since.pop(oid, None)
                self._order_transition(oid, "EXPIRED")
                self.log(-1, "order_expired", order_id=oid)

    def _dispatcher_proc(self):
        tick_min = float(self.cfg.get("time.dispatch_tick_s")) / 60.0
        while True:
            yield self.env.timeout(tick_min)
            self._inject_orders()
            if not self.open_orders:
                continue
            idle = [a for a in self.actors.values() if a.state == ActorState.IDLE]
            if not idle:
                continue
            hour = int(self.env.now // 60) % 24
            assigns = match_batch(list(self.open_orders.values()), idle, self.grid, hour,
                                  self.speed_cfg, self.disp_cfg,
                                  speed_fn=lambda cell, h: self._eff_speed(h, cell), detour=self.detour,
                                  factor_fn=self._dfac if self.road else None)
            for asg in assigns:
                order = self.open_orders.get(asg.order_id)
                actor = self.actors.get(asg.actor_id)
                if order is None or actor is None or actor.state != ActorState.IDLE:
                    continue
                # Tuyến đường ngập lụt phong tỏa tuyệt đối không gán xe vào
                if self.envctx is not None and hasattr(self.envctx, "is_route_passable"):
                    if not self.envctx.is_route_passable(self.env.now, order.pickup_cell, order.drop_cell):
                        continue
                # do not re-offer the same order to the same driver during the cooldown
                pair = (asg.order_id, asg.actor_id)
                t_last = self.offer_history.get(pair)
                if t_last is not None and (self.env.now - t_last) < self.offer_cooldown:
                    continue
                self.offer_history[pair] = self.env.now
                actor.orders_offered += 1
                actor.idle_streak_min = 0.0   # being offered work is evidence of demand here
                # is there charge for the whole job, pickup and trip, at each leg's own road factor?
                total_km = (asg.pickup_dist_km * self._dfac(actor.cell, order.pickup_cell)
                            + order.dist_km * self._dfac(order.pickup_cell, order.drop_cell))
                enough = actor.soc_pct - total_km * self._pct_per_km(actor) > 8.0
                forced = actor.acceptance_rate < 0.5 and actor.orders_offered > 5
                if not enough:
                    # No longer counted as a cancellation: the driver never accepted the
                    # order. Cancellations now count only what happens after acceptance, which
                    # matches the meaning of the operator's own column.
                    actor.orders_soc_skipped += 1
                    # This branch used to increment a counter only, making it invisible on the
                    # timeline, so there was no way to explain why a driver dropped out of the
                    # market for a while.
                    self.log(actor.actor_id, "order_skipped_soc", actor.cell,
                             order_id=order.order_id, soc_pct=round(actor.soc_pct, 1),
                             need_km=round(total_km, 2))
                    continue
                # The first human seam: a human driver decides whether to accept, in place of
                # the instinct model. The instinct is still evaluated first and still consumes
                # the same random draws as the autonomous arm; the gate only overrides the
                # result. The rest-commitment and advice blocks below follow the same
                # discipline. Reversing this order would shift the whole fleet's stream.
                dec = decide_accept(actor, order.gross_vnd, asg.pickup_dist_km, forced, self.rng,
                                    self.pickup_disutility_km, self.accept_center, self.accept_scale)
                if actor.actor_id == self.human_actor_id:
                    dec = yield from self.hil_gate.ask_accept_offer(actor, order, asg, dec)
                if not dec.accepted:
                    # record enough grounds to answer why this particular trip was refused
                    self.log(actor.actor_id, "order_declined", actor.cell,
                             order_id=order.order_id, reason=dec.reason,
                             net_vnd=round(dec.net_vnd), pickup_km=round(asg.pickup_dist_km, 2),
                             gross_vnd=order.gross_vnd, p_accept=round(dec.p_accept, 4))
                    continue
                # accept the order
                actor.orders_accepted += 1
                self.open_orders.pop(order.order_id, None)
                self.order_open_since.pop(order.order_id, None)
                self._order_transition(order.order_id, "MATCHED")
                # Driving to a pickup deliberately sets no movement target. The real
                # destination of this journey is the dropoff, an uncertain distance and possibly
                # tens of minutes away. Treating a driver carrying a passenger as supply
                # arriving at that cell would inflate supply everywhere and stop the advisor
                # recommending cells that are genuinely short of drivers. The limitation is
                # labelled rather than overlooked, and a test names it.
                actor.state = ActorState.ENROUTE
                # Snapshot order_matched sau boundary state mutation.  Event details remain
                # identical; only the observer's canonical actor state changes from idle to
                # enroute.  No RNG or simulator decision is added here.
                self.log(actor.actor_id, "order_matched", actor.cell, order_id=order.order_id,
                         net_vnd=round(dec.net_vnd), pickup_km=round(asg.pickup_dist_km, 2),
                         gross_vnd=order.gross_vnd, p_accept=round(dec.p_accept, 4),
                         reason=dec.reason)
                self.env.process(self._serve_trip(actor, order, asg))

    def _serve_trip(self, actor: Actor, order, asg):
        hour = int(self.env.now // 60) % 24
        pct_per_km = self._pct_per_km(actor)
        origin_cell = actor.cell
        t_assign = self.env.now
        frm = (actor.lat, actor.lon)   # position before setting off for the pickup
        # the real road factor per leg, falling back to the constant detour
        fac_pick = self._dfac(origin_cell, order.pickup_cell)
        fac_trip = self._dfac(order.pickup_cell, order.drop_cell)
        pickup_min = self._travel_min(asg.pickup_dist_km, hour, origin_cell, fac=fac_pick, dest_cell=order.pickup_cell)

        # --- Cancellation after acceptance, occurring on the way to the pickup ---
        # Decided before the timeout and without looking ahead: it uses only the random stream,
        # never the trip's outcome. The driver has covered part of the pickup leg when the order
        # is cancelled.
        if self.rng.random() < self.cancel_after_accept:
            frac = float(self.rng.uniform(0.3, 1.0))    # somewhere between a third and all of the pickup leg
            spent = pickup_min * frac
            yield self.env.timeout(spent)
            actor.consume_soc(asg.pickup_dist_km * fac_pick * frac, pct_per_km)
            actor.empty_min += spent                    # the real loss: time and charge, and no money
            actor.orders_cancelled += 1
            lat = actor.lat + (order.pickup_lat - actor.lat) * frac
            lon = actor.lon + (order.pickup_lon - actor.lon) * frac
            self._set_pos(actor, lat, lon)
            self._seg(actor.actor_id, t_assign, self.env.now, "enroute", frm, (lat, lon),
                      order_id=order.order_id)
            self._order_transition(order.order_id, "CANCELLED_AFTER_ACCEPT")
            actor.state = ActorState.IDLE
            self.log(actor.actor_id, "order_cancelled_after_accept", actor.cell,
                     order_id=order.order_id, wasted_min=round(spent, 2))
            return

        yield self.env.timeout(pickup_min)
        actor.consume_soc(asg.pickup_dist_km * fac_pick, pct_per_km)
        actor.empty_min += pickup_min
        actor.state = ActorState.ON_TRIP
        self._set_pos(actor, order.pickup_lat, order.pickup_lon)  # position is the real pickup point
        self._seg(actor.actor_id, t_assign, self.env.now, "enroute", frm,
                  (order.pickup_lat, order.pickup_lon), order_id=order.order_id)
        self._order_transition(order.order_id, "PICKED_UP")
        self.log(actor.actor_id, "pickup", order.pickup_cell,
                 order_id=order.order_id, eta_min=round(pickup_min, 2))
        # carrying the passenger
        hour = int(self.env.now // 60) % 24
        t_pickup = self.env.now
        trip_min = self._travel_min(order.dist_km, hour, order.pickup_cell, fac=fac_trip, dest_cell=order.drop_cell)
        yield self.env.timeout(trip_min)
        actor.occupied_min += trip_min
        actor.consume_soc(order.dist_km * fac_trip, pct_per_km)
        # check for being stranded, if charge runs out mid-trip
        if actor.soc_pct <= 0.0:
            actor.stranded_count += 1
            self.log(actor.actor_id, "battery_stranded", order.drop_cell, order_id=order.order_id)
        actor.trips_done += 1
        actor.orders_completed += 1
        actor.gross_vnd += order.gross_vnd
        actor.payout_vnd += self.policy.driver_payout_from_gross(order.gross_vnd, vehicle_class=getattr(actor, "vehicle_class", None))
        actor.points += self.policy.trip_points(int(order.t_min // 60) % 24)
        # after the trip the position is the real dropoff, with no teleport back to the core
        self._set_pos(actor, order.drop_lat, order.drop_lon)
        self._seg(actor.actor_id, t_pickup, self.env.now, "on_trip",
                  (order.pickup_lat, order.pickup_lon), (order.drop_lat, order.drop_lon),
                  order_id=order.order_id, gross=order.gross_vnd,
                  payout=self.policy.driver_payout_from_gross(order.gross_vnd), dist_km=order.dist_km)
        self._order_transition(order.order_id, "COMPLETED")
        self.log(actor.actor_id, "dropoff", order.drop_cell,
                 order_id=order.order_id, gross=order.gross_vnd, dist_km=order.dist_km)

        # --- The passenger rates the trip, on its own random stream ---
        if self.rng_rating.random() < self.rating_p:
            p5 = float(self.rating_p5.get(actor.archetype, 0.78))
            u = self.rng_rating.random()
            if u < p5:
                stars = 5
            elif u < p5 + (1 - p5) * self.rating_p4s:
                stars = 4
            else:
                stars = int(self.rng_rating.integers(1, 4))   # low ratings are rare
            actor.ratings_n += 1
            actor.ratings_sum += stars
            actor.ratings_5 += int(stars == 5)
            self.log(actor.actor_id, "trip_rated", order.drop_cell,
                     order_id=order.order_id, stars=stars)

        # --- Mission progress: deterministic, no random draws ---
        done_hour = int(self.env.now // 60) % 24
        for m in self.mission_catalog:
            w = m.get("window")
            if w is not None and not (int(w[0]) <= done_hour < int(w[1])):
                continue
            mid = m["mission_id"]
            cur = actor.mission_progress.get(mid, 0)
            if cur >= int(m["target"]):
                continue                               # already complete; the reward is paid once
            actor.mission_progress[mid] = cur + 1
            if cur + 1 == int(m["target"]):
                reward = int(m["reward_vnd"])
                actor.mission_reward_vnd += reward
                actor.payout_vnd += reward
                self.log(actor.actor_id, "mission_completed", order.drop_cell,
                         mission_id=mid, reward_vnd=reward, name=m.get("name", mid))

        actor.state = ActorState.IDLE

    def _o_loi_gan_nhat(self, actor: Actor) -> str:
        """The nearest core cell to where the actor stands, factored out so the human path can ask
    before moving."""
        for r in range(1, 8):
            for c in grid_disk(actor.cell, r):
                if self.grid.is_core(c):
                    return c
        return self.grid.core_cells[0]

    def _relocate_to_core(self, actor: Actor, target: str | None = None):
        """After a dropoff outside the core, deadhead back to the nearest core cell. It is a real
        leg costing time and charge, starting from the dropoff, and appears as a relocation.

        A target is given only when a human driver chose a cell at the out-of-core stop. For
        every autonomous actor it is absent, so the nearest core cell is used and the path is
        bit-identical to before, which the fingerprint gate guards.
        """
        if target is None:
            target = self._o_loi_gan_nhat(actor)
        d = cell_distance_km(self.grid, actor.cell, target)
        fac = self._dfac(actor.cell, target)
        hour = int(self.env.now // 60) % 24
        pct_per_km = self._pct_per_km(actor)
        t0 = self.env.now
        frm = (actor.lat, actor.lon)
        t = self._travel_min(d, hour, actor.cell, fac=fac, dest_cell=target)
        actor.state = ActorState.ENROUTE
        actor.enroute_cell = target        # supply on its way to this core cell
        yield self.env.timeout(t)
        actor.consume_soc(d * fac, pct_per_km)
        actor.empty_min += t
        clat, clon = self._cell_point(target)
        actor.state = ActorState.IDLE
        actor.enroute_cell = None          # on arrival it becomes supply in place and is no longer incoming
        self._set_pos(actor, clat, clon)  # M0-10: cell sync trong _set_pos
        self._seg(actor.actor_id, t0, self.env.now, "relocate", frm, (clat, clon), reason="deadhead_to_core")
        self.log(actor.actor_id, "relocate", target, reason="deadhead_to_core")

    def _actor_proc(self, actor: Actor):
        # wait for the shift to start
        if actor.shift_start_min > self.env.now:
            yield self.env.timeout(actor.shift_start_min - self.env.now)
        actor.state = ActorState.IDLE
        alat, alon = self._cell_point(actor.cell)   # the day starts at the home cell
        self._set_pos(actor, alat, alon)
        self.log(actor.actor_id, "go_online", actor.cell)
        last = self.env.now
        self._last_accrual[actor.actor_id] = last  # lets the end-of-day settlement flush the busy stretch

        # Online minutes are the whole span from going online to going offline, accumulated at
        # the top of the loop. The marker must not be reset inside an action branch, or waiting
        # and serving time would be lost.
        while self.env.now < self.end_min:
            # busy: yield and check again later
            if actor.state in (ActorState.ENROUTE, ActorState.ON_TRIP, ActorState.CHARGING, ActorState.REST):
                yield self.env.timeout(1.0)
                continue
            if actor.state == ActorState.OFFLINE:
                break
            now = self.env.now
            actor.online_min += (now - last)  # fold in all the elapsed time: waiting, serving and charging
            last = now
            self._last_accrual[actor.actor_id] = last  # M0-4
            # dropped off outside the core: deadhead back, then check again
            if not self.grid.is_core(actor.cell):
                # The third human seam. A human driver could still be moved without pressing
                # anything: the deadhead back to the core runs before the other two seams, so no
                # stop ever saw it, and it fired a few times a shift. The human actor is now
                # asked, and the question has real content — which core cell. The advisor
                # suggests the nearest; any other core cell remains selectable.
                dich = None
                if actor.actor_id == self.human_actor_id:
                    dich = yield from self.hil_gate.ask_ve_loi(
                        actor, now, self._o_loi_gan_nhat(actor))
                yield from self._relocate_to_core(actor, dich)
                continue
            hour = int(now // 60) % 24
            hint = self._actor_demand_hint(actor, hour)
            action, target = choose_idle_action(actor, now, self.grid, self.veh, hour, hint,
                                                self.rng, self.cfg.get("behavior", {}) or {})
            # Parameters chosen by a human driver, such as which station to swap at or how
            # long to rest. Empty for every autonomous actor, and empty for the human actor when
            # they choose nothing, in which case the engine follows the old path. Each key is
            # read only in its own action branch, so a parameter left over from a previous turn
            # cannot be picked up by a later one.
            tham_so: dict = {}

            # The second human seam: the human driver decides what to do while idle. Same
            # discipline as the first — the instinct runs first and consumes the same draws as
            # the autonomous arm, and the gate only overrides.
            #
            # A budget of button presses. Measured, idle points outnumber order offers by five
            # to ten times, so asking at all of them means over a hundred interruptions a day
            # and an unusable demo. The gate decides which turns are worth asking about, by
            # default only when the instinct intends something other than waiting. That
            # filtering rule lives in the gate rather than here, so the engine need know nothing
            # about the interface.
            if actor.actor_id == self.human_actor_id:
                # The advisor's recommendation goes *into* the stop rather than around it.
                #
                # The standby plan assigns destination cells each bucket. This is the
                # positioning channel, the one advisor channel with a significant measured
                # effect on earnings. For autonomous actors the idle loop below reads that plan
                # and overrides the action. For a human actor it must not: overriding after the
                # person has pressed something is the system acting on the driver's behalf,
                # which the product boundary forbids.
                #
                # So here it is offered as advice with a source, and the person decides.
                khuyen = self.standby_plan.get(actor.actor_id)
                mode = self.advice.positioning_overrides
                # The same condition as the autonomous branch below: repositioning advice is
                # only valid when the driver has nothing better to do. An instinct heading for a
                # swap, a rest or the end of the shift is a constraint of charge, health or
                # working hours, and advising a move over one of those is exactly what the rest
                # experiments measured as a loss. Here the instinct acts as a timing selector
                # rather than as something to display; a later cadence cycle replaces it with a
                # state condition carrying a measurable threshold.
                hop_luc = (action == IdleAction.WAIT if mode == "wait_only"
                           else action in (IdleAction.WAIT, IdleAction.RELOCATE))
                # Re-enforcing the distance rail, after a report that the advisor suggested
                # cells across the whole map.
                #
                # The config declares a hard rail: how many kilometres further than the nearest
                # candidate a driver may be sent. But it lived only in the choice of the
                # *preferred* cell. The allocator then redistributes whatever is contested, and
                # it received no distance at all — candidates carried a target, a charge
                # priority and a tier gap. Once the allocator pushed a driver off their
                # preferred cell, no distance constraint remained.
                #
                # Measured on one actor over dozens of assignments: the chosen cell was
                # typically two and a half kilometres away while the nearest candidate at the
                # same moment was a few hundred metres, and it was further in nearly every case
                # — beyond the rail the config declares, in a core barely two kilometres across.
                #
                # Enforced deliberately on the human branch only. Changing the allocator would
                # change the behaviour of every autonomous actor and move the fingerprint, which
                # needs its own measurement cycle. This branch sits inside the human-actor
                # condition, and the fingerprint gate runs with no human actor, so it never
                # executes during that measurement. Conflating the two kept this item blocked
                # longer than it needed to be.
                #
                # No new threshold is introduced: the figure the config already declares is
                # used, because thresholds are a matter of policy rather than of prose. Beyond
                # the rail it falls back to the nearest still-valid cell rather than going
                # silent, since silence would cost the one channel with a measured effect.
                goi_y = None
                if mode != "off" and hop_luc and khuyen is not None and khuyen != actor.cell:
                    khuyen = self.luat_nguoi.o_dich_hop_le(actor, khuyen, now)
                if mode != "off" and hop_luc and khuyen is not None and khuyen != actor.cell:
                    goi_y = {"source": "advisor", "channel": "positioning",
                             "action": IdleAction.RELOCATE.value, "target": khuyen,
                             "decision_id": self.standby_decision.get(actor.actor_id, "")}
                action, target, tham_so = yield from self.hil_gate.ask_idle_action(
                    actor, now, action, target, goi_y=goi_y)

            # --- Carrying out a rest commitment, placed after the instinct under the same
            # discipline: the instinct always consumes the same draws as the control arm and the
            # gate only overrides.
            # Only waiting, relocating and resting may be overridden. Swapping, charging and
            # ending the shift are constraints of physics or working hours, and forcing rest over
            # them means a late swap or an overrun shift. If the promised hour passes because of
            # that, the commitment breaks and the right to rest is returned, which is more
            # honest.
            rest_forced = False
            if action in (IdleAction.WAIT, IdleAction.RELOCATE, IdleAction.REST):
                fired = rest_commit_gate(actor, now)
                if fired == "kept":
                    self.log(actor.actor_id, "advice_rest_commit_kept", actor.cell,
                             channel="rest_window", from_action=action.value)
                    action, target, rest_forced = IdleAction.REST, None, True
                elif fired == "broken":
                    # log only: no decision id and no followed flag, so it stays out of the adherence denominator
                    self.log(actor.actor_id, "advice_rest_commit_broken", actor.cell,
                             channel="rest_window")

            # --- Ask the advisor, when this driver is covered and due ---
            # Deliberately after the idle choice: the instinct is still evaluated and still
            # consumes its draws exactly as in the control arm, so enabling advice does not shift
            # the actor's random stream. Advice only overrides the result, and that is what makes
            # paired-seed comparison meaningful.
            #
            # The acceptance-lift channel warns when the acceptance rate is below the bonus
            # threshold. It is the most valuable channel, because the daily bonus pays nothing
            # below that threshold regardless of points.
            gate = self.advice.check_bonus_gate(actor, now)
            if gate is not None:
                self.log(actor.actor_id, "advice_bonus_gate", actor.cell,
                         acceptance=gate.acceptance_now, threshold=gate.threshold,
                         lift=gate.lift_applied, followed=gate.followed,
                         accept_lift_total=round(actor.accept_lift, 4),
                         decision_id=self._decision_id(actor.actor_id, "accept_lift", now),
                         channel="accept_lift")

            # the shift-extension channel: defer the end of shift when close to a tier, with a cap
            added, extend_why = self.advice.check_shift_extend(
                actor, now, float(self.veh["swap_soc_threshold_pct"]))
            if added:
                self.log(actor.actor_id, "advice_shift_extend", actor.cell,
                         added_min=round(added, 1), points=int(actor.points),
                         new_shift_end=round(actor.shift_end_min, 1), followed=True,
                         decision_id=self._decision_id(actor.actor_id, "shift_extend", now),
                         channel="shift_extend")
            elif extend_why in EXTEND_RAILS:
                # A health rail has to be visible. This function previously returned a bare
                # zero and the caller's truthiness check swallowed it, so a rail added here would
                # block correctly while leaving no trace at all: no reason, no counter, nothing
                # the guardrail layer could see. That is a silent rejection, and a mechanism that
                # exists only on paper — two failure families this repository has paid for.
                #
                # Logged only when the reason is a rail, never for a disabled channel or a
                # cadence miss: writing events for a disabled channel would change the event
                # stream of every default run and break comparability with every paired result
                # already measured. Symmetric, line for line, with the rest veto above.
                #
                # Only the reason is recorded, never the fatigue threshold value. An earlier
                # version logged the threshold for readability and a gate caught it correctly:
                # this function sits inside the money scope, where fatigue tokens are forbidden.
                # The gate was right — the reason alone is enough, and the number was a
                # convenience bought with a boundary violation.
                self.log(actor.actor_id, "advice_extend_veto", actor.cell,
                         reason=extend_why, points=int(actor.points),
                         online_min=round(actor.online_min, 1),
                         soc_pct=round(actor.soc_pct, 1), channel="shift_extend")

            adv = self.advice.consult(actor, now, self._actor_demand_hint, actor.shift_end_min)
            if adv is not None:
                # within one tick, given, followed and suppressed share a decision id
                did = self._decision_id(actor.actor_id, "shift_plan", now)
                self.log(actor.actor_id, "advice_given", actor.cell,
                         solver_action=adv.solver_action, adherence=adv.adherence,
                         followed=adv.followed, instinct_action=action.value,
                         plan_next=adv.plan_next_action, reason=adv.reason,
                         decision_id=did, channel="shift_plan")
                if adv.followed and adv.mapped_action is not None:
                    # A rest recommendation from the shift solver means "do not spend this
                    # window online earning". But swapping, charging and relocating are already
                    # not earning: they are transitional actions the solver does not model at
                    # all, since its action space is coarser than the actor's.
                    #
                    # Overriding them with rest is therefore both redundant, because the
                    # solver's intent is already satisfied, and harmful. Measured over six
                    # seeds, it forced drivers who were on their way to swap, or moving toward a
                    # busy area, to stop and rest instead — together more than half of all
                    # advisor interventions.
                    if (self.advice.rest_only_overrides_wait
                            and adv.mapped_action == IdleAction.REST
                            and action in (IdleAction.GO_SWAP, IdleAction.GO_CHARGE,
                                           IdleAction.RELOCATE)):
                        self.log(actor.actor_id, "advice_suppressed", actor.cell,
                                 solver_action=adv.solver_action,
                                 instinct_action=action.value,
                                 reason="rest_would_override_productive_action",
                                 decision_id=did, channel="shift_plan")
                    else:
                        if adv.mapped_action != action:
                            self.log(actor.actor_id, "advice_followed", actor.cell,
                                     from_action=action.value, to_action=adv.mapped_action.value,
                                     decision_id=did, channel="shift_plan")
                        action = adv.mapped_action
                    target = None      # advice does not name a cell

            # --- The early-swap channel: it overrides waiting only, never rest or
            # relocation, and sits before positioning so that charge wins over position when
            # both want to speak.
            # The flag check comes before the station choice, and that order is kept under the
            # general discipline that a disabled channel calls nothing extra.
            #
            # A correction worth keeping: an earlier note here claimed the station chooser
            # consumed a random draw. It does not. The ordering is still right and still worth
            # keeping, but the real reason is the general discipline rather than a draw needing
            # protection. Do not reason further from the old premise.
            if action == IdleAction.WAIT and self.advice.ch_swap_early:
                # The live stations, which carry queues and batteries, rather than the static
                # geographic list. The charging routine uses this same list.
                st_n = choose_station(actor, self.grid, self.stations, now, self.rng)
                if st_n is not None:
                    ok_swap, why_swap = self.advice.check_swap_early(
                        actor, now, int(st_n.queue_len),
                        st_n.available_full(now) > 0,
                        float(self.veh["swap_soc_threshold_pct"]))
                    if ok_swap:
                        self.log(actor.actor_id, "advice_swap_early", actor.cell,
                                 station=st_n.node_id, soc=round(actor.soc_pct, 1),
                                 queue_len=int(st_n.queue_len), followed=True,
                                 decision_id=self._decision_id(actor.actor_id, "swap_early", now),
                                 channel="swap_early")
                        action, target = IdleAction.GO_SWAP, None

            # --- The positioning channel: the idle loop only reads the plan the planner
            # wrote ---
            # Repositioning is forbidden on the product path and enabled here in the simulation
            # to study systemic risk. The compliance draw was taken once at assignment time and
            # is not repeated here.
            reloc_reason = "demand_seek"
            sb_cell = self.standby_plan.get(actor.actor_id)
            # A human actor already heard this recommendation at the stop above and decided
            # for themselves. The reason lives in the human-rules module rather than as a bare
            # constant here.
            if actor.actor_id == self.human_actor_id:
                if not self.luat_nguoi.cho_phep_ghi_de_standby():
                    sb_cell = None
            if sb_cell is not None:
                mode = self.advice.positioning_overrides
                if sb_cell == actor.cell:
                    # already in the right cell: the assignment is complete and waiting here is what it meant
                    self.standby_plan.pop(actor.actor_id, None)
                    self.standby_decision.pop(actor.actor_id, None)
                    self.market.pending_targets.pop(actor.actor_id, None)
                elif (action == IdleAction.WAIT if mode == "wait_only"
                      else action in (IdleAction.WAIT, IdleAction.RELOCATE)):
                    # Never override swapping, resting or ending the shift: charge, health and
                    # working hours outrank position, and overriding them was measured as a
                    # loss.
                    self.standby_plan.pop(actor.actor_id, None)
                    self.market.pending_targets.pop(actor.actor_id, None)
                    self.log(actor.actor_id, "standby_followed", actor.cell,
                             from_action=action.value, to_cell=sb_cell,
                             decision_id=self.standby_decision.pop(actor.actor_id, ""),
                             channel="positioning")
                    action, target, reloc_reason = IdleAction.RELOCATE, sb_cell, "standby"

            # suppressed by the shared cadence: log a typed reason, once per decision
            for (t_s, aid_s, topic_s, reason_s) in self.advice.drain_suppressed():
                # Its own decision id: what is suppressed is a *new* candidate, not the
                # decision already spoken in the same bucket. Sharing an id would let the
                # suppressed event overwrite the earlier decision's followed state, and the
                # measured effect was a large drop in decision-level adherence while the
                # event-level figure stayed put.
                self.log(aid_s, "advice_suppressed", "",
                         channel=topic_s, reason=reason_s,
                         decision_id=self._decision_id(aid_s, topic_s, t_s) + "-sup")

            # --- The rest-window channel: concentrate rest into quiet hours ---
            #   - Only rest is handled. The swap and charge branches were dead code, never
            #     reached because the charge check fires first, and keeping them misrepresented
            #     the mechanism's scope.
            #   - Deferring is a commitment: the bridge records the due mark and adds to the
            #     quota once. The fall-through must not be waiting; setting it to wait turned
            #     most rest into empty waiting that produced no extra orders, and that single
            #     line was the entire mechanism.
            #   - A forced rest, coming from the commitment gate above, is not re-asked:
            #     re-asking would let the bridge defer the very rest it just promised.
            if action == IdleAction.REST and not rest_forced:
                defer, why, alt = self.advice.should_defer_rest(
                    actor, now, hour, self._actor_demand_hint,
                    float(self.veh["swap_soc_threshold_pct"]),
                    # A deterministic probe that takes no draws. Passing the combined
                    # relocation function drew while answering "is there useful work", and that
                    # gate runs before the cadence check, so the treatment arm consumed draws
                    # for decisions that were rejected while the control arm did not. Measured
                    # over three days and two seeds, the draw counts diverged and so did the
                    # fingerprints.
                    self._alt_action_probe(hour, hint))
                if defer:
                    if why != "committed":
                        # A new commitment, logged once rather than every tick; later ticks
                        # before the promised hour take the committed branch and log nothing.
                        self.log(actor.actor_id, "advice_rest_window", actor.cell,
                                 deferred_from=action.value, reason=why, followed=True,
                                 commit_hour=int(actor.rest_commit_due_min // 60) % 24,
                                 alt_action=alt[0].value,
                                 deferred_total_min=round(actor.rest_deferred_min, 1),
                                 decision_id=self._decision_id(actor.actor_id, "rest_window", now),
                                 channel="rest_window")
                    # The relocation reason makes a deferral-driven move traceable through
                    # events and segments. Without it the fall-through looks identical to the
                    # instinct's own demand-seeking, and a regression back to waiting would have
                    # nothing to catch it — the familiar case of a gate checking both ends and
                    # missing the middle.
                    action, target = alt
                    reloc_reason = "rest_defer"
                else:
                    # The non-deferral outcome must be observable too. A rail veto is
                    # evidence that the rail is alive: remove the rail and the veto count
                    # collapses to zero, which the guardrail layer reports. Log only — no
                    # decision id and no followed flag, so it stays out of the adherence
                    # denominator — with no draws and no change to state or action.
                    self.log(actor.actor_id, "advice_rest_veto", actor.cell,
                             reason=why or "cadence_suppressed", from_action=action.value,
                             channel="rest_window")

            # The outcome of advice that was given on a branch which does not log itself.
            # Drained after both call sites; draining earlier would push this tick's record into
            # the next tick.
            #
            # Without this, events for those two channels exist only where the advice was
            # followed, so the adherence denominator contains only compliers and the rate is one
            # by construction — the same failure as an earlier missing denominator.
            #
            # The decision id is derived from the time the advice was *given*, not from the drain
            # time, so it matches the followed branch's decision. And it carries no suppression
            # suffix: this was not suppressed. The advisor spoke, the driver heard and did
            # otherwise, which belongs in the denominator.
            for (t_nf, aid_nf, topic_nf, followed_nf, reason_nf) in \
                    self.advice.drain_spoken_outcomes():
                self.log(aid_nf, _SPOKEN_OUTCOME_KIND[topic_nf], "",
                         channel=topic_nf, followed=followed_nf, reason=reason_nf,
                         added_min=0.0,
                         decision_id=self._decision_id(aid_nf, topic_nf, t_nf))

            if action == IdleAction.END_SHIFT:
                actor.state = ActorState.OFFLINE
                self._newbie_settle(actor)   # settle the newcomer top-up before closing the books
                # the daily bonus layer, a rule component; bonuses are a fifth to a third of income
                bonus = self.policy.day_bonus(actor.points, actor.acceptance_rate, actor.completion_rate)
                actor.payout_vnd += bonus
                self.log(actor.actor_id, "end_shift", actor.cell,
                         trips=actor.trips_done, payout=actor.payout_vnd,
                         points=actor.points, day_bonus=bonus)
                break
            elif action in (IdleAction.GO_SWAP, IdleAction.GO_CHARGE):
                yield from self._do_charge(actor, action, tham_so.get("station_id"))
            elif action == IdleAction.REST:
                # Rest actually happened, so any open commitment is cleared and the right to
                # rest is restored. A rest forced by the gate already cleared the commitment
                # there, so it is not logged as cleared twice. With the channel off both fields
                # stay unset and this block is inert.
                if actor.rest_commit_due_min is not None:
                    actor.rest_commit_due_min = None
                    self.log(actor.actor_id, "advice_rest_commit_cleared", actor.cell,
                             channel="rest_window")
                actor.rest_commit_broken = False
                actor.state = ActorState.REST
                self.log(actor.actor_id, "rest", actor.cell)
                t0 = now
                # Draw first, then override, as at every other seam in this file: the instinct
                # always consumes exactly the autonomous arm's draws and the human only replaces
                # the value. Skipping this call when the person chooses a duration would shift
                # the whole fleet's stream.
                rest_min = self.rng.uniform(REST_MIN_MINUTES, REST_MAX_MINUTES)
                if tham_so.get("rest_min") is not None:
                    rest_min = float(tham_so["rest_min"])
                actor.rest_min += rest_min
                yield self.env.timeout(rest_min)
                actor.state = ActorState.IDLE
                self._seg(actor.actor_id, t0, self.env.now, "rest",
                          (actor.lat, actor.lon), (actor.lat, actor.lon))
            elif action == IdleAction.RELOCATE and target:
                d = cell_distance_km(self.grid, actor.cell, target)
                fac_rel = self._dfac(actor.cell, target)
                t0 = now
                frm = (actor.lat, actor.lon)
                t = self._travel_min(d, hour, actor.cell, fac=fac_rel, dest_cell=target)
                actor.state = ActorState.ENROUTE
                actor.enroute_cell = target    # supply on its way to this cell
                yield self.env.timeout(t)
                # A voluntary relocation costs charge along the real route. This block used to
                # deduct none, which made movement energy-free.
                actor.consume_soc(d * fac_rel, self._pct_per_km(actor))
                actor.empty_min += t
                clat, clon = self._cell_point(target)
                actor.state = ActorState.IDLE
                actor.enroute_cell = None     # on arrival it is no longer incoming supply
                self._set_pos(actor, clat, clon)  # M0-10
                self._seg(actor.actor_id, t0, self.env.now, "relocate", frm, (clat, clon), reason=reloc_reason)
                actor.idle_streak_min = 0.0   # having moved, the streak starts again
                self.log(actor.actor_id, "relocate", target, reason=reloc_reason)
            else:  # WAIT
                actor.idle_min += 2.0
                actor.idle_streak_min += 2.0   # the idle streak, reset whenever work is offered
                # record idle time per hour so the solver can name an hour worth resting through
                actor.idle_by_hour[hour] = actor.idle_by_hour.get(hour, 0.0) + 2.0
                yield self.env.timeout(2.0)  # wait for work and check again shortly

    def _actor_demand_hint(self, actor: Actor, hour: int) -> dict[str, float]:
        """Personal experience: the expected demand field from configuration, times per-actor noise
        sampled once per actor and hour and then cached. The belief is stable across the day, is
        never read from the realised trace, and is not resampled at every idle check.

        The per-cell noise uses a sub-generator seeded deterministically from the seed, actor and
        hour, and iterates cells in sorted order, so the result does not depend on the process
        hash seed — the root cause of a cross-process nondeterminism found earlier."""
        key = (actor.actor_id, hour, actor.cell)  # the cell is in the key: the view changes on moving
        cached = self._belief_cache.get(key)
        if cached is not None:
            return cached
        field = self.demand_field.get(hour, {})
        if not field:
            self._belief_cache[key] = {}
            return {}
        sigma = actor.demand_prior_sigma
        hint: dict[str, float] = {}
        from .geo import grid_disk
        for c in sorted(grid_disk(actor.cell, 2)):
            base = field.get(c, 0.0)
            # Lognormal noise, always positive, so the error is multiplicative; experienced
            # drivers have a smaller sigma and so are more accurate.
            # The noise is per cell and deterministic in the seed, actor, hour and cell, so a
            # given cell always looks the same to a given driver whatever position they view it
            # from. That makes the memory consistent and independent of iteration order.
            rng_c = np.random.default_rng((self.seed, actor.actor_id, hour, int(c, 16)))
            noise = math.exp(rng_c.normal(0.0, sigma))
            hint[c] = base * noise
        self._belief_cache[key] = hint
        return hint

    def _do_charge(self, actor: Actor, action: IdleAction, station_id: int | None = None):
        if action == IdleAction.GO_CHARGE:
            # Charging at home means travelling home first: a real leg costing time and
            # charge and producing a segment, rather than teleporting or charging in place.
            hour = int(self.env.now // 60) % 24
            pct_per_km = self._pct_per_km(actor)
            actor.state = ActorState.CHARGING
            if actor.cell != actor.home_cell:
                d = cell_distance_km(self.grid, actor.cell, actor.home_cell)
                fac = self._dfac(actor.cell, actor.home_cell)
                t0 = self.env.now
                frm = (actor.lat, actor.lon)
                t = self._travel_min(d, hour, actor.cell, fac=fac, dest_cell=actor.home_cell)
                yield self.env.timeout(t)
                actor.consume_soc(d * fac, pct_per_km)
                actor.empty_min += t
                hlat, hlon = self._cell_point(actor.home_cell)
                self._set_pos(actor, hlat, hlon)  # M0-10
                self._seg(actor.actor_id, t0, self.env.now, "relocate", frm, (hlat, hlon),
                          reason="go_home_charge")
            self.log(actor.actor_id, "charge_home_start", actor.cell)
            t0 = self.env.now
            dur = float(self.veh["home_charge_min"])
            actor.charge_min += dur
            yield self.env.timeout(dur)
            actor.soc_pct = 100.0
            actor.state = ActorState.IDLE
            self._seg(actor.actor_id, t0, self.env.now, "charge",
                      (actor.lat, actor.lon), (actor.lat, actor.lon), mode="home")
            self.log(actor.actor_id, "charge_home_end", actor.cell)
            return
        # swap at the station
        station = choose_station(actor, self.grid, self.stations, self.env.now, self.rng)
        # --- The advisor suggests a station from live global state. Placed after the
        # instinct under the usual discipline: the instinct always draws as in the control arm
        # and the advisor only overrides. The picker is deterministic and the compliance draw is
        # a hash, so a disabled channel takes no extra draws.
        if station is not None and self.advice.ch_station_choice:
            _h = int(self.env.now // 60) % 24

            def _tmin(s):
                _d = cell_distance_km(self.grid, actor.cell, s.cell)
                return self._travel_min(_d, _h, actor.cell, fac=self._dfac(actor.cell, s.cell), dest_cell=s.cell)

            better, why_st = self.advice.pick_station(actor, self.stations, self.env.now,
                                                      _tmin, station)
            if better is not None:
                self.log(actor.actor_id, "advice_station_choice", actor.cell,
                         station=better.node_id, instinct_station=station.node_id,
                         followed=True,
                         decision_id=self._decision_id(actor.actor_id, "station_choice",
                                                       self.env.now),
                         channel="station_choice")
                station = better
        # A station chosen by a human driver beats both the instinct and the advisor: it is
        # their decision, and the system does not act on a driver's behalf. This block therefore
        # has to sit after the picker; placed before it, the station channel would silently
        # change the station the person had just chosen. The instinct call above is left in place
        # rather than replaced: it takes no draws, but leaving it means nobody has to prove that
        # again.
        if station_id is not None and station_id in self.station_by_id:
            station = self.station_by_id[station_id]
        if station is None:
            # no stations exist in this world: a labelled fallback rather than silence
            actor.soc_pct = 100.0
            actor.state = ActorState.IDLE
            self.log(actor.actor_id, "swap_fallback_no_station", actor.cell)
            return
        hour = int(self.env.now // 60) % 24
        pct_per_km = self._pct_per_km(actor)
        d = cell_distance_km(self.grid, actor.cell, station.cell)
        fac = self._dfac(actor.cell, station.cell)
        travel = self._travel_min(d, hour, actor.cell, fac=fac, dest_cell=station.cell)
        t0 = self.env.now
        frm = (actor.lat, actor.lon)
        actor.state = ActorState.CHARGING
        self.log(actor.actor_id, "go_swap", actor.cell, station=station.node_id)
        yield self.env.timeout(travel)
        actor.consume_soc(d * fac, pct_per_km)
        self._set_pos(actor, station.lat, station.lon)  # the position is the real station, with the cell kept in sync
        actor.empty_min += travel
        # the leg travelling to the station
        self._seg(actor.actor_id, t0, self.env.now, "relocate", frm, (station.lat, station.lon),
                  reason="go_swap", station=station.node_id)
        t_arrive = self.env.now

        # --- SẠC NHANH V-GREEN DC (Ô TÔ ĐIỆN) HOẶC ĐỔI PIN TỦ (XE MÁY) ---
        if self.car_sim_enabled:
            station.queue_len += 1
            wait = 0.0
            wait_cap = float(self.cfg.get("station.wait_cap_min", 60.0))
            if station.queue_len > station.slots:
                wait = min(wait_cap, float(self.rng.uniform(3.0, 10.0)))
                yield self.env.timeout(wait)
            station.queue_len = max(0, station.queue_len - 1)

            # Thời gian sạc trụ nhanh V-Green DC theo tỷ lệ pin cần sạc
            avg_charge_min = float(self.car_charging.get("avg_charge_duration_min", 35.0))
            needed_pct = max(10.0, 100.0 - actor.soc_pct)
            charge_duration = avg_charge_min * (needed_pct / 80.0)
            yield self.env.timeout(charge_duration)

            cinfo = self.car_classes.get(getattr(actor, "vehicle_class", "A_COMPACT"), {})
            bat_kwh = float(cinfo.get("battery_kwh", 37.23))
            kwh_charged = (needed_pct / 100.0) * bat_kwh
            price_kwh = float(self.car_charging.get("electricity_price_kwh_vnd", 3858.0))
            charge_cost = int(round(kwh_charged * price_kwh))

            actor.charge_min += travel + wait + charge_duration
            actor.cost_vnd += charge_cost
            actor.soc_pct = 100.0
            actor.state = ActorState.IDLE

            self._seg(actor.actor_id, t_arrive, self.env.now, "charge",
                      (station.lat, station.lon), (station.lat, station.lon),
                      mode="vgreen_fast_dc", wait_min=round(wait, 1), station=station.node_id,
                      charge_duration_min=round(charge_duration, 1), kwh=round(kwh_charged, 2),
                      cost_vnd=charge_cost)
            self.log(actor.actor_id, "swap_done", station.cell, station=station.node_id,
                     wait_min=round(wait, 1), mode="vgreen_fast_dc",
                     charge_duration_min=round(charge_duration, 1), kwh_charged=round(kwh_charged, 2),
                     cost_vnd=charge_cost, vehicle_class=getattr(actor, "vehicle_class", "A_COMPACT"))
            return

        # queue: swap only when a battery is genuinely ready; past the wait cap the swap fails
        station.queue_len += 1
        wait = 0.0
        wait_cap = float(self.cfg.get("station.wait_cap_min", 60.0))
        swapped = False
        recharge = float(self.cfg.get("station.battery_recharge_min"))
        while wait <= wait_cap:
            full = [b for b in station.batteries
                    if b.soc_pct >= station.ready_soc_pct and b.ready_at_min <= self.env.now]
            if full:
                # The exchange is atomic at the start of the swap: a full battery out and the
                # empty one straight into a charging slot. The cabinet's battery count is
                # invariant at every instant, which prevents two drivers claiming one battery.
                full.sort(key=lambda b: b.ready_at_min)
                station.batteries.remove(full[0])
                station.batteries.append(
                    BatteryInStation(soc_pct=100.0, ready_at_min=self.env.now + recharge))
                swapped = True
                break
            yield self.env.timeout(1.0)
            wait += 1.0
        station.queue_len = max(0, station.queue_len - 1)
        if not swapped:
            # No battery ready within the wait cap: leave with charge unchanged rather than
            # conjuring one. The instinct will head for a swap again at a later decision point,
            # by which time the cabinet has recharged, so there is no livelock.
            actor.charge_min += travel + wait
            actor.state = ActorState.IDLE
            self._seg(actor.actor_id, t_arrive, self.env.now, "charge",
                      (station.lat, station.lon), (station.lat, station.lon),
                      mode="swap_failed", wait_min=round(wait, 1), station=station.node_id)
            self.log(actor.actor_id, "swap_failed", station.cell,
                     station=station.node_id, wait_min=round(wait, 1))
            return
        swap_s = self.rng.uniform(float(self.cfg.get("station.swap_time_s_min")),
                                  float(self.cfg.get("station.swap_time_s_max")))
        yield self.env.timeout(swap_s / 60.0)
        actor.charge_min += travel + wait + swap_s / 60.0
        # the one-for-one exchange already happened atomically in the queue above
        actor.soc_pct = 100.0
        actor.state = ActorState.IDLE
        self._seg(actor.actor_id, t_arrive, self.env.now, "charge",
                  (station.lat, station.lon), (station.lat, station.lon),
                  mode="swap", wait_min=round(wait, 1), station=station.node_id)
        actor.cost_vnd += self.swap_fee_vnd   # zero by default; a separate ledger that never touches payout
        self.log(actor.actor_id, "swap_done", station.cell, station=station.node_id, wait_min=round(wait, 1))
