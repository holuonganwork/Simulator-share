"""fare_breakdown - hang muc 8 "Chien luoc lam giau du lieu" (tham so simulator:
thoi tiet lien quan gia tien). Mo khoa mau hinh "thong dong tao tang gia gia"
(dang Thieu han trong tai lieu chien luoc goc, muc 02).

GIOI HAN QUAN TRONG (ghi lai thay vi giau di, dung tinh than route_deviation.py):
scenario sinh trips.parquet hien co la `dry_weekday` voi `environment.rain.series
= []` (xem GSM_SIMULATOR-main/configs/pilot_dongda.yaml muc 367-392) - TUC LA
khong co mua nao xay ra trong luc sinh du lieu that. trips.parquet cung KHONG
luu lai cuong do mua tai thoi diem tung cuoc (do la tham so moi truong toan
cuc luc mo phong, khong phai truong cua tung dong). Vi vay KHONG THE tai tao
lai chinh xac lich su mua that cho cac cuoc da co.

Module nay tach ro 2 phan:
  - dow_multiplier: THAT, suy truc tiep tu request_time (ngay trong tuan) va
    gia tri cau hinh that trong pilot_dongda.yaml (environment.dow.level).
  - rain_multiplier: MO PHONG LAI (khong phai tai tao) - sinh mot chuoi mua
    theo (gio, o hex tho res-7) bang RNG co seed co dinh, roi ap dung DUNG
    cong thuc bao hoa trong config (environment.rain.demand: delta_peak=0.22,
    r_peak_mmph=8.0) - chi tham so cua cong thuc la that, du lieu mua dau vao
    la gia lap de CHUNG MINH duoc co che, khong phai suy luan nguoc lich su that.
"""

from __future__ import annotations

import random
from datetime import datetime

import h3
import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.fraud_injector.injector import INJECTED_SOURCE, InjectedLabel

PATTERN = "artificial_surge_collusion"

DOW_LEVEL = {"weekday": 1.0, "friday": 1.10, "weekend": 1.05}  # that, tu pilot_dongda.yaml
RAIN_DELTA_PEAK = 0.22  # that, environment.rain.demand.delta_peak
RAIN_R_PEAK_MMPH = 8.0  # that, environment.rain.demand.r_peak_mmph

SEVERITY_PARAMS: dict[str, int] = {"light": 3, "medium": 8, "heavy": 15}  # so cuoc surge them/ngay


def _dow_bucket(iso_ts: str) -> str:
    weekday = datetime.fromisoformat(iso_ts).weekday()  # 0=Mon .. 6=Sun
    if weekday == 4:
        return "friday"
    if weekday >= 5:
        return "weekend"
    return "weekday"


def _zone_prefix(hex_id: str) -> str:
    """Coarse-grain hex res-9 ve res-7 (~5x5 o res-9 gop 1) lam 'khu vuc' tho -
    KHONG mo rong pham vi dia ly, chi gop nhom trong dung Dong Da hien co."""
    try:
        return h3.cell_to_parent(hex_id, 7)
    except Exception:
        return hex_id


def _simulated_rain_mm(zone: str, hour: int, rng: random.Random) -> float:
    """Mua gia lap THEO (zone, gio) - dinh tinh: mua de xay ra hon vao 16-19h
    (gio tan tam, khop quan sat thuc te ve mua rao chieu o Ha Noi), da so gio
    khac khong mua. Seed rieng theo (zone,hour) de KET QUA ON DINH qua nhieu
    lan goi cung du lieu dau vao (khong doi moi lan chay)."""
    local_rng = random.Random(f"{zone}-{hour}-{rng.random()}")
    base_prob = 0.35 if 16 <= hour <= 19 else 0.08
    if local_rng.random() > base_prob:
        return 0.0
    return local_rng.uniform(2.0, 18.0)


def _rain_multiplier(rain_mm: float) -> float:
    """Dung DUNG cong thuc bao hoa cua config (dang x/(x+k)) - khong bia tham
    so moi."""
    return 1.0 + RAIN_DELTA_PEAK * (rain_mm / (rain_mm + RAIN_R_PEAK_MMPH))


def compute_fare_breakdown(store: GsmDataStore, seed: int = 42) -> pl.DataFrame:
    """Tra ve 1 dong/cuoc THAT (status='completed'): trip_id, driver_id,
    pickup_h3, hour_of_day, dow_bucket, dow_multiplier, zone, rain_mm,
    rain_multiplier, surge_multiplier, source."""
    rng = random.Random(seed)
    trips = store.table("trips").filter(pl.col("status") == "completed")

    rows: list[dict] = []
    for row in trips.select(["trip_id", "driver_id", "pickup_h3", "request_time"]).iter_rows(named=True):
        ts = datetime.fromisoformat(row["request_time"])
        dow = _dow_bucket(row["request_time"])
        zone = _zone_prefix(row["pickup_h3"])
        rain_mm = _simulated_rain_mm(zone, ts.hour, rng)
        rain_mult = _rain_multiplier(rain_mm)
        dow_mult = DOW_LEVEL[dow]
        rows.append(
            {
                "trip_id": row["trip_id"],
                "driver_id": row["driver_id"],
                "pickup_h3": row["pickup_h3"],
                "zone": zone,
                "local_date": ts.date().isoformat(),
                "hour_of_day": ts.hour,
                "dow_bucket": dow,
                "dow_multiplier": dow_mult,
                "rain_mm": round(rain_mm, 2),
                "rain_multiplier": round(rain_mult, 4),
                "surge_multiplier": round(dow_mult * rain_mult, 4),
                "source": "SIM",
            }
        )
    return pl.DataFrame(rows)


def inject_artificial_surge(
    store: GsmDataStore, baseline: pl.DataFrame, seed: int = 43, n_per_severity: int = 15
) -> tuple[pl.DataFrame, list[InjectedLabel]]:
    """Them cac cuoc gia CHO 1 driver, TAP TRUNG vao dung 1 (zone, gio) dang co
    surge cao trong ngay do - dai dien mot driver "vet" het cuoc surge trong 1
    khung gio/khu vuc, dau hieu tho cua thong dong/giu don. Dung osrm_matrix de
    lay cap hex that (cung ky thuat voi fraud_injector.route_deviation)."""
    rng = random.Random(seed)
    osrm_pairs = store.osrm_matrix.filter(pl.col("road_km") >= 0.3).to_dicts()
    driver_ids = baseline["driver_id"].unique().to_list()

    high_surge = baseline.filter(pl.col("surge_multiplier") >= baseline["surge_multiplier"].quantile(0.90))
    candidate_windows = high_surge.select(["zone", "hour_of_day"]).unique().to_dicts()

    new_rows: list[dict] = []
    labels: list[InjectedLabel] = []
    counter = 0
    for severity, n_trips in SEVERITY_PARAMS.items():
        for _ in range(n_per_severity):
            driver_id = rng.choice(driver_ids)
            window = rng.choice(candidate_windows)
            zone, hour = window["zone"], window["hour_of_day"]
            local_date = f"2099-{(counter // 28) % 12 + 1:02d}-{(counter % 28) + 1:02d}"
            # gia dinh dang mua RAT to trong khung nay - can vuot han p90 THAT
            # cua toan bo phan phoi surge_multiplier (da gap truong hop rain_mm
            # o bien duoi 10-11mm cho ra rain_multiplier NGAY DUOI nguong p90
            # that, khien don vi tiem khong duoc dem la "surge cao" - nang bien
            # duoi len de tranh boundary case nay).
            rain_mm = rng.uniform(15.0, 22.0)
            rain_mult = _rain_multiplier(rain_mm)
            dow_mult = 1.0
            for j in range(n_trips):
                pair = rng.choice(osrm_pairs)
                trip_id = f"inject-{PATTERN}-{severity}-{counter}-{j}"
                new_rows.append(
                    {
                        "trip_id": trip_id,
                        "driver_id": driver_id,
                        "pickup_h3": pair["cell_from"],
                        "zone": zone,
                        "local_date": local_date,
                        "hour_of_day": hour,
                        "dow_bucket": "weekday",
                        "dow_multiplier": dow_mult,
                        "rain_mm": round(rain_mm, 2),
                        "rain_multiplier": round(rain_mult, 4),
                        "surge_multiplier": round(dow_mult * rain_mult, 4),
                        "source": INJECTED_SOURCE,
                    }
                )
            labels.append(InjectedLabel(PATTERN, driver_id, severity, "local_date", local_date))
            counter += 1

    return pl.DataFrame(new_rows, schema=baseline.schema), labels
