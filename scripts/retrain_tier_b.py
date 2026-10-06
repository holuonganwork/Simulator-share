#!/usr/bin/env python3
"""Pha 5 - retrain Tier B khi da co du nhan doc lap tu resolve_case.

Chan CO CHU DICH neu chua du du lieu, thay vi am tham huan luyen tren mau qua
nho roi bao cao mot mo hinh khong dang tin cay (xem docs/metrics.md). Khi da du
(>= MIN_CONFIRMED_PER_PATTERN case 'confirmed' cho it nhat 1 mau hinh), in
huong dan buoc tiep theo - viec thay IsolationForest bang mot mo hinh giam sat
that su la mot thay doi kien truc can duoc lam co chu dich, khong tu dong o day.
"""

from __future__ import annotations

from collections import Counter

from lcx_core.labels import all_resolutions

MIN_CONFIRMED_PER_PATTERN = 30


def main() -> None:
    resolutions = all_resolutions()
    confirmed_by_pattern = Counter(
        r.pattern for r in resolutions if r.verdict == "confirmed"
    )

    print("So case 'confirmed' theo mau hinh (nguon nhan cho retrain):")
    if not confirmed_by_pattern:
        print("  (chua co case nao)")
    for pattern, n in confirmed_by_pattern.most_common():
        print(f"  {pattern}: {n}")

    ready = [p for p, n in confirmed_by_pattern.items() if n >= MIN_CONFIRMED_PER_PATTERN]
    if not ready:
        print(
            f"\nChua co mau hinh nao dat >= {MIN_CONFIRMED_PER_PATTERN} case confirmed. "
            "Tier B tiep tuc dung IsolationForest khong giam sat "
            "(src/lcx_core/tier_b/risk_scorer.py) - KHONG retrain o buoc nay."
        )
        return

    print(
        f"\nDa du du lieu cho: {ready}. Buoc tiep theo (thu cong, ngoai script nay): "
        "thiet ke lai tier_b/risk_scorer.py thanh mo hinh giam sat "
        "(vd LogisticRegression/XGBoost) rieng cho tung mau hinh da du nhan, "
        "giu IsolationForest cho cac mau hinh con lai chua du nhan."
    )


if __name__ == "__main__":
    main()
