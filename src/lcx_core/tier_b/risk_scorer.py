"""Cham diem rui ro tong hop (Tier B / Pha 2).

Vi sao IsolationForest thay vi mo hinh giam sat (XGBoost/LightGBM nhu
docs/architecture.md muc 03 de xuat cho tuong lai): nhan that su duy nhat hien
co la 122 case 'abnormal_cancel' (public_frauds, tu chinh Tier A tao ra) - CHUA
DU va CHUA DOC LAP de huan luyen mot bo phan loai giam sat co y nghia (se hoc
lai chinh luat da dung de tao nhan). Isolation Forest la lua chon THANH THAT hon
cho Pha 2 hien tai: no cham diem BAT THUONG tren toan bo dac trung nhieu chieu
(feature_builder.build_feature_matrix) ma khong can nhan, giup bat duoc driver
"bat thuong tren NHIEU tin hieu cung luc" ke ca khi tung tin hieu rieng le chua
vuot nguong Tier A - dung dung vai tro "Tier B" mo ta trong chien luoc (giam
false positive so voi xet tung nguong doc lap).

Khi co it nhat vai chuc case da duoc quan ly resolve_case(verdict='confirmed')
(xem labels/label_store.py), nen thay the bang mo hinh giam sat that su - luc do
moi co nhan doc lap voi luat sinh feature.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
from sklearn.ensemble import IsolationForest

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_b.feature_builder import build_feature_matrix

DEFAULT_CONTAMINATION = 0.02  # gia dinh ~2% driver la bat thuong - dieu chinh theo van hanh
RANDOM_STATE = 42


@dataclass(frozen=True)
class DriverRiskScore:
    driver_id: str
    risk_score: float          # cang cao cang bat thuong (da chuan hoa ve [0, 1])
    top_contributing_features: list[str]


def score_all_drivers(
    store: GsmDataStore,
    contamination: float = DEFAULT_CONTAMINATION,
) -> list[DriverRiskScore]:
    features = build_feature_matrix(store)
    if features.height == 0:
        return []

    driver_ids = features["driver_id"].to_list()
    feature_cols = [c for c in features.columns if c != "driver_id"]
    X = features.select(feature_cols).to_numpy()

    model = IsolationForest(
        contamination=contamination, random_state=RANDOM_STATE, n_estimators=200
    )
    model.fit(X)
    # decision_function: cang AM cang bat thuong -> dao dau va chuan hoa ve [0, 1]
    raw = -model.decision_function(X)
    lo, hi = raw.min(), raw.max()
    normalized = (raw - lo) / (hi - lo) if hi > lo else np.zeros_like(raw)

    results = []
    for i, driver_id in enumerate(driver_ids):
        row = X[i]
        # dac trung dong gop nhieu nhat = cac cot co gia tri > 0 lon nhat cho driver nay
        contributing = sorted(
            zip(feature_cols, row), key=lambda kv: kv[1], reverse=True
        )
        top = [name for name, val in contributing if val > 0][:3]
        results.append(
            DriverRiskScore(
                driver_id=driver_id,
                risk_score=round(float(normalized[i]), 4),
                top_contributing_features=top,
            )
        )

    return sorted(results, key=lambda r: r.risk_score, reverse=True)


def score_to_dataframe(scores: list[DriverRiskScore]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "driver_id": s.driver_id,
                "risk_score": s.risk_score,
                "top_contributing_features": ", ".join(s.top_contributing_features),
            }
            for s in scores
        ]
    )
