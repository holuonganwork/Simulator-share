#!/usr/bin/env python3
"""Chay toan bo Tier A (9 mau hinh trien khai duoc) + Tier B tren du lieu that.

Dung:
    ./.venv/bin/python3 scripts/run_tier_a_scan.py

In ra bang tom tat n_evaluated/n_flagged/threshold cho tung mau hinh, roi top 10
driver rui ro cao nhat theo Tier B. Cac mau hinh "thieu du lieu"
(fake_no_show, off_app_cash_pressure) duoc bao cao rieng, khong lam dung chuong trinh.
"""

from __future__ import annotations

import time

from lcx_core.data import load_data_store
from lcx_core.tier_a import IMPLEMENTED_PATTERNS, PENDING_DATA_PATTERNS, REGISTRY
from lcx_core.tier_b import score_all_drivers


def main() -> None:
    store = load_data_store()
    print(store.describe())
    print()

    print(f"{'pattern':<28}{'n_evaluated':>12}{'n_flagged':>11}{'flag_rate':>11}{'threshold':>14}")
    print("-" * 76)
    for pattern in IMPLEMENTED_PATTERNS:
        t0 = time.time()
        result = REGISTRY[pattern].detect(store)
        elapsed = time.time() - t0
        threshold_str = (
            f"{result.threshold:.4f}" if isinstance(result.threshold, float) else str(result.threshold)
        )
        print(
            f"{pattern:<28}{result.n_evaluated:>12}{result.n_flagged:>11}"
            f"{result.flag_rate:>11.4f}{threshold_str:>14}  ({elapsed:.1f}s)"
        )

    print()
    print("Mau hinh CHUA THE trien khai (thieu du lieu, xem docs/appendix-pending-data.md):")
    for pattern in PENDING_DATA_PATTERNS:
        try:
            REGISTRY[pattern].detect(store)
        except NotImplementedError as exc:
            print(f"  - {pattern}: {exc}")

    print()
    print("Top 10 driver rui ro cao nhat (Tier B - IsolationForest tren feature Tier A):")
    scores = score_all_drivers(store)
    for s in scores[:10]:
        print(f"  {s.driver_id:<10} risk={s.risk_score:.4f}  top_features={s.top_contributing_features}")


if __name__ == "__main__":
    main()
