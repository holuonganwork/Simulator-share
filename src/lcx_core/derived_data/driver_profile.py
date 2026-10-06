"""driver_profile_enriched - hang muc 1 "Chien luoc lam giau du lieu" (PHAN HO
SO, KHONG bao gom mo rong khong gian/ban do - xem README.md muc gioi han pham
vi). Khong mo mau hinh moi rieng (tai lieu chien luoc goc: "nen dai han",
"Tier C that su van can GSM cap device data") - day la lam giau MO TA, dung de
lam ngu canh cho agent/manager_chat khi tra cuu ho so tai xe
(get_driver_risk_profile.py co the doc them bang nay sau).

Tach ro 2 loai truong, KHONG lam nhoe ranh gioi (dung tinh than fare_breakdown.py):
  - THAT, gop tu du lieu co san: violation_count_total, violation_amount_vnd_total
    (tu driver_penalization_ATA.parquet - da co san, chi CHUA duoc gop theo driver),
    registration_zone_hex (tu chinh phan phoi pickup_h3 THAT cua driver do, KHONG
    phai mo rong khu vuc moi).
  - SYNTHETIC (khong the tai tao tu du lieu hien co, GHI RO trong cot
    `synthetic_fields`): join_date, contract_type.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import timedelta

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore

CONTRACT_TYPES = ["employee"]  # 100% Taxi Cơ Hữu ký HĐLĐ với GSM
_ANCHOR_MIN = "2024-01-01"


def compute_driver_profile_enriched(store: GsmDataStore, seed: int = 42) -> pl.DataFrame:
    rng = random.Random(seed)

    stat_daily = store.table("driver_statistic_daily")
    driver_ids = stat_daily["driver_id"].unique().to_list()

    trips = store.table("trips").filter(pl.col("status") == "completed")
    pickup_by_driver = trips.group_by("driver_id").agg(pl.col("pickup_h3").alias("pickup_hexes"))
    pickup_map = {
        row["driver_id"]: Counter(row["pickup_hexes"]).most_common(1)[0][0]
        for row in pickup_by_driver.iter_rows(named=True)
        if row["pickup_hexes"]
    }

    penal = store.table("driver_penalization_ATA")
    penal_agg = penal.group_by("driver_id").agg(
        pl.len().alias("violation_count_total"),
        pl.col("amount_vnd").sum().alias("violation_amount_vnd_total"),
    )
    penal_map = {
        row["driver_id"]: (row["violation_count_total"], row["violation_amount_vnd_total"])
        for row in penal_agg.iter_rows(named=True)
    }

    first_trip_by_driver = trips.group_by("driver_id").agg(pl.col("request_time").min().alias("first_trip_at"))
    first_trip_map = {
        row["driver_id"]: row["first_trip_at"] for row in first_trip_by_driver.iter_rows(named=True)
    }

    rows: list[dict] = []
    for driver_id in driver_ids:
        vio_count, vio_amount = penal_map.get(driver_id, (0, 0))
        first_trip_at = first_trip_map.get(driver_id)
        # join_date SYNTHETIC: uoc luong som hon lan xuat hien dau tien trong
        # trips.parquet mot khoang ngau nhien (0-90 ngay) - khong the biet
        # ngay gia nhap THAT vi khong co bang onboarding trong du lieu goc.
        join_date = None
        if first_trip_at:
            from datetime import datetime

            join_date = (
                datetime.fromisoformat(first_trip_at) - timedelta(days=rng.randint(0, 90))
            ).date().isoformat()

        rows.append(
            {
                "driver_id": driver_id,
                "registration_zone_hex": pickup_map.get(driver_id),
                "violation_count_total": vio_count,
                "violation_amount_vnd_total": vio_amount,
                "join_date": join_date,
                "contract_type": rng.choice(CONTRACT_TYPES),
                "synthetic_fields": "join_date,contract_type",
            }
        )
    return pl.DataFrame(rows)
