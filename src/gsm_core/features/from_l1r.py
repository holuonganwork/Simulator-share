"""PI-4a — Derive L3 views TỪ BẢNG THẬT (l1r) thay vì recompute từ event sim.

Khác biệt cốt lõi vs `bonus_gap.py`/`session_summary.py` (đường L1 sim): bảng thật
gsm-data-prod đã có **field ĐO ĐƯỢC** — `acceptance_rate`, `fulfillment_rate`,
`online_time`, `total_fee`/`commission` — nên ĐỌC THẲNG, KHÔNG tự đếm lại từ event
(recompute chính là nguồn sai lệch: gốc BUG-PI2b-02 online=0).

Chỉ thứ bảng thật KHÔNG có mới tính: `points_now` (điểm = trips × PolicyBundle),
`historical_points_per_hour`. `soc_pct` = None (13 bảng không có telemetry pin — TBC).

Input `l1r` = dict {entity: [records]} (shape `generate_realdata()["tables"]`) — KHÔNG
đọc file (file I/O thuộc PI-3 DataSource). S4 `allocation_input` KHÔNG remap: bảng thật
thiếu station/battery capacity.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _dt_date, datetime as _dt_datetime, timedelta

from gsm_core.policy import PolicyBundle
from gsm_core.features._common import date as _date, hour as _hour, min_of_day
from gsm_core.rates import (bucket_of_hour, bucket_online_hours_estimated,
                            bucket_rate_samples, median_bucket_rates)

VIEW_VERSION = "1.0.0"


def _rows(l1r: dict, entity: str) -> list[dict]:
    return l1r.get(entity) or []


def _parsable_ts(ts) -> bool:
    try:
        _dt_datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return False
    return True


def _observed_seconds(start: str, dur_s: int, t_now: str) -> int:
    """Phần dwell ĐÃ QUAN SÁT ĐƯỢC tại `t_now` (D-M3-11).

    Trả 0 nếu dwell bắt đầu sau `t_now` (chưa xảy ra); cắt về `t_now - start` nếu còn đang
    diễn ra; giữ nguyên nếu đã kết thúc. Chuỗi không parse được ⇒ giữ nguyên `dur_s` (thà
    giữ số cũ hơn là im lặng làm rỗng view khi gặp định dạng lạ).
    """
    try:
        st = _dt_datetime.fromisoformat(start)
        tn = _dt_datetime.fromisoformat(t_now)
    except (TypeError, ValueError):
        return dur_s
    if st.tzinfo is None or tn.tzinfo is None:  # so aware với naive là TypeError
        return dur_s
    elapsed = (tn - st).total_seconds()
    if elapsed <= 0:
        return 0
    return int(min(dur_s, elapsed))


def _provenance(*records) -> str:
    """Nhãn source THEO record nguồn (§5) — không hard-code MOCK."""
    for r in records:
        if r and r.get("source") == "REAL":
            return "REAL"
    return "MOCK"


def _stat_row(l1r: dict, driver_id: str, d: str) -> dict | None:
    for r in _rows(l1r, "driver_statistic_daily"):
        if r["driver_id"] == driver_id and r["local_date"] == d:
            return r
    return None


def _online_row(l1r: dict, driver_id: str, d: str) -> dict | None:
    for r in _rows(l1r, "driver_online_hours_sap_id"):
        if r["driver_id"] == driver_id and r["local_date"] == d:
            return r
    return None


def _income_row(l1r: dict, driver_id: str, d: str) -> dict | None:
    for r in _rows(l1r, "driver_income_daily"):
        if r["driver_id"] == driver_id and r["order_date"] == d:
            return r
    return None


def _driver_trips(l1r: dict, driver_id: str, d: str | None = None) -> list[dict]:
    out = [t for t in _rows(l1r, "trips") if t["driver_id"] == driver_id]
    if d is not None:
        out = [t for t in out if _date(t["complete_time"]) == d]
    return sorted(out, key=lambda t: t["complete_time"])


def _points_from_trips(trips: list[dict], policy: PolicyBundle) -> int:
    """Điểm KHÔNG có trong bảng thật → suy từ giờ KHÁCH ĐẶT (request_time) × policy."""
    return sum(policy.trip_points(_hour(t["request_time"])) for t in trips)


# ---------- S1: bonus_gap_input ----------

def derive_bonus_gap_input_l1r(driver_id: str, t_now: str, l1r: dict, policy: PolicyBundle,
                               shift_window: list[int] | None = None) -> dict:
    """acceptance/completion ĐỌC THẲNG từ `driver_statistic_daily` (đo được)."""
    today = _date(t_now)
    stat = _stat_row(l1r, driver_id, today)
    onl = _online_row(l1r, driver_id, today)
    # AUDIT A3 LAYEROUT-4 (UPDATE-070): CẮT tại t_now — bản cũ lọc theo NGÀY nên 08:00 sáng
    # đã cộng điểm của cuốc chạy chiều ⇒ S1 tuyên bố "đã đạt mốc cao nhất" lúc 8h (rò tương
    # lai TRONG ngày). UI adapter vốn đã cắt đúng (`_points_until`) — nay hai đường khớp nhau.
    trips_today = [t for t in _driver_trips(l1r, driver_id, today)
                   if t["complete_time"] <= t_now]

    points_now = _points_from_trips(trips_today, policy)

    # measured — KHÔNG tự đếm accept/decline.
    # Không có dòng đo cho hôm nay (vd sáng sớm chưa có cuốc) → KHÔNG bịa 1.0 lạc quan:
    # carry-forward giá trị ĐO gần nhất, và hạ nhãn source xuống ESTIMATED (§5 truy vết).
    estimated = False
    if stat:
        acceptance = float(stat["acceptance_rate"])
        completion = float(stat["fulfillment_rate"])
    else:
        prior = sorted((r for r in _rows(l1r, "driver_statistic_daily")
                        if r["driver_id"] == driver_id and r["local_date"] < today),
                       key=lambda r: r["local_date"])
        estimated = True
        if prior:
            acceptance = float(prior[-1]["acceptance_rate"])
            completion = float(prior[-1]["fulfillment_rate"])
        else:  # chưa từng có dữ liệu đo → neutral, nhãn ESTIMATED (không phải "đo được")
            acceptance = completion = 1.0

    # quỹ giờ còn: ưu tiên declared window; nếu không có → suy từ online_time đã dùng
    now_min = min_of_day(t_now)
    if shift_window:
        hours_budget = max(0.0, (shift_window[1] - now_min) / 60.0)
    else:
        # ASSUMPTION: trần ca 12h/ngày (không có declared window trong bảng thật)
        used = float(onl["online_time"]) if onl else 0.0
        hours_budget = max(0.0, min(12.0 - used, (24 * 60 - now_min) / 60.0))

    # historical điểm/giờ theo khung (ngày trước today)
    per_bucket: dict[str, list[float]] = defaultdict(list)
    # D-M3-11: `- {today}` bỏ ĐÚNG hôm nay nhưng KHÔNG bỏ ngày SAU hôm nay ⇒ "tốc độ điểm
    # lịch sử" gộp cả ngày chưa tới (bảng 6 ngày, hỏi ngày 3: lịch sử gồm ngày 4/5/6). Đo được
    # ở seed 900: có số {peak 3.846, offpeak 5.769} khi thấy tương lai, và {} khi không —
    # tức chính ngày tương lai làm đủ ngưỡng ≥3 ngày. S1 dùng số này để nói "đạt mốc được".
    hist_days = sorted(d for d in {_date(t["complete_time"]) for t in _driver_trips(l1r, driver_id)}
                       if d < today)
    # ⚠ D-ADV-04 (2026-08-06): mẫu số phải là giờ online TRONG BUCKET (solver nhân rate với TỪNG
    # GIỜ của bucket). Bảng thật KHÔNG có `go_online`/`go_offline` — chỉ có TỔNG `online_time`
    # (`specs/real-data/data-contract-counterfactual.md`) ⇒ đường này buộc phải XẤP XỈ: phân bổ tổng
    # đó theo HÌNH DẠNG span hoạt động ∩ bucket. Là xấp xỉ ⇒ có nhãn `estimated_span_scaled`, và bất
    # biến "không bịa thêm giờ online" được ghim bằng test.
    rate_method = "none"
    for d in hist_days:
        o = _online_row(l1r, driver_id, d)
        oh = float(o["online_time"]) if o else 0.0
        if oh <= 0:
            continue
        day_trips = _driver_trips(l1r, driver_id, d)
        if not day_trips:
            continue
        span = (min(min_of_day(t["request_time"]) for t in day_trips),
                max(min_of_day(t["complete_time"]) for t in day_trips))
        bucket_hours, m = bucket_online_hours_estimated(policy, oh, span)
        if not bucket_hours:
            continue
        rate_method = m
        bp: dict[str, float] = defaultdict(float)
        for t in day_trips:
            h = _hour(t["request_time"])
            b = bucket_of_hour(policy, h)
            if b is not None:
                bp[b] += policy.trip_points(h)
        # ADV-05: bucket ĐÃ online mà 0 điểm ⇒ đóng 0.0, không bị loại khỏi mẫu (survivorship)
        for b, rate in bucket_rate_samples(bp, bucket_hours).items():
            per_bucket[b].append(rate)
    hist_rate = median_bucket_rates(per_bucket)

    return {
        "schema_version": "1.1.0", "driver_id": driver_id, "t_now": t_now,
        "points_now": points_now,
        "next_tiers": [[pt, vnd] for pt, vnd in policy.day_bonus_tiers if pt > points_now],
        "historical_points_per_hour": hist_rate,
        # D-ADV-04: bảng thật không có mốc online ⇒ mẫu số là XẤP XỈ, phải nói ra
        "historical_rate_method": rate_method,
        "hours_budget_remaining": round(hours_budget, 3),
        "acceptance_rate": round(acceptance, 4), "completion_rate": round(completion, 4),
        "policy_bundle_version": policy.version, "view_version": VIEW_VERSION,
        # ESTIMATED khi rate là carry-forward/neutral (không có dòng đo hôm nay)
        "source": "ESTIMATED" if estimated else _provenance(stat, onl),
    }


# ---------- S3: session_summary_input ----------

def derive_session_summary_input_l1r(driver_id: str, session_date: str, l1r: dict,
                                     policy: PolicyBundle | None = None) -> dict:
    """payout_breakdown ĐỌC THẲNG: gross=total_fee, driver_payout=commission (đã tách sẵn)."""
    inc = _income_row(l1r, driver_id, session_date)
    stat = _stat_row(l1r, driver_id, session_date)
    onl = _online_row(l1r, driver_id, session_date)
    trips = _driver_trips(l1r, driver_id, session_date)

    gross = int(inc["total_fee"]) if inc else sum(int(t["gross_vnd"]) for t in trips)
    payout = int(inc["commission"]) if inc else 0

    day_state = {}
    if stat:
        day_state = {"acceptance_rate": stat["acceptance_rate"],
                     "fulfillment_rate": stat["fulfillment_rate"],
                     "completed_count": stat["completed_count"],
                     "count_rating_5_star": stat.get("count_rating_5_star"),
                     "online_time": (onl or {}).get("online_time")}
        if policy is not None:
            day_state["points"] = _points_from_trips(trips, policy)

    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "session_date": session_date,
        "trips": [{"trip_id": t["trip_id"], "request_time": t["request_time"],
                   "complete_time": t["complete_time"], "gross_vnd": t["gross_vnd"],
                   "distance_km": t.get("distance_km"), "rush_hour": t.get("rush_hour"),
                   "pickup_h3": t.get("pickup_h3"), "drop_h3": t.get("drop_h3")} for t in trips],
        "inferred_activities": [],  # L2i — điền ở PI-5 (idle từ hex_tracking)
        "payout_breakdown": {
            "gross_vnd": gross, "driver_payout_vnd": payout,
            # estimated_net CHỈ khi đủ known cost + definition version (§5) — chưa có
            "estimated_net_vnd": None, "net_definition_version": None},
        "day_state_end": day_state,
        "view_version": VIEW_VERSION, "source": _provenance(inc, stat),
    }


# ---------- S5: weekly_khoan_input ----------

def derive_weekly_khoan_input_l1r(driver_id: str, t_now: str, l1r: dict, policy: PolicyBundle,
                                  money_basis: str = "gross",
                                  hours_per_day: float = 10.0) -> dict:
    """Tiến độ khoán TUẦN (UC3). revenue = Σ `total_fee` (GROSS — quyết định (d)).

    Tuần lấy từ `kpi_driver_platform_calculator_gbq` (đo được) nếu có; else ISO week của t_now.
    quota lấy TỪ POLICY — nếu policy chưa có số thì để `None` (solver KHÔNG bịa).
    """
    today = _date(t_now)
    d0 = _dt_date.fromisoformat(today)

    wk_row = next((r for r in _rows(l1r, "kpi_driver_platform_calculator_gbq")
                   if r["driver_id"] == driver_id
                   and r["week_start"] <= today <= r["week_end"]), None)
    if wk_row:
        week_key, week_start, week_end = wk_row["week_key"], wk_row["week_start"], wk_row["week_end"]
    else:
        monday = d0 - timedelta(days=d0.weekday())
        iso = d0.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        week_start, week_end = monday.isoformat(), (monday + timedelta(days=6)).isoformat()

    field = "total_fee" if money_basis == "gross" else "commission"
    # AUDIT A1 S5-1 (UPDATE-065): "so_far" nghĩa là TỚI t_now — cắt tại `today`, nếu không
    # dataset chứa cả tuần (mock 90 ngày) sẽ RÒ TƯƠNG LAI vào revenue/days_active/avg_rate.
    cutoff = min(today, week_end)
    in_week = [r for r in _rows(l1r, "driver_income_daily")
               if r["driver_id"] == driver_id and week_start <= r["order_date"] <= cutoff]
    revenue = sum(int(r[field]) for r in in_week)
    days_active = sum(1 for r in in_week if r["total_order"] > 0)

    # giờ ĐO ĐƯỢC trong tuần (tới t_now) → avg revenue/giờ (null nếu chưa đủ dữ liệu)
    onl_week = [r for r in _rows(l1r, "driver_online_hours_sap_id")
                if r["driver_id"] == driver_id and week_start <= r["local_date"] <= cutoff]
    online_h = sum(float(r["online_time"]) for r in onl_week)
    avg_rev_per_hour = round(revenue / online_h, 2) if online_h > 0 else None

    days_remaining = max(0, (_dt_date.fromisoformat(week_end) - d0).days)
    hours_today_left = max(0.0, (24 * 60 - min_of_day(t_now)) / 60.0)
    hours_budget = round(min(hours_today_left, hours_per_day) + days_remaining * hours_per_day, 2)

    q = policy.weekly_quota if policy.has_weekly_quota() else None
    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "t_now": t_now,
        "week_key": week_key, "week_start": week_start, "week_end": week_end,
        "revenue_so_far_vnd": int(revenue), "days_active": days_active,
        "days_remaining": days_remaining, "hours_budget_remaining": hours_budget,
        "avg_revenue_per_hour": avg_rev_per_hour,
        "quota": dict(q) if q else None,
        "money_basis": money_basis,
        "policy_bundle_version": policy.version, "view_version": VIEW_VERSION,
        "source": _provenance(wk_row, in_week[0] if in_week else None),
    }


# ---------- S6: mission_select_input ----------

def derive_mission_select_input_l1r(driver_id: str, t_now: str, l1r: dict,
                                    hours_budget_remaining: float,
                                    trips_per_hour: float | None = None) -> dict:
    """Mission còn hiệu lực + tiến độ (UC8). reward CHỈ từ `public_mission.rewards`."""
    progress = {r["mission_id"]: r for r in _rows(l1r, "public_user_mission_progress")
                if r["driver_id"] == driver_id}

    # trips/hour ĐO ĐƯỢC từ lịch sử driver (fallback 2.0 — ASSUMPTION có nhãn)
    if trips_per_hour is None:
        inc = [r for r in _rows(l1r, "driver_income_daily") if r["driver_id"] == driver_id]
        onl = {r["local_date"]: float(r["online_time"])
               for r in _rows(l1r, "driver_online_hours_sap_id") if r["driver_id"] == driver_id}
        tot_o = sum(r["total_order"] for r in inc)
        tot_h = sum(onl.get(r["order_date"], 0.0) for r in inc)
        trips_per_hour = round(tot_o / tot_h, 3) if tot_h > 0 else 2.0
    trips_per_hour = max(0.1, float(trips_per_hour))

    missions = []
    for m in _rows(l1r, "public_mission"):
        p = progress.get(m["id"])
        state = (p or {}).get("state")
        if state in ("completed", "claimed", "expired"):
            continue  # đã xong → không đề xuất nữa
        ws, we = m.get("start_time"), m.get("end_time")
        if ws and we and not (ws <= t_now <= we):
            continue  # ngoài khung giờ hiệu lực
        rewards = m.get("rewards") or {}
        reward = int(rewards.get("vnd", 0))
        target = int((p or {}).get("target_count") or rewards.get("target_count") or 0)
        done = int((p or {}).get("progress_count") or 0)
        remaining = max(0, target - done)
        missions.append({
            "mission_id": m["id"], "name": m.get("name"), "mission_type": m.get("mission_type"),
            "reward_vnd": reward, "remaining_count": remaining,
            "window_start": ws, "window_end": we, "state": state})

    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "t_now": t_now,
        "hours_budget_remaining": round(float(hours_budget_remaining), 3),
        "trips_per_hour": trips_per_hour,
        "missions": sorted(missions, key=lambda x: x["mission_id"]),
        "view_version": VIEW_VERSION,
        "source": _provenance(*(_rows(l1r, "public_mission")[:1] or [None])),
    }


# ---------- S7: idle_reduction_input (UC5) ----------

def derive_idle_reduction_input_l1r(driver_id: str, t_now: str, l1r: dict,
                                    session_date: str | None = None,
                                    idle_min_seconds: int = 300) -> dict:
    """Khoảng chờ ĐO ĐƯỢC từ `public_driver_hex_tracking` + demand PROXY từ `trips`.

    D-004b: `hex` chỉ để THỐNG KÊ; solver KHÔNG dùng nó để chỉ định chỗ đứng (B1).
    `active_reposition` chỉ lấy khi data có `campaign_id` (nhiệm vụ CHÍNH THỨC GSM).
    """
    d = session_date or _date(t_now)
    segs, repo = [], None
    for r in _rows(l1r, "public_driver_hex_tracking"):
        if r["driver_id"] != driver_id:
            continue
        seen = r.get("last_seen_at") or r.get("created_at") or ""
        if seen[:10] != d:
            continue
        dur = int(r.get("stay_duration_seconds") or 0)
        if r.get("tracking_status") == "idle":
            start = r.get("entered_current_hex_at") or seen
            # `_hour(start)` làm `int(iso[11:13])` ⇒ timestamp RÁC gây ValueError, tức view NỔ
            # thay vì degrade. Lỗi này có TRƯỚC D-M3-11 (`or seen` chỉ đỡ field RỖNG, không đỡ
            # field rác). Lùi về `last_seen_at`; nếu nó cũng không parse được thì record không
            # định vị được trong thời gian ⇒ BỎ, vì mọi con số của nó đều vô nghĩa.
            if not _parsable_ts(start):
                start = seen
                if not _parsable_ts(start):
                    continue
            # D-M3-11: chỉ phần ĐÃ quan sát được tại t_now. Lọc theo ngày là KHÔNG đủ — dwell
            # bắt đầu 23:03 vẫn lọt vào view hỏi lúc 23:00, làm `total_idle_min` vượt cả
            # `online_time` (bất khả) và làm S7 khuyên trên thời gian chờ của TƯƠNG LAI.
            # Ngưỡng `idle_min_seconds` áp SAU khi cắt: dwell vừa dài 2′ chưa phải "chờ lâu".
            dur = _observed_seconds(start, dur, t_now)
            if dur >= idle_min_seconds:
                segs.append({"hex": r.get("current_hex"), "start": start,
                             "duration_seconds": dur, "hour": _hour(start)})
        if repo is None and r.get("campaign_id"):  # nhiệm vụ reposition của GSM
            # D-M3-11: nhiệm vụ chưa BẮT ĐẦU thì không tồn tại với người hỏi lúc t_now; và
            # `reached_target` là kết cục TỔNG KẾT của segment (`reached_target_at` rỗng trong
            # cả mock lẫn 13 bảng thật) ⇒ segment còn đang diễn ra thì kết cục CHƯA CHỐT, khai
            # `None` = "chưa biết" thay vì chép sẵn True/False của tương lai.
            r_start = r.get("entered_current_hex_at") or seen
            if r_start <= t_now:
                reached_at = r.get("reached_target_at")
                if reached_at:
                    reached = r.get("reached_target") if reached_at <= t_now else None
                else:
                    reached = r.get("reached_target") if seen <= t_now else None
                repo = {"campaign_id": r.get("campaign_id"), "target_hex": r.get("target_hex"),
                        "reached": reached}

    total_s = sum(s["duration_seconds"] for s in segs)
    longest_s = max((s["duration_seconds"] for s in segs), default=0)

    # demand PROXY theo giờ (chỉ đơn ĐÃ phục vụ) — chuẩn hoá [0,1]
    # D-M3-11: cắt tại t_now. Prior này gộp mọi ngày trong bảng nên KHÔNG cắt sẽ để lọt cầu
    # của chính buổi chiều chưa diễn ra ⇒ hình dạng cầu "biết trước" đúng thứ S7 định khuyên.
    # Đo được ở seed 900: chuẩn hoá đỉnh dịch từ giờ 7 (1.0) xuống 0.859 khi thấy cả tương lai.
    by_hour: dict[int, int] = defaultdict(int)
    for t in _rows(l1r, "trips"):
        if str(t.get("request_time") or "") > t_now:
            continue
        by_hour[_hour(t["request_time"])] += 1
    peak = max(by_hour.values()) if by_hour else 0
    demand = {str(h): round(n / peak, 3) for h, n in sorted(by_hour.items())} if peak else {}

    onl = _online_row(l1r, driver_id, d)
    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "t_now": t_now, "session_date": d,
        "idle_segments": sorted(segs, key=lambda s: s["start"] or ""),
        "total_idle_min": round(total_s / 60, 2),
        "longest_idle_min": round(longest_s / 60, 2),
        "online_hours": float(onl["online_time"]) if onl else None,
        "demand_by_hour": demand,
        "active_reposition": repo,
        "view_version": VIEW_VERSION, "source": _provenance(onl),
    }


# ---------- S8: penalty_explain_input (UC6) ----------

def derive_penalty_explain_input_l1r(driver_id: str, t_now: str, l1r: dict,
                                     policy: PolicyBundle | None = None,
                                     days_back: int = 30,
                                     khoan_gap_vnd: int | None = None) -> dict:
    """Khoản trừ ĐO ĐƯỢC (`driver_penalization_ATA`) + tỷ lệ + ngưỡng policy.

    Guardrail: chỉ tập hợp DỮ LIỆU; giải thích/khuyến nghị do solver (không dạy lách).
    """
    today = _date(t_now)
    start = (_dt_date.fromisoformat(today) - timedelta(days=days_back)).isoformat()

    pens = []
    for r in _rows(l1r, "driver_penalization_ATA"):
        if r["driver_id"] != driver_id:
            continue
        d = r.get("local_date") or ""
        if not (start <= d <= today):
            continue
        pens.append({"penalization_id": r["penalization_id"], "local_date": d,
                     "penalty_type": r["penalty_type"], "amount_vnd": int(r["amount_vnd"]),
                     "reason": r.get("reason"), "status": r.get("status"),
                     "week_key": r.get("week_key")})

    stat = _stat_row(l1r, driver_id, today)
    if stat is None:  # lấy dòng đo gần nhất trong kỳ
        prior = sorted((s for s in _rows(l1r, "driver_statistic_daily")
                        if s["driver_id"] == driver_id and s["local_date"] <= today),
                       key=lambda s: s["local_date"])
        stat = prior[-1] if prior else None
    rates = {"acceptance": (stat or {}).get("acceptance_rate"),
             "fulfillment": (stat or {}).get("fulfillment_rate"),
             "cancellation": (stat or {}).get("cancellation_rate")}
    thresholds = {"min_acceptance": policy.bonus_min_acceptance if policy else None,
                  "min_completion": policy.bonus_min_completion if policy else None}

    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "t_now": t_now,
        "period_start": start, "period_end": today,
        "penalties": sorted(pens, key=lambda p: (p["local_date"] or "", p["penalization_id"])),
        "rates": rates, "thresholds": thresholds, "khoan_gap_vnd": khoan_gap_vnd,
        "view_version": VIEW_VERSION, "source": _provenance(stat, pens[0] if pens else None),
    }


# ---------- S9: anomaly_alert_input (UC7) ----------

def derive_anomaly_alert_input_l1r(driver_id: str, t_now: str, l1r: dict,
                                   days_back: int = 14) -> dict:
    """Cờ bất thường (`public_frauds`) — nhãn INFERRED.

    KHÔNG mang `evidence_ref` sang view (chống lộ cách phát hiện → chống dạy lách).
    """
    today = _date(t_now)
    start = (_dt_date.fromisoformat(today) - timedelta(days=days_back)).isoformat()
    flags = []
    for r in _rows(l1r, "public_frauds"):
        if r["driver_id"] != driver_id:
            continue
        det = (r.get("detected_at") or "")[:10]
        if det and not (start <= det <= today):
            continue
        flags.append({"fraud_id": r["fraud_id"], "detected_at": r.get("detected_at"),
                      "fraud_type": r["fraud_type"], "severity": r["severity"],
                      "confidence": float(r["confidence"]), "status": r["status"]})
    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "t_now": t_now,
        "flags": sorted(flags, key=lambda f: f["detected_at"] or ""),
        "view_version": VIEW_VERSION,
        "source": "INFERRED",  # cờ là SUY DIỄN của nền tảng, không phải sự kiện đã kết luận
    }


# ---------- S2: shift_plan_input ----------

def derive_shift_plan_input_l1r(driver_id: str, t_now: str, l1r: dict, policy: PolicyBundle,
                                bucket_min: int = 60, top_cells: int = 3) -> dict:
    """demand_forecast từ MẬT ĐỘ TRIPS THẬT; soc_pct=None (không có telemetry pin).

    Contract L3 (`shift_plan_input`): `buckets_remaining` = **SỐ bucket còn lại** (int);
    `demand_forecast` = [{bucket (ISO start), cell_cluster (H3), expected_orders}].
    `expected_orders` = trung bình cuốc/ngày trong (giờ × cell) tính từ `trips` thật —
    nhãn ESTIMATED-from-REAL (chỉ đếm đơn ĐÃ phục vụ, không có unserved).
    """
    today = _date(t_now)
    now_min = min_of_day(t_now)
    # D-M3-11: cắt tại t_now — CÙNG luật `derive_bonus_gap_input_l1r` đã áp từ AUDIT A3
    # LAYEROUT-4, deriver này bỏ sót. Đo được ở seed 900 lúc 08:00: points_now=35 (điểm của
    # cuốc chạy CHIỀU) thay vì 0 ⇒ S2 lập kế hoạch ca trên điểm chưa kiếm được.
    trips_today = [t for t in _driver_trips(l1r, driver_id, today) if t["complete_time"] <= t_now]
    points_now = _points_from_trips(trips_today, policy)

    # Dự báo NÓI về tương lai là đúng; DỮ LIỆU nuôi nó thì không được lấy từ tương lai.
    all_trips = [t for t in _rows(l1r, "trips") if str(t.get("request_time") or "") <= t_now]
    n_days = len({_date(t["request_time"]) for t in all_trips}) or 1
    by_hour_cell: dict[tuple[int, str], int] = defaultdict(int)
    for t in all_trips:
        by_hour_cell[(_hour(t["request_time"]), t.get("pickup_h3") or "unknown")] += 1

    starts = [s for s in range(now_min - now_min % bucket_min, 24 * 60, bucket_min)
              if s + bucket_min > now_min]
    forecast = []
    for s in starts:
        h = s // 60
        cells = sorted(((c, n) for (hh, c), n in by_hour_cell.items() if hh == h),
                       key=lambda x: (-x[1], x[0]))[:top_cells]
        for cell, n in cells:
            forecast.append({"bucket": f"{today}T{s // 60:02d}:{s % 60:02d}:00+07:00",
                             "cell_cluster": cell,
                             "expected_orders": round(n / n_days, 3)})

    stat = _stat_row(l1r, driver_id, today)
    return {
        "schema_version": "1.0.0", "driver_id": driver_id, "t_now": t_now,
        "buckets_remaining": len(starts),
        "soc_pct": None,  # TBC-với-GSM: 13 bảng thật không có telemetry pin
        "points_now": points_now,
        "demand_forecast": forecast,
        "policy_bundle_version": policy.version, "view_version": VIEW_VERSION,
        "source": _provenance(stat),
    }
