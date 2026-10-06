"""Ha tang chung cho Tier A: kieu du lieu ket qua + ham tinh nguong percentile.

Trong he thong that, Tier A la lop luat nguong cung chay o do tre <100ms (xem
docs/architecture.md muc 03). O day chung ta khong co pipeline real-time; moi
detector la mot ham thuan tuy nhan vao DataFrame va tra ve danh sach RuleFlag,
de co the:
  - goi truc tiep tu scripts/run_tier_a_scan.py (batch, mo phong "chay dinh ky")
  - goi tu agent tool get_driver_risk_profile / check_location_integrity ...
    (xem src/lcx_core/agent/tools/) khi can bang chung cho mot case cu the
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuleFlag:
    """Mot lan mot driver-ngay/chuyen vuot nguong cua mot luat Tier A."""

    pattern: str          # dung enum trong schemas/case/fraud_case.schema.json
    driver_id: str
    signal_value: float
    threshold: float
    evidence: dict[str, Any] = field(default_factory=dict)
    caveat: str | None = None   # non-null neu day la proxy "THO", xem configs/tiers.yaml

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "driver_id": self.driver_id,
            "signal_value": self.signal_value,
            "threshold": self.threshold,
            "evidence": self.evidence,
            "caveat": self.caveat,
        }


@dataclass(frozen=True)
class RuleResult:
    """Ket qua chay mot detector: nguong da dung + danh sach flag."""

    pattern: str
    method: str            # vd "percentile_p99"
    threshold: float
    n_evaluated: int
    flags: list[RuleFlag]

    @property
    def n_flagged(self) -> int:
        return len(self.flags)

    @property
    def flag_rate(self) -> float:
        return self.n_flagged / self.n_evaluated if self.n_evaluated else 0.0


def percentile_threshold(values: list[float], percentile: float) -> float:
    """Nguong = percentile thu `percentile` (0..1) cua phan phoi THAT trong `values`.

    Day chinh la cach abnormal_cancel duoc hieu chuan trong GSM_SIMULATOR
    (src/gsm_core/mockgen/realdata.py:548-566, p99 -> 0.20) - khong dat so tuy y.
    Tra ve +inf neu khong co du lieu (khong flag gi ca thay vi crash).
    """
    if not values:
        return float("inf")
    sorted_vals = sorted(values)
    idx = min(int(len(sorted_vals) * percentile), len(sorted_vals) - 1)
    return sorted_vals[idx]
