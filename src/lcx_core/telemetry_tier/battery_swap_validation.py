"""Xac thuc phien doi pin hop le (battery_swap_validation) - hang muc 6
"Chien luoc lam giau du lieu" (phan LOG, khong bao gom mo rong ban do tram).

Tin hieu: toc do sac ngu y = (soc_after_pct - soc_before_pct) / duration_seconds.
Gioi han vat ly biet truoc tu chinh mo phong doi pin cua GSM_SIMULATOR/telemetry_mock
(SWAP_DURATION_TICKS_RANGE ~ 120-300s cho 15%->98%, xem
docs/telemetry-research.md muc 12.3) - vuot HARD_CAP nghia la BAO CAO phien sac
khong khop vat ly (gian lan phia van hanh/tram, khong phai hanh vi tai xe) ->
flag ngay, khong can percentile (giong tinh than
tier_a/gps_distance_inflation.py).
"""

from __future__ import annotations

import polars as pl

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "battery_swap_validation"
HARD_CAP_SWAP_RATE_PCT_PER_S = 1.5  # ~90% trong 60s - vuot moi gioi han pin that


def detect(store: EventDataStore) -> RuleResult:
    df = store.charging_sessions.filter(
        pl.col("soc_after_pct").is_not_null() & pl.col("duration_seconds").is_not_null() & (pl.col("duration_seconds") > 0)
    )
    df = df.with_columns(
        ((pl.col("soc_after_pct") - pl.col("soc_before_pct")) / pl.col("duration_seconds")).alias("swap_rate")
    )

    flags: list[RuleFlag] = []
    for row in df.iter_rows(named=True):
        if row["swap_rate"] > HARD_CAP_SWAP_RATE_PCT_PER_S:
            flags.append(
                RuleFlag(
                    pattern=PATTERN,
                    driver_id=row["driver_id"],
                    signal_value=row["swap_rate"],
                    threshold=HARD_CAP_SWAP_RATE_PCT_PER_S,
                    evidence={
                        "session_id": row["session_id"],
                        "station_id": row["station_id"],
                        "duration_seconds": row["duration_seconds"],
                        "soc_before_pct": row["soc_before_pct"],
                        "soc_after_pct": row["soc_after_pct"],
                    },
                )
            )

    return RuleResult(
        pattern=PATTERN,
        method=f"hard_cap_pct_per_s={HARD_CAP_SWAP_RATE_PCT_PER_S}",
        threshold=HARD_CAP_SWAP_RATE_PCT_PER_S,
        n_evaluated=df.height,
        flags=flags,
    )
