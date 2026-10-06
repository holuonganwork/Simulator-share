#!/usr/bin/env python3
"""Pha 5 - xuat toan bo kho nhan (var/cases/resolutions.jsonl) ra 1 file Parquet.

Dung de phan tich/huan luyen lai ben ngoai (notebook, BI...) ma khong phai doc
JSONL bang tay. Neu chua co resolution nao (chua ai chay manager_chat.py va
resolve_case), in thong bao ro rang thay vi tao file rong gay hieu lam.

    ./.venv/bin/python3 scripts/build_label_store.py [duong_dan_output.parquet]
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import polars as pl

from lcx_core.labels import all_resolutions

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "var" / "cases" / "label_store_export.parquet"


def main() -> None:
    resolutions = all_resolutions()
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    if not resolutions:
        print(
            "Chua co resolution nao trong var/cases/resolutions.jsonl. Can chay "
            "manager_chat.py va goi resolve_case truoc. Khong tao file rong."
        )
        return

    df = pl.DataFrame([asdict(r) | {"signal_snapshot": str(r.signal_snapshot)} for r in resolutions])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path)
    print(f"Da xuat {df.height} resolution -> {output_path}")
    print(df.group_by("verdict").len())


if __name__ == "__main__":
    main()
