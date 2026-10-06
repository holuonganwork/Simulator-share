"""Hằng số chính sách KHOÁ — không sweep, không override (D-M3-08 cơ chế 1).

Ranh giới: `specs/advisor-objective-model-v2.md` §1.2b — *"sức khoẻ tài xế KHÔNG phải biến
để tối ưu"*. Mục đích cụ thể của khoá: không bao giờ tồn tại một bảng "hoãn lâu hơn = nhiều
tiền hơn" tạo áp lực nới trần (đúng kịch bản mà spec §7.4 của
`data-contract-counterfactual.md` gọi tên khi khai test này CHƯA TỒN TẠI).

Muốn đổi một giá trị ở đây: đó là quyết định NGUYÊN TẮC (Cường + spec §1.2b), KHÔNG phải
kết quả của một sweep. Đường đổi hợp lệ: sửa file này + `configs/pilot_hanoi.yaml` + spec,
có duyệt — không đổi qua override/sweep/CLI.

Luật chọn khoá (để lần sau không khoá theo cảm giác) — khoá khi và chỉ khi CẢ BA:
(i) là TRẦN/ngưỡng mà chức năng duy nhất là GIỚI HẠN lời khuyên;
(ii) nới nó làm headroom tiền của kênh tăng đơn điệu;
(iii) nó không có vai trò nào khác trong world.
Vì thế KHÔNG khoá: `rest_min_per_4h` (spec ĐÒI sweep được —
`test_objective_DOES_change_under_feasible_set_perturbation`), `fatigue_threshold_min`
(hardcode trong ARCHETYPES, không phải khoá config — entry sẽ chết giả),
`swap_soc_threshold_pct` (điều khiển cả hành vi đổi pin của world — khoá nó là khoá
calibration, vi phạm (iii)).
"""
from __future__ import annotations

import importlib
from typing import Any, Callable


class PolicyLockViolation(RuntimeError):
    """Một khoá chính sách sức khoẻ bị override. KHÔNG bắt exception này để chạy tiếp.

    Cố ý kế thừa RuntimeError chứ KHÔNG phải ValueError: đã kiểm, các `except ValueError`
    ở `lifecycle/event_log.py`, `schema_registry.py`, `solvers/anomaly_alert.py` sẽ nuốt
    mất nếu chọn ValueError.
    """


# (1) khoá theo KHOÁ CONFIG — dotted path trong Config của gsm_sim
POLICY_LOCKED_KEYS: dict[str, float] = {
    # trần HOÃN nghỉ (advice_bridge.should_defer_rest — lan can defer_cap)
    "advice.rest_defer_max_min": 120.0,
    # trần hoãn KẾT CA (cùng họ: kéo dài thời gian làm việc vì tiền)
    "advice.shift_extend_max_min": 60.0,
}

# (2) khoá theo HẰNG MODULE — sweep bằng monkeypatch, config không với tới.
# Khoá CẢ BA vì gate của idle_reduction là phép OR (`total >= 45 or longest >= 25`,
# cộng điều kiện thấp điểm) — khoá một cái thì bypass qua cái còn lại.
POLICY_LOCKED_CONSTS: dict[str, float] = {
    "gsm_core.solvers.idle_reduction.IDLE_TOTAL_ALERT_MIN": 45.0,
    "gsm_core.solvers.idle_reduction.IDLE_LONGEST_ALERT_MIN": 25.0,
    "gsm_core.solvers.idle_reduction.LOW_DEMAND_MAX": 0.5,
}


def is_locked(name: str) -> bool:
    """Khớp cả dotted path lẫn tên lá (spec §7.4 viết `"rest_defer_max_min"` trần)."""
    return any(name == k or k.rsplit(".", 1)[-1] == name
               for k in (*POLICY_LOCKED_KEYS, *POLICY_LOCKED_CONSTS))


def _eq(got: Any, canon: float) -> bool:
    try:
        return float(got) == float(canon)
    except (TypeError, ValueError):
        return False        # kiểu lạ = vi phạm tường minh, không phải crash mơ hồ


def assert_policy_locks(cfg_get: Callable[..., Any], *, where: str = "") -> None:
    """Nổ nếu bất kỳ khoá chính sách nào lệch giá trị chuẩn.

    `cfg_get` = `Config.get` (duck-typed `(dotted, default) -> Any`). Nhận callable chứ
    không nhận `Config` để `gsm_core` KHÔNG import `gsm_sim` (chiều import hiện hành là
    `gsm_sim -> gsm_core`).

    VẮNG MẶT = HỢP LỆ: `cfg_get(key, canon)` rơi về canonical — hàng chục fixture dựng
    Config tối giản không có block `advice` đầy đủ. Chỉ "CÓ MẶT và KHÁC" mới là vi phạm.
    """
    bad: list[tuple[str, float, Any]] = []
    for key, canon in POLICY_LOCKED_KEYS.items():
        got = cfg_get(key, canon)
        if not _eq(got, canon):
            bad.append((key, canon, got))
    for dotted, canon in POLICY_LOCKED_CONSTS.items():
        mod_path, attr = dotted.rsplit(".", 1)
        got = getattr(importlib.import_module(mod_path), attr)
        if not _eq(got, canon):
            bad.append((dotted, canon, got))
    if bad:
        lines = "\n".join(f"  {k}: chuẩn {c!r} — nhận {g!r}" for k, c, g in bad)
        raise PolicyLockViolation(
            f"KHOÁ CHÍNH SÁCH SỨC KHOẺ BỊ OVERRIDE ({where or 'unknown'}):\n{lines}\n"
            "Đây là lan can sức khoẻ (specs/advisor-objective-model-v2.md §1.2b), KHÔNG "
            "phải tham số hiệu chỉnh — sweep nó sẽ sinh ra bảng 'hoãn lâu hơn = nhiều tiền "
            "hơn'. Muốn đổi: sửa src/gsm_core/policy_locks.py + configs/pilot_hanoi.yaml "
            "+ spec §1.2b, có duyệt của Cường — không đổi qua override/sweep/CLI.")
