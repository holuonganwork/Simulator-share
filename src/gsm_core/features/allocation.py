"""Derive L3 `allocation_input` — input S4 CapacityAlloc (platform-level chống herding).

candidates = advice của nhiều driver (swap_window/standby_zone). station_capacity từ
station_registry throughput; zone_supply từ config threshold. Nhãn source MOCK.
"""

from __future__ import annotations

BUCKET_MIN = 30        # mặc định lịch sử — giữ để caller cũ không đổi hành vi


def derive_allocation_input(t_now: str, driver_reports: list[dict],
                            stations: list[dict], zones: list[dict],
                            params: dict | None = None,
                            bucket_min: int = BUCKET_MIN,
                            tier_weight: float = 0.0,
                            km_weight: float = 0.0) -> dict:
    """Dựng `allocation_input` cho S4.

    `bucket_min` là **độ dài bucket THẬT của caller**, tính bằng phút. Trước 2026-07-28 hàm này
    dùng cứng hằng `BUCKET_MIN = 30`, nên bất kỳ ai chạy nhịp khác 30′ đều nhận trần trạm sai mà
    **không có tín hiệu nào** (b0-C): batch tick 60′ sẽ chỉ thấy **một nửa** sức chứa, rồi số
    `unassigned` cao sẽ bị đọc nhầm thành "chống dồn cục hiệu quả".

    Lưu ý ĐƠN VỊ — hai loại trần khác nhau, đừng gộp:
      * **trạm** = thông lượng (cuốc đổi pin/giờ) ⇒ **có** nhân theo độ dài bucket;
      * **zone** = số tài xế đứng cùng lúc ⇒ **không** nhân theo thời gian.
    """
    if int(bucket_min) <= 0:
        raise ValueError("bucket_min phải > 0")
    p = {"zone_capacity_default": 5, **(params or {})}
    bucket = t_now

    # Cycle 8: `tier_gap_points` chỉ được đưa vào khi producer THẬT SỰ có nó. Không stamp `None`
    # cho mọi ứng viên: schema cho phép null, nhưng "vắng mặt" và "null" đọc như nhau ở solver mà
    # KHÁC nhau ở artifact — vắng mặt nói "producer này không biết khái niệm đó", null nói
    # "biết nhưng không tính được". Giữ phân biệt đó cho người đọc checkpoint sau này.
    candidates = []
    for r in driver_reports:
        c = {"driver_id": r["driver_id"], "advice_kind": r["advice_kind"],
             "target": r["target"], "priority_soc": r.get("priority_soc")}
        if "tier_gap_points" in r:
            c["tier_gap_points"] = r["tier_gap_points"]
        # `6.4` — cự ly tới **TỪNG Ô ĐÍCH** (`{ô: km}`), không phải một số tới ô ưu tiên. Một
        # số vô hướng mỗi tài xế vào ma trận Hungarian thành hằng theo HÀNG ⇒ không đổi được
        # nghiệm (đo 400 ma trận: cự ly đứng yên ở mọi trọng số). Cùng luật "vắng mặt ≠ null"
        # với `tier_gap_points`: producer không biết cự ly thì KHÔNG stamp trường.
        if "km_to_zones" in r:
            c["km_to_zones"] = dict(r["km_to_zones"])
        candidates.append(c)

    # station capacity = throughput danh định × độ dài bucket (giờ); default nếu null
    station_cap = []
    for s in stations:
        thr = s.get("throughput_nominal_per_hour")
        cap = int(round((thr if thr else 6.0) * (int(bucket_min) / 60.0)))
        station_cap.append({"station_id": s["station_id"], "bucket": bucket,
                            "capacity": max(1, cap)})

    zone_cap = [{"zone": z["zone"], "bucket": bucket,
                 "capacity": int(z.get("capacity", p["zone_capacity_default"]))}
                for z in zones]

    out = {
        "schema_version": "1.1.0", "t_now": t_now, "candidates": candidates,
        "station_capacity": station_cap, "zone_supply": zone_cap,
        "view_version": "1.0.0", "source": "MOCK",
    }
    # Chỉ ghi khi BẬT: ở mặc định (0.0) artifact giữ nguyên hình dạng cũ ngoài `schema_version`,
    # nên diff của một run mặc định vẫn đọc được bằng mắt.
    if float(tier_weight or 0.0) > 0.0:
        out["tier_weight"] = float(tier_weight)
    # `6.4` — cùng luật: mặc định 0 ⇒ artifact giữ nguyên hình dạng cũ, diff đọc được bằng mắt.
    if float(km_weight or 0.0) > 0.0:
        out["km_weight"] = float(km_weight)
    return out
