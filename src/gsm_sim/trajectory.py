"""Trajectory & segment post-processing — biến event log + segments thô thành dữ liệu
visualize: đường đi (TripsLayer), timeline Gantt (theo cuốc/idle/hành vi), vòng đời khách,
và phát hiện flaw (hành vi chưa tối ưu — vd sạc vào khung điểm-thưởng cao).

Mọi số MOCK/illustrative — flaw là heuristic để minh hoạ dư địa advisor, KHÔNG phải phán
quyết chính sách. Số tài chính (payout) lấy từ segment (policy đã tính), không bịa thêm.

⚠⚠ **MODULE KHÔNG CÓ ĐƯỜNG CHẠY (`D-M3-15`, quét 2026-08-01).** `grep -rn trajectory src/
scripts/ ui/ tests/` = **0 kết quả** ngoài chính file này (một dòng trong
`tests/_health_boundary_manifest.py` chỉ khai `detect_flaws` là money-scope). `dashboard.py`
(696 dòng) **không import gì từ đây** — nó dùng `dashboard_theme.ACTIVITY_COLORS` và tự dựng
timeline. Nghĩa là:

- `build_paths`, `build_customer_events` chưa từng chạy ngoài file này (**0 test**);
- `detect_flaws` có 2 test nhưng **không đường chạy sản phẩm** — tức cơ chế "phát hiện hành
  vi chưa tối ưu" đang không phát hiện gì cả;
- 🔴 **`STATE_COLORS` dưới đây là BẢNG MÀU THỨ HAI** và nó **xung đột** với bảng dashboard
  đang dùng: `enroute` ở đây là **cam** `(255,165,0)` còn `dashboard_theme` là **xanh dương**
  `#3987e5`; `relocate` ở đây **vàng** `(255,205,86)` còn kia **hồng** `#d55181`.
  ⇒ Nối module này vào UI mà không thống nhất màu trước sẽ tạo ra hai cách đọc cùng một
  trạng thái — đúng loại lỗi mà `CLAUDE.md` §4b gọi là *"UI tự recompute khác engine"*.

**Chưa xoá** vì đây là 300 dòng có thể dùng lại cho lớp visual (quyết định giữ/xoá thuộc
Cường). Nhưng đừng đọc nó như tài liệu về hành vi hệ thống hiện hành: nó không mô tả gì đang
chạy. Trước khi nối lại: (1) thống nhất `STATE_COLORS` với `dashboard_theme`, (2) kiểm
`detect_flaws` không quy loss ra VND cho tài xế thấy (nó tính `loss_vnd` từ `rev_per_min` —
hợp lệ vì có nhãn illustrative, nhưng KHÔNG được lên UI như số tài chính).
"""

from __future__ import annotations

# màu theo trạng thái (RGB) — dùng chung Gantt + map
STATE_COLORS = {
    "enroute": (255, 165, 0),    # cam: đi đón (empty)
    "on_trip": (40, 167, 69),    # xanh lá: có khách (kiếm tiền)
    "relocate": (255, 205, 86),  # vàng: đổi vị trí/deadhead
    "charge": (0, 122, 255),     # xanh dương: sạc/đổi pin
    "rest": (150, 150, 150),     # xám: nghỉ
    "idle": (210, 210, 210),     # xám nhạt: chờ đơn tại chỗ
}


def build_paths(result) -> dict[int, list[list[float]]]:
    """{actor_id: [[lon, lat, t_min], ...]} cho pydeck TripsLayer (nội suy tuyến tính).

    Ghép endpoint các segment theo thời gian: mỗi segment cho (from@t0) và (to@t1).
    Khoảng idle giữa 2 segment → tài xế đứng yên (điểm giữ nguyên)."""
    by_actor: dict[int, list[tuple]] = {}
    for s in result.segments:
        by_actor.setdefault(s["actor_id"], []).append(s)
    paths: dict[int, list[list[float]]] = {}
    for aid, segs in by_actor.items():
        segs = sorted(segs, key=lambda s: s["t0"])
        pts: list[list[float]] = []
        for s in segs:
            p0 = [s["from_lon"], s["from_lat"], s["t0"]]
            p1 = [s["to_lon"], s["to_lat"], s["t1"]]
            if not pts or pts[-1][:2] != p0[:2] or pts[-1][2] != p0[2]:
                pts.append(p0)
            pts.append(p1)
        paths[aid] = pts
    return paths


def _actor_windows(result) -> dict[int, tuple[float, float]]:
    """(t_online, t_offline) mỗi actor từ event go_online / end_shift (fallback: run end)."""
    end_min = float(result.config.get("time.end_min"))
    online: dict[int, float] = {}
    offline: dict[int, float] = {}
    for e in result.events:
        if e.kind == "go_online":
            online.setdefault(e.actor_id, e.t_min)
        elif e.kind in ("end_shift", "day_end_settle"):
            offline[e.actor_id] = e.t_min
    return {aid: (online.get(aid, 0.0), offline.get(aid, end_min)) for aid in online}


def build_segments(result) -> list[dict]:
    """Segment hoạt động + CHÈN idle vào khoảng trống (chờ đơn tại chỗ). Sort theo (actor, t0)."""
    by_actor: dict[int, list[dict]] = {}
    for s in result.segments:
        by_actor.setdefault(s["actor_id"], []).append(dict(s))
    windows = _actor_windows(result)
    out: list[dict] = []
    for aid, segs in by_actor.items():
        segs = sorted(segs, key=lambda s: s["t0"])
        t_on, t_off = windows.get(aid, (segs[0]["t0"] if segs else 0.0,
                                        segs[-1]["t1"] if segs else 0.0))
        cursor = t_on
        last_pos = (segs[0]["from_lat"], segs[0]["from_lon"]) if segs else (0.0, 0.0)
        for s in segs:
            if s["t0"] - cursor > 0.5:  # >30s trống → idle (chờ đơn)
                out.append({"actor_id": aid, "t0": round(cursor, 3), "t1": round(s["t0"], 3),
                            "kind": "idle", "from_lat": last_pos[0], "from_lon": last_pos[1],
                            "to_lat": last_pos[0], "to_lon": last_pos[1]})
            out.append(s)
            cursor = s["t1"]
            last_pos = (s["to_lat"], s["to_lon"])
        if t_off - cursor > 0.5:
            out.append({"actor_id": aid, "t0": round(cursor, 3), "t1": round(t_off, 3),
                        "kind": "idle", "from_lat": last_pos[0], "from_lon": last_pos[1],
                        "to_lat": last_pos[0], "to_lon": last_pos[1]})
    out.sort(key=lambda s: (s["actor_id"], s["t0"]))
    return out


def build_customer_events(result) -> list[dict]:
    """Vòng đời khách/đơn: xuất hiện tại pickup(lat,lon) từ t_spawn tới khi được đón
    (served) hoặc hết kiên nhẫn (expired) hoặc còn treo cuối ngày (open)."""
    pickup_t: dict[int, float] = {}
    expired_t: dict[int, float] = {}
    for e in result.events:
        if e.kind == "pickup":
            oid = e.detail.get("order_id")
            if oid is not None and oid not in pickup_t:
                pickup_t[oid] = e.t_min
        elif e.kind == "order_expired":
            oid = e.detail.get("order_id")
            if oid is not None:
                expired_t[oid] = e.t_min
    out: list[dict] = []
    for o in result.orders:
        if o.order_id in pickup_t:
            status, t_end = "served", pickup_t[o.order_id]
        elif o.order_id in expired_t:
            status, t_end = "expired", expired_t[o.order_id]
        else:
            status, t_end = "open", o.t_min + o.patience_min
        out.append({"order_id": o.order_id, "lat": o.pickup_lat, "lon": o.pickup_lon,
                    "t_spawn": o.t_min, "t_end": t_end, "status": status,
                    "patience_min": o.patience_min})
    return out


def detect_flaws(result, segments: list[dict] | None = None) -> list[dict]:
    """Heuristic flaw (minh hoạ dư địa advisor). Trả [{actor_id, t, kind, desc, loss_vnd}].

    - charge_peak: sạc/đổi pin trùng khung giờ điểm-thưởng cao (bỏ lỡ điểm + cuốc).
    - rest_peak: nghỉ dài đúng giờ cao điểm nhu cầu.
    - long_idle_peak: idle quá lâu giờ cao điểm (đáng lẽ relocate sang vùng đông)."""
    segs = segments if segments is not None else build_segments(result)
    peak_hours = set(result.config.get("policy.point_peak_hours", []))
    hour_w = result.config.get("demand.hour_weights", {})
    hw = {int(h): float(w) for h, w in hour_w.items()}
    busy = {h for h, w in hw.items() if w >= 1.0}  # giờ cầu cao (weight ≥ 1)
    # ước lượng payout trung bình/phút có khách để quy đổi loss
    trip_segs = [s for s in segs if s["kind"] == "on_trip" and s["t1"] > s["t0"]]
    rev_per_min = (sum(s.get("payout", 0) for s in trip_segs)
                   / max(1.0, sum(s["t1"] - s["t0"] for s in trip_segs))) if trip_segs else 0.0

    flaws: list[dict] = []
    for s in segs:
        h0 = int(s["t0"] // 60) % 24
        dur = s["t1"] - s["t0"]
        if s["kind"] == "charge" and h0 in peak_hours:
            flaws.append({"actor_id": s["actor_id"], "t": s["t0"], "kind": "charge_peak",
                          "desc": f"Sạc/đổi pin lúc {h0:02d}h (khung điểm-thưởng cao) — mất {dur:.0f} phút giờ vàng",
                          "loss_vnd": int(dur * rev_per_min)})
        elif s["kind"] == "rest" and h0 in busy and dur >= 20:
            flaws.append({"actor_id": s["actor_id"], "t": s["t0"], "kind": "rest_peak",
                          "desc": f"Nghỉ {dur:.0f} phút lúc {h0:02d}h (giờ cầu cao)",
                          "loss_vnd": int(dur * rev_per_min)})
        elif s["kind"] == "idle" and h0 in busy and dur >= 25:
            flaws.append({"actor_id": s["actor_id"], "t": s["t0"], "kind": "long_idle_peak",
                          "desc": f"Chờ tại chỗ {dur:.0f} phút lúc {h0:02d}h (đáng lẽ chuyển vùng đông)",
                          "loss_vnd": int(dur * rev_per_min * 0.5)})
    flaws.sort(key=lambda f: (f["actor_id"], f["t"]))
    return flaws
