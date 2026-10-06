"""The spatial layer: loading map data (ward polygons, swap stations, points of interest),
filling the hex grid, and grid arithmetic.

Source data is an offline OpenStreetMap snapshot under the research directory.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import h3
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

# --- Types ---


@dataclass(frozen=True)
class Station:
    node_id: int
    lat: float
    lon: float
    cell: str


@dataclass(frozen=True)
class Poi:
    lat: float
    lon: float
    kind: str  # hospital/university/college/marketplace/bus_station/mall/...
    cell: str


@dataclass
class Grid:
    """The hex grid covering the pilot area."""

    res: int
    res_report: int
    core_cells: list[str]  # core cells, whose centre lies inside the polygon
    core_set: frozenset[str]
    boundary: MultiPolygon
    stations: list[Station]
    pois: list[Poi]
    cell_centroid: dict[str, tuple[float, float]] = field(default_factory=dict)
    pois_by_cell: dict[str, list[Poi]] = field(default_factory=dict)

    def is_core(self, cell: str) -> bool:
        return cell in self.core_set

    def parent_report(self, cell: str) -> str:
        return h3.cell_to_parent(cell, self.res_report)


# --- Loading map data ---


def _osm_relation_to_polygon(element: dict) -> Polygon | MultiPolygon | None:
    """Join the outer ways of a boundary relation into a polygon.

    Each member way carries its own geometry, and the outer ways are disjoint segments, usually
    of two points, that have to be reassembled into a ring.
    """
    segments: list[LineString] = []
    for m in element.get("members", []):
        if m.get("type") != "way" or m.get("role") not in ("outer", ""):
            continue
        geom = m.get("geometry")
        if not geom or len(geom) < 2:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in geom]
        segments.append(LineString(coords))
    if not segments:
        return None
    # join the segments into a polygon
    merged_lines = unary_union(segments)
    polys = list(polygonize(merged_lines))
    if not polys:
        return None
    merged = unary_union([p.buffer(0) for p in polys if p.area > 0])
    if merged.is_empty:
        return None
    return merged


def load_boundary(geom_path: Path) -> MultiPolygon:
    """The area polygon: the union of the ward relations in the file."""
    data = json.loads(geom_path.read_text(encoding="utf-8"))
    polys: list[Polygon] = []
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        poly = _osm_relation_to_polygon(el)
        if poly is None:
            continue
        if isinstance(poly, MultiPolygon):
            polys.extend(list(poly.geoms))
        else:
            polys.append(poly)
    if not polys:
        raise ValueError(f"Không dựng được polygon từ {geom_path}")
    merged = unary_union(polys)
    if isinstance(merged, Polygon):
        return MultiPolygon([merged])
    return merged


def _load_points(path: Path) -> list[tuple[float, float, dict]]:
    """Position and tags for every locatable element: a node with coordinates, or a way or
    relation carrying a centre."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[float, float, dict]] = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el.get("type") == "node" and "lat" in el and "lon" in el:
            out.append((float(el["lat"]), float(el["lon"]), tags))
        elif "center" in el:
            c = el["center"]
            out.append((float(c["lat"]), float(c["lon"]), tags))
    return out


def _poi_kind(tags: dict) -> str | None:
    amenity = tags.get("amenity")
    if amenity in ("hospital", "university", "college", "marketplace", "bus_station"):
        return amenity
    if tags.get("shop") == "mall":
        return "mall"
    return None


# --- Polyfill H3 ---


def _polygon_to_cells(poly: Polygon, res: int) -> set[str]:
    """The cells whose centre falls inside the polygon, across all rings and holes."""
    exterior = [(lat, lng) for lng, lat in poly.exterior.coords]
    holes = [[(lat, lng) for lng, lat in interior.coords] for interior in poly.interiors]
    h3shape = h3.LatLngPoly(exterior, *holes)
    return set(h3.h3shape_to_cells(h3shape, res))


def build_grid(
    geom_path: Path,
    stations_path: Path,
    poi_path: Path,
    res: int,
    res_report: int,
) -> Grid:
    boundary = load_boundary(geom_path)

    core: set[str] = set()
    for poly in boundary.geoms:
        core |= _polygon_to_cells(poly, res)

    centroid: dict[str, tuple[float, float]] = {}
    for c in core:
        lat, lng = h3.cell_to_latlng(c)
        centroid[c] = (lat, lng)

    stations: list[Station] = []
    st_data = json.loads(stations_path.read_text(encoding="utf-8"))
    for n in st_data.get("elements", []):
        if n.get("type") != "node":
            continue
        lat, lon = float(n["lat"]), float(n["lon"])
        cell = h3.latlng_to_cell(lat, lon, res)
        stations.append(Station(node_id=int(n["id"]), lat=lat, lon=lon, cell=cell))
        centroid.setdefault(cell, h3.cell_to_latlng(cell))

    pois: list[Poi] = []
    for lat, lon, tags in _load_points(poi_path):
        kind = _poi_kind(tags)
        if kind is None:
            continue
        cell = h3.latlng_to_cell(lat, lon, res)
        pois.append(Poi(lat=lat, lon=lon, kind=kind, cell=cell))
        centroid.setdefault(cell, h3.cell_to_latlng(cell))

    pois_by_cell: dict[str, list[Poi]] = {}
    for p in pois:
        pois_by_cell.setdefault(p.cell, []).append(p)

    return Grid(
        res=res,
        res_report=res_report,
        core_cells=sorted(core),
        core_set=frozenset(core),
        boundary=boundary,
        stations=stations,
        pois=pois,
        cell_centroid=centroid,
        pois_by_cell=pois_by_cell,
    )


# --- Grid arithmetic ---


def grid_distance(a: str, b: str) -> int:
    """Grid steps between two cells, symmetric; -1 when they are not connected."""
    try:
        return h3.grid_distance(a, b)
    except Exception:
        return -1


def grid_disk(cell: str, k: int) -> list[str]:
    return list(h3.grid_disk(cell, k))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class RoadMatrix:
    """Real road factors per cell pair, from an offline routing matrix.

    The simulation previously approximated every distance as straight-line times a single
    detour constant for the whole district. Real streets are not uniform: around the lake, along
    one-way streets and through alleys, the true detour varies sharply by pair.

    The design keeps the single-source-of-distance contract:

        road_km = haversine(endpoint A, endpoint B) * factor(cell_A, cell_B)

    where the factor is the routed distance over the straight-line distance between centroids,
    clamped to [1.0, 3.5]: below one is a data error, above 3.5 an outlier from a bridge or a
    local restriction. A missing pair falls back to the configured detour, which is countable and
    keeps the model offline-first.
    """

    FACTOR_MIN, FACTOR_MAX = 1.0, 3.5
    _MIN_BASE_KM = 0.05          # too close for a stable ratio: treat the pair as missing

    def __init__(
        self,
        factors: dict[tuple[str, str], float],
        tiers: dict[tuple[str, str], str] | None = None,
        speeds: dict[tuple[str, str], float] | None = None,
    ):
        self._f = factors
        self._tiers = tiers or {}
        self._speeds = speeds or {}
        self.hits = 0
        self.misses = 0

    @classmethod
    def load(cls, path, centroids: dict[str, tuple[float, float]]) -> "RoadMatrix":
        import polars as pl
        df = pl.read_parquet(path)
        factors: dict[tuple[str, str], float] = {}
        tiers: dict[tuple[str, str], str] = {}
        speeds: dict[tuple[str, str], float] = {}

        has_tier = "road_tier" in df.columns
        has_speed = "inferred_speed_kmh" in df.columns
        tier_list = df["road_tier"].to_list() if has_tier else None
        speed_list = df["inferred_speed_kmh"].to_list() if has_speed else None

        for idx, (a, b, km) in enumerate(zip(df["cell_from"], df["cell_to"], df["road_km"])):
            pair = (a, b)
            if has_tier and tier_list is not None:
                tiers[pair] = str(tier_list[idx])
            if has_speed and speed_list is not None:
                speeds[pair] = float(speed_list[idx])

            ca, cb = centroids.get(a), centroids.get(b)
            if ca is None:
                ca = h3.cell_to_latlng(a)
            if cb is None:
                cb = h3.cell_to_latlng(b)
            base = haversine_km(ca[0], ca[1], cb[0], cb[1])
            if base < cls._MIN_BASE_KM or km <= 0:
                continue
            factors[pair] = max(cls.FACTOR_MIN, min(cls.FACTOR_MAX, km / base))
        return cls(factors, tiers=tiers, speeds=speeds)

    def factor(self, a: str, b: str, default: float) -> float:
        """The road factor for a pair, or the configured detour when the pair is absent."""
        f = self._f.get((a, b))
        if f is None:
            self.misses += 1
            return default
        self.hits += 1
        return f

    def road_tier(self, a: str, b: str, default: str = "LOCAL_STREET") -> str:
        """Phân loại cấp độ đường giữa cặp ô: EXPRESSWAY, ARTERIAL, hoặc LOCAL_STREET."""
        return self._tiers.get((a, b), default)

    def inferred_speed(self, a: str, b: str, default: float = 35.0) -> float:
        """Vận tốc cơ sở OSRM giữa cặp ô (km/h)."""
        return self._speeds.get((a, b), default)

    @property
    def fallback_rate(self) -> float:
        tot = self.hits + self.misses
        return self.misses / tot if tot else 0.0


_ROAD_CACHE: dict[str, RoadMatrix] = {}


def load_road_matrix(cfg, grid: Grid) -> RoadMatrix | None:
    """Load the road matrix from config, cached per path at module level.

    The matrix holds on the order of a hundred thousand pairs while sweeps and gates run dozens
    of simulations, so reparsing per run is pure waste. The file is static, fetched once, which
    makes caching by path safe.

    Disabling routing, or a missing file, yields no matrix and every call site falls back to the
    configured detour, restoring the previous behaviour figure for figure.
    """
    rcfg = cfg.get("routing", {}) or {}
    if not rcfg.get("enabled", False):
        return None
    path = cfg.resolve_path("world.data_dir") / str(rcfg.get("matrix_file",
                                                             "osrm_matrix_dd.parquet"))
    key = str(path)
    if key in _ROAD_CACHE:
        return _ROAD_CACHE[key]
    if not path.exists():
        return None
    rm = RoadMatrix.load(path, grid.cell_centroid)
    _ROAD_CACHE[key] = rm
    return rm


def cell_distance_km(grid: Grid, a: str, b: str) -> float:
    """Approximate distance between two cells, centroid to centroid."""
    if a == b:
        return 0.0
    la = grid.cell_centroid.get(a) or h3.cell_to_latlng(a)
    lb = grid.cell_centroid.get(b) or h3.cell_to_latlng(b)
    return haversine_km(la[0], la[1], lb[0], lb[1])


# --- Continuous coordinates inside a cell, for movement and display ---

_KM_PER_DEG_LAT = 110.574
_EDGE_KM_CACHE: dict[int, float] = {}


def _cell_inradius_km(res: int) -> float:
    """A safe radius for sampling inside a cell. The apothem is edge times root-three-halves;
    a smaller coefficient leaves margin for the flat-earth approximation and for the fact that
    the cells are not perfectly regular."""
    if res not in _EDGE_KM_CACHE:
        _EDGE_KM_CACHE[res] = float(h3.average_hexagon_edge_length(res, unit="km"))
    return _EDGE_KM_CACHE[res] * 0.72


def offset_latlng(lat: float, lon: float, dx_km: float, dy_km: float) -> tuple[float, float]:
    """Offset a position east and north by the given kilometres. A flat approximation, which is
    adequate at cell scale."""
    dlat = dy_km / _KM_PER_DEG_LAT
    dlon = dx_km / (111.320 * math.cos(math.radians(lat)) or 1e-9)
    return lat + dlat, lon + dlon


def diem_quen(seed: int, actor_id: int, cell: str, clat: float, clon: float,
              r_km: float) -> tuple[float, float]:
    """A driver's habitual waiting spot inside a cell: a deterministic point in place of the
    cell centre.

    Why it exists: parking every waiting driver on the exact centre made most positions coincide,
    which made arrival times tie exactly, which made "who gets the trip" a function of index
    order inside the assignment solver. The fairness gates were then reading array order rather
    than anything about the market.

    Why a hash and not a random draw: it consumes no draw from any random stream, so paired A/B
    runs keep their common random numbers.

    The arithmetic is byte-for-byte load-bearing. The per-seed fingerprint gate depends on the
    exact hash format, the byte order and the digest size; changing one character produces a
    different world and turns `tests/test_cho_dung_quen_r25.py` red.

    The offset constant is borrowed from the point-of-interest jitter rather than introduced as a
    free parameter: if a passenger address is already a landmark plus that much noise, a driver's
    waiting spot deserves the same. A test chains the two so they cannot drift apart.

    Known limit: multi-day runs build a world per day from a per-day seed, so the spot is
    habitual within a day and changes between days. Within a day is enough to break the ties, and
    common random numbers survive; carrying it across days would mean threading the base seed
    into the world, a larger change that has not been measured.
    """
    if r_km <= 0.0:
        return (clat, clon)
    h = hashlib.blake2b(f"{seed}|{actor_id}|{cell}".encode(), digest_size=16).digest()
    u = int.from_bytes(h[:8], "big") / 2**64
    th = 2 * math.pi * (int.from_bytes(h[8:], "big") / 2**64)
    r = r_km * math.sqrt(u)
    return offset_latlng(clat, clon, r * math.cos(th), r * math.sin(th))


def sample_point_in_cell(grid: Grid, cell: str, rng, poi_bias: float = 0.5,
                         poi_jitter_km: float = 0.025) -> tuple[float, float]:
    """Sample a plausible street address inside a cell.

    With the configured probability, and where the cell has points of interest, the point snaps
    to one of them with small jitter, the way real pickups cluster around hospitals, schools and
    markets. Otherwise it is uniform within the inscribed disc around the centroid, which keeps
    it inside the hexagon. The generator is deterministic."""
    pois = grid.pois_by_cell.get(cell)
    if pois and rng.random() < poi_bias:
        p = pois[rng.integers(len(pois))]
        r = poi_jitter_km * math.sqrt(rng.random())
        th = 2 * math.pi * rng.random()
        return offset_latlng(p.lat, p.lon, r * math.cos(th), r * math.sin(th))
    clat, clon = grid.cell_centroid.get(cell) or h3.cell_to_latlng(cell)
    r = _cell_inradius_km(grid.res) * math.sqrt(rng.random())
    th = 2 * math.pi * rng.random()
    return offset_latlng(clat, clon, r * math.cos(th), r * math.sin(th))
