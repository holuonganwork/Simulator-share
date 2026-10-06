"""The session snapshot: what the interface and the agent read to know the current state.

The first rule is that nothing may leak from the future, and it is not automatically satisfied.
The live world holds the whole day's orders in memory, generated once at the start. Reading
casually from that would show a driver orders that have not happened, ruining both the
demonstration and any research conclusion.

There are three legitimate sources and no fourth:
  1. the event log, filtered to events at or before now. It is append-only, so its tail is always
     the past, but the filter is explicit so that a later refactor cannot quietly open the door;
  2. the actor's current state, which by definition is the value at now, because the world is
     frozen;
  3. the context of the pending stop itself: the trip being offered is what the driver is looking
     at, not the future.

Absolutely excluded: the order list, run segments, run results, or anything computed over the
whole day. A gate enforces this.

Four stops, of which two are commands and two are signals. New trip and idle are implemented as
stops that take a command; low charge and approaching bonus tier appear here as signals in the
snapshot for the interface to display, with their corresponding actions belonging to a later
slice. Stated so that nobody reads it as an omission.
"""
from __future__ import annotations

from typing import Any

from .commands import PauseKind

# Signal thresholds, gathered here rather than scattered, so the interface, the agent and the
# tests all read one place.
SOC_CANH_BAO_PCT = 25.0        # below this, mention charge
SOC_NGUY_HIEM_PCT = 15.0       # below this, being stranded is imminent
SAP_HET_CA_MIN = 60.0          # with under an hour of shift left, mention finishing
BONUS_GAN_DIEM = 3             # within a few points of the next tier, mention it

# Events carrying money or trips, used to build the day so far without touching future data.
KIND_HOAN_THANH = "dropoff"
KIND_CHAO = ("order_matched", "order_declined", "order_skipped_soc")

# How many cells the heatmap shows. The tail is cut because drawing the whole grid makes the map
# unreadable and because cells with near-zero demand tell the driver nothing. Cells are ranked by
# demand and the top ones kept.
HEATMAP_TOP_N = 40
# The radius of an actor's beliefs. It must match the disc the world uses to build the demand
# hint; a different number here would misrepresent what the driver knows.
VIEW_RADIUS = 2


def _enum_hoac_chuoi(value):
    """The enum's value, a plain string as it is, or nothing; the type is never guessed."""
    if value is None:
        return None
    return getattr(value, "value", value if isinstance(value, str) else None)


def _hh_mm(t_min: float) -> str:
    """Minutes of the day as a clock time. A simulated day is one day long, so there is no
    wrap to handle."""
    t = int(round(t_min))
    return f"{(t // 60) % 24:02d}:{t % 60:02d}"


def snapshot(session) -> dict[str, Any]:
    """The session state at now. Read-only: it never advances the simulation.

    Two consecutive calls must return an identical result, and a gate checks that. If they
    differ, something is recomputing with a random draw or reading the real clock."""
    w = session.world
    now = float(w.env.now)
    aid = session.human_actor_id
    actor = w.actors.get(aid) if aid is not None else None

    snap: dict[str, Any] = {
        "session_id": session.session_id,
        "state": session.state,
        "version": session.version,
        "t_min": round(now, 3),
        "clock": _hh_mm(now),
        "day_end_min": float(w.end_min),
        "human_actor_id": aid,
        "allowed_commands": session.allowed_commands(),
        "pause": _pause_block(session),
        "driver": _driver_block(w, actor, now) if actor is not None else None,
        "day_so_far": _day_so_far(w, aid, now) if aid is not None else None,
    }
    if snap.get("day_so_far") is not None:
        snap["day_so_far"]["phan_bo_thoi_gian"] = _phan_bo_thoi_gian(w, aid, now, actor)
        snap["day_so_far"]["doanh_thu_theo_gio"] = _doanh_thu_theo_gio(w, aid, now)
    snap["chang"] = _chang(w, aid, now)
    snap["tram"] = _tram(w, actor)
    snap["thang_thuong"] = _thang_thuong(w, actor)
    snap["nhiem_vu"] = _nhiem_vu(w, actor, now)
    snap["signals"] = _signals(snap, actor, now, w)
    # Cost-benefit counters, so that suppressed advice and its effect can be counted.
    #
    # These are diagnostics rather than anything said to a driver: they sit in the snapshot so an
    # engineer can read the real cadence, not for the interface to draw. Empty when the weight is
    # zero, which is the default, and empty there is correct rather than broken.
    dem = getattr(w, "_dem_cost_benefit", None)
    if dem:
        snap["cost_benefit"] = dict(dem)
    return snap


def _nhiem_vu(world, actor, now: float) -> dict[str, Any]:
    """The day's missions and this driver's progress through them.

    Why this block exists: the data already existed, in the mission catalogue from config and the
    actor's own progress, and a solver could read it. But it only ever reached chat: a driver
    heard that two more trips would finish something without ever seeing the list — how many
    missions there are, which are done, which have not opened, and what each is worth.

    That is the same omission the bonus ladder had: a table that exists in the engine with no
    window onto it in the interface.

    Every figure comes from one source, the catalogue and the progress record. The mission table
    is never copied into the interface, because a copy is a second source and a config change
    would then make the screen lie with no gate firing.

    Three states rather than two: complete, where the target is met and the reward is already in
    the payout; open, where the window is running or there is none, and progress can continue;
    and not yet open, where the window has not arrived. Folding the third into the second is what
    produced advice urging a driver toward a mission that had not opened, so they are separated
    here and the interface does not have to infer it.
    """
    catalog = list(getattr(world, "mission_catalog", None) or [])
    if not catalog or actor is None:
        return {"co": False, "danh_sach": [], "tong_thuong_da_dat_vnd": 0}
    gio = int(now // 60) % 24
    tien_do = dict(getattr(actor, "mission_progress", None) or {})
    ds: list[dict[str, Any]] = []
    for m in catalog:
        mid = str(m.get("mission_id") or "")
        target = int(m.get("target") or 0)
        dem = int(tien_do.get(mid, 0) or 0)
        khung = m.get("window")
        # A null window means the whole day, not "not yet open". Misreading it labels an
        # all-day mission as unopened for the entire shift.
        trong_khung = khung is None or (int(khung[0]) <= gio < int(khung[1]))
        chua_mo = khung is not None and gio < int(khung[0])
        xong = target > 0 and dem >= target
        ds.append({
            "mission_id": mid,
            "ten": str(m.get("name") or mid),
            "muc_tieu": target,
            "da_lam": min(dem, target) if target else dem,
            "con_lai": max(0, target - dem),
            "thuong_vnd": int(m.get("reward_vnd") or 0),
            "khung": [int(khung[0]), int(khung[1])] if khung else None,
            "trang_thai": "xong" if xong else ("chua_mo" if chua_mo else
                                               ("dang_mo" if trong_khung else "het_khung")),
        })
    # Ordered by what can be acted on now: open first, then not yet open, then expired, then
    # complete. A driver opens this panel to see what to do now, not to admire achievements.
    _UU = {"dang_mo": 0, "chua_mo": 1, "het_khung": 2, "xong": 3}
    ds.sort(key=lambda x: (_UU.get(x["trang_thai"], 9), x["mission_id"]))
    return {
        "co": True,
        "danh_sach": ds,
        # Rewards already earned are read from the actor rather than re-summed from the list: a
        # second computation of the same money figure would eventually drift from the first.
        "tong_thuong_da_dat_vnd": int(getattr(actor, "mission_reward_vnd", 0) or 0),
    }


def _thang_thuong(world, actor) -> dict[str, Any]:
    """The daily bonus ladder and progress through it, read from the running policy bundle.

    The data already fed the "approaching a tier" signal, but a driver only ever saw how many
    points remained, never the ladder itself: not what the tier after that is worth, nor what the
    one they just passed was worth.

    Every figure comes from one source, the policy's own tier table and accessors. The interface
    must not copy the table: a copy is a second source, and after a policy change the screen would
    lie with no gate firing.

    The policy's two constraints are stated as well, since the daily bonus also requires the
    acceptance and completion rates to clear their thresholds. Hiding them would promise money
    that may not arrive.
    """
    pol = getattr(world, "policy", None)
    if pol is None or not getattr(pol, "day_bonus_tiers", None):
        return {"co_thang": False, "moc": [], "diem": 0, "thuong_dang_co_vnd": 0,
                "con_thieu_diem": None, "thuong_moc_ke_vnd": None, "rang_buoc": None}
    diem = int(getattr(actor, "points", 0) or 0) if actor is not None else 0
    ty_nhan = float(getattr(actor, "acceptance_rate", 0.0) or 0.0) if actor is not None else 0.0
    ty_xong = float(getattr(actor, "completion_rate", 0.0) or 0.0) if actor is not None else 0.0
    ke = pol.next_tier_gap(diem)
    return {
        "co_thang": True,
        "diem": diem,
        "moc": [{"diem": int(pt), "thuong_vnd": int(vnd), "da_dat": diem >= int(pt)}
                for pt, vnd in pol.day_bonus_tiers],
        # The daily bonus accessor applies the rate constraints, unlike the raw tier lookup.
        # Using the constrained one is deliberate: the figure a driver reads must be what they
        # would actually receive if the shift ended now, not the tier they would otherwise have
        # reached.
        "thuong_dang_co_vnd": int(pol.day_bonus(diem, ty_nhan, ty_xong)),
        "con_thieu_diem": int(ke[0]) if ke else None,
        "thuong_moc_ke_vnd": int(ke[1]) if ke else None,
        # Both constraints are exposed along with the current rates. Hiding them would promise
        # money that may not arrive, and income is never promised.
        "rang_buoc": {"ty_le_nhan_toi_thieu": round(float(pol.bonus_min_acceptance), 4),
                      "ty_le_hoan_thanh_toi_thieu": round(float(pol.bonus_min_completion), 4),
                      "ty_le_nhan_hien_tai": round(ty_nhan, 4),
                      "ty_le_hoan_thanh_hien_tai": round(ty_xong, 4),
                      "dat": ty_nhan >= float(pol.bonus_min_acceptance)
                             and ty_xong >= float(pol.bonus_min_completion)},
    }


def _tram(world, actor) -> dict[str, Any]:
    """The swap stations of the running world, or the reason there are none.

    Why this is not simply a station list: the fleet type is a *battery strategy* rather than a
    vehicle class. One fleet swaps packs at a cabinet; the other has a fixed pack and charges at
    home. For that second group there is no station to choose, and showing an empty list would be
    a lie told by the interface, since they would take it for a fault. So this block carries a
    reason and not only an array.

    Distances are computed by the server with the same function the engine uses to move actors. A
    client computing its own straight line would produce a slightly different number that nobody
    could account for.

    The station markers on the base map are drawn from a static demo profile and may not be the
    stations the engine is using; they yield once a session is active.
    """
    from ..entities import FleetType
    from ..geo import cell_distance_km

    if actor is None:
        return {"doi_pin_duoc": False, "ly_do": "phiên không có tài xế", "danh_sach": []}
    if getattr(actor, "fleet", None) is not FleetType.SWAP:
        return {"doi_pin_duoc": False,
                # This string reaches the driver directly, through the station query and the
                # energy channel, so its form of address matters; it is not JSON-only.
                "ly_do": "xe của bác sạc tại nhà (pin liền, không tháo ra được)",
                "danh_sach": []}
    ds = []
    for st in world.stations:
        try:
            km = round(float(cell_distance_km(world.grid, actor.cell, st.cell)), 2)
        except Exception:  # noqa: BLE001 - an unknown cell shows no distance rather than a guessed one
            km = None
        ds.append({"station_id": int(st.node_id), "lat": float(st.lat), "lon": float(st.lon),
                   "cell": str(st.cell), "queue_len": int(st.queue_len),
                   "cu_ly_km": km})
    # Nearest first; a station whose distance cannot be measured sinks to the end rather than
# disappearing.
    ds.sort(key=lambda t: (t["cu_ly_km"] is None, t["cu_ly_km"] or 0.0, t["station_id"]))
    return {"doi_pin_duoc": True, "ly_do": None, "danh_sach": ds}


# How many recent legs are exposed: enough for a trip's three legs with room to spare, and
# capped so the snapshot does not grow with the length of the shift.
MAX_CHANG = 8


def _chang(world, actor_id: int | None, now: float) -> list[dict[str, Any]]:
    """The legs the driver has completed, most recent first, capped in number.

    Why it is needed: between two snapshots a driver may travel three legs — out to the pickup,
    on to the dropoff, then away again — while the interface receives only two endpoints, the old
    position and the new one. Joining those two with a line draws a journey that never happened,
    which is what the reports of teleporting were seeing. The engine does not teleport: it records
    the pickup and then the dropoff. The loss is in transmission, not in the model.

    Three rules for this block:

    1. A leg that has not ended does not exist. Segments are only written after the wait
       completes, so the condition is mechanically redundant; it stays because it is the
       no-future invariant, and one day someone will write a segment early.
    2. No money. Trip segments carry gross and payout in their metadata. Exposing them here would
       create a second source for a figure the day summary already gives, and would have to be
       declared in the money manifest. A driver's money has one window.
    3. Idempotent. There is no notion of "since the last snapshot": a polling read must return
       exactly the same thing, or one poll would rob the next render of its legs.
    """
    if actor_id is None:
        return []
    ra = []
    for sg in world.segments:
        if sg.get("actor_id") != actor_id or float(sg.get("t1", 0.0)) > now:
            continue
        ra.append({
            "t0": round(float(sg["t0"]), 3),
            "t1": round(float(sg["t1"]), 3),
            "kind": str(sg.get("kind") or ""),
            "reason": str(sg.get("reason") or "") or None,
            "order_id": str(sg["order_id"]) if sg.get("order_id") is not None else None,
            "from_lat": round(float(sg["from_lat"]), 6),
            "from_lon": round(float(sg["from_lon"]), 6),
            "to_lat": round(float(sg["to_lat"]), 6),
            "to_lon": round(float(sg["to_lon"]), 6),
        })
    return ra[-MAX_CHANG:]


def demand_map(session, *, top_n: int = HEATMAP_TOP_N) -> dict[str, Any]:
    """A demand heatmap per cell for the current hour, plus the cell currently suggested.

    Why the source leaks nothing: it is the expected demand field, which is orders per day times
    the hour's share times the cell's weight — pure configuration, deterministic, reading no order
    of the run. That function exists precisely as the safe replacement for an earlier field built
    from the run's own orders. It is also what actors use as their beliefs, so the heatmap matches
    what the engine sees.

    Only the current hour is returned. Returning the whole day would still be model parameters,
    but it would turn a read-the-present surface into a preview one, and the module's discipline
    that everything speaks about now would be lost to two functions with two horizons.

    The server sends the hexagon boundaries because the client has no grid library, and because
    clients here never compute displayed values. The geometry travels with the data and the client
    only draws.
    """
    import h3

    w = session.world
    now = float(w.env.now)
    hour = int(now // 60) % 24
    field = dict((getattr(w, "demand_field", None) or {}).get(hour) or {})

    aid = session.human_actor_id
    actor = w.actors.get(aid) if aid is not None else None
    driver_cell = getattr(actor, "cell", None) if actor is not None else None

    # A target cell only means something when the pending stop is an idle one and something has
    # pointed at a cell. It is read from the stop's own hint, the same source the interface is
    # displaying, rather than by re-running the idle chooser, which would consume a random draw
    # and create a second source for one decision.
    #
    # Advisor only. The branch that also accepted the simulation's instinct was removed: the
    # heatmap was marking the instinct's chosen cell as "suggested by the assistant" about ten
    # times a shift, attributing a behaviour-model decision to the advisor. The old branch had
    # been kept "for compatibility", and what it was compatible with was the defect itself.
    target_cell = None
    p = session.pause
    if p is not None and p.kind is PauseKind.IDLE:
        hint = p.hint or {}
        if hint.get("source") == "advisor" and str(hint.get("action") or "") == "relocate":
            target_cell = hint.get("target") or None

    xep = sorted(field.items(), key=lambda kv: (-float(kv[1]), kv[0]))[:max(1, int(top_n))]
    # The target cell is always included, even when its demand does not reach the top of the
    # ranking. The ranking exists to keep the map readable, not to hide the very thing being
    # recommended: the positioning channel scores by supply against demand, so it can point at a
    # cell with moderate demand and few drivers.
    if target_cell and target_cell not in dict(xep):
        xep.append((target_cell, float(field.get(target_cell, 0.0))))
    # Normalised against the busiest cell of this hour, taken before the target is appended so
    # that appending it does not depress the colour scale for everything else.
    max_v = max((float(v) for _, v in xep), default=0.0)

    # What the driver can see. The map draws the model's global demand, while the repositioning
    # decision reads beliefs only within a small disc around the driver. Without stating that
    # difference, a bright cell outside their view looks exactly like a broken algorithm.
    #
    # The disc is computed directly rather than by calling the belief function: only membership
    # is needed, not the values. That keeps this read-only surface away from the belief cache, so
    # there is no question of opening the map perturbing the random stream.
    from ..geo import grid_disk
    trong_tam = frozenset(grid_disk(driver_cell, VIEW_RADIUS)) if driver_cell else frozenset()

    # The catchment around the target cell.
    #
    # Measured, only about a quarter of a driver's pickups fall in the cell they are standing in;
    # the rest come from neighbours, some several rings out, at a median distance of a few
    # hundred metres.
    #
    # That is not a simulation defect. The dispatcher draws candidates from a disc around the
    # pickup and the real constraint is the arrival-time limit, so standing in a busy cell brings
    # more work because more orders arise *around* it, not because they arise inside it. Real
    # dispatch works the same way.
    #
    # It is, however, an explanation defect: highlighting one hexagon and then sourcing trips
    # from elsewhere makes the system look self-contradictory. So the catchment is exposed too,
    # for the interface to shade lightly around the target.
    tam_don = int(session.world.cfg.get("dispatcher.candidate_ring_k_max") or 0)
    vung_don = frozenset(grid_disk(target_cell, tam_don)) if (target_cell and tam_don)         else frozenset()

    cells: list[dict[str, Any]] = []
    for cell, val in xep:
        try:
            bien = [[round(float(la), 6), round(float(lo), 6)]
                    for la, lo in h3.cell_to_boundary(cell)]
        except Exception:  # noqa: BLE001 - skip an unknown cell rather than draw a wrong shape
            continue
        cells.append({
            "h3": cell,
            "boundary": bien,
            "expected_orders": round(float(val), 3),
            # A normalised intensity for the client's colour scale, relative to the busiest
            # cell of this hour rather than absolute across the day.
            "intensity": round(float(val) / max_v, 3) if max_v > 0 else 0.0,
            "is_target": cell == target_cell,
            "is_driver": cell == driver_cell,
            # Whether the cell is within the driver's view. Absent when their position is not
            # known, which is different from false, and the interface must distinguish the two or
            # it will grey out the whole map.
            "in_view": (cell in trong_tam) if driver_cell else None,
            # Whether the cell is in the target's catchment, meaning a trip arising here could
            # reach the driver standing at the target. Absent when there is no target.
            "in_reach": (cell in vung_don) if target_cell else None,
        })

    # Empty is a valid state rather than a failure: the config models demand for daytime hours
    # only, so night hours have no cells at all. The reason is stated so the interface can show an
    # honest sentence instead of an unexplained blank map, which reads as a broken system.
    empty_reason = None
    if not cells:
        empty_reason = (f"chưa mô hình hoá nhu cầu cho khung {hour:02d}h "
                        f"(config chỉ có {min(_gio_co(w), default=0):02d}h–{max(_gio_co(w), default=0):02d}h)")

    return {
        "session_id": session.session_id,
        "t_min": round(now, 3),
        "clock": _hh_mm(now),
        "hour": hour,
        "cells": cells,
        "count": len(cells),
        "target_cell": target_cell,
        "driver_cell": driver_cell,
        # Distances are computed by the server, with the same function the engine uses to move
        # actors, so the figure a driver reads matches the distance the simulation actually makes
        # them travel. A client computing its own straight line would produce a slightly
        # different number that nobody could account for.
        "target_distance_km": _khoang_cach_km(w, driver_cell, target_cell),
        "max_expected_orders": round(max_v, 3),
        "view_radius": VIEW_RADIUS if driver_cell else None,
        "reach_radius": tam_don if target_cell else None,
        "cells_in_reach": sum(1 for c in cells if c.get("in_reach")) if target_cell else None,
        "reach_note": ("cuốc tới tay tài xế khi ô phát sinh đơn nằm trong tầm đón của chỗ anh "
                       "ta đứng — nên đổi chỗ là đổi cả VÙNG, không phải một ô"),
        "cells_in_view": sum(1 for c in cells if c["in_view"]) if driver_cell else None,
        "view_note": ("bản đồ vẽ nhu cầu mô hình của cả khu vực; tài xế chỉ quan sát được "
                      f"{VIEW_RADIUS} vòng ô quanh chỗ đang đứng, và quyết định đổi chỗ chỉ "
                      "dùng phần trong tầm đó"),
        "empty_reason": empty_reason,
        "is_mock": True,
        "source": "gsm_sim.demand.expected_demand_field (λ từ config, không đọc orders của run)",
        "scale_note": "cường độ tương đối trong GIỜ hiện tại, không so được giữa các giờ",
    }


def _khoang_cach_km(world, a: str | None, b: str | None) -> float | None:
    """Distance between two cells, on the engine's own measure; absent without both ends."""
    if not a or not b:
        return None
    from ..geo import cell_distance_km
    try:
        return round(float(cell_distance_km(world.grid, a, b)), 2)
    except Exception:  # noqa: BLE001 - an unknown cell shows no distance rather than a guessed one
        return None


def _gio_co(world) -> list[int]:
    """The hours the config models demand for, used to explain an empty heatmap."""
    return sorted(int(h) for h in (getattr(world, "demand_field", None) or {}))


def _pause_block(session) -> dict[str, Any] | None:
    """The pending stop and its context.

    The hint is a suggestion, not an instruction: the system does not act on the driver's behalf,
    so the interface must present it as a suggestion with its reason."""
    p = session.pause
    if p is None:
        if session.state == "READY_TO_START":
            return {"kind": "BEFORE_SHIFT", "t_min": round(float(session.world.env.now), 3),
                    "hint": session._before_shift_hint(), "context": {}, "offer_ref": None}
        return None
    pending = session.current_pending
    return {"kind": p.kind.value, "t_min": p.t_min, "offer_ref": p.offer_ref,
            "hint": dict(p.hint), "context": dict(pending.context) if pending else {}}


def khung_gio(cfg, policy) -> dict[str, Any]:
    """The hours of this world worth knowing about, for the panel shown before an experience is
    chosen.

    Every figure is read from the running config and policy bundle; none is written by hand in the
    interface. That is what keeps the panel correct after someone edits the config: a hard-coded
    "peak is six to eight" would start lying the moment the config changed, with nobody knowing.

    Two different kinds of "special" are kept apart, because they are easily confused:
      - busy hours, where more orders arise;
      - high-scoring hours, where each trip earns more points.
    An hour can be one without being the other, in either direction.
    """
    from ..demand import _hour_intensity

    share = _hour_intensity(cfg)
    tong = sum(share.values()) or 1.0
    xep = sorted(share.items(), key=lambda kv: (-kv[1], kv[0]))
    dong = [{"hour": h, "share_pct": round(100.0 * w / tong, 1)} for h, w in xep[:5]]
    vang = [{"hour": h, "share_pct": round(100.0 * w / tong, 1)}
            for h, w in sorted(xep[-3:], key=lambda kv: kv[0])]

    diem_cao = sorted(int(h) for h in (getattr(policy, "point_peak_hours", None) or []))
    tiers = [{"points": int(p), "vnd": int(v)}
             for p, v in (getattr(policy, "day_bonus_tiers", None) or [])]
    gio_co = sorted(share)
    return {
        "gio_mo_hinh_hoa": [gio_co[0], gio_co[-1]] if gio_co else [],
        "dong_khach": dong,
        "vang_khach": vang,
        "gio_diem_cao": diem_cao,
        "diem_moi_cuoc": {"cao_diem": int(getattr(policy, "point_peak", 0) or 0),
                          "thuong": int(getattr(policy, "point_normal", 0) or 0)},
        "moc_thuong_ngay": tiers,
        "source": "configs/pilot_hanoi.yaml (demand.hour_weights) + PolicyBundle đang chạy",
    }


def _vi_tri(w, actor) -> tuple[float, float]:
    """The driver's coordinates, falling back to the cell centre before a continuous position
    has been set.

    A measured defect: before the shift starts the engine has never set a position, so the
    coordinates are still zero. The interface faithfully took them and panned to the origin,
    leaving the driver with a blank map at the very step where they choose when to start.

    The cell is always present, assigned at sampling time, so the cell centre is the right answer:
    it is honest about where the driver is, only less precise. Fixed on the server, because the
    client must not derive coordinates from a cell index itself.
    """
    lat = float(getattr(actor, "lat", 0.0) or 0.0)
    lon = float(getattr(actor, "lon", 0.0) or 0.0)
    if lat or lon:
        return round(lat, 6), round(lon, 6)
    tam = (getattr(w.grid, "cell_centroid", None) or {}).get(getattr(actor, "cell", None))
    if tam:
        return round(float(tam[0]), 6), round(float(tam[1]), 6)
    return 0.0, 0.0


def _driver_block(w, actor, now: float) -> dict[str, Any]:
    """The driver's state at now, which is valid because the world is frozen."""
    lat, lon = _vi_tri(w, actor)
    return {
        "actor_id": actor.actor_id,
        "state": actor.state.value,
        "cell": actor.cell,
        "lat": lat,
        "lon": lon,
        "soc_pct": round(float(actor.soc_pct), 2),
        # The fleet is an enum while the archetype is a plain string. An earlier version called
        # the enum accessor on both, so the archetype was silently null in every snapshot, which
        # the schema permitted. It surfaced only when the driver profile was built and the field
        # came back empty.
        "fleet": _enum_hoac_chuoi(getattr(actor, "fleet", None)),
        "archetype": _enum_hoac_chuoi(getattr(actor, "archetype", None)),
        "shift_start_min": round(float(actor.shift_start_min), 1),
        "shift_end_min": round(float(getattr(actor, "shift_end_min", 0.0) or 0.0), 1),
        "online_min": round(float(actor.online_min), 1),
        "idle_streak_min": round(float(getattr(actor, "idle_streak_min", 0.0)), 1),
        # The three rates a driver is judged on.
        #
        # Cancellations count only those after acceptance, matching the meaning of the operator's
        # own column, and never fold in ignored offers.
        #
        # The denominator is trips accepted, not offers received: cancelling a trip never
        # accepted is not a thing. With no trips accepted the rate is absent rather than zero —
        # "no data yet" is not "never cancelled", and presenting the first as the second is an
        # invented claim.
        "orders_offered": int(getattr(actor, "orders_offered", 0) or 0),
        "orders_accepted": int(getattr(actor, "orders_accepted", 0) or 0),
        "orders_cancelled": int(getattr(actor, "orders_cancelled", 0) or 0),
        "ty_le_huy": (round(float(getattr(actor, "orders_cancelled", 0) or 0)
                            / float(actor.orders_accepted), 4)
                      if int(getattr(actor, "orders_accepted", 0) or 0) > 0 else None),
        "ty_le_nhan": (round(float(actor.acceptance_rate), 4)
                       if int(getattr(actor, "orders_offered", 0) or 0) > 0 else None),
        "ty_le_hoan_thanh": (round(float(actor.completion_rate), 4)
                             if int(getattr(actor, "orders_accepted", 0) or 0) > 0 else None),
    }


# Gom 6 loai `_seg` thanh 4 nhom tai xe hieu duoc. Nhom, khong phoi ten ky thuat:
# "deadhead_to_core" khong noi gi voi nguoi lai xe.
_NHOM_CHANG = {
    "on_trip": "cho_khach",
    "enroute": "di_don",
    "relocate": "di_chuyen",
    "deadhead_to_core": "di_chuyen",
    "charge": "nghi_sac",
    "rest": "nghi_sac",
}
_NHAN_NHOM = {
    "cho_khach": "Chở khách",
    "di_don": "Đi đón",
    "di_chuyen": "Di chuyển",
    "nghi_sac": "Nghỉ / đổi pin",
    "cho": "Chờ",
}


def _doanh_thu_theo_gio(w, aid: int, now: float) -> list[dict[str, Any]] | None:
    """Gross revenue per hour, built from the event log.

    Why gross and not payout: the dropoff event already carries the trip's gross figure, so an
    hourly total is a *sum* of real numbers. Payout cannot be derived: it is gross times a share,
    and that share is a policy figure. Multiplying here would mean computing a financial number
    ourselves, which the product boundary forbids and which the whole placeholder-first
    architecture exists to prevent.

    The label must therefore say revenue and never income. The day's payout already has one
    window, in the day summary, and it stays one window.

    Why adding is allowed where multiplying is not: the boundary is not whether arithmetic is
    permitted but whether the result has a source. A sum of real gross figures still traces back
    to individual trips; a product with a policy coefficient produces a number no trip carries.

    Only elapsed hours are included, under the same no-future rule as the rest of this module. A
    revenue bar for an hour that has not happened is a promise, and income is never promised.
    """
    ev = [e for e in w.events
          if e.actor_id == aid and e.t_min <= now and e.kind == KIND_HOAN_THANH]
    if not ev:
        return None
    theo_gio: dict[int, dict[str, int]] = {}
    for e in ev:
        gio = int(float(e.t_min) // 60)
        o = theo_gio.setdefault(gio, {"gross_vnd": 0, "so_cuoc": 0})
        # An event built by hand in a test may carry no gross figure. Skip it rather than
        # assuming zero and drawing an empty bar as though that hour earned nothing.
        g = e.detail.get("gross")
        if g is None:
            continue
        o["gross_vnd"] += int(g)
        o["so_cuoc"] += 1
    if not theo_gio:
        return None
    return [{"gio": g, "clock": f"{g % 24:02d}:00",
             "gross_vnd": v["gross_vnd"], "so_cuoc": v["so_cuoc"]}
            for g, v in sorted(theo_gio.items())]


def _phan_bo_thoi_gian(w, aid: int, now: float, actor) -> dict[str, Any] | None:
    """Where the shift has gone so far, in minutes and percentages, aggregated for the interface.

    Why it is aggregated on the server: the legs block returns only the most recent few, which is
    not enough for a whole shift, and the interface must not recompute figures. The client
    receives both the minutes and the percentages and only sets a width. One division performed in
    two places will eventually give two answers.

    Waiting is the remainder rather than a kind of leg. The engine writes no segment while a
    driver stands still, so waiting exists only as the gaps between legs. It is elapsed time less
    the sum of the legs, floored at zero.

    That floor is not idle defensiveness: legs can overlap by thousandths of a minute through
    rounding, and a negative number on a driver's screen is far more meaningless than a zero.

    No money here. This block holds time only; a driver's money has one window, and that window
    is the day summary. Adding money here would open a second.
    """
    if actor is None:
        return None
    bat_dau = float(getattr(actor, "shift_start_min", 0.0) or 0.0)
    het = float(getattr(actor, "shift_end_min", 0.0) or 0.0)
    if het <= bat_dau:
        return None
    da_troi = max(0.0, min(float(now), het) - bat_dau)
    if da_troi <= 0.0:
        return None

    phut = {k: 0.0 for k in _NHAN_NHOM}
    for sg in w.segments:
        if sg.get("actor_id") != aid:
            continue
        t1 = float(sg.get("t1", 0.0))
        if t1 > now:            # the same no-future rule as the legs builder
            continue
        nhom = _NHOM_CHANG.get(str(sg.get("kind") or ""))
        if nhom is None:
            continue
        t0 = max(float(sg.get("t0", 0.0)), bat_dau)
        if t1 > t0:
            phut[nhom] += t1 - t0
    phut["cho"] = max(0.0, da_troi - sum(v for k, v in phut.items() if k != "cho"))

    muc = []
    for khoa, nhan in _NHAN_NHOM.items():
        p = round(phut[khoa], 1)
        muc.append({"khoa": khoa, "nhan": nhan, "phut": p,
                    "phan_tram": round(p / da_troi * 100.0, 1)})
    return {"tong_phut": round(da_troi, 1), "muc": muc}


def _day_so_far(w, aid: int, now: float) -> dict[str, Any]:
    """The day so far, built from the event log filtered to now.

    Why from events rather than from the actor's own totals: the actor's payout is the correct
    value at now and is used as such below. But trip and offer counts must be counted from
    events, because that is what the interface displays along a timeline and what the agent has
    to quote back — a refusal at a particular minute, for a particular fare. Two sources for one
    figure is a defect this codebase has had to undo, so every number here has exactly one source
    and says which.
    """
    ev = [e for e in w.events if e.actor_id == aid and e.t_min <= now]
    xong = [e for e in ev if e.kind == KIND_HOAN_THANH]
    chao = [e for e in ev if e.kind in KIND_CHAO]
    tu_choi = [e for e in ev if e.kind == "order_declined"]
    a = w.actors.get(aid)
    return {
        # source: the actor's state at now, which is canonical for money
        "payout_vnd": int(getattr(a, "payout_vnd", 0)) if a is not None else 0,
        # Gross revenue is exposed because the weekly quota is measured on revenue rather than
        # on payout. With only payout in the snapshot, anyone comparing shift progress against
        # the quota would take the wrong figure, and since payout is always the smaller of the
        # two, a driver would be told they were short of a quota they had in fact met. A silent
        # error, and exactly why revenue, payout and estimated net are kept separate.
        "gross_vnd": int(getattr(a, "gross_vnd", 0)) if a is not None else 0,
        "points": int(getattr(a, "points", 0)) if a is not None else 0,
        # source: the filtered event log
        "trips_done": len(xong),
        "offers_seen": len(chao),
        "declines": len(tu_choi),
        "last_event_min": round(float(ev[-1].t_min), 3) if ev else None,
        "timeline": [{"t_min": round(float(e.t_min), 3), "clock": _hh_mm(e.t_min),
                      "kind": e.kind, "cell": e.cell} for e in ev[-20:]],
    }


def _signals(snap: dict, actor, now: float, w) -> list[dict[str, Any]]:
    """The two stops that are not yet commands, emitted as signals.

    Each signal carries a severity and the figure that produced it. The basis is mandatory: a
    warning that does not say what number it rests on forces the agent to invent a reason when
    explaining it. Every threshold here is either a module constant or read from the policy
    bundle; none is invented on the spot."""
    out: list[dict[str, Any]] = []
    if actor is None:
        return out

    # --- pin ---------------------------------------------------------------------------
    soc = float(actor.soc_pct)
    if soc <= SOC_NGUY_HIEM_PCT:
        out.append({"code": "SOC_CRITICAL", "severity": "high",
                    "basis": {"soc_pct": round(soc, 2), "threshold": SOC_NGUY_HIEM_PCT}})
    elif soc <= SOC_CANH_BAO_PCT:
        out.append({"code": "SOC_LOW", "severity": "medium",
                    "basis": {"soc_pct": round(soc, 2), "threshold": SOC_CANH_BAO_PCT}})

    # --- End of shift: no longer emitted -----------------------------------------------
    #
    # This signal used to announce how many minutes remained in the shift. The objection was that
    # the agent cannot know that, and that a driver decides when to stop.
    #
    # The defect goes deeper than the wording. The actor's shift end is an assumption of the
    # *behaviour model*: the simulation assigns a shift length per archetype so that autonomous
    # actors can run. It is not a fact about the person at the screen, and that person has their
    # own finish button. Emitting it as a warning presents a simulation parameter as a fact.
    #
    # The field itself is kept: the shift solver uses it as a planning horizon, which is a valid
    # use, since a plan must have a boundary. What was wrong was telling the driver that horizon
    # as though we knew when they would stop.
    #
    # The signal name is also kept in the contract enum: earlier sessions stored it in their
    # reminder logs, and narrowing the enum would make those unreadable. Not emitting it is
    # enough.

    # --- Close to a bonus tier ----------------------------------------------------------
    # The tiers come from the policy bundle: a verifiable, versioned source. This is the easiest
    # place to violate the rule against invented figures — "three points to go" thought up by the
    # agent would be a fabricated financial number. Here it comes from policy and travels with
    # its basis.
    tiers = tuple(getattr(w.policy, "day_bonus_tiers", ()) or ())
    if tiers:
        diem = int(getattr(actor, "points", 0) or 0)
        ke_tiep = [(pt, vnd) for pt, vnd in tiers if diem < pt]
        if ke_tiep:
            pt, vnd = min(ke_tiep, key=lambda x: x[0])
            thieu = pt - diem
            if thieu <= BONUS_GAN_DIEM:
                out.append({"code": "BONUS_NEAR", "severity": "medium",
                            "basis": {"points": diem, "next_tier_points": pt,
                                      "points_needed": thieu, "tier_bonus_vnd": int(vnd),
                                      "threshold": BONUS_GAN_DIEM}})
    return out
