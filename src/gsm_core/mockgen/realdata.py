"""PI-2/PI-2b — gen MOCK 13 bảng l1r (shape data thật) profile-driven.

Ô tô Taxi Cơ Hữu simulate qua `adapter_sim.generate_day` (event nền). Aggregate ĐỒNG NHẤT
→ KPI daily/weekly. **Acceptance theo
archetype target + noise** (sửa caveat R2 acceptance≈1.00, thêm randomness). Per-driver
`driver_share`. Mọi record MOCK/INFERRED, deterministic seed. Zone enlargement DEFER.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import date as _date, datetime, timedelta
from pathlib import Path

import polars as pl

from gsm_core.mockgen.adapter_sim import generate_day, generate_days_continuous, iter_days_continuous
from gsm_core.mockgen.profiles import build_profile_universe, kind_distribution
from gsm_core.schema_registry import L1R_ENTITIES


def _reg():
    """Registry cho manifest — version từ nguồn sự thật, không hardcode (Cycle V)."""
    from pathlib import Path

    from gsm_core.schema_registry import SchemaRegistry
    return SchemaRegistry(Path(__file__).resolve().parents[3] / "schemas")

ROOT = Path(__file__).resolve().parents[3]
RUSH_HOURS = {6, 7, 8, 16, 17, 18}
# Dwell dài hơn ngưỡng này = tài xế nghỉ/không vận doanh (KHÔNG phải "chờ khách").
# Chống trạng thái bất khả: Σ idle không được vượt giờ online (BUG-PI5b-01).
OFFLINE_DWELL_SECONDS = 90 * 60


def write_table_parquet(records: list[dict], path: Path, chunk_size: int = 50000) -> int:
    """Ghi parquet AN TOÀN KIỂU (BUG-PI2b-05) và TIẾT KIỆM BỘ NHỚ.

    `pl.DataFrame(...)` mặc định chỉ suy kiểu từ 100 dòng đầu → cột THƯA
    (`campaign_id`/`target_hex` chỉ ~5% có giá trị) bị suy thành Null rồi CRASH khi
    gặp giá trị str ở dòng sau ("could not append value ... to the builder").
    Lỗi phụ thuộc seed → phải quét TOÀN BỘ (`infer_schema_length=None`).

    Với bảng lớn (>50k dòng như public_driver_hex_tracking), ghi tuần tự theo chunk bằng
    `pyarrow.parquet.ParquetWriter` để tránh duplicate hàng triệu dict trong RAM dẫn đến OOM.
    """
    if not records:
        pl.DataFrame([]).write_parquet(path)
        return 0

    if len(records) <= chunk_size:
        flat = [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                 for k, v in r.items()} for r in records]
        pl.DataFrame(flat, infer_schema_length=None).write_parquet(path)
        return len(records)

    import gc
    import pyarrow.parquet as pq

    first_chunk = [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                    for k, v in r.items()} for r in records[:chunk_size]]
    df0 = pl.DataFrame(first_chunk, infer_schema_length=None)
    # Cột THƯA toàn None trong khối đầu (vd `trips.cancel_reason`: 50.000 cuốc đầu đều hoàn thành)
    # bị suy thành Null rồi CRASH ở khối sau. Tìm giá trị đầu tiên khác None phía sau để lấy kiểu.
    null_cols = [c for c, dt in df0.schema.items() if dt == pl.Null]
    if null_cols:
        for c in null_cols:
            for r in records[chunk_size:]:
                v = r.get(c)
                if v is not None:
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, ensure_ascii=False)
                    df0 = df0.with_columns(pl.col(c).cast(pl.DataFrame({c: [v]}).schema[c]))
                    break
    t0 = df0.to_arrow()
    schema = t0.schema
    pl_schema = df0.schema

    with pq.ParquetWriter(path, schema) as writer:
        writer.write_table(t0)
        del first_chunk, df0, t0
        for i in range(chunk_size, len(records), chunk_size):
            chunk = [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                      for k, v in r.items()} for r in records[i:i + chunk_size]]
            df = pl.DataFrame(chunk, schema=pl_schema)
            writer.write_table(df.to_arrow())
            del chunk, df
        gc.collect()

    return len(records)


class TableStreamWriter:
    """Quản lý các ParquetWriter mở để append dữ liệu theo từng ngày (streaming).

    Cho phép ghi dữ liệu từng ngày trực tiếp xuống file Parquet và giải phóng RAM,
    giúp chạy 30-90 ngày với hàng nghìn tài xế mà bộ nhớ RAM luôn ở mức thấp (<500MB).
    """
    def __init__(self, out_dir: Path, canonical_schemas_dir: Path | None = None):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.writers = {}
        self.schemas = {}
        self.counts = defaultdict(int)
        self.canonical_schemas = {}
        ref_dir = canonical_schemas_dir or (ROOT / "data" / "mock" / "realdata-v1")
        if ref_dir.exists():
            import pyarrow.parquet as _pq
            for p in ref_dir.glob("*.parquet"):
                try:
                    self.canonical_schemas[p.stem] = _pq.read_schema(p)
                except Exception:
                    pass

    def write_chunk(self, entity: str, records: list[dict]) -> int:
        if not records:
            return 0
        import gc
        import json
        import polars as pl
        import pyarrow as pa
        import pyarrow.parquet as _pq

        flat = [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                 for k, v in r.items()} for r in records]

        if entity not in self.writers:
            path = self.out_dir / f"{entity}.parquet"
            if entity in self.canonical_schemas:
                schema = self.canonical_schemas[entity]
            else:
                df = pl.DataFrame(flat, infer_schema_length=None)
                schema = df.to_arrow().schema
            self.schemas[entity] = schema
            self.writers[entity] = _pq.ParquetWriter(path, schema, compression="snappy")

        schema = self.schemas[entity]
        arrow_table = pa.Table.from_pylist(flat, schema=schema)
        self.writers[entity].write_table(arrow_table)

        n = len(records)
        self.counts[entity] += n
        del flat, arrow_table
        gc.collect()
        return n

    def close(self, all_entities: list[str] | None = None) -> dict[str, int]:
        import polars as pl
        for entity, writer in self.writers.items():
            writer.close()
        self.writers.clear()

        if all_entities:
            for entity in all_entities:
                path = self.out_dir / f"{entity}.parquet"
                if not path.exists():
                    pl.DataFrame([]).write_parquet(path)
                    self.counts[entity] = 0
        return dict(self.counts)


def _hour(iso: str) -> int:
    return datetime.fromisoformat(iso).hour


def _date_of(iso: str) -> str:
    return datetime.fromisoformat(iso).date().isoformat()


def _week_key(d: str):
    dt = _date.fromisoformat(d)
    iso = dt.isocalendar()
    monday = dt - timedelta(days=dt.weekday())
    return f"{iso.year}-W{iso.week:02d}", monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _trip_hours(trips: list) -> float:
    """Tổng giờ THỰC phục vụ cuốc (request→complete) — sàn cho online_time."""
    tot = 0.0
    for t in trips:
        tot += (datetime.fromisoformat(t["t_complete"])
                - datetime.fromisoformat(t["t_request"])).total_seconds() / 3600
    return tot


def _emit_day(out: dict, drv: str, d: str, trips: list, prof: dict, online_h: float,
              rng: random.Random, sim_stats: dict | None = None) -> None:
    """Aggregate 1 driver-day → mọi bảng KPI daily + trips.

    BUG-PI2b-02 fix: online_time PHẢI ≥ giờ phục vụ cuốc (không thể có cuốc khi offline).
    Sàn = trip_hours/0.55 (utilization ≤55% — realism-benchmarks FT 45-55%).

    SIM-1 fix D — NGUỒN SỰ THẬT của accept/cancel:
      - tài xế BIKE có sim (`sim_stats` khác None) → lấy THẲNG counter của sim. Sim là
        nguồn sự thật duy nhất; tầng data KHÔNG suy ngược từ target nữa.
      - tài xế CAR/PREMIUM/RTO **không có sim** → vẫn dùng target profile + noise (đó là
        cách sinh duy nhất cho họ, không phải "vá").
    """
    share = prof["driver_share"]
    # Tài xế CÓ sim thì giờ online lấy THẲNG từ sim, không dùng số profile: `hex_tracking` đã
    # dựng từ quỹ đạo của chính run đó, nên hai bảng phải nói về cùng một ngày. Tài xế CAR/
    # PREMIUM/RTO không có sim ⇒ vẫn là số profile, đó là cách sinh duy nhất cho họ.
    if sim_stats is not None and sim_stats.get("online_min") is not None:
        online_h = float(sim_stats["online_min"]) / 60.0
    if trips:
        online_h = max(online_h, _trip_hours(trips) / 0.55)
    completed = len(trips)
    if sim_stats is not None:
        accepted = max(completed, int(sim_stats["accepted"]))
        # Cycle 1 — MẪU SỐ là lượt tài xế THẬT SỰ ĐƯỢC HỎI, không phải mọi lượt dispatcher
        # định tuyến tới. `offered` gồm cả lượt bị chặn vì pin (`world.py:647` đếm trước cổng
        # SOC ở `:654`), mà ở đó `decide_accept` KHÔNG BAO GIỜ được gọi. Để nguyên thì
        # `acceptance_rate` của `driver_statistic_daily` bị nhiễm, và ĐƯỜNG SẢN PHẨM đọc số
        # nhiễm đó — advisor thật sẽ tưởng tài xế đang từ chối nhiều.
        # `.get(...)` có fallback: snapshot sim CŨ (trước Cycle 1) không có khoá `decided`.
        _decided = sim_stats.get("decided")
        if _decided is None:
            _decided = int(sim_stats["offered"]) - int(sim_stats.get("soc_skipped", 0))
        req_accept = max(accepted, int(_decided))
        declined = req_accept - accepted
        cancelled = accepted - completed          # gồm huỷ-sau-nhận + đơn bị censor 24:00
    else:
        ful = _clamp(rng.gauss(prof["target_fulfil"], 0.02), 0.6, 1.0)
        accepted = max(completed, round(completed / ful)) if completed else 0
        acc = _clamp(rng.gauss(prof["target_acceptance"], 0.04), 0.5, 1.0)
        req_accept = max(accepted, round(accepted / acc)) if accepted else 0
        declined = req_accept - accepted
        cancelled = accepted - completed
    acc_rate = round(accepted / req_accept, 4) if req_accept else 1.0
    ful_rate = round(completed / accepted, 4) if accepted else 1.0
    can_rate = round(cancelled / req_accept, 4) if req_accept else 0.0
    # SIM-XANH P2: BIKE lấy rating THẬT từ sự kiện sim (khách chấm sao); car/rto vẫn
    # gauss vì không có sim. Trước đây bike cũng gauss — hai tầng kể hai chuyện.
    if sim_stats is not None and "ratings_n" in sim_stats:
        rated_n = int(sim_stats["ratings_n"])
        n_5 = int(sim_stats["ratings_5"])
        total_rating = float(sim_stats["ratings_sum"])
    else:
        rated_n = completed
        n_5 = int(round(completed * _clamp(rng.gauss(0.78, 0.08), 0.4, 1.0)))
        total_rating = round(_clamp(rng.gauss(4.7, 0.15), 3.5, 5.0) * completed, 2)

    out["driver_statistic_daily"].append({
        "schema_version": "1.0.0", "source": "MOCK", "local_date": d, "driver_id": drv,
        "completed_count": completed, "accepted_count": accepted, "cancelled_count": cancelled,
        "total_request_calculate_complete": accepted, "total_request_calculate_cancel": cancelled + declined,
        "total_request_calculate_accept": req_accept,
        "count_cancel_not_relate_driver": rng.randint(0, cancelled) if cancelled else 0,
        "total_rating": total_rating, "total_order_rating": rated_n,
        "count_rating_5_star": min(n_5, rated_n),
        "acceptance_rate": acc_rate, "fulfillment_rate": ful_rate, "cancellation_rate": can_rate})

    out["driver_online_hours_sap_id"].append({
        "schema_version": "1.0.0", "source": "MOCK", "local_date": d, "schedule_date": d,
        "driver_id": drv, "full_name": f"MOCK Driver {drv}", "sap_profile_id": f"SAP-{drv}",
        "hub_id": prof.get("hub_id", "hub-gialam"), "depot_id": prof.get("depot_id", "depot-01"),
        "phone_number": "+8490MOCK000",
        "driver_type": prof["driver_type"], "online_time": round(online_h, 2)})

    gross = sum(t["gross_vnd"] for t in trips)
    commission = int(round(gross * share))
    rush = [t for t in trips if _hour(t["t_complete"]) in RUSH_HOURS]
    g_rush = sum(t["gross_vnd"] for t in rush)
    c_rush = int(round(g_rush * share))
    g_norm, c_norm = gross - g_rush, commission - c_rush
    out["driver_orders_rush_hours"].append({
        "schema_version": "1.0.0", "source": "MOCK", "driver_id": drv, "local_date": d,
        "total_order": completed, "commission": commission, "total_fee": gross,
        "revenue_not_relate_driver": gross - commission,
        "total_order_normal_hour": completed - len(rush), "commission_normal_hour": c_norm,
        "total_fee_normal_hour": g_norm, "revenue_not_relate_driver_normal_hour": g_norm - c_norm,
        "total_order_rush_hour": len(rush), "commission_rush_hour": c_rush,
        "total_fee_rush_hour": g_rush, "revenue_not_relate_driver_rush_hour": g_rush - c_rush})
    # BUG-PI2b-04 fix: core_order ⊊ total (85-95% — catalog), KHÔNG degenerate
    core = int(round(completed * _clamp(rng.gauss(0.90, 0.04), 0.80, 1.0)))
    out["driver_income_daily"].append({
        "schema_version": "1.0.0", "source": "MOCK", "driver_id": drv, "order_date": d,
        "commission": commission, "total_order": completed, "total_fee": gross,
        "revenue_not_relate_driver": gross - commission,
        "avg_daily_revenue": round(gross / completed, 2) if completed else 0.0,
        "total_core_order": min(core, completed)})
    # BUG-PI2b-04 fix: stoppoints = điểm trả khách + điểm dừng CHỜ (idle) → ≠ số cuốc
    idle_stops = rng.randint(0, max(1, completed // 2)) if completed else 0
    idle_rush = rng.randint(0, idle_stops) if idle_stops else 0
    out["driver_bike_stoppoints"].append({
        "schema_version": "1.0.0", "source": "MOCK", "driver_id": drv, "local_date": d,
        "total_stoppoints": completed + idle_stops,
        "total_stoppoints_rush_hour": len(rush) + idle_rush})

    for t in trips:
        out["trips"].append({
            "schema_version": "1.1.0", "source": "MOCK", "trip_id": t["order_id"], "driver_id": drv,
            "customer_id": f"cust-{t['order_id']}", "service_type": prof["service_type"],
            "vehicle_class": t.get("vehicle_class"), "cancel_reason": None, "cancelled_by": None,
            "status": "completed", "request_time": t["t_request"], "assign_time": t["t_assign"],
            "pickup_time": t["t_pickup"], "complete_time": t["t_complete"],
            "pickup_h3": t["pickup"]["h3"], "drop_h3": t["drop"]["h3"], "distance_km": t["dist_km"],
            "duration_seconds": int((datetime.fromisoformat(t["t_complete"])
                                     - datetime.fromisoformat(t["t_pickup"])).total_seconds()),
            "gross_vnd": t["gross_vnd"], "commission_vnd": int(round(t["gross_vnd"] * share)),
            "rush_hour": _hour(t["t_complete"]) in RUSH_HOURS, "travel_mode": prof["service_type"],
            "created_at": t["t_request"], "datastream_metadata": None})


def build_tables(day_outputs: list[dict], universe: dict, seed: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {e: [] for e in L1R_ENTITIES}
    rng = random.Random(seed ^ 0x5EED)

    # --- BIKE simulated: gom trips + online span + pings từ sim ---
    trips_by = defaultdict(list)
    online_by = defaultdict(list)
    pings_by = defaultdict(list)
    dates = set()
    for day in day_outputs:
        for t in day.get("trip_record", []):
            trips_by[(t["driver_id"], _date_of(t["t_complete"]))].append(t)
        for e in day.get("app_event", []):
            if e["kind"] in ("go_online", "go_offline"):
                online_by[(e["driver_id"], _date_of(e["t"]))].append((e["t"], e["kind"]))
        # Nén pings ngay tại đây: chỉ giữ (t, h3) và gộp các ping liên tiếp cùng hex
        for p in day.pop("gps_ping", []):
            drv = p["driver_id"]
            t = p["t"]
            h = p["location"]["h3"]
            lst = pings_by[drv]
            if len(lst) >= 2 and lst[-1][1] == h and lst[-2][1] == h:
                lst[-1] = (t, h)
            else:
                lst.append((t, h))
        # Giải phóng các bảng phụ không dùng để tiết kiệm RAM
        day.pop("payout_ledger", None)
        day.pop("swap_transaction", None)
        day.pop("station_registry", None)
        day.pop("policy_bundle", None)

    # SIM-1 fix D: counter accept/cancel THẬT từ sim, khoá theo (driver, ngày)
    sim_stats: dict[tuple[str, str], dict] = {}
    sim_mission_events: list[dict] = []
    sim_mission_catalog: list[dict] = []
    for day in day_outputs:
        sd = day.get("_sim_driver_day")
        if sd:
            for drv_id, stt in sd["stats"].items():
                sim_stats[(drv_id, sd["date"])] = stt
        sm = day.get("_sim_missions")
        if sm:
            sim_mission_events.extend(sm.get("completed", []))
            if not sim_mission_catalog:
                sim_mission_catalog = list(sm.get("catalog", []))
    # kênh nội bộ (tiền tố "_") — generate_realdata sẽ POP trước khi ghi parquet
    out["_sim_mission_events"] = sim_mission_events
    out["_sim_mission_catalog"] = sim_mission_catalog
    out["_sim_stats"] = sim_stats
    for day in day_outputs:  # tập ngày
        for t in day.get("trip_record", []):
            dates.add(_date_of(t["t_complete"]))
    dates = sorted(dates)

    def online_hours(drv, d):
        spans = sorted(online_by[(drv, d)])
        h, stack = 0.0, None
        for ts, kind in spans:
            if kind == "go_online":
                stack = ts
            elif kind == "go_offline" and stack:
                h += (datetime.fromisoformat(ts) - datetime.fromisoformat(stack)).total_seconds() / 3600
                stack = None
        return h

    # emit driver-days
    for (drv, d), trips in sorted(trips_by.items()):
        prof = universe.get(drv)
        if prof is None:
            continue
        _emit_day(out, drv, d, sorted(trips, key=lambda x: x["t_complete"]), prof,
                  online_hours(drv, d), rng, sim_stats=sim_stats.get((drv, d)))

    # --- cuốc HUỶ (status=cancelled): đơn tài xế đã nhận rồi bị huỷ, từ sự kiện thật của engine ---
    # Engine chỉ biết "huỷ sau nhận" — KHÔNG biết nguyên nhân. Lý do/bên huỷ là GIẢ ĐỊNH MOCK gán
    # theo tỷ lệ cố định (xem schemas/CHANGELOG.md, trips 1.1.0). RNG riêng để không dịch dòng RNG chính.
    crng = random.Random(seed ^ 0xCA9C)
    for day in day_outputs:
        for c in day.get("_cancelled_orders", []):
            if c["driver_id"] not in universe:
                continue
            roll = crng.random()
            if roll < 0.55:
                reason, by = "khach_doi_y", "customer"
            elif roll < 0.80:
                reason, by = "su_co_xe", "driver"
            else:
                reason, by = "khach_khong_den", "driver"
            out["trips"].append({
                "schema_version": "1.1.0", "source": "MOCK", "trip_id": c["order_id"], "driver_id": c["driver_id"],
                "customer_id": f"cust-{c['order_id']}", "service_type": universe[c["driver_id"]]["service_type"],
                "vehicle_class": c["vehicle_class"], "cancel_reason": reason, "cancelled_by": by,
                "status": "cancelled", "request_time": c["t_request"], "assign_time": c["t_assign"],
                "pickup_time": None, "complete_time": None,
                "pickup_h3": c["pickup_h3"], "drop_h3": c["drop_h3"], "distance_km": 0.0,
                "duration_seconds": None, "gross_vnd": 0, "commission_vnd": 0,
                "rush_hour": _hour(c["t_request"]) in RUSH_HOURS,
                "travel_mode": universe[c["driver_id"]]["service_type"],
                "created_at": c["t_request"], "datastream_metadata": None})

    # --- hex_tracking từ gps dwell ---
    hx = 0
    for drv in sorted(list(pings_by.keys())):
        ps = pings_by.pop(drv)
        ps.sort(key=lambda x: x[0])
        seg_hex = seg_start = prev_hex = prev_t = None
        for t, h in ps:
            if h != seg_hex:
                if seg_hex is not None:
                    dur = int((datetime.fromisoformat(prev_t) - datetime.fromisoformat(seg_start)).total_seconds())
                    reposition = rng.random() < 0.05  # ~5% có target reposition (diversity)
                    out["public_driver_hex_tracking"].append({
                        "schema_version": "1.0.0", "source": "MOCK", "id": f"hx-{seed}-{hx}", "driver_id": drv,
                        "campaign_id": "repo-01" if reposition else None, "log_id": None,
                        "init_hex": prev_hex, "current_hex": seg_hex, "last_hex": prev_hex,
                        "target_hex": (f"8amock{rng.randint(0, 60):02d}") if reposition else None,
                        "last_seen_at": prev_t, "entered_current_hex_at": seg_start,
                        "stay_duration_seconds": dur,
                        "reached_target": (rng.random() < 0.6) if reposition else None,
                        "reached_target_at": None, "hex_history": None, "created_at": seg_start,
                        "updated_at": prev_t, "schedule_job_id": None, "datastream_metadata": None,
                        # BUG-PI5b-01: trước đây MỌI dwell >5 phút đều gán "idle" ⇒ khoảng
                        # NGHỈ/OFFLINE dài (vd 20 giờ đứng yên) bị tính là "đang chờ khách"
                        # → tổng idle 1300 phút > online 4.8h (bất khả). Dwell quá dài =
                        # tài xế KHÔNG vận doanh → "offline" (enum schema đã có).
                        "tracking_status": ("offline" if dur > OFFLINE_DWELL_SECONDS
                                            else ("idle" if dur > 300 else "moving"))})
                    hx += 1
                seg_hex, seg_start, prev_hex = h, t, seg_hex
            prev_t = t
        del ps
    del pings_by
    import gc
    gc.collect()

    return out


def _vn_tien(v) -> str:
    """Số tiền kiểu Việt: ngăn nghìn bằng dấu chấm. Chỉ dùng cho chuỗi `reason` của mock."""
    return f"{int(round(float(v))):,}".replace(",", ".") + "đ"


def build_weekly_and_missions(daily: dict, universe: dict, seed: int,
                              sim_mission_events: list | None = None,
                              sim_mission_catalog: list | None = None,
                              sim_stats: dict | None = None,
                              quota: dict | None = None,
                              window: tuple[str, str] | None = None) -> None:
    rng = random.Random(seed ^ 0xB0B)
    by_dw = defaultdict(list)
    for r in daily["driver_income_daily"]:
        wk, ws, we = _week_key(r["order_date"])
        by_dw[(r["driver_id"], wk, ws, we)].append(r)
    quota = quota or {}
    quota_min = quota.get("min_revenue_vnd")
    min_days = int(quota.get("min_active_days") or 0)
    win_lo, win_hi = window or ("", "")
    kid = 0
    for (drv, wk, ws, we), rows in sorted(by_dw.items()):
        rev = sum(x["total_fee"] for x in rows)
        prof = universe.get(drv, {})
        # 🔴 TUẦN CỤT — sửa 2026-08-17. Một tuần bị cắt bởi biên cửa sổ sinh không phải một
        # tuần tài xế làm việc kém; ta chỉ đơn giản không quan sát hết nó. Bản cũ không phân
        # biệt hai thứ đó (`at_risk` khi `len(rows) < 5`) nên **148/158 bản ghi `at_risk` rơi
        # đúng vào tuần đầu và tuần cuối** — và vì phạt neo vào `at_risk`, 71/75 khoản phạt
        # cũng là artifact biên. Tuần cụt nay là `partial`: không khen, không phạt.
        cut = bool(win_lo and win_hi) and (ws < win_lo or we > win_hi)
        if cut:
            status = "partial"
        elif quota_min is None:
            # Không có số khoán từ policy ⇒ KHÔNG suy đoán đạt/không đạt (§5).
            status = "unknown"
        else:
            days_active = len(rows)
            status = ("achieved" if rev >= int(quota_min) and days_active >= min_days
                      else "at_risk" if rev < int(quota_min) else "active")
        daily["kpi_driver_platform_calculator_gbq"].append({
            "schema_version": "1.0.0", "source": "MOCK", "id": f"kpi-{seed}-{kid}", "driver_id": drv,
            "driver_name": f"MOCK Driver {drv}", "sap_id": f"SAP-{drv}", "status": status,
            "week_key": wk, "week_start": ws, "week_end": we, "kpi_month": _date.fromisoformat(ws).month,
            "kpi_year": _date.fromisoformat(ws).year, "email": None, "tel": None, "engname": None,
            "depot_code": prof.get("depot_code", "DPT01"), "depot_name": prof.get("depot_name", "Gia Lam"),
            "vehicle_vin_number": f"VIN{drv}",
            "vehicle_license_plate": f"29-MOCK{drv}", "vehicle_model": prof.get("vehicle_model", "Feliz S"),
            "country": "VN", "type": prof.get("track", "platform"), "last_updated_date": f"{we}T23:59:00+07:00"})
        kid += 1

    # (id, type, tên, thưởng VND, SỐ CUỐC CẦN) — target_count BẮT BUỘC: thiếu thì
    # mission vô nghĩa (remaining=0 → S6 coi như đã xong). Grounded mini-task thật.
    missions = [
        ("m-trip20", "trip_count", "20 chuyến/ngày", 30000, 20),
        ("m-rush", "rush_hour", "2 chuyến khung vàng", 30000, 2),
        ("m-week250", "trip_count", "250 chuyến/tuần", 1000000, 250),
    ]
    for mid, mtype, name, reward, target in missions:
        daily["public_mission"].append({
            "schema_version": "1.0.0", "source": "MOCK", "id": mid, "created_at": "2026-07-01T00:00:00+07:00",
            "updated_at": None, "deleted_at": None, "created_by": "gsm", "updated_by": None,
            "mission_type": mtype, "parent_id": None, "name": name, "state": "active", "audience": "core_owned",
            "description": name, "start_time": "2026-07-01T00:00:00+07:00", "end_time": "2026-12-31T23:59:00+07:00",
            "point_id": None, "rewards": {"vnd": reward, "target_count": target},
            "mission_claim": None, "mission_code": mid,
            "time_claim_reward": None, "rule_code": None, "meta_data": None, "contract_type": None,
            "qualify_execute_code": None, "status": "active", "datastream_metadata": None,
            "business_code": None, "show_only": False, "is_ddi_mission": False})

    # SIM-XANH P2: catalog mission SIM (bike) vào public_mission — đúng mission mà actor
    # THỰC SỰ chạy trong engine, thay vì chỉ catalog rule-based
    for m in (sim_mission_catalog or []):
        w = m.get("window")
        daily["public_mission"].append({
            "schema_version": "1.0.0", "source": "MOCK", "id": m["mission_id"],
            "created_at": "2026-07-01T00:00:00+07:00",
            "updated_at": None, "deleted_at": None, "created_by": "gsm", "updated_by": None,
            "mission_type": "trip_count" if w is None else "rush_hour", "parent_id": None,
            "name": m["name"], "state": "active", "audience": "core_owned",
            "description": m["name"], "start_time": "2026-07-01T00:00:00+07:00",
            "end_time": "2026-12-31T23:59:00+07:00", "point_id": None,
            "rewards": {"vnd": int(m["reward_vnd"]), "target_count": int(m["target"])},
            "mission_claim": None, "mission_code": m["mission_id"],
            "time_claim_reward": None, "rule_code": None, "meta_data": None, "contract_type": None,
            "qualify_execute_code": None, "status": "active", "datastream_metadata": None,
            "business_code": None, "show_only": False, "is_ddi_mission": False})

    sim_set = {drv for drv, prf in universe.items() if prf.get("simulated")}
    eid = pid = 0
    _targets = {m["mission_id"]: int(m["target"]) for m in (sim_mission_catalog or [])}
    # SIM-XANH P2: earn_history BIKE = SỰ KIỆN mission_completed thật từ engine
    for ev in sorted((sim_mission_events or []), key=lambda x: (x["driver_id"], x["t_iso"])):
        daily["public_mission_earn_history"].append({
            "schema_version": "1.0.0", "source": "MOCK", "id": f"eh-sim-{seed}-{eid}",
            "created_at": ev["t_iso"], "updated_at": ev["t_iso"], "deleted_at": None,
            "mission_id": ev["mission_id"], "order_id": None, "order_status": "completed",
            "driver_id": ev["driver_id"], "customer_id": None, "service_type": "car",
            "order_time": ev["t_iso"], "complete_time": ev["t_iso"], "travel_mode": "car",
            "sap_contract_type": "core_owned", "type": "mission",
            # schema đòi integer: số cuốc tính cho mission = target của mốc vừa chạm
            "count_order": _targets.get(ev["mission_id"], 0),
            "count_stoppoint": _targets.get(ev["mission_id"], 0),
            "earn": int(ev["reward_vnd"]),
            "description": ev.get("name") or ev["mission_id"], "datastream_metadata": None,
            "reward_level": "1"})
        eid += 1
    # tiến độ mission BIKE: ngày CUỐI CÙNG quan sát được của từng (driver, mission)
    if sim_stats and sim_mission_catalog:
        last_prog: dict[tuple[str, str], tuple[str, int]] = {}
        for (drv, d), stt in sim_stats.items():
            for mid, cnt in (stt.get("mission_progress") or {}).items():
                cur = last_prog.get((drv, mid))
                if cur is None or d > cur[0]:
                    last_prog[(drv, mid)] = (d, int(cnt))
        targets = {m["mission_id"]: int(m["target"]) for m in sim_mission_catalog}
        rewards = {m["mission_id"]: int(m["reward_vnd"]) for m in sim_mission_catalog}
        for (drv, mid), (d, cnt) in sorted(last_prog.items()):
            tgt = targets.get(mid, 0)
            daily["public_user_mission_progress"].append({
                "schema_version": "1.0.0", "source": "MOCK", "id": f"ump-sim-{seed}-{pid}",
                "driver_id": drv, "mission_id": mid, "progress_count": cnt,
                "target_count": tgt, "progress_value_vnd": 0,
                "target_value_vnd": rewards.get(mid, 0),
                "state": "completed" if cnt >= tgt else "in_progress",
                "started_at": f"{d}T00:00:00+07:00", "updated_at": None, "claimed_at": None,
                "datastream_metadata": None})
            pid += 1

    trips_by_dd = {(r["driver_id"], r["order_date"]): r["total_order"] for r in daily["driver_income_daily"]}
    prog = defaultdict(int)
    for (drv, d), n in sorted(trips_by_dd.items()):
        if drv in sim_set:
            continue          # BIKE đã có mission từ SỰ KIỆN sim — rule-based chỉ cho car/rto
        if n >= 20:
            daily["public_mission_earn_history"].append({
                # THỨ TỰ CỘT theo đúng spec GSM (21 cột) — id, audit, rồi nghiệp vụ
                "schema_version": "1.0.0", "source": "MOCK", "id": f"eh-{seed}-{eid}",
                "created_at": f"{d}T20:00:00+07:00", "updated_at": f"{d}T20:00:00+07:00",
                "deleted_at": None, "mission_id": "m-trip20",
                "order_id": None, "order_status": "completed", "driver_id": drv, "customer_id": None,
                "service_type": universe.get(drv, {}).get("service_type", "car"),
                "order_time": f"{d}T20:00:00+07:00", "complete_time": f"{d}T20:00:00+07:00", "travel_mode": "car",
                "sap_contract_type": universe.get(drv, {}).get("track", "core_owned"), "type": "mission",
                "count_order": n, "count_stoppoint": n, "earn": 30000, "description": "20 chuyến/ngày",
                "datastream_metadata": None, "reward_level": "1"})
            eid += 1
        prog[drv] += n
    for drv, total in sorted(prog.items()):
        if drv in sim_set:
            continue          # BIKE: tiến độ từ sim (ở trên)
        daily["public_user_mission_progress"].append({
            "schema_version": "1.0.0", "source": "MOCK", "id": f"ump-{seed}-{pid}", "driver_id": drv,
            "mission_id": "m-week250", "progress_count": min(total, 250), "target_count": 250,
            "progress_value_vnd": 0, "target_value_vnd": 1000000,
            "state": "completed" if total >= 250 else "in_progress",
            "started_at": "2026-07-01T00:00:00+07:00", "updated_at": None, "claimed_at": None,
            "datastream_metadata": None})
        pid += 1

    # 🔴 CƠ CHẾ THẬT thay coin-flip (2026-08-17). Bản cũ: `at_risk ∧ rng<0.5` ⇒ loại phạt
    # `rng.choice(4)` ⇒ số tiền `rng.choice([40k,100k,200k])`. Ba lần tung xúc xắc, và con số
    # cuối cùng hiện ra trước mặt tài xế như một khoản trừ có thật.
    # `data-contract-counterfactual.md` §3.4 gọi đây là RNG_NOISE + audience=driver ⇒ REFUSED.
    #
    # Nay: truy thu khoán tuần là khoản DUY NHẤT ta có công thức official, nên nó là khoản duy
    # nhất được sinh cho cửa sổ hiện hành. `clawback_vnd` là hàm S5/S8 cũng gọi ⇒ số tài xế
    # thấy trên bảng phạt và số agent nói khi được hỏi không thể trôi khỏi nhau.
    from gsm_core.policy import clawback_vnd

    rev_by_dw = {(drv, wk): sum(x["total_fee"] for x in rows)
                 for (drv, wk, _ws, _we), rows in by_dw.items()}
    for k in daily["kpi_driver_platform_calculator_gbq"]:
        if k["status"] != "at_risk":
            continue          # `partial` (tuần cụt) và `achieved` KHÔNG bị truy thu
        rev = rev_by_dw.get((k["driver_id"], k["week_key"]), 0)
        amount = clawback_vnd(rev, quota)
        if not amount:
            continue          # None = chưa biết tỷ lệ · 0 = không hụt ⇒ không dựng khoản trừ
        gap = int(quota["min_revenue_vnd"]) - int(rev)
        daily["driver_penalization_ATA"].append({
            "schema_version": "1.0.0", "source": "MOCK", "penalization_id": f"pen-{seed}-{k['id']}",
            "driver_id": k["driver_id"], "local_date": k["week_end"], "week_key": k["week_key"],
            "penalty_type": "clawback_khoan", "amount_vnd": amount,
            # `reason` mang VẾT TÍNH: đủ để người đọc (và test) dựng lại con số mà không cần
            # thêm cột vào bảng — schema `additionalProperties:false` nên thêm cột = bump version.
            # ⚠ Định dạng từng số RIÊNG rồi mới ghép: bản đầu `f"...{rev:,}đ, hụt..."` xong
            # `.replace(",", ".")` cho cả câu — nó nuốt luôn dấu phẩy ngữ pháp thành dấu chấm
            # ("1.049.027đ. hụt"). Số Việt dùng `.` ngăn nghìn nên phải đổi, nhưng chỉ trong số.
            "reason": (f"doanh số tuần {_vn_tien(rev)}, hụt {_vn_tien(gap)} so với khoán "
                       f"{_vn_tien(quota['min_revenue_vnd'])}; truy thu "
                       f"{float(quota['clawback_rate']):.0%} phần hụt"),
            "related_metric": "weekly_revenue", "ata_code": None,
            "status": "applied", "created_at": f"{k['week_end']}T23:00:00+07:00"})

    # 🔴 CƠ CHẾ THẬT thay `rng.random() < 0.01` (2026-08-17).
    #
    # Ba trong bốn loại cờ cũ (`route_deviation`, `gps_anomaly`, `off_app`) mô phỏng một hệ
    # phát hiện mà sim KHÔNG có tín hiệu tương ứng — không GPS lệch tuyến, không phiên tắt app.
    # Sinh chúng là khẳng định một năng lực không tồn tại. Chỉ `abnormal_cancel` có bằng chứng
    # thật: `cancellation_rate` đo từ counter huỷ-sau-khi-nhận của sim.
    #
    # `confidence` KHÔNG còn là `rng.uniform` — nó là mức lệch của chính tỷ lệ huỷ so với
    # ngưỡng. Và vì mọi dòng đều phái sinh từ bằng chứng, `confidence` không bao giờ null:
    # `from_l1r.py:488` gọi `float(r["confidence"])` nên một dòng null sẽ làm S9 NỔ.
    # Ngưỡng ĐO ĐƯỢC, không phải đặt bừa: trên 12.831 driver-day của bộ hiện hành,
    # `cancellation_rate` có median 0,040 · p90 0,111 · p95 0,143 · **p99 0,200** · max 0,750.
    # Lấy đúng p99 làm ngưỡng ⇒ "bất thường" nghĩa là *1% driver-day tệ nhất*, một định nghĩa
    # có thể kiểm lại được, thay vì một con số tôi nghĩ ra. Dải severity cũng theo phân vị.
    NGUONG_HUY = 0.20          # p99
    fid = 0
    for r in daily["driver_statistic_daily"]:
        ty_le_huy = float(r.get("cancellation_rate") or 0.0)
        if ty_le_huy <= NGUONG_HUY:
            continue
        # Lệch càng xa ngưỡng thì càng chắc — kẹp [0,50; 0,95] để không bao giờ tuyên bố
        # chắc chắn tuyệt đối về một suy luận.
        do_lech = (ty_le_huy - NGUONG_HUY) / (1.0 - NGUONG_HUY)
        confidence = round(min(0.95, 0.50 + 0.45 * do_lech), 2)
        severity = "high" if ty_le_huy > 0.30 else ("medium" if ty_le_huy > 0.25 else "low")
        daily["public_frauds"].append({
            "schema_version": "1.0.0", "source": "INFERRED", "fraud_id": f"f-{seed}-{fid}",
            "driver_id": r["driver_id"], "detected_at": f"{r['local_date']}T18:00:00+07:00",
            "fraud_type": "abnormal_cancel", "severity": severity,
            "confidence": confidence, "evidence_ref": None,
            # Không phải cờ nào cũng còn mở: cờ cũ đã được xem xét xong. Bản cũ luôn `"open"`
            # nên nhánh im lặng của S9 (`anomaly_alert.py:78`) chưa từng chạy trên data thật.
            "status": "open" if ty_le_huy > 0.25 else "cleared",
            "created_at": f"{r['local_date']}T18:00:00+07:00", "datastream_metadata": None})
        fid += 1


def _git_commit() -> str | None:
    """Commit của engine đang sinh data. None nếu không phải git repo (không chặn việc gen).

    REVIEW-C7/C12: nếu working tree có thay đổi CHƯA COMMIT ở file được track thì gắn hậu tố
    `+dirty` — commit ghi trong manifest khi đó KHÔNG tái lập được bộ data, và trường
    truy vết này sinh ra chính là để chống data lạc hậu âm thầm. Nói dối ở đây phá đúng
    mục đích của nó.
    """
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        sha = out.stdout.strip()
        if not sha:
            return None
        st = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                             "--untracked-files=no"],
                            capture_output=True, text=True, timeout=10)
        return sha + ("+dirty" if st.stdout.strip() else "")
    except Exception:
        return None


def generate_realdata_stream(days: int, seed_base: int, out_dir: Path,
                             config_path: Path | None = None,
                             start_date: str = "2026-07-01",
                             continuous: bool = True,
                             telemetry: bool = False) -> dict:
    """Sinh 13 bảng Parquet L1R + 2 bảng L0 bằng cơ chế STREAMING cuốn chiếu từng ngày.

    Đặc điểm kiến trúc:
    - Bảo toàn 100% tính `continuous` xuyên suốt qua `iter_days_continuous` và `DriverMemory`.
    - Sinh xong ngày nào, chuyển thành bảng và GHI NGAY vào Parquet qua TableStreamWriter.
    - Giải phóng hoàn toàn dữ liệu thô (GPS pings, SimPy events, trips) của ngày cũ khỏi RAM.
    - Duy trì bộ nhớ RAM ở mức cực thấp (< 500MB), không bao giờ bị tràn RAM hay văng tiến trình
      ngay cả khi tăng số lượng tài xế lên hàng nghìn xe và mô phỏng 30-90 ngày liên tục.
    - Tùy chọn `telemetry=True`: tự động xuất song song `vehicle_telemetry_ping.parquet` cho toàn bộ 3.000 xe.
    """
    import gc
    from gsm_sim.config import Config as _Cfg
    from gsm_sim.policy import PolicyBundle as _SimPolicy
    from gsm_core.policy import clawback_vnd

    cfg_path = config_path or (ROOT / "configs" / "pilot_hanoi.yaml")
    out_dir.mkdir(parents=True, exist_ok=True)
    d0 = _date.fromisoformat(start_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(days)]
    quota = _SimPolicy.from_config(_Cfg.load(cfg_path)).weekly_quota or {}
    quota_min = quota.get("min_revenue_vnd")
    min_days = int(quota.get("min_active_days") or 0)
    win_lo, win_hi = (dates[0], dates[-1]) if dates else ("", "")

    stream_writer = TableStreamWriter(out_dir)

    rng = random.Random(seed_base ^ 0x5EED)
    crng = random.Random(seed_base ^ 0xCA9C)

    universe = None
    l0_driver_profile = None
    l0_policy_bundle = None
    sim_mission_catalog = []

    # Bộ tích lũy tuần và tiến độ nhiệm vụ (siêu nhẹ, chỉ vài chục KB)
    by_dw: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    last_prog: dict[tuple[str, str], tuple[str, int]] = {}
    prog_car: dict[str, int] = defaultdict(int)

    # Bộ nhớ phần cứng telemetry xuyên suốt 30 ngày continuous
    tel_hw_state: dict[str, dict[str, Any]] = {}
    tel_seq = 0

    hx = 0
    fid = 0
    eid = 0

    print(f"\n[Streaming Pipeline] Khởi chạy mô phỏng {days} ngày ({start_date} -> {dates[-1]}), continuous={continuous}...", flush=True)

    if continuous:
        day_iter = iter_days_continuous(cfg_path, seed=seed_base, dates=dates)
    else:
        def _legacy_iter():
            for i, d_str in enumerate(dates):
                s = seed_base + i
                day = generate_day(cfg_path, seed=s, date=d_str)
                yield i, d_str, day, None
        day_iter = _legacy_iter()

    for day_idx, d_str, day, _mem in day_iter:
        print(f"[Day {day_idx+1:02d}/{days}] Date: {d_str}...", flush=True)

        if "app_event" in day:
            day["app_event"] = [e for e in day["app_event"] if e["kind"] in ("go_online", "go_offline")]

        # Ngày đầu: ghi L0 tables và dựng universe
        if day_idx == 0:
            l0_driver_profile = day.get("driver_profile", [])
            l0_policy_bundle = day.get("policy_bundle", [])
            universe = build_profile_universe(l0_driver_profile, seed_base)
            if l0_driver_profile:
                write_table_parquet(l0_driver_profile, out_dir / "driver_profile.parquet")
                stream_writer.counts["driver_profile"] = len(l0_driver_profile)
            if l0_policy_bundle:
                write_table_parquet(l0_policy_bundle, out_dir / "policy_bundle.parquet")
                stream_writer.counts["policy_bundle"] = len(l0_policy_bundle)
            sm0 = day.get("_sim_missions")
            if sm0 and "catalog" in sm0:
                sim_mission_catalog = list(sm0["catalog"])

        day_out: dict[str, list[dict]] = {e: [] for e in L1R_ENTITIES}

        # 1. Gom trips, online span, pings của ngày này
        trips_by = defaultdict(list)
        online_by = defaultdict(list)
        for t in day.get("trip_record", []):
            trips_by[t["driver_id"]].append(t)
        for e in day.get("app_event", []):
            if e["kind"] in ("go_online", "go_offline"):
                online_by[e["driver_id"]].append((e["t"], e["kind"]))

        sd = day.get("_sim_driver_day")
        day_sim_stats = sd["stats"] if sd else {}

        def _calc_online_h(drv):
            spans = sorted(online_by.get(drv, []))
            h, stack = 0.0, None
            for ts, kind in spans:
                if kind == "go_online":
                    stack = ts
                elif kind == "go_offline" and stack:
                    h += (datetime.fromisoformat(ts) - datetime.fromisoformat(stack)).total_seconds() / 3600
                    stack = None
            return h

        for drv, prof in universe.items():
            trips = trips_by.get(drv, [])
            _emit_day(day_out, drv, d_str, sorted(trips, key=lambda x: x["t_complete"]),
                      prof, _calc_online_h(drv), rng, sim_stats=day_sim_stats.get(drv))

        # 2. Cuốc hủy trong ngày
        for c in day.get("_cancelled_orders", []):
            if c["driver_id"] not in universe:
                continue
            roll = crng.random()
            if roll < 0.55:
                reason, by = "khach_doi_y", "customer"
            elif roll < 0.80:
                reason, by = "su_co_xe", "driver"
            else:
                reason, by = "khach_khong_den", "driver"
            day_out["trips"].append({
                "schema_version": "1.1.0", "source": "MOCK", "trip_id": c["order_id"], "driver_id": c["driver_id"],
                "customer_id": f"cust-{c['order_id']}", "service_type": universe[c["driver_id"]]["service_type"],
                "vehicle_class": c["vehicle_class"], "cancel_reason": reason, "cancelled_by": by,
                "status": "cancelled", "request_time": c["t_request"], "assign_time": c["t_assign"],
                "pickup_time": None, "complete_time": None,
                "pickup_h3": c["pickup_h3"], "drop_h3": c["drop_h3"], "distance_km": 0.0,
                "duration_seconds": None, "gross_vnd": 0, "commission_vnd": 0,
                "rush_hour": _hour(c["t_request"]) in RUSH_HOURS,
                "travel_mode": universe[c["driver_id"]]["service_type"],
                "created_at": c["t_request"], "datastream_metadata": None})

        # 3. Telemetry CAN-bus/TCU xe điện VinFast (nếu bật cờ --telemetry)
        if telemetry:
            import bisect
            from lcx_core.frauds._common import make_valid_vin
            raw_pings = day.get("gps_ping", [])
            day_intervals = {}
            for t in day_out["trips"]:
                if t.get("pickup_time") and t.get("complete_time"):
                    day_intervals.setdefault(t["driver_id"], []).append(
                        (str(t["pickup_time"]), str(t["complete_time"]), str(t["trip_id"]))
                    )
            for d in day_intervals:
                day_intervals[d].sort(key=lambda x: x[0])
            day_starts = {d: [x[0] for x in day_intervals[d]] for d in day_intervals}

            daily_tel_records = []
            for p in raw_pings:
                d_id = p["driver_id"]
                t_str = p["t"]
                loc = p.get("location") or {}
                lat = float(loc.get("lat", 21.0285))
                lng = float(loc.get("lon", 105.8542))
                h3_res9 = str(loc.get("h3", ""))
                speed = float(p.get("speed_kmh", 0.0))

                if d_id not in tel_hw_state:
                    d_seed = abs(hash(str(d_id))) % 10000
                    tel_hw_state[d_id] = {
                        "odometer_km": 15000.0 + d_seed * 1.5,
                        "battery_pct": 92.0 + (d_seed % 8),
                        "vin": make_valid_vin(d_id),
                    }
                hw = tel_hw_state[d_id]
                dist_km = (speed * (0.5 / 60.0)) if speed > 0 else 0.0
                hw["odometer_km"] = round(hw["odometer_km"] + dist_km, 2)
                hw["battery_pct"] = max(10.0, round(hw["battery_pct"] - (dist_km * 0.16), 1))

                active_trip = None
                if d_id in day_starts:
                    idx = bisect.bisect_right(day_starts[d_id], t_str) - 1
                    if idx >= 0:
                        tp, tc, oid = day_intervals[d_id][idx]
                        if tp <= t_str <= tc:
                            active_trip = oid

                is_passenger = active_trip is not None
                daily_tel_records.append({
                    "ping_id": f"tp-{seed_base}-{d_id}-{tel_seq}",
                    "driver_id": d_id,
                    "trip_id": active_trip,
                    "source_device": "vehicle_tcu_car",
                    "occurred_at": t_str,
                    "lat": lat,
                    "lng": lng,
                    "current_hex_h3_res9": h3_res9,
                    "speed_kmh": speed,
                    "heading_deg": None,
                    "vin": hw["vin"],
                    "gear": "D" if speed > 1.0 else "P",
                    "odometer_km": hw["odometer_km"],
                    "battery_level_pct": hw["battery_pct"],
                    "remaining_range_km": max(30.0, round(hw["battery_pct"] * 3.5, 1)),
                    "charging_status": "DISCHARGING" if speed > 1.0 else "IDLE",
                    "seat_occupancy": json.dumps({"driver": True, "any_passenger_seat": is_passenger}),
                    "door_lock_status": "LOCKED" if speed > 15.0 else "UNLOCKED",
                    "trunk_status": "CLOSED",
                    "phone_connectivity_lost": False,
                })
                tel_seq += 1

            if daily_tel_records:
                stream_writer.write_chunk("vehicle_telemetry_ping", daily_tel_records)
                del daily_tel_records

        # 4. Hex tracking từ gps dwell của ngày này
        pings_by = defaultdict(list)
        for p in day.pop("gps_ping", []):
            drv = p["driver_id"]
            t = p["t"]
            h = p["location"]["h3"]
            lst = pings_by[drv]
            if len(lst) >= 2 and lst[-1][1] == h and lst[-2][1] == h:
                lst[-1] = (t, h)
            else:
                lst.append((t, h))

        for drv in sorted(list(pings_by.keys())):
            ps = pings_by.pop(drv)
            ps.sort(key=lambda x: x[0])
            seg_hex = seg_start = prev_hex = prev_t = None
            for t, h in ps:
                if h != seg_hex:
                    if seg_hex is not None:
                        dur = int((datetime.fromisoformat(prev_t) - datetime.fromisoformat(seg_start)).total_seconds())
                        reposition = rng.random() < 0.05
                        day_out["public_driver_hex_tracking"].append({
                            "schema_version": "1.0.0", "source": "MOCK", "id": f"hx-{seed_base}-{hx}", "driver_id": drv,
                            "campaign_id": "repo-01" if reposition else None, "log_id": None,
                            "init_hex": prev_hex, "current_hex": seg_hex, "last_hex": prev_hex,
                            "target_hex": (f"8amock{rng.randint(0, 60):02d}") if reposition else None,
                            "last_seen_at": prev_t, "entered_current_hex_at": seg_start,
                            "stay_duration_seconds": dur,
                            "reached_target": (rng.random() < 0.6) if reposition else None,
                            "reached_target_at": None, "hex_history": None, "created_at": seg_start,
                            "updated_at": prev_t, "schedule_job_id": None, "datastream_metadata": None,
                            "tracking_status": ("offline" if dur > OFFLINE_DWELL_SECONDS
                                                else ("idle" if dur > 300 else "moving"))})
                        hx += 1
                    seg_hex, seg_start, prev_hex = h, t, seg_hex
                prev_t = t
            del ps
        del pings_by

        # 4. Public frauds trong ngày
        NGUONG_HUY = 0.20
        for r in day_out["driver_statistic_daily"]:
            ty_le_huy = float(r.get("cancellation_rate") or 0.0)
            if ty_le_huy <= NGUONG_HUY:
                continue
            do_lech = (ty_le_huy - NGUONG_HUY) / (1.0 - NGUONG_HUY)
            confidence = round(min(0.95, 0.50 + 0.45 * do_lech), 2)
            severity = "high" if ty_le_huy > 0.30 else ("medium" if ty_le_huy > 0.25 else "low")
            day_out["public_frauds"].append({
                "schema_version": "1.0.0", "source": "INFERRED", "fraud_id": f"f-{seed_base}-{fid}",
                "driver_id": r["driver_id"], "detected_at": f"{r['local_date']}T18:00:00+07:00",
                "fraud_type": "abnormal_cancel", "severity": severity,
                "confidence": confidence, "evidence_ref": None,
                "status": "open" if ty_le_huy > 0.25 else "cleared",
                "created_at": f"{r['local_date']}T18:00:00+07:00", "datastream_metadata": None})
            fid += 1

        # 5. Public mission earn history trong ngày
        sm = day.get("_sim_missions")
        _targets = {m["mission_id"]: int(m["target"]) for m in (sim_mission_catalog or [])}
        if sm:
            for ev in sorted(sm.get("completed", []), key=lambda x: (x["driver_id"], x["t_iso"])):
                day_out["public_mission_earn_history"].append({
                    "schema_version": "1.0.0", "source": "MOCK", "id": f"eh-sim-{seed_base}-{eid}",
                    "created_at": ev["t_iso"], "updated_at": ev["t_iso"], "deleted_at": None,
                    "mission_id": ev["mission_id"], "order_id": None, "order_status": "completed",
                    "driver_id": ev["driver_id"], "customer_id": None, "service_type": "car",
                    "order_time": ev["t_iso"], "complete_time": ev["t_iso"], "travel_mode": "car",
                    "sap_contract_type": "core_owned", "type": "mission",
                    "count_order": _targets.get(ev["mission_id"], 0),
                    "count_stoppoint": _targets.get(ev["mission_id"], 0),
                    "earn": int(ev["reward_vnd"]),
                    "description": ev.get("name") or ev["mission_id"], "datastream_metadata": None,
                    "reward_level": "1"})
                eid += 1

        sim_set = {d_k for d_k, prf in universe.items() if prf.get("simulated")}
        for r in day_out["driver_income_daily"]:
            d_drv = r["driver_id"]
            d_dt = r["order_date"]
            if d_drv not in sim_set and r["total_order"] >= 20:
                day_out["public_mission_earn_history"].append({
                    "schema_version": "1.0.0", "source": "MOCK", "id": f"eh-{seed_base}-{eid}",
                    "created_at": f"{d_dt}T20:00:00+07:00", "updated_at": f"{d_dt}T20:00:00+07:00",
                    "deleted_at": None, "mission_id": "m-trip20",
                    "order_id": None, "order_status": "completed", "driver_id": d_drv, "customer_id": None,
                    "service_type": universe.get(d_drv, {}).get("service_type", "car"),
                    "order_time": f"{d_dt}T20:00:00+07:00", "complete_time": f"{d_dt}T20:00:00+07:00",
                    "travel_mode": universe.get(d_drv, {}).get("service_type", "car"),
                    "sap_contract_type": universe.get(d_drv, {}).get("track", "core_owned"), "type": "mission",
                    "count_order": r["total_order"], "count_stoppoint": r["total_order"], "earn": 30000,
                    "description": "20 chuyến/ngày", "datastream_metadata": None, "reward_level": "1"})
                eid += 1
            if d_drv not in sim_set:
                prog_car[d_drv] += r["total_order"]

        # 6. Tích lũy tuần & tiến độ mission
        for r in day_out["driver_income_daily"]:
            wk, ws, we = _week_key(r["order_date"])
            by_dw[(r["driver_id"], wk, ws, we)].append(r["total_fee"])

        if day_sim_stats:
            for drv_id, stt in day_sim_stats.items():
                for mid, cnt in (stt.get("mission_progress") or {}).items():
                    cur = last_prog.get((drv_id, mid))
                    if cur is None or d_str > cur[0]:
                        last_prog[(drv_id, mid)] = (d_str, int(cnt))

        # 7. GHI NGAY các bảng hàng ngày xuống đĩa
        daily_entities = [
            "trips", "public_driver_hex_tracking", "driver_statistic_daily",
            "driver_income_daily", "driver_orders_rush_hours", "driver_online_hours_sap_id",
            "driver_bike_stoppoints", "public_frauds", "public_mission_earn_history",
        ]
        for ent in daily_entities:
            recs = day_out.pop(ent, [])
            if recs:
                stream_writer.write_chunk(ent, recs)

        # 8. Giải phóng hoàn toàn dữ liệu ngày này khỏi RAM
        del day, day_out, trips_by, online_by, day_sim_stats
        gc.collect()

    # ---- Kết thúc vòng lặp ngày: xuất các bảng tuần, catalog và tiến độ ----
    print("\n[Finalizing] Chốt các bảng tuần, catalog và tiến độ nhiệm vụ...", flush=True)

    # A. kpi_driver_platform_calculator_gbq & driver_penalization_ATA
    kid = 0
    kpi_records = []
    rev_by_dw = {}
    for (drv, wk, ws, we), fees in sorted(by_dw.items()):
        rev = sum(fees)
        rev_by_dw[(drv, wk)] = rev
        prof = universe.get(drv, {})
        cut = bool(win_lo and win_hi) and (ws < win_lo or we > win_hi)
        if cut:
            status = "partial"
        elif quota_min is None:
            status = "unknown"
        else:
            days_active = len(fees)
            status = ("achieved" if rev >= int(quota_min) and days_active >= min_days
                      else "at_risk" if rev < int(quota_min) else "active")
        kpi_records.append({
            "schema_version": "1.0.0", "source": "MOCK", "id": f"kpi-{seed_base}-{kid}", "driver_id": drv,
            "driver_name": f"MOCK Driver {drv}", "sap_id": f"SAP-{drv}", "status": status,
            "week_key": wk, "week_start": ws, "week_end": we, "kpi_month": _date.fromisoformat(ws).month,
            "kpi_year": _date.fromisoformat(ws).year, "email": None, "tel": None, "engname": None,
            "depot_code": prof.get("depot_code", "DPT01"), "depot_name": prof.get("depot_name", "Gia Lam"),
            "vehicle_vin_number": f"VIN{drv}",
            "vehicle_license_plate": f"29-MOCK{drv}", "vehicle_model": prof.get("vehicle_model", "Feliz S"),
            "country": "VN", "type": prof.get("track", "platform"), "last_updated_date": f"{we}T23:59:00+07:00"})
        kid += 1
    stream_writer.write_chunk("kpi_driver_platform_calculator_gbq", kpi_records)

    pen_records = []
    for k in kpi_records:
        if k["status"] != "at_risk":
            continue
        rev = rev_by_dw.get((k["driver_id"], k["week_key"]), 0)
        amount = clawback_vnd(rev, quota)
        if not amount:
            continue
        gap = int(quota["min_revenue_vnd"]) - int(rev)
        pen_records.append({
            "schema_version": "1.0.0", "source": "MOCK", "penalization_id": f"pen-{seed_base}-{k['id']}",
            "driver_id": k["driver_id"], "local_date": k["week_end"], "week_key": k["week_key"],
            "penalty_type": "clawback_khoan", "amount_vnd": amount,
            "reason": (f"doanh số tuần {_vn_tien(rev)}, hụt {_vn_tien(gap)} so với khoán "
                       f"{_vn_tien(quota['min_revenue_vnd'])}; truy thu "
                       f"{float(quota['clawback_rate']):.0%} phần hụt"),
            "related_metric": "weekly_revenue", "ata_code": None,
            "status": "applied", "created_at": f"{k['week_end']}T23:00:00+07:00"})
    stream_writer.write_chunk("driver_penalization_ATA", pen_records)

    # B. public_mission
    mission_records = []
    base_missions = [
        ("m-trip20", "trip_count", "20 chuyến/ngày", 30000, 20),
        ("m-rush", "rush_hour", "2 chuyến khung vàng", 30000, 2),
        ("m-week250", "trip_count", "250 chuyến/tuần", 1000000, 250),
    ]
    for mid, mtype, name, reward, target in base_missions:
        mission_records.append({
            "schema_version": "1.0.0", "source": "MOCK", "id": mid, "created_at": "2026-07-01T00:00:00+07:00",
            "updated_at": None, "deleted_at": None, "created_by": "gsm", "updated_by": None,
            "mission_type": mtype, "parent_id": None, "name": name, "state": "active", "audience": "core_owned",
            "description": name, "start_time": "2026-07-01T00:00:00+07:00", "end_time": "2026-12-31T23:59:00+07:00",
            "point_id": None, "rewards": {"vnd": reward, "target_count": target},
            "mission_claim": None, "mission_code": mid,
            "time_claim_reward": None, "rule_code": None, "meta_data": None, "contract_type": None,
            "qualify_execute_code": None, "status": "active", "datastream_metadata": None,
            "business_code": None, "show_only": False, "is_ddi_mission": False})

    for m in (sim_mission_catalog or []):
        w = m.get("window")
        mission_records.append({
            "schema_version": "1.0.0", "source": "MOCK", "id": m["mission_id"],
            "created_at": "2026-07-01T00:00:00+07:00",
            "updated_at": None, "deleted_at": None, "created_by": "gsm", "updated_by": None,
            "mission_type": "trip_count" if w is None else "rush_hour", "parent_id": None,
            "name": m["name"], "state": "active", "audience": "core_owned",
            "description": m["name"], "start_time": "2026-07-01T00:00:00+07:00",
            "end_time": "2026-12-31T23:59:00+07:00", "point_id": None,
            "rewards": {"vnd": int(m["reward_vnd"]), "target_count": int(m["target"])},
            "mission_claim": None, "mission_code": m["mission_id"],
            "time_claim_reward": None, "rule_code": None, "meta_data": None, "contract_type": None,
            "qualify_execute_code": None, "status": "active", "datastream_metadata": None,
            "business_code": None, "show_only": False, "is_ddi_mission": False})
    stream_writer.write_chunk("public_mission", mission_records)

    # C. public_user_mission_progress
    user_prog_records = []
    pid = 0
    if last_prog and sim_mission_catalog:
        targets = {m["mission_id"]: int(m["target"]) for m in sim_mission_catalog}
        rewards = {m["mission_id"]: int(m["reward_vnd"]) for m in sim_mission_catalog}
        for (drv, mid), (d_last, cnt) in sorted(last_prog.items()):
            tgt = targets.get(mid, 0)
            user_prog_records.append({
                "schema_version": "1.0.0", "source": "MOCK", "id": f"ump-sim-{seed_base}-{pid}",
                "driver_id": drv, "mission_id": mid, "progress_count": cnt,
                "target_count": tgt, "progress_value_vnd": 0,
                "target_value_vnd": rewards.get(mid, 0),
                "state": "completed" if cnt >= tgt else "in_progress",
                "started_at": f"{d_last}T00:00:00+07:00", "updated_at": None, "claimed_at": None,
                "datastream_metadata": None})
            pid += 1

    for drv, total in sorted(prog_car.items()):
        user_prog_records.append({
            "schema_version": "1.0.0", "source": "MOCK", "id": f"ump-{seed_base}-{pid}", "driver_id": drv,
            "mission_id": "m-week250", "progress_count": min(total, 250), "target_count": 250,
            "progress_value_vnd": 0, "target_value_vnd": 1000000,
            "state": "completed" if total >= 250 else "in_progress",
            "started_at": f"{dates[0]}T00:00:00+07:00", "updated_at": None, "claimed_at": None,
            "datastream_metadata": None})
        pid += 1
    stream_writer.write_chunk("public_user_mission_progress", user_prog_records)

    # Đóng tất cả writer
    counts = stream_writer.close(all_entities=L1R_ENTITIES)
    if "driver_profile" not in counts and (out_dir / "driver_profile.parquet").exists():
        counts["driver_profile"] = len(l0_driver_profile) if l0_driver_profile else 0
    if "policy_bundle" not in counts and (out_dir / "policy_bundle.parquet").exists():
        counts["policy_bundle"] = len(l0_policy_bundle) if l0_policy_bundle else 0

    print(f"\n[Writing] Đã hoàn thành xuất {len(counts)} bảng Parquet (13 L1R + 2 L0) vào {out_dir}:", flush=True)
    for ent, cnt in sorted(counts.items()):
        print(f"  - {ent}.parquet: {cnt:,} records", flush=True)

    manifest = {"label": "MOCK", "generator": "gsm_core.mockgen.realdata v5 (streaming continuous, D-SIM-13)",
                "engine_commit": _git_commit(),
                "engine_note": ("hành vi sim sau SIM-1 (served ~82%, accept bám accept_base, "
                                "completion ~95%, có huỷ-sau-nhận) và SIM-3 fix D "
                                "(driver BIKE đọc counter sim, không suy ngược từ target). "
                                "Cơ chế streaming ghi từng ngày tiết kiệm RAM."),
                "days": days, "seed_base": seed_base, "start_date": start_date, "record_counts": counts,
                "profile_universe": kind_distribution(universe) if universe else {},
                "schema_versions": {e: _reg().schema_version(e) for e in counts if e in L1R_ENTITIES}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return {"tables": counts, "manifest": manifest, "universe": universe}


def generate_realdata(days: int, seed_base: int, out_dir: Path, config_path: Path | None = None,
                      start_date: str = "2026-07-01",
                      continuous: bool = True,
                      stream: bool = True,
                      telemetry: bool = False) -> dict:
    """`continuous=True` (mặc định, D-SIM-13): các ngày là CHUỖI LIÊN TỤC — cùng nhóm
    tài xế, trạng thái mang sang qua `run_multiday`. `False` = đường cũ (mỗi ngày một run
    độc lập).

    `stream=True` (mặc định): cơ chế streaming cuốn chiếu từng ngày, ghi trực tiếp ra đĩa
    và giải phóng RAM, giúp chạy 30-90 ngày với quy mô lớn mà không bao giờ bị OOM crash.
    `stream=False`: đường cũ (gom toàn bộ 30 ngày vào RAM rồi mới xuất).
    `telemetry=True`: tự động xuất kèm bảng vehicle_telemetry_ping.parquet cho toàn bộ 3.000 xe.
    """
    if stream:
        return generate_realdata_stream(days, seed_base, out_dir, config_path=config_path,
                                        start_date=start_date, continuous=continuous,
                                        telemetry=telemetry)

    cfg_path = config_path or (ROOT / "configs" / "pilot_hanoi.yaml")
    out_dir.mkdir(parents=True, exist_ok=True)
    d0 = _date.fromisoformat(start_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(days)]
    if continuous:
        day_outputs = generate_days_continuous(cfg_path, seed=seed_base, dates=dates)
        for day in day_outputs:
            if "app_event" in day:
                day["app_event"] = [e for e in day["app_event"] if e["kind"] in ("go_online", "go_offline")]
    else:
        day_outputs = []
        for i in range(days):
            d_str = dates[i]
            s = seed_base + i
            print(f"[Simulating Day {i+1:02d}/{days}] Date: {d_str} (Seed: {s})...", flush=True)
            day = generate_day(cfg_path, seed=s, date=d_str)
            if "app_event" in day:
                day["app_event"] = [e for e in day["app_event"] if e["kind"] in ("go_online", "go_offline")]
            day_outputs.append(day)
    print(f"\n[Processing] Aggregating 13 production tables across {days} days...", flush=True)
    l0_driver_profile = day_outputs[0]["driver_profile"]
    l0_policy_bundle = day_outputs[0]["policy_bundle"]
    universe = build_profile_universe(l0_driver_profile, seed_base)
    tables = build_tables(day_outputs, universe, seed_base)
    sim_ev = tables.pop("_sim_mission_events", [])
    sim_cat = tables.pop("_sim_mission_catalog", [])
    sim_st = tables.pop("_sim_stats", {})

    from gsm_sim.config import Config as _Cfg
    from gsm_sim.policy import PolicyBundle as _SimPolicy
    quota = _SimPolicy.from_config(_Cfg.load(cfg_path)).weekly_quota
    build_weekly_and_missions(tables, universe, seed_base,
                              sim_mission_events=sim_ev, sim_mission_catalog=sim_cat,
                              sim_stats=sim_st, quota=quota,
                              window=(dates[0], dates[-1]) if dates else None)

    tables["driver_profile"] = l0_driver_profile
    tables["policy_bundle"] = l0_policy_bundle

    print(f"[Writing] Exporting {len(tables)} Parquet files (13 L1R + 2 L0) to {out_dir}...", flush=True)
    counts = {}
    for entity, records in tables.items():
        counts[entity] = write_table_parquet(records, out_dir / f"{entity}.parquet")
        print(f"  - {entity}.parquet: {counts[entity]:,} records", flush=True)
    manifest = {"label": "MOCK", "generator": "gsm_core.mockgen.realdata v4 (multi-day continuous, D-SIM-13)",
                "engine_commit": _git_commit(),
                "engine_note": ("hành vi sim sau SIM-1 (served ~82%, accept bám accept_base, "
                                "completion ~95%, có huỷ-sau-nhận) và SIM-3 fix D "
                                "(driver BIKE đọc counter sim, không suy ngược từ target)"),
                "days": days, "seed_base": seed_base, "start_date": start_date, "record_counts": counts,
                "profile_universe": kind_distribution(universe),
                "schema_versions": {e: _reg().schema_version(e) for e in tables}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return {"tables": tables, "manifest": manifest, "universe": universe}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Sinh toan bo 13 bang Parquet L1R tu mo phong.")
    ap.add_argument("--days", type=int, default=1, help="So ngay mo phong")
    ap.add_argument("--seed-base", type=int, default=7000, help="Seed ngau nhien")
    ap.add_argument("--out", type=str, default="data/mock/my_13_tables", help="Thu muc xuat")
    ap.add_argument("--start-date", type=str, default="2026-07-01", help="Ngay bat dau YYYY-MM-DD")
    ap.add_argument("--continuous", action="store_true", default=False, help="Chay chuoi ngay lien tuc mang theo trang thai tai xe")
    ap.add_argument("--stream", action="store_true", default=True, help="Che do streaming ghi tung ngay tiet kiem RAM (mac dinh bat)")
    ap.add_argument("--no-stream", dest="stream", action="store_false", help="Tat che do streaming (gom toan bo vao RAM)")
    ap.add_argument("--telemetry", action="store_true", default=False, help="Sinh dong thoi bang vehicle_telemetry_ping.parquet truc tiep tu mo phong cho toan bo 3.000 xe")
    args = ap.parse_args()

    out_dir = ROOT / args.out
    res = generate_realdata(args.days, args.seed_base, out_dir, start_date=args.start_date,
                            continuous=args.continuous, stream=args.stream,
                            telemetry=args.telemetry)
    print(json.dumps({
        "status": "SUCCESS",
        "tables_generated": len(res["tables"]),
        "out_dir": str(out_dir),
        "record_counts": res["manifest"]["record_counts"]
    }, indent=2))


if __name__ == "__main__":
    main()

