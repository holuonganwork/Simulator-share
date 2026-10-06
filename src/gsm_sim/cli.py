"""CLI: `gsm-sim run --config configs/pilot_hanoi.yaml --seed 1`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


from .config import Config
from .logging_ev import write_run
from .metrics import summarize, trips_by_hour
from .runner import run_once


def _ascii_bar(v: int, vmax: int, width: int = 40) -> str:
    n = int(round(v / vmax * width)) if vmax else 0
    return "#" * n


def cmd_run(args) -> int:
    cfg = Config.load(args.config)
    date_str = getattr(args, "date", None)
    result = run_once(cfg, args.seed, date_str=date_str)
    m = summarize(result)

    print(f"\n=== SIM RUN (arm B, seed={args.seed}) — DỮ LIỆU MÔ PHỎNG ===")
    if date_str:
        print(f"  Ngày mô phỏng:            {date_str}")
    for k in ("orders_total", "orders_completed", "served_rate", "unserved_rate",
              "orders_declined", "orders_expired", "battery_stranded",
              "trips_per_actor_median", "trips_fulltime_median",
              "payout_fulltime_median", "utilization_ft_median",
              "pickup_eta_median", "pickup_eta_p90",
              "swap_events", "swap_wait_median", "swap_wait_p90", "swap_wait_max",
              "n_actors", "n_fulltime"):
        print(f"  {k:26s}: {m[k]}")

    print("\n  Cuốc hoàn thành theo giờ:")
    tbh = trips_by_hour(result)
    vmax = max(tbh.values()) if tbh else 1
    for h in sorted(tbh):
        print(f"    {h:02d}h {tbh[h]:4d} {_ascii_bar(tbh[h], vmax)}")

    if not args.no_write:
        out = write_run(result, cfg, Path(cfg.get("output.runs_dir", "runs")))
        print(f"\n  → Ghi: {out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gsm-sim", description="Twin-world sim (pilot Đống Đa)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="Chạy 1 sim run (arm B)")
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--seed", type=int, default=1)
    p_run.add_argument("--date", type=str, default=None, help="Ngày mô phỏng YYYY-MM-DD (dùng cho biến thiên thời tiết/nhu cầu)")
    p_run.add_argument("--no-write", action="store_true", help="Không ghi parquet")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
