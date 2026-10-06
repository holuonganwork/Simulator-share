#!/usr/bin/env python3
"""Sinh va luu driver_profile_enriched.parquet (hang muc 1 "Chien luoc lam
giau du lieu", PHAN HO SO - khong bao gom mo rong khong gian/ban do). Ghi vao
data/derived/ CUA REPO NAY (khong dong cham GSM_SIMULATOR).

Dung:
    ./.venv/bin/python3 scripts/build_driver_profile_enriched.py
"""

from __future__ import annotations

from pathlib import Path

from lcx_core.data import load_data_store
from lcx_core.derived_data.driver_profile import compute_driver_profile_enriched

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "derived"
OUT_FILE = OUT_DIR / "driver_profile_enriched.parquet"


def main() -> None:
    store = load_data_store()
    profile = compute_driver_profile_enriched(store, seed=42)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    profile.write_parquet(OUT_FILE)

    n_with_violations = profile.filter(profile["violation_count_total"] > 0).height
    print(f"Da sinh {profile.height} ho so tai xe -> {OUT_FILE}")
    print(f"  - {n_with_violations} driver co lich su vi pham THAT (tu driver_penalization_ATA.parquet)")
    print(f"  - Truong SYNTHETIC (khong the tai tao tu du lieu that): join_date, contract_type")
    print(profile.head(5))


if __name__ == "__main__":
    main()
