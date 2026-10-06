"""Tang doc du lieu tu repo GSM_SIMULATOR.

GSM_FRAUD_DETECTION KHONG tu sinh du lieu - no doc truc tiep 13 bang Parquet L1R
va ma tran OSRM da co san trong repo GSM_SIMULATOR (sibling repo). Duong dan lay
tu configs/data_sources.yaml, co the override bang bien moi truong GSM_SIM_ROOT
(vi du khi CI checkout GSM_SIMULATOR o vi tri khac).

Nhac lai (xem docs/gsm-simulator-alignment.md): moi bang o day co cot `source` =
"MOCK" hoac "INFERRED" - la du lieu tong hop co shape/quy mo giong san xuat that
cua Green SM, KHONG phai du lieu san xuat that. Cac ham trong module nay khong
che giau dieu do - `GsmDataStore.describe()` in lai nguyen nhan `source` cho tung
bang de khong ai nham la dang phan tich du lieu that.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import polars as pl
import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "data_sources.yaml"


class DataSourceError(RuntimeError):
    """Khong tim thay du lieu nguon - kem huong dan khac phuc trong message."""


@dataclass
class GsmDataStore:
    """Nap va cache cac bang tu GSM_SIMULATOR. Lazy: chi doc khi duoc goi lan dau."""

    realdata_dir: Path
    osrm_matrix_path: Path
    tables: dict[str, str]
    battery_stations_path: Optional[Path] = None
    _cache: dict[str, pl.DataFrame] = field(default_factory=dict, repr=False)

    def table(self, name: str) -> pl.DataFrame:
        """Tra ve mot bang L1R theo ten logic (vd 'trips', 'driver_statistic_daily')."""
        if name in self._cache:
            return self._cache[name]
        if name not in self.tables:
            raise KeyError(
                f"Khong biet bang '{name}'. Cac bang hop le: {sorted(self.tables)}"
            )
        path = self.realdata_dir / self.tables[name]
        if not path.exists():
            raise DataSourceError(
                f"Khong thay file {path}. Kiem tra bien moi truong GSM_SIM_ROOT hoac "
                f"configs/data_sources.yaml co tro dung repo GSM_SIMULATOR khong."
            )
        df = pl.read_parquet(path)
        self._cache[name] = df
        return df

    @property
    def osrm_matrix(self) -> pl.DataFrame:
        if "__osrm__" in self._cache:
            return self._cache["__osrm__"]
        if not self.osrm_matrix_path.exists():
            raise DataSourceError(
                f"Khong thay ma tran OSRM tai {self.osrm_matrix_path}."
            )
        df = pl.read_parquet(self.osrm_matrix_path)
        self._cache["__osrm__"] = df
        return df

    @property
    def battery_stations(self) -> list[dict]:
        """399 tram sac/doi pin THAT (node OSM: node_id, lat, lon) - nguon vi tri
        cho lcx_core.event_sim (xem hanoi_geo.py). Cache trong tien trinh nhu cac
        bang khac."""
        if "__stations__" in self._cache:
            return self._cache["__stations__"]
        if self.battery_stations_path is None or not self.battery_stations_path.exists():
            raise DataSourceError(
                f"Khong thay danh sach tram sac tai {self.battery_stations_path}."
            )
        data = json.loads(self.battery_stations_path.read_text(encoding="utf-8"))
        rows = [
            {"node_id": int(el["id"]), "lat": float(el["lat"]), "lon": float(el["lon"])}
            for el in data.get("elements", [])
            if el.get("type") == "node" and "lat" in el and "lon" in el
        ]
        self._cache["__stations__"] = rows
        return rows

    def describe(self) -> str:
        """In tom tat nguon goc + so dong cua tung bang - dung khi audit/bao cao."""
        lines = [f"GSM_SIMULATOR root du lieu: {self.realdata_dir}"]
        for name in sorted(self.tables):
            try:
                df = self.table(name)
                sources = (
                    df["source"].unique().to_list() if "source" in df.columns else ["?"]
                )
                lines.append(f"  - {name}: {df.height} dong, source={sources}")
            except DataSourceError as exc:
                lines.append(f"  - {name}: LOI ({exc})")
        return "\n".join(lines)


def _resolve_gsm_sim_root(config: dict) -> Path:
    env_override = os.environ.get("GSM_SIM_ROOT")
    if env_override:
        return Path(env_override).expanduser().resolve()
    project_root = _CONFIG_PATH.parents[1]
    return (project_root / config["gsm_simulator_root"]).resolve()


@lru_cache(maxsize=1)
def load_data_store(config_path: Optional[str] = None) -> GsmDataStore:
    """Diem vao duy nhat de lay GsmDataStore. Ket qua duoc cache trong tien trinh."""
    path = Path(config_path) if config_path else _CONFIG_PATH
    with open(path, encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    gsm_root = _resolve_gsm_sim_root(config)
    if not gsm_root.exists():
        raise DataSourceError(
            f"Khong thay repo GSM_SIMULATOR tai {gsm_root}. Neu ban clone GSM_SIMULATOR "
            "o vi tri khac 'la thu muc anh em cua GSM_FRAUD_DETECTION', hay dat bien moi "
            "truong GSM_SIM_ROOT tro dung thu muc goc cua no."
        )

    realdata_dir = gsm_root / config["realdata_dir"]
    if not realdata_dir.exists() and (gsm_root / "data" / "mock" / "realdata-v1").exists():
        realdata_dir = gsm_root / "data" / "mock" / "realdata-v1"

    battery_stations_path = config.get("battery_stations_path")
    return GsmDataStore(
        realdata_dir=realdata_dir,
        osrm_matrix_path=gsm_root / config["osrm_matrix_path"],
        tables=dict(config["tables"]),
        battery_stations_path=(gsm_root / battery_stations_path) if battery_stations_path else None,
    )
