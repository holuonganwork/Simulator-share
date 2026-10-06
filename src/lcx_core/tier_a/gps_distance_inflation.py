"""Gia GPS phong dai quang duong (gps_distance_inflation) - giai doan trong chuyen.

Khong co GPS tho (met/giay) trong repo - chi co public_driver_hex_tracking: moi
ban ghi la mot lan doi o H3 res-9 (`current_hex`, `last_hex`) kem
`entered_current_hex_at` va `stay_duration_seconds`. Day chinh xac la dieu
docs/gsm-simulator-alignment.md da luu y: phai "ha xuong muc chuyen dong giua o
hex theo phut" thay vi toa do chinh xac.

Van toc ngu y giua 2 lan doi o lien tiep = khoang cach tam o (haversine qua
h3.cell_to_latlng) / thoi gian giua hai lan `entered_current_hex_at`. Vuot tran
vat ly (hard cap cua o to dien Taxi Co Huu) -> flag ngay, khong can percentile them.
"""

from __future__ import annotations

import h3
import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "gps_distance_inflation"
HARD_CAP_KMH = {"car": 110.0}
DEFAULT_MODE = "car"


def _vehicle_mode_by_driver(store: GsmDataStore) -> dict[str, str]:
    """100% Taxi Co Huu la o to => moi driver deu la "car"."""
    kpi = store.table("kpi_driver_platform_calculator_gbq")
    return {driver_id: "car" for driver_id in kpi["driver_id"].unique().to_list()}


def _haversine_km(hex_a: str, hex_b: str) -> float:
    if hex_a == hex_b:
        return 0.0
    lat1, lng1 = h3.cell_to_latlng(hex_a)
    lat2, lng2 = h3.cell_to_latlng(hex_b)
    return h3.great_circle_distance((lat1, lng1), (lat2, lng2), unit="km")


def detect(store: GsmDataStore) -> RuleResult:
    hex_t = store.table("public_driver_hex_tracking").filter(
        pl.col("last_hex").is_not_null()
    )
    mode_by_driver = _vehicle_mode_by_driver(store)

    flags: list[RuleFlag] = []
    n_evaluated = 0

    for row in hex_t.iter_rows(named=True):
        last_hex, current_hex = row["last_hex"], row["current_hex"]
        if last_hex == current_hex:
            continue
        n_evaluated += 1

        dt_seconds = row["stay_duration_seconds"]
        if not dt_seconds or dt_seconds <= 0:
            continue

        dist_km = _haversine_km(last_hex, current_hex)
        speed_kmh = dist_km / (dt_seconds / 3600.0)

        mode = mode_by_driver.get(row["driver_id"], DEFAULT_MODE)
        cap = HARD_CAP_KMH[mode]
        if speed_kmh > cap:
            flags.append(
                RuleFlag(
                    pattern=PATTERN,
                    driver_id=row["driver_id"],
                    signal_value=speed_kmh,
                    threshold=cap,
                    evidence={
                        "vehicle_mode": mode,
                        "from_hex": last_hex,
                        "to_hex": current_hex,
                        "implied_distance_km": round(dist_km, 3),
                        "elapsed_seconds": dt_seconds,
                        "entered_current_hex_at": row["entered_current_hex_at"],
                    },
                )
            )

    return RuleResult(
        pattern=PATTERN,
        method=f"hard_cap_kmh={HARD_CAP_KMH}",
        threshold=min(HARD_CAP_KMH.values()),
        n_evaluated=n_evaluated,
        flags=flags,
    )
