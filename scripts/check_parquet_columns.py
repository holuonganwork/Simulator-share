#!/usr/bin/env python3
"""Pha 0 - soi ten cot + kieu du lieu cua toan bo bang trong GSM_SIMULATOR.

Dung khi GSM cap them bang moi hoac doi schema - chay lai script nay truoc khi
sua bat ky detector nao, thay vi doan ten cot.

    ./.venv/bin/python3 scripts/check_parquet_columns.py [ten_bang ...]
"""

from __future__ import annotations

import sys

from lcx_core.data import load_data_store


def main() -> None:
    store = load_data_store()
    names = sys.argv[1:] or sorted(store.tables)

    for name in names:
        try:
            df = store.table(name)
        except KeyError as exc:
            print(f"{name}: {exc}")
            continue
        print(f"\n=== {name} ({df.height} dong) ===")
        for col, dtype in df.schema.items():
            print(f"  {col}: {dtype}")


if __name__ == "__main__":
    main()
