"""F3 - Lam mat tin hieu dinh vi TCU (tcu_signal_loss), FRAUDS.md muc F3.

Dau hieu: GIUA ca cam ket, 2 ping TCU lien tiep cach nhau > GAP_MIN_THRESHOLD phut
va ODO phan cung tang >= MIN_ODO_JUMP_KM giua hai ping - xe da chay trong luc khong
co tin hieu. Mat song ngan (tunnel, <= 14 phut) xe dung yen nen ODO khong doi -> khong flag.

Truy thu: delta ODO x 13.000 d/km (don gia cuoc TB).
"""

from __future__ import annotations

import polars as pl

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "tcu_signal_loss"
GAP_MIN_THRESHOLD = 15
MIN_ODO_JUMP_KM = 3.0
GRACE_BEFORE_MIN = 15
GRACE_AFTER_MIN = 30
RATE_VND_PER_KM = 13_000


def detect(store: EventDataStore) -> RuleResult:
    tel = store.telemetry.with_columns(
        pl.col("occurred_at").str.slice(0, 10).alias("local_date"),
        (
            pl.col("occurred_at").str.slice(11, 2).cast(pl.Int32) * 60
            + pl.col("occurred_at").str.slice(14, 2).cast(pl.Int32)
        ).alias("minute"),
    ).sort(["driver_id", "occurred_at"])

    seq = (
        tel.with_columns(
            pl.col("minute").shift(1).over(["driver_id", "local_date"]).alias("prev_minute"),
            pl.col("odometer_km").shift(1).over(["driver_id", "local_date"]).alias("prev_odo"),
        )
        .drop_nulls(["prev_minute"])
        .join(store.declared_shifts, on="driver_id", how="inner")
        .with_columns(
            (pl.col("minute") - pl.col("prev_minute")).alias("gap_minutes"),
            (pl.col("odometer_km") - pl.col("prev_odo")).alias("odo_jump_km"),
        )
    )
    in_shift = (pl.col("prev_minute") >= pl.col("declared_start_min") - GRACE_BEFORE_MIN) & (
        pl.col("minute") <= pl.col("declared_end_min") + GRACE_AFTER_MIN
    )
    flagged = seq.filter(
        in_shift & (pl.col("gap_minutes") > GAP_MIN_THRESHOLD) & (pl.col("odo_jump_km") >= MIN_ODO_JUMP_KM)
    )

    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=float(row["odo_jump_km"]),
            threshold=MIN_ODO_JUMP_KM,
            evidence={
                "local_date": row["local_date"],
                "gap_minutes": row["gap_minutes"],
                "odo_jump_km": round(row["odo_jump_km"], 2),
                "est_clawback_vnd": int(row["odo_jump_km"] * RATE_VND_PER_KM),
            },
        )
        for row in flagged.iter_rows(named=True)
    ]
    return RuleResult(
        pattern=PATTERN,
        method=f"gap>{GAP_MIN_THRESHOLD}min_and_odo_jump>={MIN_ODO_JUMP_KM}km_in_shift",
        threshold=MIN_ODO_JUMP_KM,
        n_evaluated=seq.height,
        flags=flags,
    )
