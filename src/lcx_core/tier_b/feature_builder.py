"""Gop ket qua Tier A thanh ma tran dac trung theo tung driver (Pha 2).

Moi mau hinh Tier A tra ve danh sach RuleFlag roi rac theo driver-ngay/chuyen.
De Tier B cham diem rui ro TONG HOP (thay vi tach roi tung nguong), ta gop lai
thanh 1 dong / driver_id voi cac cot dac trung:

  - <pattern>_flag_count : so lan driver do bi flag boi mau hinh do
  - <pattern>_max_severity : gia tri signal_value/threshold lon nhat (ty le vuot
    nguong) trong cac lan bi flag - 0 neu khong bi flag lan nao

Chi dung 6 mau hinh CO LOGIC THAT (khong dung 2 proxy "THO" wait_fee_manipulation/
ride_preview_selection lam dac trung chinh vi do tin cay thap hon - nhung van
giu lai o cot rieng de tham khao, khong dua vao IsolationForest).
"""

from __future__ import annotations

from collections import defaultdict

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a import (
    abnormal_cancel,
    fake_ride_for_kpi,
    gps_distance_inflation,
    location_dropout,
    ride_preview_selection,
    route_deviation,
    split_long_trip,
    wait_fee_manipulation,
)
from lcx_core.tier_a.base import RuleResult

CORE_PATTERNS = [
    abnormal_cancel,
    route_deviation,
    gps_distance_inflation,
    location_dropout,
    split_long_trip,
]
AUX_PATTERNS = [wait_fee_manipulation, fake_ride_for_kpi, ride_preview_selection]


def run_all_core(store: GsmDataStore) -> dict[str, RuleResult]:
    return {mod.PATTERN: mod.detect(store) for mod in CORE_PATTERNS}


def run_all_aux(store: GsmDataStore) -> dict[str, RuleResult]:
    return {mod.PATTERN: mod.detect(store) for mod in AUX_PATTERNS}


def build_feature_matrix(store: GsmDataStore) -> pl.DataFrame:
    """Tra ve 1 DataFrame: 1 dong/driver_id, cac cot <pattern>_flag_count/_max_severity.

    Quan trong: quan the driver la TOAN BO driver xuat hien trong
    driver_statistic_daily (khong chi driver da tung bi flag) - de IsolationForest
    co ca nhom "binh thuong" lam nen so sanh, dung tinh than phat hien BAT THUONG
    (thieu buoc nay se lam moi driver deu "bat thuong" vi tat ca hang trong ma
    tran deu da co it nhat 1 flag).
    """
    results = {**run_all_core(store), **run_all_aux(store)}

    all_driver_ids: set[str] = set(
        store.table("driver_statistic_daily")["driver_id"].unique().to_list()
    )

    per_driver: dict[str, dict[str, float]] = defaultdict(dict)

    for pattern, result in results.items():
        counts: dict[str, int] = defaultdict(int)
        max_sev: dict[str, float] = defaultdict(float)
        for flag in result.flags:
            counts[flag.driver_id] += 1
            severity = (
                abs(flag.signal_value / flag.threshold)
                if flag.threshold not in (0, float("inf"))
                else abs(flag.signal_value)
            )
            max_sev[flag.driver_id] = max(max_sev[flag.driver_id], severity)
            all_driver_ids.add(flag.driver_id)  # phong khi flag co driver la ngoai bang thong ke

        for driver_id in all_driver_ids:
            per_driver[driver_id][f"{pattern}_flag_count"] = counts.get(driver_id, 0)
            per_driver[driver_id][f"{pattern}_max_severity"] = max_sev.get(driver_id, 0.0)

    # dam bao MOI driver deu co du cot (kem driver chua bao gio bi flag - severity 0)
    all_cols = [f"{p}_flag_count" for p in results] + [
        f"{p}_max_severity" for p in results
    ]
    rows = []
    for driver_id in sorted(all_driver_ids):
        row = {"driver_id": driver_id}
        for col in all_cols:
            row[col] = per_driver[driver_id].get(col, 0)
        rows.append(row)

    if not rows:
        return pl.DataFrame({"driver_id": []})
    return pl.DataFrame(rows)
