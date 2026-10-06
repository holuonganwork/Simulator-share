"""Thong dong tao tang gia gia (artificial_surge_collusion) - hang muc 8
"Chien luoc lam giau du lieu". Truoc day KHONG co (dang "Thieu han" trong tai
lieu chien luoc goc, muc 02) - can `fare_breakdown` (surge_multiplier) moi mo
khoa duoc.

CANH BAO PROXY THO (cung tinh than cac detector THO khac trong tier_a - vd
wait_fee_manipulation.py, ride_preview_selection.py): tin hieu that su can co
la trang thai online/offline cua CA NHOM driver quanh khung gio surge (de bat
"giu don cho toi luc tang gia" hay "cung tat ung dung de tao khan hiem gia").
O day KHONG co du lieu do (Tier C/device-account graph van la stub) - chi bat
duoc mot he qua GIAN TIEP: MOT driver chiem ty le bat thuong nhieu cuoc GIA
SURGE CAO trong CUNG 1 ngay, so voi phan phoi binh thuong cua chinh du lieu.
Day la tin hieu THAM KHAO, khong du de flag_case muc do cao mot minh.
"""

from __future__ import annotations

import polars as pl

from lcx_core.tier_a.base import RuleFlag, RuleResult, percentile_threshold

PATTERN = "artificial_surge_collusion"
DEFAULT_PERCENTILE = 0.99
HIGH_SURGE_QUANTILE = 0.90
CAVEAT = (
    "THO: chi bat so cuoc gia surge cao bat thuong trong ngay cua MOT driver, "
    "KHONG co du lieu online/offline theo nhom de xac nhan thong dong that su. "
    "Xem docstring module nay."
)


def detect(fare_breakdown: pl.DataFrame, percentile: float = DEFAULT_PERCENTILE) -> RuleResult:
    high_surge_cut = fare_breakdown["surge_multiplier"].quantile(HIGH_SURGE_QUANTILE)
    high = fare_breakdown.filter(pl.col("surge_multiplier") >= high_surge_cut)
    daily = (
        high.group_by(["driver_id", "local_date"])
        .agg(pl.len().alias("n_high_surge_trips"))
        .sort(["driver_id", "local_date"])
    )

    values = daily["n_high_surge_trips"].to_list()
    threshold = percentile_threshold(values, percentile)

    flagged = daily.filter(pl.col("n_high_surge_trips") > threshold)
    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=row["n_high_surge_trips"],
            threshold=threshold,
            evidence={
                "local_date": row["local_date"],
                "n_high_surge_trips": row["n_high_surge_trips"],
                "high_surge_cutoff": round(float(high_surge_cut), 4),
            },
            caveat=CAVEAT,
        )
        for row in flagged.iter_rows(named=True)
    ]

    return RuleResult(
        pattern=PATTERN,
        method=f"percentile_p{int(percentile * 100)}_daily_count_surge>=p{int(HIGH_SURGE_QUANTILE * 100)}",
        threshold=threshold,
        n_evaluated=daily.height,
        flags=flags,
    )
