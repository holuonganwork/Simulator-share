"""Nạp OVERLAY cấu hình cho phiên đóng vai — tầng 1 của việc tách HIL khỏi SIM.

## Vì sao tồn tại

`configs/pilot_hanoi.yaml` là **nền của mọi phép đo A/B**, và cổng vân tay
(`tests/test_hil_world_unchanged.py`) nạp đúng file đó, **thuần**, với `human_actor_id=None`.
Sửa nền vì một nhu cầu của demo ⇒ trôi vân tay ⇒ **mọi số A/B đã công bố mất giá trị so sánh**.

Nhưng demo **thật sự cần** vài tham số khác: trần cự ly hợp lý hơn, điểm trả khách bám cầu.
Trước cycle này, mâu thuẫn đó bị giải quyết bằng cách **không làm gì** — bốn việc mang nhãn
`BLOCKED` nhiều tuần vì một ràng buộc mà thật ra **không áp cho chúng**.

Module này là lối thoát có tên: ghi đè khoá **chỉ trong bộ nhớ, chỉ cho phiên HIL**.

## Vì sao là DANH SÁCH GHI ĐÈ, không phải file config con

Một file con là một **bản sao**, và bản sao thì trôi. Ngày nào đó có người sửa
`policy.day_bonus_tiers` ở nền, demo vẫn chạy bảng cũ và **nói sai chính sách với tài xế** —
không cổng nào bắn được, vì cả hai file đều "hợp lệ" xét riêng.

Danh sách ghi đè không trôi được: khoá nào không có trong đó thì **luôn** lấy từ nền.

## Ba luật, và luật đầu là quan trọng nhất

1. 🔴 **Khoá lạ thì NỔ.** Một khoá không tồn tại ở nền là lỗi gõ, và im lặng thêm nó vào là
   dựng một tham số mà **không code nào đọc** — đúng bẫy *"cấu hình quảng cáo một thứ, engine
   làm một thứ khác"* (`bẫy #12`). Đây là lý do hàm này kiểm `cfg.get(khoa)` trước khi ghi.
2. **Deep-copy.** Ghi đè tại chỗ là để phiên HIL sửa dict mà cổng vân tay đang dùng chung —
   cùng lý do `parallel._cfg_with` phải deep-copy.
3. **Overlay rỗng là hợp lệ** và là mặc định. Đường phải chạy được trước khi có khoá nào.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from ..config import Config

# Đường dẫn mặc định, tính từ `root_dir` của config nền.
OVERLAY_MAC_DINH = "configs/hil_demo.yaml"


def doc_overlay(duong_dan: str | Path) -> dict[str, Any]:
    """Đọc file overlay → dict khoá-chấm. File vắng ⇒ `{}` (chạy như không có overlay)."""
    p = Path(duong_dan)
    if not p.is_file():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    ghi_de = data.get("ghi_de")
    if ghi_de is None:
        return {}
    if not isinstance(ghi_de, dict):
        raise ValueError(
            f"{p}: khoá `ghi_de` phải là một map khoá-chấm → giá trị, đang là "
            f"{type(ghi_de).__name__}")
    return dict(ghi_de)


def _dat(data: dict[str, Any], khoa_cham: str, gia_tri: Any) -> None:
    """Ghi `gia_tri` vào đúng nhánh `a.b.c` của `data`. Nhánh phải tồn tại sẵn."""
    phan = khoa_cham.split(".")
    node: Any = data
    for p in phan[:-1]:
        node = node[p]
    node[phan[-1]] = gia_tri


# ══════════════════════════════════════════════════════════════════════════════════════════
# KHOÁ CHỈ CỦA HIL — danh sách KHAI TƯỜNG MINH, không phải cửa sau
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# Luật chung vẫn giữ: khoá không có ở nền ⇒ **NỔ**, vì đó là lỗi gõ. Danh sách này là ngoại lệ
# **được khai**, và mỗi khoá phải có lý do ngay bên cạnh.
#
# ## Vì sao cần ngoại lệ (đo 2026-08-21, việc `6.4`)
#
# `advice.km_weight` phải nằm ở nền thì overlay mới đặt được. Nhưng chỉ riêng việc **thêm một
# khoá vào mục `advice:` của `pilot_hanoi.yaml`** đã làm đỏ
# `test_postgres_http_checkpoint_chat_has_real_why_and_trace`:
#
#     có khoá   → advice KHÔNG BAO GIỜ `ready` trong 400 bước ⇒ ca chạy hết ⇒ HTTP 410
#     bỏ khoá   → xanh
#     đo 2 lần mỗi chiều: TẤT ĐỊNH
#
# 🔴 Tôi **chưa giải thích được cơ chế**. Nó không đi qua `solver_params` (dict dựng tường
# minh), không đi qua `_default_run` (cache theo seed), và giá trị là `0.0` nên không nhánh nào
# đổi. Xem `UNRESOLVED` ở `UPDATE-281` — theo `CLAUDE.md` §4b, chưa chứng minh root cause thì
# **không được ghi "đã sửa"**.
#
# Nên đường an toàn là **không đụng nền đo**: nền là gốc của mọi phép đo A/B, và một thay đổi
# có tác dụng phụ chưa hiểu thì không được nằm ở đó. Khoá sống ở overlay, `advice_bridge` đọc
# bằng `.get(..., 0.0)` nên nền vắng khoá là chuyện bình thường.
KHOA_CHI_CUA_HIL: frozenset[str] = frozenset({
    # `6.4` — trọng số cost/benefit cho quyết định đổi vị trí. Chỉ phiên đóng vai dùng; nền đo
    # phải KHÔNG có nó để `capacity_alloc` chạy bit-identical (400 ma trận, 0/400 đổi nghiệm).
    "advice.km_weight",
    # `6.4` — cổng cost/benefit thay lan can cự ly. Khoá RIÊNG với `km_weight` vì hai cần gạt
    # đi **ngược chiều nhau** (đo 5 seed: Hungarian giảm cự ly, cost/benefit tăng).
    "advice.cost_benefit_vi_tri",
})


def nap_overlay(cfg: Config, duong_dan: str | Path | None = None) -> Config:
    """Trả BẢN SAO của `cfg` đã áp overlay. `cfg` gốc không bị đụng.

    🔴 Khoá không tồn tại ở nền ⇒ **`KeyError`**, không im lặng thêm mới. Xem docstring module.
    """
    p = Path(duong_dan) if duong_dan is not None else (cfg.root_dir / OVERLAY_MAC_DINH)
    ghi_de = doc_overlay(p)
    if not ghi_de:
        return cfg
    moi = Config(copy.deepcopy(cfg.data), cfg.root_dir)
    for khoa, gia_tri in ghi_de.items():
        if khoa in KHOA_CHI_CUA_HIL:
            _dat(moi.data, khoa, gia_tri)
            continue
        try:
            cfg.get(khoa)
        except KeyError as exc:
            raise KeyError(
                f"overlay {p} khai khoá {khoa!r} nhưng nền không có khoá đó. "
                f"Đây là lỗi gõ — thêm nó vào nền TRƯỚC nếu thật sự cần một tham số mới. "
                f"Im lặng tạo khoá mới ở overlay là dựng một tham số không code nào đọc."
            ) from exc
        _dat(moi.data, khoa, gia_tri)
    return moi


def khoa_ghi_de(duong_dan: str | Path | None = None, root_dir: Path | None = None) -> set[str]:
    """Tập khoá mà overlay ghi đè — cho cổng canh đọc, không phải cho engine."""
    p = Path(duong_dan) if duong_dan is not None else (
        (root_dir or Path(".")) / OVERLAY_MAC_DINH)
    return set(doc_overlay(p))
