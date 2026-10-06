"""Test lớp không gian: polyfill H3, nạp trạm/POI, phép toán lưới."""

from pathlib import Path

import pytest

from gsm_sim import geo

DATA = Path(__file__).resolve().parent.parent / "research" / "simulation" / "data"


@pytest.fixture(scope="module")
def grid():
    return geo.build_grid(
        geom_path=DATA / "hanoi_geom.json",
        stations_path=DATA / "batt_hanoi.json",
        poi_path=DATA / "poi_hanoi.json",
        res=7,
        res_report=6,
    )


def test_core_cells_count(grid):
    # Toàn Hà Nội: ~925 cells lõi Res 7
    assert 800 <= len(grid.core_cells) <= 1100, len(grid.core_cells)


def test_stations_loaded(grid):
    # 399 trạm đổi pin / sạc VinFast toàn Hà Nội
    assert len(grid.stations) >= 300, len(grid.stations)
    # phần lớn trạm nằm trong lõi
    in_core = sum(1 for s in grid.stations if grid.is_core(s.cell))
    assert in_core >= 200, in_core


def test_pois_loaded(grid):
    kinds = {}
    for p in grid.pois:
        kinds[p.kind] = kinds.get(p.kind, 0) + 1
    assert kinds.get("hospital", 0) >= 20, kinds
    assert kinds.get("university", 0) >= 10, kinds


def test_grid_distance_symmetric(grid):
    a, b = grid.core_cells[0], grid.core_cells[len(grid.core_cells) // 2]
    assert geo.grid_distance(a, b) == geo.grid_distance(b, a)
    assert geo.grid_distance(a, a) == 0


def test_cell_distance_km(grid):
    a, b = grid.core_cells[0], grid.core_cells[-1]
    d = geo.cell_distance_km(grid, a, b)
    # Toàn Hà Nội rộng ~60-80 km → khoảng cách 2 cell phải nằm trong dải này
    assert 0.0 < d < 100.0, d
    assert geo.cell_distance_km(grid, a, a) == 0.0
