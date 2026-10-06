"""Fraud injector - Pha A cua "Chien luoc lam giau du lieu" (muc 7 mentor giao).

Van de dang giai quyet (docs/metrics.md): he thong chi co DUNG 1 nguon nhan doc
lap that (abnormal_cancel, tu public_frauds.parquet). 7 mau hinh Tier A con lai
(route_deviation, gps_distance_inflation, location_dropout, split_long_trip,
wait_fee_manipulation, fake_ride_for_kpi, ride_preview_selection) chua bao gio
duoc chay tren du lieu co hanh vi gian lan CO CHU DICH - nen Precision/Recall
cua chung khong co y nghia thong ke.

Module nay KHONG sua doi du lieu that tren dia (repo GSM_SIMULATOR khong bi
dung cham). No doc du lieu that qua GsmDataStore, THEM (append, khong sua) cac
dong tong hop dai dien cho hanh vi gian lan tham so hoa vao BAN SAO trong bo
nho cua tung bang lien quan, roi tra ve mot GsmDataStore moi (cung interface,
_cache duoc mo tien) de goi thang REGISTRY[pattern].detect() khong sua mot dong
code detector nao ca.

Nhan (ground truth) duoc gom trong mot DataFrame TACH BIET (InjectedLabel),
khong bao gio ghi vao cac bang ma detector doc - dung nguyen tac "khong lo de"
neu trong tai lieu chien luoc goc. Moi dong tiem co source="INJECTED_FRAUD"
(tai dung chinh cot `source` da co san trong moi bang GSM) de co the loc ra/
kiem tra lai bat cu luc nao bang `pl.col("source") == "INJECTED_FRAUD"`.

Muc tieu la DO duoc kha nang phat hien that, khong phai "qua" nguong that de -
vi vay moi mau hinh co 3 muc do nghiem trong (light/medium/heavy) voi tham so
KHONG duoc hieu chuan theo nguong percentile cua chinh luat dang kiem tra (xem
canh bao "lo de" trong tai lieu chien luoc lam giau du lieu, muc 05).
"""

from __future__ import annotations

import random
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.fake_ride_for_kpi import _week_key
from lcx_core.tier_a.gps_distance_inflation import (
    HARD_CAP_KMH,
    _haversine_km,
    _vehicle_mode_by_driver,
)

INJECTED_SOURCE = "INJECTED_FRAUD"
SEVERITIES = ("light", "medium", "heavy")
_VN_TZ = timezone(timedelta(hours=7))
_SYNTH_ANCHOR = datetime(2099, 1, 1, tzinfo=_VN_TZ)

# Tham so tiem theo muc do nghiem trong - xem docstring tung ham _inject_* de
# biet don vi/y nghia cu the cua tung con so.
SEVERITY_PARAMS: dict[str, dict[str, float]] = {
    "route_deviation": {"light": 0.35, "medium": 0.70, "heavy": 1.50},
    "gps_distance_inflation": {"light": 1.15, "medium": 1.60, "heavy": 3.50},
    "location_dropout": {"light": 1.3, "medium": 2.5, "heavy": 6.0},
    "split_long_trip": {"light": 4.9, "medium": 2.5, "heavy": 0.3},
    "wait_fee_manipulation": {"light": 15.0, "medium": 25.0, "heavy": 50.0},
    "fake_ride_for_kpi": {"light": 3.3, "medium": 6.0, "heavy": 12.0},
    "ride_preview_selection": {"light": 0.45, "medium": 0.30, "heavy": 0.10},
}

PATTERNS: list[str] = list(SEVERITY_PARAMS)
DEFAULT_N_PER_SEVERITY = 40

_GT_SCHEMA = {
    "pattern": pl.Utf8,
    "driver_id": pl.Utf8,
    "severity": pl.Utf8,
    "match_field": pl.Utf8,
    "match_value": pl.Utf8,
}


@dataclass(frozen=True)
class InjectedLabel:
    """Mot don vi gian lan da tiem - nhan doc lap, TACH KHOI du lieu detector doc.

    match_field/match_value la ten+gia tri truong trong RuleFlag.evidence dung
    de doi chieu lai xem detector co bat duoc chinh don vi nay khong (xem
    scripts/run_injector_eval.py::_flag_matches_label).
    """

    pattern: str
    driver_id: str
    severity: str
    match_field: str
    match_value: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _get_table(store: GsmDataStore, tables: dict[str, pl.DataFrame], name: str) -> pl.DataFrame:
    return tables.get(name, store.table(name))


def _synthetic_ts(offset_minutes: float) -> str:
    return (_SYNTH_ANCHOR + timedelta(minutes=offset_minutes)).isoformat()


def _synthetic_date(counter: int) -> str:
    """Ngay gia lap khong bao gio trung du lieu that (nam 2026)."""
    return f"2099-{(counter // 28) % 12 + 1:02d}-{(counter % 28) + 1:02d}"


def _new_rows_frame(columns: list[str], rows: list[dict], schema: dict) -> pl.DataFrame:
    filled = []
    for row in rows:
        full = {col: None for col in columns}
        full.update(row)
        filled.append(full)
    return pl.DataFrame(filled, schema=schema)


# --------------------------------------------------------------------------
# 1. route_deviation - trips.parquet
# --------------------------------------------------------------------------
def _inject_route_deviation(
    store: GsmDataStore, tables: dict[str, pl.DataFrame], rng: random.Random, n: int
) -> tuple[str, pl.DataFrame, list[InjectedLabel]]:
    """Them cuoc gia voi distance_km = road_km_optimal * (1 + extra_pct).

    extra_pct la % di vong THEM SO VOI khoang cach toi uu that cua OSRM (khong
    phai so voi chinh distance_km cua no) - danh gia dung ban chat "di vong tang
    cuoc" ma khong bi anh huong boi do lech he thong -32% da ghi nhan trong
    du lieu that (xem docstring route_deviation.py).
    """
    trips = _get_table(store, tables, "trips")
    osrm_pairs = store.osrm_matrix.filter(pl.col("road_km") >= 0.5).to_dicts()
    real_trips = trips.filter(pl.col("source") != INJECTED_SOURCE)
    driver_ids = real_trips["driver_id"].unique().to_list()

    # Chi tiem theo travel_mode co du lieu that de doi chieu (percentile duoc
    # tinh RIENG theo travel_mode - neu mot mode gan nhu khong co cuoc that nao
    # khop duoc OSRM, nguong cua mode do se tu tham chieu vao chinh du lieu da
    # tiem, lam sai lech ket qua).
    real_joined_modes = (
        real_trips.filter(pl.col("status") == "completed")
        .join(
            store.osrm_matrix.rename({"cell_from": "pickup_h3", "cell_to": "drop_h3"}),
            on=["pickup_h3", "drop_h3"],
            how="inner",
        )["travel_mode"]
        .value_counts()
    )
    valid_modes = [
        row["travel_mode"] for row in real_joined_modes.iter_rows(named=True) if row["count"] >= 100
    ] or ["car"]

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, extra_pct in SEVERITY_PARAMS["route_deviation"].items():
        for _ in range(n):
            pair = rng.choice(osrm_pairs)
            driver_id = rng.choice(driver_ids)
            travel_mode = rng.choice(valid_modes)
            distance_km = pair["road_km"] * (1 + extra_pct)
            trip_id = f"inject-route_deviation-{severity}-{counter}"
            ts = _synthetic_ts(counter)
            new_rows.append(
                {
                    "schema_version": "1.0.0",
                    "source": INJECTED_SOURCE,
                    "trip_id": trip_id,
                    "driver_id": driver_id,
                    "customer_id": f"cust-inject-rd-{counter}",
                    "service_type": "standard",
                    "status": "completed",
                    "request_time": ts,
                    "complete_time": ts,
                    "pickup_h3": pair["cell_from"],
                    "drop_h3": pair["cell_to"],
                    "distance_km": distance_km,
                    "duration_seconds": int(pair.get("duration_s") or 600),
                    "gross_vnd": 0,
                    "commission_vnd": 0,
                    "rush_hour": False,
                    "travel_mode": travel_mode,
                    "created_at": ts,
                }
            )
            labels.append(
                InjectedLabel("route_deviation", driver_id, severity, "trip_id", trip_id)
            )
            counter += 1

    new_df = _new_rows_frame(trips.columns, new_rows, trips.schema)
    updated = pl.concat([trips, new_df], how="vertical")
    return "trips", updated, labels


# --------------------------------------------------------------------------
# 2. gps_distance_inflation - public_driver_hex_tracking.parquet
# --------------------------------------------------------------------------
def _inject_gps_distance_inflation(
    store: GsmDataStore, tables: dict[str, pl.DataFrame], rng: random.Random, n: int
) -> tuple[str, pl.DataFrame, list[InjectedLabel]]:
    """Them 1 lan doi o vien vien qua nhanh: van toc ngu y = cap_mult * gioi han vat ly.

    Dung LAI (khong doan lai) logic suy loai xe cua chinh detector
    (_vehicle_mode_by_driver) de dam bao gioi han toc do dung dung nhu detector
    se ap dung khi cham diem - neu tu doan sai loai xe, muc do nghiem trong se
    lech khoi du kien.
    """
    hex_t = _get_table(store, tables, "public_driver_hex_tracking")
    real_hex = hex_t.filter(pl.col("source") != INJECTED_SOURCE)
    driver_ids = real_hex["driver_id"].unique().to_list()
    unique_hexes = real_hex["current_hex"].drop_nulls().unique().to_list()
    mode_by_driver = _vehicle_mode_by_driver(store)

    # neo thoi gian tiem theo mot ban ghi that cua chinh driver do (+jitter nho)
    # de khong vo tinh tao ra khoang trong lon gay nhieu cho location_dropout.
    anchors = real_hex.select(["driver_id", "entered_current_hex_at"]).to_dicts()
    anchor_by_driver: dict[str, list[str]] = {}
    for row in anchors:
        anchor_by_driver.setdefault(row["driver_id"], []).append(row["entered_current_hex_at"])

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, cap_mult in SEVERITY_PARAMS["gps_distance_inflation"].items():
        for _ in range(n):
            driver_id = rng.choice(driver_ids)
            hex_a, hex_b = rng.sample(unique_hexes, 2)
            dist_km = _haversine_km(hex_a, hex_b)
            mode = mode_by_driver.get(driver_id, "car")
            cap = HARD_CAP_KMH[mode]
            target_speed = cap * cap_mult
            dt_seconds = max(1.0, dist_km / (target_speed / 3600.0))

            driver_anchors = anchor_by_driver.get(driver_id)
            base_ts = (
                datetime.fromisoformat(rng.choice(driver_anchors))
                if driver_anchors
                else _SYNTH_ANCHOR
            )
            entered_at = (base_ts + timedelta(seconds=rng.uniform(1, 30))).isoformat()

            row_id = f"inject-gps_distance_inflation-{severity}-{counter}"
            new_rows.append(
                {
                    "schema_version": "1.0.0",
                    "source": INJECTED_SOURCE,
                    "id": row_id,
                    "driver_id": driver_id,
                    "init_hex": hex_a,
                    "current_hex": hex_b,
                    "last_hex": hex_a,
                    "target_hex": hex_b,
                    "last_seen_at": entered_at,
                    "entered_current_hex_at": entered_at,
                    "stay_duration_seconds": int(dt_seconds),
                    "reached_target": False,
                    "created_at": entered_at,
                    "updated_at": entered_at,
                    "tracking_status": "moving",
                }
            )
            labels.append(
                InjectedLabel(
                    "gps_distance_inflation",
                    driver_id,
                    severity,
                    "entered_current_hex_at",
                    entered_at,
                )
            )
            counter += 1

    new_df = _new_rows_frame(hex_t.columns, new_rows, hex_t.schema)
    updated = pl.concat([hex_t, new_df], how="vertical")
    return "public_driver_hex_tracking", updated, labels


# --------------------------------------------------------------------------
# 3. location_dropout - public_driver_hex_tracking.parquet (chain sau muc 2)
# --------------------------------------------------------------------------
def _inject_location_dropout(
    store: GsmDataStore, tables: dict[str, pl.DataFrame], rng: random.Random, n: int
) -> tuple[str, pl.DataFrame, list[InjectedLabel]]:
    """Them 2 ban ghi lien tiep cach nhau dung `gap_seconds` cho MOI driver duoc chon.

    gap_seconds = he so muc do nghiem trong (SEVERITY_PARAMS, vd 1.3x/2.5x/6x)
    NHAN VOI nguong p99 THAT cua chinh phan phoi gap hien co - khong dung so
    tuyet doi co dinh, vi du lieu that cho thay p99 gap giua 2 lan doi o cua
    MOT driver co the len toi hang chuc gio (driver khong lai xe lien tuc moi
    ngay) - mot con so tuyet doi doan truoc de khong khop thuc te.

    Dat ngay SAU ban ghi cuoi cung (that) cua driver do de dam bao khong ban
    ghi that nao chen vao giua - khoang trong do luong duoc chinh xac bang
    gap_seconds du sau khi sap xep lai toan bang.
    """
    from lcx_core.tier_a import location_dropout as _location_dropout_rule

    real_p99_gap = _location_dropout_rule.detect(store).threshold

    hex_t = _get_table(store, tables, "public_driver_hex_tracking")
    real_hex = hex_t.filter(pl.col("source") != INJECTED_SOURCE)

    last_by_driver = (
        real_hex.sort("entered_current_hex_at")
        .group_by("driver_id", maintain_order=True)
        .agg(pl.all().last())
    )
    last_rows = {r["driver_id"]: r for r in last_by_driver.to_dicts()}
    driver_ids = list(last_rows)

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, gap_multiplier in SEVERITY_PARAMS["location_dropout"].items():
        gap_seconds = real_p99_gap * gap_multiplier
        chosen = rng.sample(driver_ids, min(n, len(driver_ids)))
        for driver_id in chosen:
            last = last_rows[driver_id]
            t0 = datetime.fromisoformat(last["entered_current_hex_at"])
            t_a = t0 + timedelta(seconds=5)
            t_b = t_a + timedelta(seconds=gap_seconds)
            hexval = last["current_hex"]

            for suffix, ts in (("a", t_a), ("b", t_b)):
                row_id = f"inject-location_dropout-{severity}-{counter}-{suffix}"
                iso = ts.isoformat()
                new_rows.append(
                    {
                        "schema_version": "1.0.0",
                        "source": INJECTED_SOURCE,
                        "id": row_id,
                        "driver_id": driver_id,
                        "init_hex": hexval,
                        "current_hex": hexval,
                        "last_hex": hexval,
                        "target_hex": hexval,
                        "last_seen_at": iso,
                        "entered_current_hex_at": iso,
                        "stay_duration_seconds": 60,
                        "reached_target": False,
                        "created_at": iso,
                        "updated_at": iso,
                        "tracking_status": "idle",
                    }
                )
            labels.append(
                InjectedLabel(
                    "location_dropout",
                    driver_id,
                    severity,
                    "entered_current_hex_at",
                    t_b.isoformat(),
                )
            )
            counter += 1

    new_df = _new_rows_frame(hex_t.columns, new_rows, hex_t.schema)
    updated = pl.concat([hex_t, new_df], how="vertical")
    return "public_driver_hex_tracking", updated, labels


# --------------------------------------------------------------------------
# 4. split_long_trip - trips.parquet (chain sau muc 1)
# --------------------------------------------------------------------------
def _inject_split_long_trip(
    store: GsmDataStore, tables: dict[str, pl.DataFrame], rng: random.Random, n: int
) -> tuple[str, pl.DataFrame, list[InjectedLabel]]:
    """Them 1 cap cuoc lien tiep cung driver_id+customer_id (moi), cung diem
    tra/don (ring_gap=0), cach nhau `gap_minutes` - muc do nghiem trong CANG
    NANG thi khoang cach thoi gian CANG NGAN (cang lo lieu).
    """
    trips = _get_table(store, tables, "trips")
    # loai cac cuoc co pickup_h3/drop_h3 dang placeholder "8amockNN" (khong
    # phai H3 that - ~50% du lieu that cua nhom driver "ce-*" dung dinh dang
    # nay) vi h3.grid_distance() trong detector se bao loi tren gia tri nay.
    completed = trips.filter(
        (pl.col("status") == "completed")
        & (pl.col("source") != INJECTED_SOURCE)
        & (pl.col("pickup_h3").str.len_chars() == 15)
        & (pl.col("drop_h3").str.len_chars() == 15)
    )
    driver_ids = completed["driver_id"].unique().to_list()

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, gap_minutes in SEVERITY_PARAMS["split_long_trip"].items():
        for _ in range(n):
            driver_id = rng.choice(driver_ids)
            template = (
                completed.filter(pl.col("driver_id") == driver_id)
                .sample(n=1, seed=rng.randint(0, 2**31 - 1))
                .to_dicts()[0]
            )
            customer_id = f"cust-inject-slt-{counter}"
            t_complete_a = _SYNTH_ANCHOR + timedelta(minutes=counter * 60)
            t_request_a = t_complete_a - timedelta(minutes=10)
            t_request_b = t_complete_a + timedelta(minutes=gap_minutes)
            t_complete_b = t_request_b + timedelta(minutes=8)

            trip_id_a = f"inject-split_long_trip-{severity}-{counter}-a"
            trip_id_b = f"inject-split_long_trip-{severity}-{counter}-b"

            row_a = dict(template)
            row_a.update(
                source=INJECTED_SOURCE,
                trip_id=trip_id_a,
                customer_id=customer_id,
                request_time=t_request_a.isoformat(),
                complete_time=t_complete_a.isoformat(),
            )
            row_b = dict(template)
            row_b.update(
                source=INJECTED_SOURCE,
                trip_id=trip_id_b,
                customer_id=customer_id,
                pickup_h3=template["drop_h3"],
                request_time=t_request_b.isoformat(),
                complete_time=t_complete_b.isoformat(),
            )
            new_rows.extend([row_a, row_b])
            labels.append(
                InjectedLabel("split_long_trip", driver_id, severity, "trip_id", trip_id_b)
            )
            counter += 1

    new_df = _new_rows_frame(trips.columns, new_rows, trips.schema)
    updated = pl.concat([trips, new_df], how="vertical")
    return "trips", updated, labels


# --------------------------------------------------------------------------
# 5. wait_fee_manipulation - driver_bike_stoppoints.parquet
# --------------------------------------------------------------------------
def _inject_wait_fee_manipulation(
    store: GsmDataStore, tables: dict[str, pl.DataFrame], rng: random.Random, n: int
) -> tuple[str, pl.DataFrame, list[InjectedLabel]]:
    sp = _get_table(store, tables, "driver_bike_stoppoints")
    driver_ids = (
        sp.filter(pl.col("source") != INJECTED_SOURCE)["driver_id"].unique().to_list()
    )

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, rush_count in SEVERITY_PARAMS["wait_fee_manipulation"].items():
        for _ in range(n):
            driver_id = rng.choice(driver_ids)
            local_date = _synthetic_date(counter)
            new_rows.append(
                {
                    "schema_version": "1.0.0",
                    "source": INJECTED_SOURCE,
                    "driver_id": driver_id,
                    "local_date": local_date,
                    "total_stoppoints": int(rush_count) + 5,
                    "total_stoppoints_rush_hour": int(rush_count),
                }
            )
            labels.append(
                InjectedLabel(
                    "wait_fee_manipulation", driver_id, severity, "local_date", local_date
                )
            )
            counter += 1

    new_df = _new_rows_frame(sp.columns, new_rows, sp.schema)
    updated = pl.concat([sp, new_df], how="vertical")
    return "driver_bike_stoppoints", updated, labels


# --------------------------------------------------------------------------
# 6. fake_ride_for_kpi - public_mission_earn_history.parquet
# --------------------------------------------------------------------------
def _inject_fake_ride_for_kpi(
    store: GsmDataStore, tables: dict[str, pl.DataFrame], rng: random.Random, n: int
) -> tuple[str, pl.DataFrame, list[InjectedLabel]]:
    """Them 1 tuan gia voi tong earn = mean + z*std TU CHINH lich su that cua
    driver do (khong dung so tuyet doi toan cuc) - dung cach detector tu hieu
    chuan cho tung driver.
    """
    earn = _get_table(store, tables, "public_mission_earn_history")
    real_earn = earn.filter(
        (pl.col("source") != INJECTED_SOURCE) & pl.col("order_time").is_not_null()
    )
    real_earn = real_earn.with_columns(
        pl.col("order_time").map_elements(_week_key, return_dtype=pl.Utf8).alias("week_key")
    )
    weekly = real_earn.group_by(["driver_id", "week_key"]).agg(
        pl.col("earn").sum().alias("weekly_earn")
    )

    driver_stats: dict[str, tuple[float, float]] = {}
    for driver_id, group in weekly.group_by("driver_id"):
        driver_id = driver_id[0] if isinstance(driver_id, tuple) else driver_id
        values = group["weekly_earn"].to_list()
        if len(values) >= 10:
            driver_stats[driver_id] = (
                statistics.mean(values),
                statistics.pstdev(values) or 1.0,
            )
    eligible_drivers = list(driver_stats)
    template = real_earn.row(0, named=True)

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, z in SEVERITY_PARAMS["fake_ride_for_kpi"].items():
        chosen = rng.sample(eligible_drivers, min(n, len(eligible_drivers)))
        for driver_id in chosen:
            mean, stdev = driver_stats[driver_id]
            target_weekly = max(mean + z * stdev, 1.0)
            order_time = (_SYNTH_ANCHOR + timedelta(weeks=counter)).isoformat()
            eid = f"inject-fake_ride_for_kpi-{severity}-{counter}"
            new_rows.append(
                {
                    "schema_version": "1.0.0",
                    "source": INJECTED_SOURCE,
                    "id": eid,
                    "created_at": order_time,
                    "updated_at": order_time,
                    "mission_id": template["mission_id"],
                    "order_status": "completed",
                    "driver_id": driver_id,
                    "service_type": template["service_type"],
                    "order_time": order_time,
                    "complete_time": order_time,
                    "travel_mode": template["travel_mode"],
                    "sap_contract_type": template["sap_contract_type"],
                    "type": "injected_fraud_probe",
                    "count_order": 1,
                    "count_stoppoint": 0,
                    "earn": int(target_weekly),
                    "description": "injected",
                    "reward_level": "1",
                }
            )
            week_key_val = _week_key(order_time)
            labels.append(
                InjectedLabel(
                    "fake_ride_for_kpi", driver_id, severity, "week_key", week_key_val
                )
            )
            counter += 1

    new_df = _new_rows_frame(earn.columns, new_rows, earn.schema)
    updated = pl.concat([earn, new_df], how="vertical")
    return "public_mission_earn_history", updated, labels


# --------------------------------------------------------------------------
# 7. ride_preview_selection - driver_statistic_daily.parquet
# --------------------------------------------------------------------------
def _inject_ride_preview_selection(
    store: GsmDataStore, tables: dict[str, pl.DataFrame], rng: random.Random, n: int
) -> tuple[str, pl.DataFrame, list[InjectedLabel]]:
    sd = _get_table(store, tables, "driver_statistic_daily")
    driver_ids = (
        sd.filter(pl.col("source") != INJECTED_SOURCE)["driver_id"].unique().to_list()
    )

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, rate in SEVERITY_PARAMS["ride_preview_selection"].items():
        for _ in range(n):
            driver_id = rng.choice(driver_ids)
            local_date = _synthetic_date(counter)
            accepted_count = 10
            new_rows.append(
                {
                    "schema_version": "1.0.0",
                    "source": INJECTED_SOURCE,
                    "local_date": local_date,
                    "driver_id": driver_id,
                    "completed_count": accepted_count,
                    "accepted_count": accepted_count,
                    "cancelled_count": 0,
                    "total_request_calculate_complete": accepted_count,
                    "total_request_calculate_cancel": 0,
                    "total_request_calculate_accept": accepted_count,
                    "count_cancel_not_relate_driver": 0,
                    "total_rating": 5.0 * accepted_count,
                    "total_order_rating": accepted_count,
                    "count_rating_5_star": accepted_count,
                    "acceptance_rate": rate,
                    "fulfillment_rate": 1.0,
                    "cancellation_rate": 0.0,
                }
            )
            labels.append(
                InjectedLabel(
                    "ride_preview_selection", driver_id, severity, "local_date", local_date
                )
            )
            counter += 1

    new_df = _new_rows_frame(sd.columns, new_rows, sd.schema)
    updated = pl.concat([sd, new_df], how="vertical")
    return "driver_statistic_daily", updated, labels


_INJECTORS: dict[str, Callable] = {
    "route_deviation": _inject_route_deviation,
    "gps_distance_inflation": _inject_gps_distance_inflation,
    "location_dropout": _inject_location_dropout,
    "split_long_trip": _inject_split_long_trip,
    "wait_fee_manipulation": _inject_wait_fee_manipulation,
    "fake_ride_for_kpi": _inject_fake_ride_for_kpi,
    "ride_preview_selection": _inject_ride_preview_selection,
}


def inject_all(
    store: GsmDataStore,
    seed: int = 42,
    n_per_severity: int = DEFAULT_N_PER_SEVERITY,
    patterns: list[str] | None = None,
) -> tuple[GsmDataStore, pl.DataFrame]:
    """Tiem hanh vi gian lan co tham so cho cac mau hinh chi dinh (mac dinh ca 7).

    Tra ve (injected_store, ground_truth):
      - injected_store: GsmDataStore MOI, cache duoc mo san cho cac bang bi
        tiem - goi injected_store.table(...) se KHONG dong cham file that tren
        dia. Cac bang KHONG bi tiem van doc lazy tu du lieu that nhu binh thuong.
      - ground_truth: DataFrame nhan doc lap (pattern, driver_id, severity,
        match_field, match_value) - dung de doi chieu voi RuleFlag.evidence
        trong scripts/run_injector_eval.py, KHONG duoc dua vao bat ky bang nao
        ma detector doc.
    """
    patterns = patterns or PATTERNS
    rng = random.Random(seed)
    tables: dict[str, pl.DataFrame] = {}
    all_labels: list[InjectedLabel] = []

    for pattern in patterns:
        fn = _INJECTORS[pattern]
        table_name, updated_df, labels = fn(store, tables, rng, n_per_severity)
        tables[table_name] = updated_df
        all_labels.extend(labels)

    injected_store = GsmDataStore(
        realdata_dir=store.realdata_dir,
        osrm_matrix_path=store.osrm_matrix_path,
        tables=store.tables,
    )
    for name, df in tables.items():
        injected_store._cache[name] = df

    ground_truth = (
        pl.DataFrame([label.to_dict() for label in all_labels], schema=_GT_SCHEMA)
        if all_labels
        else pl.DataFrame(schema=_GT_SCHEMA)
    )
    return injected_store, ground_truth
