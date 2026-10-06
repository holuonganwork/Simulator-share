"""Thao tung phi cho (wait_fee_manipulation) - giai doan trong chuyen.

CANH BAO PROXY THO (xem docs/data-matrix.md): driver_bike_stoppoints chi co so
dem tong hop total_stoppoints_rush_hour theo NGAY, khong co ly do dung/do (ket xe
that vs co y dung yen), khong gan voi tung chuyen cu the. Nguong percentile o day
chi bat "so lan dung bat thuong nhieu" - KHONG the phan biet voi ket xe that.

Dung ket qua nay nhu mot tin hieu THAM KHAO de agent hoi tai xe qua chat (mau 07),
KHONG dung lam can cu duy nhat de flag_case muc do cao.
"""

from __future__ import annotations

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult, percentile_threshold

PATTERN = "wait_fee_manipulation"
DEFAULT_PERCENTILE = 0.99
CAVEAT = (
    "THO: total_stoppoints_rush_hour la so dem tong hop theo ngay, chua doi chieu "
    "duoc voi ly do hop le (ket xe, khach tre). Xem docs/data-matrix.md."
)


def detect(store: GsmDataStore, percentile: float = DEFAULT_PERCENTILE) -> RuleResult:
    df = store.table("driver_bike_stoppoints")
    values = df["total_stoppoints_rush_hour"].drop_nulls().to_list()
    threshold = percentile_threshold(values, percentile)

    flagged = df.filter(pl.col("total_stoppoints_rush_hour") > threshold)
    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=row["total_stoppoints_rush_hour"],
            threshold=threshold,
            evidence={
                "local_date": row["local_date"],
                "total_stoppoints": row["total_stoppoints"],
            },
            caveat=CAVEAT,
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
