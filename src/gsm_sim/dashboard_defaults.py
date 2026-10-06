"""Mặc định slider của dashboard — ĐỌC TỪ CONFIG, không hardcode.

AUDIT A1 BEHAV-2 (UPDATE-065): dashboard từng hardcode default thời SIM-1
(center 6000 với slider max 12000, trong khi config đã recalibrate 21200; n=50 vs 90;
eta 8 vs 11; patience 3 vs 5) ⇒ MỌI run chỉnh-tham-số từ dashboard chạy kinh tế học cũ.
Module này không import streamlit để test được bằng pytest thuần.
"""

from __future__ import annotations

from gsm_sim.config import Config

# key config → (min, max) cho slider; range phải CHỨA giá trị hiệu chỉnh hiện hành
SLIDER_KEYS: dict[str, tuple[float, float]] = {
    "demand.orders_per_day": (500, 20000),
    "demand.trip_km_median": (2.0, 10.0),
    "demand.detour_factor": (1.0, 2.0),
    "actors.n": (20, 2000),
    "dispatcher.eta_max_min": (4.0, 20.0),
    "dispatcher.candidate_ring_k": (1, 8),
    "dispatcher.patience_median_min": (1.0, 15.0),
    "behavior.accept_logit_center_vnd": (5000, 50000),
    "behavior.pickup_disutility_vnd_per_km": (1000, 10000),
}


def slider_defaults(cfg: Config) -> dict[str, float]:
    """Giá trị mặc định cho từng slider = giá trị CONFIG hiện hành (đã hiệu chỉnh)."""
    out: dict[str, float] = {}
    for key, (lo, hi) in SLIDER_KEYS.items():
        v = cfg.get(key)
        if not (lo <= float(v) <= hi):
            raise ValueError(f"config {key}={v} nằm ngoài range slider [{lo}, {hi}] "
                             "— nới SLIDER_KEYS trước khi đổi config")
        out[key] = v
    return out
