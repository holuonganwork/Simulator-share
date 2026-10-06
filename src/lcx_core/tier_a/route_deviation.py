"""Di vong de tang cuoc (route_deviation) - giai doan trong chuyen.

Tin hieu: so sanh trips.distance_km (quang duong thuc te tinh cuoc) voi
road_km toi uu tra tu ma tran OSRM (research/simulation/data/osrm_matrix_dd.parquet,
khoa boi cap H3 res-9 pickup_h3/drop_h3 - da xac minh ca hai dung chung he H3 res 9,
ma tran phu day 316x316 o = toan bo luoi Dong Da).

% lech = (distance_km - road_km) / road_km. Nguong = percentile p99, TACH RIENG
theo travel_mode (bike/car) dung theo khuyen nghi trong docs/strategy.md muc 08 -
hai loai xe co dac tinh di chuyen khac nhau nen gop chung se lam nhieu nguong.

Cuoc qua ngan (road_km < min_road_km) bi loai vi ty le % rat khong on dinh khi
mau so nho (mot sai so 200m tren cuoc 500m da la 40%).

PHAT HIEN KHI HIEU CHUAN TREN DU LIEU THAT (dang ghi lai o day thay vi giau di):
trips.distance_km TRUNG BINH THAP HON road_km khoang 32% (mean deviation_pct =
-0.32, xem src/gsm_core/mockgen/realdata.py:229 - distance_km duoc gan tu
`t["dist_km"]` cua adapter_sim, nhieu kha nang la khoang cach duong chim (straight-
line) chu khong phai khoang cach di theo mang luoi duong nhu road_km cua OSRM).
Vi vay deviation_pct KHONG the doc nhu "% di vong so voi toi uu" theo nghia tuyet
doi - no mang mot do lech he thong am. Nguong percentile p99 van dung duoc VI no
tu hieu chinh theo phan phoi (bat outlier TUONG DOI so voi cac chuyen khac cung
travel_mode), nhung khi trinh bay cho quan ly/tai xe PHAI kem ca distance_km va
road_km_optimal tho (da co san trong evidence) thay vi chi noi con so %.
"""

from __future__ import annotations

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult, percentile_threshold

PATTERN = "route_deviation"
DEFAULT_PERCENTILE = 0.99
MIN_ROAD_KM = 0.3


def _join_with_osrm(store: GsmDataStore) -> pl.DataFrame:
    trips = store.table("trips").filter(pl.col("status") == "completed")
    osrm = store.osrm_matrix.rename({"cell_from": "pickup_h3", "cell_to": "drop_h3"})
    joined = trips.join(osrm, on=["pickup_h3", "drop_h3"], how="inner")
    joined = joined.filter(pl.col("road_km") >= MIN_ROAD_KM)
    joined = joined.with_columns(
        ((pl.col("distance_km") - pl.col("road_km")) / pl.col("road_km")).alias(
            "deviation_pct"
        )
    )
    return joined


def detect(store: GsmDataStore, percentile: float = DEFAULT_PERCENTILE) -> RuleResult:
    joined = _join_with_osrm(store)
    all_flags: list[RuleFlag] = []
    thresholds: dict[str, float] = {}

    for mode in sorted(joined["travel_mode"].drop_nulls().unique().to_list()):
        subset = joined.filter(pl.col("travel_mode") == mode)
        values = subset["deviation_pct"].to_list()
        threshold = percentile_threshold(values, percentile)
        thresholds[mode] = threshold

        flagged = subset.filter(pl.col("deviation_pct") > threshold)
        for row in flagged.iter_rows(named=True):
            all_flags.append(
                RuleFlag(
                    pattern=PATTERN,
                    driver_id=row["driver_id"],
                    signal_value=row["deviation_pct"],
                    threshold=threshold,
                    evidence={
                        "trip_id": row["trip_id"],
                        "travel_mode": mode,
                        "distance_km": row["distance_km"],
                        "road_km_optimal": row["road_km"],
                        "pickup_h3": row["pickup_h3"],
                        "drop_h3": row["drop_h3"],
                    },
                )
            )

    return RuleResult(
        pattern=PATTERN,
        method=f"percentile_p{int(percentile * 100)}_by_travel_mode={thresholds}",
        threshold=max(thresholds.values()) if thresholds else float("inf"),
        n_evaluated=joined.height,
        flags=all_flags,
    )
