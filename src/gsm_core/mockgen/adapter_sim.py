"""Adapter: sim T-030 (đã qua M0 integrity) → mock data đúng schema L0/L1.

Nguyên tắc (spec §8.1):
- Chỉ xuất OBSERVABLE — latent của sim (meals_taken, belief, patience) KHÔNG ra ngoài.
- Ledger amount TÁI TÍNH được: trip_payout = driver_share × gross (đúng PolicyBundle sim).
- GPS nội suy tuyến tính dọc segments (~30s/ping) — khớp movement model đi-thẳng của sim.
- Timestamp: phút-sim → ISO UTC+7 trên `date` cho trước. Mọi record nhãn MOCK.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import h3

from gsm_sim.config import Config
from gsm_sim.runner import run_once

TZ = timezone(timedelta(hours=7))
PING_INTERVAL_MIN = 0.5  # ~30s/ping

# sim event kind -> app_event kind (observable projection)
_KIND_MAP = {
    "go_online": "go_online",
    "end_shift": "go_offline",
    "order_matched": "accept",
    "order_declined": "decline",
    "dropoff": "complete",
}


def _iso(date: str, t_min: float) -> str:
    base = datetime.fromisoformat(date).replace(tzinfo=TZ)
    return (base + timedelta(minutes=float(t_min))).isoformat()


def entity_tables(day: dict) -> dict[str, list[dict]]:
    """Chỉ các bảng L1R THẬT trong output của `generate_day`.

    SIM-1: `generate_day` còn trả **kênh nội bộ** (key có tiền tố `_`) cho tầng mockgen —
    KHÔNG phải bảng, KHÔNG theo schema, KHÔNG bao giờ được ghi ra parquet/đưa cho advisor.
    Mọi chỗ duyệt "tất cả bảng" phải đi qua hàm này.
    """
    return {k: v for k, v in day.items() if not k.startswith("_")}


def _tables_from_run(r, cfg: Config, date: str) -> dict[str, list[dict]]:
    """Chuyển MỘT `RunResult` → dict {entity: records} đúng schema.

    D-SIM-13(D): tách khỏi `generate_day` để dùng chung cho cả hai đường:
    ngày ĐỘC LẬP (`generate_day`, giữ cho test cũ) và CHUỖI LIÊN TỤC
    (`generate_days_continuous` — cùng nhóm tài xế qua các ngày, từ `run_multiday`).
    ID trong bảng dùng `r.seed` (mỗi ngày một seed) ⇒ không đụng nhau giữa các ngày.
    """
    seed = r.seed          # tiền tố ID; giữ tên biến cũ cho thân hàm
    out: dict[str, list[dict]] = {}

    # ---- L0: policy_bundle từ config sim (nguồn: sim-policy-bundle-v0 — research thật) ----
    # SIM-3: nội dung policy lấy từ `PolicyBundle.to_core_record()` — nguồn chuyển đổi DUY
    # NHẤT dùng chung với `gsm_sim.advice_bridge`, để tầng data và tầng advisor không bao
    # giờ nói hai con số policy khác nhau. Ở đây chỉ bọc thêm trường riêng của schema L1R.
    out["policy_bundle"] = [{
        "schema_version": "1.0.0",
        "bundle_id": "sim-policy",
        "effective_from": _iso(date, 0),
        "track": "core_owned",  # 100% Taxi Cơ Hữu
        "service": "car",
        **r.policy.to_core_record(),
        "version": str(cfg.get("meta.policy_bundle_version")),
        "source_url": None, "source": "MOCK",
    }]

    # ---- L0: driver_profile (observable projection của Actor — KHÔNG latent) ----
    out["driver_profile"] = [{
        "schema_version": "1.0.0", "driver_id": f"d-{a.actor_id}",
        "track": "core_owned",  # 100% Taxi Cơ Hữu (HĐLĐ, xe công ty)
        "tier": None, "tenure_months": 12,
        "vehicle_type": "car",
        "vehicle_class": getattr(a, "vehicle_class", "A_COMPACT"),
        "vehicle_model": getattr(a, "vehicle_model", "VF5"),
        "seat_capacity": getattr(a, "seat_capacity", 4),
        "fleet": a.fleet.value,
        "home_zone_h3r8": None,  # coarse privacy: không xuất home_cell res9
        "declared_shift_window": [int(a.shift_start_min), int(a.shift_end_min)],
        "source": "MOCK",
    } for a in r.actors]

    # ---- SIM-1 fix D: COUNTER THẬT của sim (KHÔNG phải bảng L1R — tiền tố "_") ----
    # Trước đây tầng data suy NGƯỢC acceptance từ `profiles.target_acceptance` vì sim cũ
    # nhận gần 100% mọi đơn (accept 96.3%) ⇒ data và sim nói 2 con số khác nhau.
    # SIM-1 đã sửa ở GỐC (accept bám accept_base từng archetype) nên data lấy thẳng số này.
    out["_sim_driver_day"] = {
        "date": date,
        "stats": {f"d-{a.actor_id}": {
            "offered": a.orders_offered, "accepted": a.orders_accepted,
            # Cycle 1: `offered` gồm CẢ lượt bị chặn vì pin (`world.py:647` đếm trước cổng SOC ở
            # `:654`) — những lượt tài xế CHƯA TỪNG được hỏi. Xuất thêm `decided` để tầng data
            # không phải tự trừ tay và lệch định nghĩa với `Actor.orders_decided`.
            "soc_skipped": a.orders_soc_skipped, "decided": a.orders_decided,
            "completed": a.orders_completed, "cancelled": a.orders_cancelled,
            # 🔴 2026-08-18: `online_time` của tầng data vốn là số PROFILE, chỉ có sàn là giờ
            # phục vụ cuốc — không dính gì tới quãng thời gian tài xế thật sự có mặt trong
            # thế giới. Còn `public_driver_hex_tracking` thì dựng từ quỹ đạo THẬT. Hai nguồn
            # cho cùng một ngày ⇒ bất biến "Σ chờ ≤ giờ online" đúng nhờ MAY: đo được đỉnh
            # 0,81 trên bộ cũ, và tụt xuống bất khả (1,21) ngay khi quỹ đạo đổi.
            # Đây đúng nguyên tắc SIM-1 fix D ở ngay dưới, chỉ là chưa ai áp cho thời gian.
            "online_min": a.online_min, "idle_min": a.idle_min,
            # SIM-XANH P2: rating + mission + tân binh — SỰ KIỆN sim, không phải gauss tầng data
            "ratings_n": a.ratings_n, "ratings_sum": a.ratings_sum, "ratings_5": a.ratings_5,
            "mission_reward_vnd": a.mission_reward_vnd,
            "mission_progress": dict(a.mission_progress),
            "newbie_topup_vnd": a.newbie_topup_vnd, "tenure_days": a.tenure_days,
        } for a in r.actors},
    }
    # SIM-XANH P2: sự kiện mission hoàn thành + catalog ngày (kênh nội bộ "_")
    out["_sim_missions"] = {
        "date": date,
        "catalog": list(cfg.get("missions.daily_catalog", []) or []),
        "completed": [{"driver_id": f"d-{e.actor_id}", "mission_id": e.detail["mission_id"],
                       "name": e.detail.get("name"), "reward_vnd": e.detail["reward_vnd"],
                       "t_iso": _iso(date, e.t_min)}
                      for e in r.events if e.kind == "mission_completed"],
    }

    # ---- L0: station_registry ----
    out["station_registry"] = [{
        "schema_version": "1.0.0", "station_id": f"s-{s.node_id}",
        "location": {"lat": s.lat, "lon": s.lon, "h3": s.cell},
        "slots": s.slots, "throughput_nominal_per_hour": None, "source": "MOCK",
    } for s in r.stations]

    # ---- L1: app_event từ sim events (observable projection) ----
    events = []
    eid = 0
    for e in r.events:
        kind = _KIND_MAP.get(e.kind)
        if kind is None or e.actor_id < 0:
            continue
        rec = {"schema_version": "1.0.0", "event_id": f"ev-{seed}-{eid}",
               "driver_id": f"d-{e.actor_id}", "t": _iso(date, e.t_min),
               "kind": kind, "source": "MOCK"}
        oid = e.detail.get("order_id")
        if oid is not None:
            rec["order_ref"] = f"o-{seed}-{oid}"
        events.append(rec)
        eid += 1
    # forced_auto_accept flags: sim track acceptance <50% + offers>5 (observable qua app thật)
    out["app_event"] = sorted(events, key=lambda x: (x["driver_id"], x["t"]))

    # ---- L1: trip_record từ orders đã complete ----
    completed: dict[int, dict] = {}
    matched_t: dict[int, float] = {}
    pickup_t: dict[int, float] = {}
    drop_t: dict[int, tuple[float, int]] = {}
    for e in r.events:
        oid = e.detail.get("order_id")
        if oid is None:
            continue
        if e.kind == "order_matched":
            matched_t[oid] = e.t_min
        elif e.kind == "pickup":
            pickup_t[oid] = e.t_min
        elif e.kind == "dropoff":
            drop_t[oid] = (e.t_min, e.actor_id)
    orders = {o.order_id: o for o in r.orders}
    actors_map = {a.actor_id: a for a in r.actors}
    trips = []
    for oid, (t_drop, actor_id) in sorted(drop_t.items()):
        o = orders[oid]
        act = actors_map.get(actor_id)
        v_class = getattr(act, "vehicle_class", getattr(o, "vehicle_class", "A_COMPACT"))
        trips.append({
            "schema_version": "1.0.0", "order_id": f"o-{seed}-{oid}",
            "driver_id": f"d-{actor_id}",
            "service_type": "car",
            "vehicle_class": v_class,
            "t_request": _iso(date, o.t_min),
            "t_assign": _iso(date, matched_t.get(oid, o.t_min)),
            "t_pickup": _iso(date, pickup_t.get(oid, o.t_min)),
            "t_complete": _iso(date, t_drop),
            "pickup": {"lat": o.pickup_lat, "lon": o.pickup_lon, "h3": o.pickup_cell},
            "drop": {"lat": o.drop_lat, "lon": o.drop_lon, "h3": o.drop_cell},
            "dist_km": o.dist_km, "gross_vnd": o.gross_vnd, "source": "MOCK",
        })
    out["trip_record"] = trips

    # Kênh nội bộ (tiền tố "_", không phải bảng): đơn tài xế đã nhận rồi bị huỷ trên đường đón —
    # sự kiện thật của engine (`order_cancelled_after_accept`). mockgen dùng để sinh dòng
    # `trips.status=cancelled`; trước đây các đơn này chỉ hiện như bộ đếm `cancelled_count`.
    out["_cancelled_orders"] = [{
        "order_id": f"o-{seed}-{e.detail['order_id']}",
        "driver_id": f"d-{e.actor_id}",
        "vehicle_class": getattr(actors_map.get(e.actor_id), "vehicle_class", "A_COMPACT"),
        "t_request": _iso(date, orders[e.detail["order_id"]].t_min),
        "t_assign": _iso(date, matched_t.get(e.detail["order_id"], orders[e.detail["order_id"]].t_min)),
        "t_cancel": _iso(date, e.t_min),
        "pickup_h3": orders[e.detail["order_id"]].pickup_cell,
        "drop_h3": orders[e.detail["order_id"]].drop_cell,
    } for e in r.events
        if e.kind == "order_cancelled_after_accept" and e.detail.get("order_id") in orders]

    # ---- L1: swap_transaction ----
    out["swap_transaction"] = [{
        "schema_version": "1.0.0", "tx_id": f"sw-{seed}-{i}",
        "driver_id": f"d-{e.actor_id}", "station_id": f"s-{e.detail['station']}",
        "t": _iso(date, e.t_min), "wait_min": float(e.detail.get("wait_min", 0.0)),
        "source": "MOCK",
    } for i, e in enumerate(ev for ev in r.events if ev.kind == "swap_done")]

    # ---- L1: payout_ledger — TÁI TÍNH từ policy (không copy số sim mù) ----
    share = float(r.policy.driver_share)   # SIM-3: cùng nguồn với policy_bundle ở trên
    ledger = []
    for i, t in enumerate(trips):
        ledger.append({
            "schema_version": "1.0.0", "entry_id": f"pl-{seed}-{i}",
            "driver_id": t["driver_id"], "t": t["t_complete"], "kind": "trip_payout",
            "amount_vnd": int(round(t["gross_vnd"] * share)),
            "basis": {"trip_id": t["order_id"],
                       "policy_bundle_version": out["policy_bundle"][0]["version"]},
            "gross_vnd": t["gross_vnd"], "source": "MOCK",
        })
    # day_bonus từ sim settle events (rule component đã tính — observable trên app)
    nb = 0
    for e in r.events:
        if e.kind in ("end_shift", "day_end_settle") and e.detail.get("day_bonus", 0) > 0:
            ledger.append({
                "schema_version": "1.0.0", "entry_id": f"pb-{seed}-{nb}",
                "driver_id": f"d-{e.actor_id}", "t": _iso(date, e.t_min),
                "kind": "day_bonus", "amount_vnd": int(e.detail["day_bonus"]),
                "basis": {"trip_id": None,
                           "policy_bundle_version": out["policy_bundle"][0]["version"]},
                "gross_vnd": None, "source": "MOCK",
            })
            nb += 1
    out["payout_ledger"] = ledger

    # ---- L1: gps_ping — nội suy tuyến tính dọc segments (~30s/ping, deterministic) ----
    pings = []
    for s in sorted(r.segments, key=lambda x: (x["actor_id"], x["t0"])):
        dur = s["t1"] - s["t0"]
        n = min(400, max(2, int(dur / PING_INTERVAL_MIN)))  # cap segment idle dài
        for k in range(n):
            frac = k / (n - 1)
            lat = s["from_lat"] + (s["to_lat"] - s["from_lat"]) * frac
            lon = s["from_lon"] + (s["to_lon"] - s["from_lon"]) * frac
            t = s["t0"] + dur * frac
            pings.append({
                "schema_version": "1.0.0", "driver_id": f"d-{s['actor_id']}",
                "t": _iso(date, t),
                "location": {"lat": round(lat, 6), "lon": round(lon, 6),
                              "h3": h3.latlng_to_cell(lat, lon, 9)},
                "speed_kmh": None, "source": "MOCK",
            })
    out["gps_ping"] = pings

    return out


def generate_day(config_path: str | Path, seed: int, date: str) -> dict[str, list[dict]]:
    """Chạy 1 sim run ĐỘC LẬP → bảng. Deterministic theo seed.

    Ngoài các bảng L1R, trả thêm key nội bộ `_sim_driver_day` (xem `entity_tables`).
    LƯU Ý: các ngày sinh bằng hàm này KHÔNG liên hệ nhau (tài xế mỗi ngày một trạng thái
    độc lập). Muốn chuỗi có tính liên tục → `generate_days_continuous`.
    """
    cfg = Config.load(config_path)
    return _tables_from_run(run_once(cfg, seed, date_str=date), cfg, date)


def iter_days_continuous(config_path: str | Path, seed: int, dates: list[str]):
    """D-SIM-13(D) STREAMING: sinh CHUỖI ngày liên tục từng ngày — CÙNG nhóm tài xế, trạng thái mang sang.

    Yields (day_index, date, table_dict, memory) sau mỗi ngày và cho phép dọn dẹp RAM.
    """
    from gsm_sim.multiday import iter_multiday
    cfg = Config.load(config_path)
    # REVIEW-C13: ranh giới tuần của DriverMemory phải trùng tuần ISO của bảng data tuần
    # (`_week_key` dùng Thứ Hai) ⇒ truyền weekday của ngày đầu.
    week_offset = datetime.fromisoformat(dates[0]).weekday() if dates else 0
    for d, r, mem in iter_multiday(cfg, seed=seed, days=len(dates), week_offset=week_offset):
        date = dates[d]
        table_dict = _tables_from_run(r, cfg, date)
        yield d, date, table_dict, mem


def generate_days_continuous(config_path: str | Path, seed: int,
                             dates: list[str]) -> list[dict[str, list[dict]]]:
    """D-SIM-13(D): sinh CHUỖI ngày liên tục — CÙNG nhóm tài xế, trạng thái mang sang.

    Trước đây `generate_realdata` gọi `generate_day` 90 lần độc lập ⇒ tài xế ngày N và
    N+1 không liên hệ gì (phi thực tế, hỏng phân tích chuỗi thời gian). Nay chạy
    `run_multiday` MỘT lần rồi chuyển từng ngày thành bảng.
    """
    return [table_dict for _, _, table_dict, _ in iter_days_continuous(config_path, seed, dates)]
