"""Cuoc ao de dat KPI thuong (fake_ride_for_kpi) - giai doan cho cuoc.

CANH BAO PROXY THO: public_mission_earn_history ghi lai moi lan driver nhan
thuong tu mission, nhung KHONG co lien ket thiet bi/tai khoan (Tier C - xem
tier_c/device_account_graph.py) de xac nhan cau ket voi mot "khach quen". Detector
nay chi bat bat thuong THONG KE: tong `earn` trong mot tuan cua mot driver lech
qua xa (z-score) so voi chinh trung binh+do lech chuan cua driver do cac tuan
khac - tuc la "tuan nay bat thuong so voi chinh minh", khong phai bang chung cau ket.
"""

from __future__ import annotations

import statistics

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "fake_ride_for_kpi"
DEFAULT_Z_THRESHOLD = 3.0
CAVEAT = (
    "THO: chi la bat thuong thong ke tren tien thuong cua chinh driver do, KHONG "
    "xac nhan duoc cau ket vi thieu du lieu lien ket thiet bi/tai khoan (Tier C)."
)


def _week_key(iso_ts: str) -> str:
    from datetime import datetime

    dt = datetime.fromisoformat(iso_ts)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def detect(store: GsmDataStore, z_threshold: float = DEFAULT_Z_THRESHOLD) -> RuleResult:
    earn = store.table("public_mission_earn_history").filter(
        pl.col("order_time").is_not_null()
    )
    earn = earn.with_columns(
        pl.col("order_time")
        .map_elements(_week_key, return_dtype=pl.Utf8)
        .alias("week_key")
    )
    weekly = (
        earn.group_by(["driver_id", "week_key"])
        .agg(pl.col("earn").sum().alias("weekly_earn"))
        .sort(["driver_id", "week_key"])
    )

    flags: list[RuleFlag] = []
    n_evaluated = 0

    for _driver_id, group in weekly.group_by("driver_id"):
        values = group["weekly_earn"].to_list()
        n_evaluated += len(values)
        if len(values) < 3:
            continue  # can toi thieu vai tuan de co do lech chuan co nghia
        mean = statistics.mean(values)
        stdev = statistics.pstdev(values) or 1.0

        for row in group.iter_rows(named=True):
            z = (row["weekly_earn"] - mean) / stdev
            if z > z_threshold:
                flags.append(
                    RuleFlag(
                        pattern=PATTERN,
                        driver_id=row["driver_id"],
                        signal_value=z,
                        threshold=z_threshold,
                        evidence={
                            "week_key": row["week_key"],
                            "weekly_earn": row["weekly_earn"],
                            "driver_mean_weekly_earn": round(mean, 0),
                            "driver_stdev_weekly_earn": round(stdev, 0),
                        },
                        caveat=CAVEAT,
                    )
                )

    return RuleResult(
        pattern=PATTERN,
        method=f"z_score_threshold={z_threshold}",
        threshold=z_threshold,
        n_evaluated=n_evaluated,
        flags=flags,
    )
