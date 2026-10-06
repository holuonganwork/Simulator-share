#!/usr/bin/env python3
"""Do Precision/Recall THAT cho artificial_surge_collusion - hang muc 8 "Chien
luoc lam giau du lieu" (tham so simulator: thoi tiet lien quan gia tien).

Xem canh bao gioi han du lieu trong docstring lcx_core/derived_data/fare_breakdown.py
(scenario that KHONG co mua, rain_mm la MO PHONG LAI de chung minh co che,
KHONG phai tai tao lich su that).

Dung:
    ./.venv/bin/python3 scripts/run_surge_collusion_eval.py
    ./.venv/bin/python3 scripts/run_surge_collusion_eval.py --n-per-severity 20 --save
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from lcx_core.data import load_data_store
from lcx_core.derived_data.fare_breakdown import SEVERITY_PARAMS, compute_fare_breakdown, inject_artificial_surge
from lcx_core.derived_tier import REGISTRY
from lcx_core.fraud_injector import flag_matches_label

VAR_DIR = Path(__file__).resolve().parents[1] / "var" / "surge_collusion_eval"
SEVERITIES = list(SEVERITY_PARAMS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-per-severity", type=int, default=15)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    store = load_data_store()
    t0 = time.time()
    baseline = compute_fare_breakdown(store, seed=args.seed)
    print(f"fare_breakdown that: {baseline.height} dong ({time.time() - t0:.1f}s)")

    injected_rows, labels = inject_artificial_surge(
        store, baseline, seed=args.seed + 1, n_per_severity=args.n_per_severity
    )
    fare_breakdown = pl.concat([baseline, injected_rows], how="vertical")
    ground_truth = pl.DataFrame(
        [label.to_dict() for label in labels],
        schema={"pattern": pl.Utf8, "driver_id": pl.Utf8, "severity": pl.Utf8, "match_field": pl.Utf8, "match_value": pl.Utf8},
    )
    print(f"Da tiem {ground_truth.height} don vi gian lan co chu dich\n")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = VAR_DIR / run_id
    if args.save:
        out_dir.mkdir(parents=True, exist_ok=True)
        ground_truth.write_parquet(out_dir / "ground_truth.parquet")

    pattern = "artificial_surge_collusion"
    result = REGISTRY[pattern].detect(fare_breakdown)
    gt_rows = ground_truth.to_dicts()

    header = f"{'severity':<10}{'n_injected':>11}{'tp':>6}{'fn':>6}{'recall':>9}"
    print(header)
    print("-" * len(header))
    report: dict = {"n_flags_total": result.n_flagged, "by_severity": {}}
    for severity in SEVERITIES:
        gt_sev = [r for r in gt_rows if r["severity"] == severity]
        n_injected = len(gt_sev)
        tp = sum(
            1
            for r in gt_sev
            if any(flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"]) for f in result.flags)
        )
        fn = n_injected - tp
        recall = tp / n_injected if n_injected else float("nan")
        report["by_severity"][severity] = {"n_injected": n_injected, "tp": tp, "fn": fn, "recall": recall}
        print(f"{severity:<10}{n_injected:>11}{tp:>6}{fn:>6}{recall:>9.1%}")

    tp_flags = sum(
        1
        for f in result.flags
        if any(flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"]) for r in gt_rows)
    )
    precision = tp_flags / result.n_flagged if result.n_flagged else float("nan")
    report["tp_flags"] = tp_flags
    report["precision_among_flags"] = precision
    print(f"\nflags={result.n_flagged}  tp_flags={tp_flags}  precision_among_flags={precision:.1%}")
    print(f"threshold={result.threshold}  n_evaluated={result.n_evaluated}")

    if args.save:
        with open(out_dir / "report.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\nDa luu ket qua vao {out_dir}")


if __name__ == "__main__":
    main()
