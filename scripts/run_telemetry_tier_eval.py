#!/usr/bin/env python3
"""Do Precision/Recall cho cac mau hinh sinh boi lcx_core.event_sim: 4 mau cua Khoi 1
"Chien luoc lam giau du lieu" (camera/Mat than, online ao, doi pin, xe ma) va 5 mau
F1/F3/F4/F5/F6 cua Taxi Co Huu (off_app_pickup, tcu_signal_loss, personal_use,
fake_trip, invalid_driver_cancel - xem FRAUDS.md).

Khac voi scripts/run_injector_eval.py (lam giau du lieu THAT co san cua
GSM_SIMULATOR), o day KHONG co du lieu that de lam giau - toan bo quan the
(binh thuong + gian lan tiem co tham so) duoc SINH MOI boi
lcx_core.event_sim.simulator, roi chay THANG 4 detector cua
lcx_core.telemetry_tier (khong sua code detector).

Dung:
    ./.venv/bin/python3 scripts/run_telemetry_tier_eval.py
    ./.venv/bin/python3 scripts/run_telemetry_tier_eval.py --n-per-severity 20 --save
    ./.venv/bin/python3 scripts/run_telemetry_tier_eval.py --save-tables data/derived/event_sim
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from lcx_core.event_sim import PATTERNS, SEVERITIES, inject_all
from lcx_core.fraud_injector import flag_matches_label
from lcx_core.telemetry_tier import REGISTRY

VAR_DIR = Path(__file__).resolve().parents[1] / "var" / "telemetry_tier_eval"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-drivers", type=int, default=120)
    parser.add_argument("--n-days", type=int, default=14)
    parser.add_argument("--n-per-severity", type=int, default=15)
    parser.add_argument("--save", action="store_true")
    parser.add_argument(
        "--save-tables", type=Path, default=None,
        help="Ghi 7 bang (trips, telemetry, camera, charging, registry, events, declared_shifts) + ground_truth ra thu muc nay",
    )
    args = parser.parse_args()

    t0 = time.time()
    store, ground_truth = inject_all(
        n_drivers=args.n_drivers, n_days=args.n_days, seed=args.seed, n_per_severity=args.n_per_severity
    )
    print(store.describe())
    print(
        f"\nDa sinh {ground_truth.height} don vi gian lan co chu dich cho "
        f"{len(PATTERNS)} mau hinh ({time.time() - t0:.1f}s)\n"
    )
    if args.save_tables:
        counts = store.save(args.save_tables)
        ground_truth.write_parquet(args.save_tables / "ground_truth.parquet")
        print(f"Da ghi {len(counts)} bang + ground_truth vao {args.save_tables}\n")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = VAR_DIR / run_id
    if args.save:
        out_dir.mkdir(parents=True, exist_ok=True)
        ground_truth.write_parquet(out_dir / "ground_truth.parquet")

    report: dict[str, dict] = {}
    header = f"{'pattern':<26}{'severity':<10}{'n_injected':>11}{'tp':>6}{'fn':>6}{'recall':>9}"
    print(header)
    print("-" * len(header))

    for pattern in PATTERNS:
        t0 = time.time()
        result = REGISTRY[pattern].detect(store)
        elapsed = time.time() - t0
        gt_rows = ground_truth.filter(pl.col("pattern") == pattern).to_dicts()

        pattern_report: dict = {"n_flags_total": result.n_flagged, "by_severity": {}}
        for severity in SEVERITIES:
            gt_sev = [r for r in gt_rows if r["severity"] == severity]
            n_injected = len(gt_sev)
            tp = sum(
                1
                for r in gt_sev
                if any(
                    flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"])
                    for f in result.flags
                )
            )
            fn = n_injected - tp
            recall = tp / n_injected if n_injected else float("nan")
            pattern_report["by_severity"][severity] = {"n_injected": n_injected, "tp": tp, "fn": fn, "recall": recall}
            print(f"{pattern:<26}{severity:<10}{n_injected:>11}{tp:>6}{fn:>6}{recall:>9.1%}")

        tp_flags = sum(
            1
            for f in result.flags
            if any(flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"]) for r in gt_rows)
        )
        precision = tp_flags / result.n_flagged if result.n_flagged else float("nan")
        pattern_report["tp_flags"] = tp_flags
        pattern_report["precision_among_flags"] = precision
        print(
            f"{pattern:<26}{'(tong)':<10}{'flags=' + str(result.n_flagged):>11}"
            f"{tp_flags:>6}{'':>6}{precision:>9.1%}  precision_among_flags  ({elapsed:.1f}s)"
        )
        print()
        report[pattern] = pattern_report

    if args.save:
        with open(out_dir / "report.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"Da luu ket qua vao {out_dir}")


if __name__ == "__main__":
    main()
