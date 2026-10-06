"""5 archetype templates → sample N actors có jitter, deterministic theo seed.

Nguồn: planning/PERSONAS.md, advisor-optimization-layer-a.md §1 (σ_arch, ngưỡng mệt,
acceptance), sim-policy-bundle-v0.md §6 (đội xe).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config
from .entities import Actor, FleetType
from .geo import Grid


@dataclass(frozen=True)
class Archetype:
    name: str
    demand_prior_sigma: float   # sai số kinh nghiệm cá nhân (P4 cao, P3 thấp)
    accept_base: float
    fatigue_threshold_min: float
    shift_len_min: tuple[float, float]  # dải giờ chạy (min,max) theo phút
    shift_start_min: tuple[float, float]
    meal_hour: int
    fleet: str  # "swap" | "charge" | "mixed"


# Từ specs: σ P4 0.6 · P1 0.4 · P2 0.3 · P5 0.15 · P3 0.1; accept P3 .98/P5 .97/P2 .95/P1 .85/P4 .80
# shift_start_min / shift_len_min: hiệu chỉnh để CUNG bám CẦU (T-021, UPDATE-021). Khung cầu:
#   sáng 7-8h, TỐI đỉnh 17-19h, đuôi đêm 20-23h. Trước đây hầu hết ca kết thúc 14-19h → sụp
#   cung tối/đêm (0% phục vụ 21-23h). Căn lại theo persona:
#   P1 part-time TỐI (17-21h) · P2 full-time kéo dài phủ 2 đỉnh · P3 top 10-11h phủ chiều-tối
#   P4 tân binh CỐ Ý lệch khung (giữ làm dư địa advisor) · P5 lão làng thuộc khung vàng → phủ tối-đêm.
ARCHETYPES: dict[str, Archetype] = {
    "P1": Archetype("P1", 0.40, 0.85, 5 * 60, (3 * 60, 4 * 60), (16 * 60 + 30, 18 * 60), 19, "charge"),
    "P2": Archetype("P2", 0.30, 0.95, 10 * 60, (10 * 60, 11 * 60), (7 * 60, 9 * 60), 12, "swap"),
    "P3": Archetype("P3", 0.10, 0.98, 12 * 60, (11 * 60, 12 * 60), (9 * 60, 10 * 60), 13, "mixed"),
    "P4": Archetype("P4", 0.60, 0.80, 8 * 60, (8 * 60, 9 * 60), (6 * 60, 8 * 60), 11, "swap"),
    "P5": Archetype("P5", 0.15, 0.97, 9 * 60, (8 * 60, 9 * 60), (15 * 60, 17 * 60), 18, "mixed"),
    # --- SIM-1: 2 archetype PHỦ KHUNG TRỐNG (chứng minh bằng đo: 05-06h có 0 tài xế,
    # 21-23h chỉ 8-10 tài xế trong khi cầu 168 đơn) ---
    # P6 "ca sáng sớm": bắt đầu 5:00-6:30 để phủ khung vàng sáng 6-8h (10 điểm/cuốc,
    #    research bonus-programs). Kinh nghiệm khá → accept cao.
    #    Khung bắt đầu 4:40-5:50 (KHÔNG phải 5:00-6:30): với 5:00-6:30 thì lúc 05h mới có
    #    ~1-2 tài xế online ⇒ gate `test_no_dead_hour` đo được 49% đơn hết hạn lúc 05h.
    #    Blog official nói ca đêm phủ "4h-6h" ⇒ bắt đầu trước 5h là CÓ CĂN CỨ, không phải
    #    vặn số. Sau khi sửa: 05h còn ~30%.
    "P6": Archetype("P6", 0.25, 0.93, 7 * 60, (7 * 60, 9 * 60), (4 * 60 + 40, 5 * 60 + 50), 11, "swap"),
    # P7 "ca tối-đêm": 15-16h → 23-24h. Căn cứ: blog official "kinh nghiệm chạy ca đêm"
    #    (khung 22h-2h & 4h-6h; sân bay/bến xe/phố đi bộ/bệnh viện 24h).
    "P7": Archetype("P7", 0.20, 0.94, 9 * 60, (8 * 60, 9 * 60), (15 * 60, 16 * 60), 18, "swap"),
}


def _pick_fleet(spec: str, ratio_mixed_charge: float, rng) -> FleetType:
    if spec == "swap":
        return FleetType.SWAP
    if spec == "charge":
        return FleetType.CHARGE
    # mixed
    return FleetType.CHARGE if rng.random() < ratio_mixed_charge else FleetType.SWAP


def sample_actors(grid: Grid, cfg: Config, seed: int) -> list[Actor]:
    """Sample N actors theo archetype_mix, jitter deterministic theo seed."""
    rng = np.random.default_rng(seed ^ 0xA11CE)  # stream riêng cho actor sampling
    n = int(cfg.get("actors.n"))
    mix = cfg.get("actors.archetype_mix")
    ratio = float(cfg.get("actors.charge_fleet_ratio_p3p5"))
    start_min = int(cfg.get("time.start_min"))

    # phân bổ số actor theo tỷ trọng (làm tròn ổn định)
    names = sorted(mix.keys())
    counts = {name: int(round(n * float(mix[name]))) for name in names}
    # điều chỉnh cho đủ tổng n
    diff = n - sum(counts.values())
    for name in names:
        if diff == 0:
            break
        counts[name] += 1 if diff > 0 else -1
        diff += -1 if diff > 0 else 1

    ARCHETYPE_VEHICLES = {
        "P1": ("A_COMPACT", "VF5", 4),
        "P2": ("B_SEDAN", "VF6", 5),
        "P3": ("D_LUXURY", "VF8", 5),
        "P4": ("A_COMPACT", "VF5", 4),
        "P5": ("C_SUV", "VF_e34", 5),
        "P6": ("A_COMPACT", "VF5", 4),
        "P7": ("D_LUXURY", "VF8", 5),
    }

    core = grid.core_cells
    actors: list[Actor] = []
    aid = 0
    for name in names:
        arc = ARCHETYPES[name]
        v_class, v_model, seats = ARCHETYPE_VEHICLES.get(name, ("A_COMPACT", "VF5", 4))
        for _ in range(counts[name]):
            shift_len = rng.uniform(*arc.shift_len_min)
            shift_start = max(start_min, rng.uniform(*arc.shift_start_min))
            shift_end = min(24 * 60, shift_start + shift_len)
            home = core[rng.integers(len(core))]
            actors.append(
                Actor(
                    actor_id=aid,
                    archetype=name,
                    fleet=_pick_fleet(arc.fleet, ratio, rng),
                    home_cell=home,
                    shift_start_min=float(shift_start),
                    shift_end_min=float(shift_end),
                    demand_prior_sigma=arc.demand_prior_sigma,
                    accept_base=arc.accept_base,
                    fatigue_threshold_min=arc.fatigue_threshold_min,
                    meal_hour=arc.meal_hour,
                    vehicle_class=v_class,
                    vehicle_model=v_model,
                    seat_capacity=seats,
                    cell=home,
                    soc_pct=float(rng.uniform(70, 100)),
                    # SIM-XANH P2: tenure theo archetype — P4 là TÂN BINH THẬT (<60 ngày,
                    # nằm trong cửa sổ newbie_program 90 ngày); còn lại là tài xế cũ.
                    # RNG DẪN XUẤT riêng theo (seed, actor) — không tiêu stream sampling
                    # chính ⇒ không làm trôi home/shift đã hiệu chỉnh.
                    tenure_days=int(np.random.default_rng((seed, 0x7E4E, aid)).integers(
                        5, 60) if name == "P4" else
                        np.random.default_rng((seed, 0x7E4E, aid)).integers(90, 720)),
                )
            )
            aid += 1
    return actors
