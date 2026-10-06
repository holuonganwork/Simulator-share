"""Nguon du lieu khong gian THAT cho `event_sim` - thay the "the gioi do choi"
cu (1 tam `HANOI_CENTER` + jitter +-0.05 do, 3 tram sac gia `station-dd-01..03`,
bounding-box doan mo). Tai dung 2 nguon da co san cua repo:

  - `research/simulation/data/osrm_matrix_hanoi.parquet`: tap cac o H3 Res 7
    THAT su duoc OSRM khao sat (~925 o phu kin Ha Noi sau sap nhap 2025), truy
    cap qua `GsmDataStore.osrm_matrix` (`configs/data_sources.yaml`). Dung tap
    o nay lam "luoi that" thay vi tu dung lai polygon 194 phuong/xa.
  - `research/simulation/data/batt_hanoi.json`: 399 tram sac/doi pin THAT (OSM),
    qua `GsmDataStore.battery_stations`.

Lazy + cache 1 lan/tien trinh (`@lru_cache`) vi ma tran ~855K dong. Neu thieu du
lieu ban do (vd moi truong test khong co `research/simulation/data/`), moi ham o
day nem `DataSourceError` - noi goi PHAI tu quyet dinh fallback (xem
`vehicle_scenarios.py`: fallback ve HANOI_CENTER cu de khong lam vo test/CI
offline), giu dung triet ly "thieu file = tro ve hanh vi cu" cua
`gsm_sim.geo.load_road_matrix`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache

import h3

from lcx_core.data.gsm_loader import load_data_store

# Ban kinh jitter trong 1 o Res 7 (canh o ~1.4km) - giu diem sinh ra nam chac
# chan trong o, khong tran sang o ben canh.
_CELL_JITTER_DEG = 0.003


@dataclass(frozen=True)
class HanoiGeo:
    cells: tuple[str, ...]
    cell_set: frozenset[str]
    centroid: dict[str, tuple[float, float]]
    lat_range: tuple[float, float]
    lng_range: tuple[float, float]
    station_ids: tuple[str, ...]


@lru_cache(maxsize=1)
def load() -> HanoiGeo:
    """Nap 1 lan/tien trinh. Nem `DataSourceError` (tu `gsm_loader`) neu thieu
    file ban do - noi goi tu quyet dinh fallback, khong nuot loi o day."""
    store = load_data_store()
    df = store.osrm_matrix
    cell_set = frozenset(set(df["cell_from"].to_list()) | set(df["cell_to"].to_list()))
    cells = tuple(sorted(cell_set))
    centroid = {c: h3.cell_to_latlng(c) for c in cells}
    lats = [ll[0] for ll in centroid.values()]
    lngs = [ll[1] for ll in centroid.values()]
    station_ids = tuple(f"station-{s['node_id']}" for s in store.battery_stations)
    return HanoiGeo(
        cells=cells,
        cell_set=cell_set,
        centroid=centroid,
        lat_range=(min(lats), max(lats)),
        lng_range=(min(lngs), max(lngs)),
        station_ids=station_ids,
    )


def sample_point(rng: random.Random) -> tuple[float, float]:
    """Mot diem THAT ngau nhien tren luoi Res 7 da duoc OSRM khao sat (thay the
    jitter quanh 1 tam gia dinh cu) - lat, lng."""
    geo = load()
    cell = rng.choice(geo.cells)
    lat, lng = geo.centroid[cell]
    return lat + rng.uniform(-_CELL_JITTER_DEG, _CELL_JITTER_DEG), lng + rng.uniform(
        -_CELL_JITTER_DEG, _CELL_JITTER_DEG
    )


def is_in_hanoi(lat: float, lng: float) -> bool:
    """Kiem tra CHINH XAC bang thanh vien o H3 (khong phai bounding-box tho) -
    o chua toa do co nam trong tap o Res 7 THAT duoc OSRM phu hay khong."""
    geo = load()
    return h3.latlng_to_cell(lat, lng, 7) in geo.cell_set


def station_ids() -> tuple[str, ...]:
    return load().station_ids


def lat_range() -> tuple[float, float]:
    return load().lat_range


def lng_range() -> tuple[float, float]:
    return load().lng_range
