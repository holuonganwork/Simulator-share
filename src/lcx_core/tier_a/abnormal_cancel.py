"""Huy cuoc co chon loc (abnormal_cancel) - giai doan cho cuoc.

Tin hieu: driver_statistic_daily.cancellation_rate (co san, tinh moi ngay tren
tung driver). Nguong = percentile p99 do tren chinh phan phoi that cua bang nay,
dung phuong phap y het cach GSM_SIMULATOR da hieu chuan mau nay
(src/gsm_core/mockgen/realdata.py:548-566 -> p99 = 0.20 tren du lieu ho dung).
Day la mau hinh DUY NHAT trong 5 loai fraud_type da co logic sinh du lieu that
trong repo goc (xem public_frauds.fraud_type - chi co 'abnormal_cancel').
"""

from __future__ import annotations

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult, percentile_threshold

PATTERN = "abnormal_cancel"
DEFAULT_PERCENTILE = 0.99


def detect(store: GsmDataStore, percentile: float = DEFAULT_PERCENTILE) -> RuleResult:
    df = store.table("driver_statistic_daily")
    values = df["cancellation_rate"].drop_nulls().to_list()
    threshold = percentile_threshold(values, percentile)

    flagged = df.filter(pl.col("cancellation_rate") > threshold)
    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=row["cancellation_rate"],
            threshold=threshold,
            evidence={
                "local_date": row["local_date"],
                "cancelled_count": row["cancelled_count"],
                "accepted_count": row["accepted_count"],
            },
        )
        for row in flagged.iter_rows(named=True)
    ]
    return RuleResult(
        pattern=PATTERN,
        method=f"percentile_p{int(percentile * 100)}",
        threshold=threshold,
        n_evaluated=df.height,
        flags=flags,
    )
