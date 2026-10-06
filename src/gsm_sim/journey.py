"""SIM-2 — HÀNH TRÌNH của MỘT tài xế (`DriverJourney`).

Yêu cầu Cường (`tracking/DIRECTIVES-2026-07-24.md` §5.6, spec `00-sim-overhaul-master.md` §5):
theo dõi **mở đầu phiên làm việc**, **nhận cuốc thật**, **tỷ lệ nhận/hoàn thành**,
**hành vi random**, và **đo metric trên đúng driver đó**.

Module này **thuần LẮP RÁP** — không mô phỏng lại gì, không đụng engine. Nguồn sự thật vẫn là
`RunResult` (events + segments + actor counters). Nhờ vậy journey **không thể lệch** với sim:
nếu engine đổi, journey đổi theo.

Ba khái niệm:
  - **session**: `go_online` → `end_shift` (hoặc hết ngày nếu còn online).
  - **timeline**: mọi phút trong phiên đều có nhãn. Segment cho hoạt động; khoảng trống giữa
    hai segment là **idle** (chờ đơn tại chỗ) — sim không ghi segment cho idle nên phải suy ra.
  - **offer**: mỗi lần tài xế ĐƯỢC CHÀO một đơn, kèm quyết định + **lý do** + số liệu.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Sự kiện đánh dấu 1 lần được chào đơn → quyết định tương ứng.
# `order_matched` = nhận; 3 cái còn lại = không thành cuốc.
OFFER_EVENTS = {
    "order_matched": "accept",
    "order_declined": "decline",
    "order_skipped_soc": "skip_soc",
}
# huỷ sau khi đã nhận — không phải một offer mới, mà là KẾT CỤC của offer đã accept
CANCEL_EVENT = "order_cancelled_after_accept"


@dataclass
class Offer:
    t_min: float
    order_id: int
    decision: str            # accept | decline | skip_soc
    reason: str              # accepted | economics | base_behavior | forced | soc_insufficient
    gross_vnd: int | None = None
    net_vnd: float | None = None
    pickup_km: float | None = None
    p_accept: float | None = None
    outcome: str | None = None   # với accept: completed | cancelled_after_accept | censored


@dataclass
class TimelineBlock:
    t0: float
    t1: float
    kind: str                # enroute | on_trip | rest | charge | relocate | ... | idle
    order_id: int | None = None
    payout_vnd: int | None = None
    dist_km: float | None = None

    @property
    def minutes(self) -> float:
        return self.t1 - self.t0


@dataclass
class DriverJourney:
    driver_id: str
    actor_id: int
    archetype: str
    fleet: str
    seed: int
    sessions: list[tuple[float, float]] = field(default_factory=list)
    timeline: list[TimelineBlock] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    income_curve: list[tuple[float, int]] = field(default_factory=list)  # (t_min, payout cộng dồn)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "driver_id": self.driver_id, "actor_id": self.actor_id,
            "archetype": self.archetype, "fleet": self.fleet, "seed": self.seed,
            "source": "MOCK",
            "sessions": [{"start_min": a, "end_min": b} for a, b in self.sessions],
            "timeline": [asdict(b) for b in self.timeline],
            "offers": [asdict(o) for o in self.offers],
            "income_curve": [{"t_min": t, "payout_vnd": v} for t, v in self.income_curve],
            "metrics": self.metrics,
        }


def _sessions_of(events, actor_id: int, end_min: float) -> list[tuple[float, float]]:
    """`go_online` → `end_shift`. Còn online lúc hết ngày thì đóng phiên tại `end_min`
    (censor) — KHÔNG bỏ phiên, nếu bỏ thì thời gian cuối ngày biến mất khỏi mọi metric."""
    out: list[tuple[float, float]] = []
    start: float | None = None
    for e in events:
        if e.actor_id != actor_id:
            continue
        if e.kind == "go_online":
            if start is None:
                start = e.t_min
        elif e.kind == "end_shift" and start is not None:
            out.append((start, e.t_min))
            start = None
    if start is not None:
        out.append((start, end_min))
    return out


def _timeline_of(segments, actor_id: int, sessions) -> list[TimelineBlock]:
    """Segment của actor + idle suy ra từ khoảng trống TRONG phiên.

    Chỉ điền idle bên trong phiên: ngoài phiên tài xế offline, không phải "đang chờ đơn".
    """
    segs = sorted((s for s in segments if s["actor_id"] == actor_id), key=lambda s: s["t0"])
    blocks: list[TimelineBlock] = []
    for a, b in sessions:
        cursor = a
        for s in segs:
            if s["t1"] <= a or s["t0"] >= b:
                continue
            t0, t1 = max(s["t0"], a), min(s["t1"], b)
            if t0 - cursor > 1e-6:
                blocks.append(TimelineBlock(round(cursor, 3), round(t0, 3), "idle"))
            blocks.append(TimelineBlock(
                round(t0, 3), round(t1, 3), s["kind"],
                order_id=s.get("order_id"), payout_vnd=s.get("payout"),
                dist_km=s.get("dist_km")))
            cursor = max(cursor, t1)
        if b - cursor > 1e-6:
            blocks.append(TimelineBlock(round(cursor, 3), round(b, 3), "idle"))
    return blocks


def _offers_of(events, actor_id: int) -> list[Offer]:
    """Mọi lần được chào đơn + KẾT CỤC của các đơn đã nhận."""
    offers: list[Offer] = []
    by_order: dict[int, Offer] = {}
    for e in events:
        if e.actor_id != actor_id:
            continue
        if e.kind in OFFER_EVENTS:
            d = e.detail
            reason = d.get("reason") or ("soc_insufficient" if e.kind == "order_skipped_soc"
                                         else "unknown")
            o = Offer(
                t_min=e.t_min, order_id=d.get("order_id"), decision=OFFER_EVENTS[e.kind],
                reason=reason, gross_vnd=d.get("gross_vnd"), net_vnd=d.get("net_vnd"),
                pickup_km=d.get("pickup_km"), p_accept=d.get("p_accept"),
            )
            if o.decision == "accept":
                o.outcome = "censored"       # mặc định; ghi đè khi thấy dropoff/cancel
                by_order[o.order_id] = o
            offers.append(o)
        elif e.kind == "dropoff" and e.detail.get("order_id") in by_order:
            by_order[e.detail["order_id"]].outcome = "completed"
        elif e.kind == CANCEL_EVENT and e.detail.get("order_id") in by_order:
            by_order[e.detail["order_id"]].outcome = "cancelled_after_accept"
    return offers


def income_entries(timeline, events, actor_id: int) -> list[tuple[float, int, str]]:
    """Sổ cái tiền của MỘT tài xế: `(t_min, amount_vnd, source)` đã sort theo thời gian.

    Tách khỏi `_income_curve` để thứ "tiền đã settle tới phút t" có MỘT nguồn duy nhất.
    Trước đây chỉ có đường cong cộng dồn (mất nguồn) và tổng cuối ca (mất thời điểm) —
    hai thứ đều không trả lời được câu hỏi giữa ca, nên chỗ nào cần cũng phải chép lại
    vòng lặp này. Bốn nguồn giữ nguyên như cũ; hàm này không đổi hành vi.
    """
    money: list[tuple[float, int, str]] = []
    for b in timeline:
        if b.kind == "on_trip" and b.payout_vnd:
            money.append((b.t1, int(b.payout_vnd), "trip"))
    for e in events:
        if e.actor_id != actor_id:
            continue
        if e.kind == "mission_completed":
            money.append((e.t_min, int(e.detail.get("reward_vnd", 0) or 0), "mission"))
        elif e.kind in ("newbie_guarantee_topup", "newbie_week1_bonus"):
            amt = int(e.detail.get("topup_vnd", 0) or e.detail.get("bonus_vnd", 0) or 0)
            money.append((e.t_min, amt, "newbie"))
        elif e.kind in ("end_shift", "day_end_settle"):
            amt = int(e.detail.get("day_bonus", 0) or 0)
            if amt:
                money.append((e.t_min, amt, "day_bonus"))
    money.sort(key=lambda x: x[0])
    return money


def _income_curve(timeline, events, actor_id: int) -> tuple[list[tuple[float, int]], dict]:
    """Payout cộng dồn. Trả `(curve, breakdown)` với breakdown tách theo nguồn.

    SIM-XANH P2: payout có **BỐN nguồn** — không được bỏ nguồn nào (BUG-SIM2-01 từng thiếu
    thưởng ngày và bị test bảo toàn tiền bắt):
      1. payout từng cuốc (tại thời điểm TRẢ KHÁCH);
      2. thưởng ngày (`end_shift`/`day_end_settle`);
      3. thưởng mission (`mission_completed`, tại lúc chạm mốc);
      4. tân binh (`newbie_guarantee_topup`/`newbie_week1_bonus`, tại settle).

    Cách dựng: gom MỌI sự kiện tiền thành (t, amount), sort theo t, rồi cộng dồn —
    đơn giản và không thể sai thứ tự.
    """
    money = income_entries(timeline, events, actor_id)

    curve: list[tuple[float, int]] = [(0.0, 0)]
    breakdown = {"trip": 0, "day_bonus": 0, "mission": 0, "newbie": 0}
    total = 0
    for t, amt, src in money:
        total += amt
        breakdown[src] += amt
        curve.append((t, total))
    return curve, breakdown


def build_journey(result, actor_id: int) -> DriverJourney:
    """Lắp ráp hành trình của 1 tài xế từ `RunResult`. Deterministic (không dùng RNG)."""
    actor = next((a for a in result.actors if a.actor_id == actor_id), None)
    if actor is None:
        raise ValueError(f"không có actor {actor_id} trong run")
    end_min = float(result.config.get("time.end_min"))

    sessions = _sessions_of(result.events, actor_id, end_min)
    timeline = _timeline_of(result.segments, actor_id, sessions)
    offers = _offers_of(result.events, actor_id)

    online_min = sum(b - a for a, b in sessions)
    by_kind: dict[str, float] = {}
    for b in timeline:
        by_kind[b.kind] = by_kind.get(b.kind, 0.0) + b.minutes
    curve, money = _income_curve(timeline, result.events, actor_id)
    occupied = by_kind.get("on_trip", 0.0)
    n_offer = len(offers)
    n_accept = sum(1 for o in offers if o.decision == "accept")
    n_skip_soc = sum(1 for o in offers if o.decision == "skip_soc")
    # Cycle 1: mẫu số của tỷ lệ nhận là các lượt tài xế THẬT SỰ được hỏi. File này vốn đã BÁO
    # CÁO RIÊNG `skipped_soc` ở dưới mà vẫn để nó trong mẫu số — cùng một mâu thuẫn với
    # `entities.acceptance_rate`, nay dùng chung một định nghĩa (`Actor.orders_decided`).
    n_decided = max(0, n_offer - n_skip_soc)
    n_done = sum(1 for o in offers if o.outcome == "completed")
    n_cancel = sum(1 for o in offers if o.outcome == "cancelled_after_accept")

    metrics = {
        "online_min": round(online_min, 1),
        "occupied_min": round(occupied, 1),
        "idle_min": round(by_kind.get("idle", 0.0), 1),
        "utilization": round(occupied / online_min, 4) if online_min else 0.0,
        "minutes_by_kind": {k: round(v, 1) for k, v in sorted(by_kind.items())},
        "offers": n_offer,
        "accepted": n_accept,
        "declined": sum(1 for o in offers if o.decision == "decline"),
        "skipped_soc": n_skip_soc,
        "decided": n_decided,
        "trips_completed": n_done,
        "cancelled_after_accept": n_cancel,
        "acceptance_rate": round(n_accept / n_decided, 4) if n_decided else None,
        "completion_rate": round(n_done / n_accept, 4) if n_accept else None,
        "payout_vnd": actor.payout_vnd,
        # SIM-XANH P2: tách 4 nguồn tiền — advisor tối ưu chủ yếu ở phần NGOÀI cuốc
        "trip_payout_vnd": money["trip"],
        "day_bonus_vnd": money["day_bonus"],
        "mission_reward_vnd": money["mission"],
        "newbie_vnd": money["newbie"],
        "bonus_share": round((actor.payout_vnd - money["trip"]) / actor.payout_vnd, 4)
                       if actor.payout_vnd else 0.0,
        "gross_vnd": actor.gross_vnd,
        "points": actor.points,
        "soc_end_pct": round(actor.soc_pct, 1),
        # vì sao bị từ chối — trả lời trực tiếp câu hỏi "tài xế này kén vì tiền hay vì tính?"
        "decline_reasons": {
            r: sum(1 for o in offers if o.decision == "decline" and o.reason == r)
            for r in ("economics", "base_behavior")
        },
    }

    return DriverJourney(
        driver_id=f"d-{actor_id}", actor_id=actor_id, archetype=actor.archetype,
        fleet=actor.fleet.value, seed=result.seed, sessions=sessions, timeline=timeline,
        offers=offers, income_curve=curve, metrics=metrics,
    )


def journey_to_json(result, actor_id: int, out_dir: Path) -> Path:
    """Xuất journey ra JSON có nhãn MOCK (SIM-4 dùng lại để so sánh A/B)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    j = build_journey(result, actor_id)
    payload = j.to_dict()
    payload["manifest"] = {
        "label": "MOCK",
        "generator": "gsm_sim.journey.build_journey (SIM-2)",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "seed": result.seed,
        "spec_version": result.config.get("meta.spec_version", "sim-core-slice-v0"),
        "policy_bundle_version": result.policy.version,
    }
    path = out_dir / f"journey_seed{result.seed}_actor{actor_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
