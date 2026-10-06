#!/usr/bin/env python3
"""Do Precision/Recall THAT cho 7 mau hinh Tier A da co luat - Pha A cua
"Chien luoc lam giau du lieu" (muc 7 mentor giao, uu tien cao nhat).

Cach lam: tiem hanh vi gian lan co tham so (3 muc do light/medium/heavy) vao
BAN SAO trong bo nho cua du lieu that qua lcx_core.fraud_injector, roi goi
THANG cac ham detect() cua Tier A (khong sua mot dong code detector nao) tren
du lieu da tiem. Nhan doc lap duoc giu tach biet hoan toan, chi dung de doi
chieu SAU KHI detector da chay xong - dung nguyen tac "khong lo de".

Dung:
    ./.venv/bin/python3 scripts/run_injector_eval.py
    ./.venv/bin/python3 scripts/run_injector_eval.py --n-per-severity 60 --save

Luu y ve so lieu in ra:
    - "recall" tren tung muc do nghiem trong = ty le don vi gian lan DA TIEM o
      muc do do ma detector bat duoc. Day la so lieu chinh ma tai lieu chien
      luoc lam giau du lieu goi la "Precision/Recall that lan dau tien".
    - "precision" o dong ALL = ty le flag cua detector (tren toan bo du lieu
      that + da tiem) TRUNG voi mot don vi da tiem. Vi dan so "am" duy nhat co
      nhan la tap da tiem (dan so that KHONG co nhan doc lap), con so nay nen
      doc la "trong cac flag hien co, bao nhieu % la fraud CO CHU DICH da biet"
      chu KHONG phai precision tuyet doi tren toan bo dan so that.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from lcx_core.data import load_data_store
from lcx_core.fraud_injector import PATTERNS, SEVERITIES, flag_matches_label, inject_all
from lcx_core.tier_a import REGISTRY

VAR_DIR = Path(__file__).resolve().parents[1] / "var" / "injector_eval"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-per-severity", type=int, default=40)
    parser.add_argument(
        "--save", action="store_true", help="Ghi ground_truth.parquet + report.json vao var/injector_eval/<run_id>/"
    )
    args = parser.parse_args()

    store = load_data_store()
    print(store.describe())
    print()

    t0 = time.time()
    injected_store, ground_truth = inject_all(store, seed=args.seed, n_per_severity=args.n_per_severity)
    print(
        f"Da tiem {ground_truth.height} don vi gian lan co chu dich cho "
        f"{len(PATTERNS)} mau hinh ({time.time() - t0:.1f}s)\n"
    )

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
        result = REGISTRY[pattern].detect(injected_store)
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
            pattern_report["by_severity"][severity] = {
                "n_injected": n_injected,
                "tp": tp,
                "fn": fn,
                "recall": recall,
            }
            print(f"{pattern:<26}{severity:<10}{n_injected:>11}{tp:>6}{fn:>6}{recall:>9.1%}")

        tp_flags = sum(
            1
            for f in result.flags
            if any(
                flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"])
                for r in gt_rows
            )
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
