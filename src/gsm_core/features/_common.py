"""Shared helpers cho L3 derivation (bonus_gap, shift_plan)."""

from __future__ import annotations

from gsm_core.policy import PolicyBundle


def hour(iso: str) -> int:
    return int(iso[11:13])


def minute(iso: str) -> int:
    return int(iso[14:16])


def date(iso: str) -> str:
    return iso[:10]


def min_of_day(iso: str) -> int:
    return hour(iso) * 60 + minute(iso)


def points_on_date(trips: list[dict], driver: str, d: str, policy: PolicyBundle) -> int:
    """Σ trip_points các cuốc của driver trong ngày d (điểm suy từ trip + policy)."""
    return sum(policy.trip_points(hour(t["t_request"]))
               for t in trips if t["driver_id"] == driver and date(t["t_request"]) == d)


def online_minutes_on_date(events: list[dict], driver: str, d: str) -> float:
    """Phút online ≈ từ event sớm nhất tới muộn nhất trong ngày (xấp xỉ observable)."""
    ts = sorted(e["t"] for e in events
                if e["driver_id"] == driver and date(e["t"]) == d)
    if len(ts) < 2:
        return 0.0
    return float(min_of_day(ts[-1]) - min_of_day(ts[0]))


def online_intervals_on_date(events: list[dict], driver: str, d: str) -> list[tuple[float, float]]:
    """Các KHOẢNG online trong ngày `d`, dạng `[(phút_bắt_đầu, phút_kết_thúc)]`.

    `D-ADV-04`: cần khoảng (không chỉ TỔNG phút) để tính được giờ online **TRONG từng bucket**.
    `go_online` mở, `go_offline`/`set_offline_after_trip` đóng; khoảng chưa đóng ⇒ đóng tại event
    CUỐI của ngày — đúng giả định ngầm mà `online_minutes_on_date` đang dùng (`last − first`).

    Không có event `go_online` nào ⇒ trả **span** `[first, last]`, tức **giữ nguyên** hành vi của
    `online_minutes_on_date` về tổng thời lượng. Rỗng ⇒ caller rơi về đường ESTIMATED (một code path,
    không hai).
    """
    evs = sorted((min_of_day(e["t"]), str(e.get("kind", "")))
                 for e in events if e["driver_id"] == driver and date(e["t"]) == d)
    if not evs:
        return []
    out: list[tuple[float, float]] = []
    open_at: float | None = None
    for t, kind in evs:
        if kind == "go_online":
            if open_at is None:
                open_at = t
        elif kind in ("go_offline", "set_offline_after_trip"):
            if open_at is not None and t > open_at:
                out.append((open_at, t))
            open_at = None
    if open_at is not None and evs[-1][0] > open_at:
        out.append((open_at, evs[-1][0]))
    if out:
        return out
    return [(evs[0][0], evs[-1][0])] if evs[-1][0] > evs[0][0] else []
