"""Kho case + kho nhan (label store) - JSONL append-only tren dia.

Theo docs/gsm-simulator-alignment.md: public_frauds.status co san 4 gia tri
(open/reviewing/cleared/confirmed) nhung KHONG co API/workflow nao ghi de theo
quyet dinh that cua nguoi quan ly - trang thai chi duoc dat 1 lan luc sinh mock.
Module nay lap dung khoang trong do: mot case store that (khong phai mock) ma
flag_case/resolve_case (agent/tools/) doc-ghi vao, cong voi mot kho nhan TACH
BIET khoi case goc (khong sua de lich su - dung nguyen tac cuoi muc 07 chien luoc).

Khong dung SQLite/DB that de giu du an khong phu thuoc he thong ngoai - hai file
JSONL (`var/cases/cases.jsonl`, `var/cases/resolutions.jsonl`) la du cho quy mo
demo/nghien cuu va de doc lai bang polars/pandas khi can phan tich.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

VAR_DIR = Path(__file__).resolve().parents[3] / "var" / "cases"
CASES_FILE = VAR_DIR / "cases.jsonl"
RESOLUTIONS_FILE = VAR_DIR / "resolutions.jsonl"

Severity = Literal["low", "medium", "high"]
Status = Literal["open", "reviewing", "cleared", "confirmed"]
Verdict = Literal["false_positive", "confirmed"]


class CaseNotFoundError(KeyError):
    pass


@dataclass
class FraudCase:
    """Tuong ung schemas/case/fraud_case.schema.json."""

    case_id: str
    driver_id: str
    pattern: str
    severity: Severity
    confidence: float
    evidence: dict[str, Any]
    status: Status = "open"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    reviewer_id: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


@dataclass
class CaseResolution:
    """Ban ghi nhan doc lap - tuong ung schemas/case/case_resolution.schema.json.

    KHONG sua de case goc - moi lan resolve_case ghi THEM mot dong o day, giu
    nguyen trang tin hieu tai thoi diem phat hien + verdict cua nguoi.
    """

    case_id: str
    driver_id: str
    pattern: str
    verdict: Verdict
    reviewer_id: str
    reviewed_at: str
    note: str
    signal_snapshot: dict[str, Any]


def _ensure_dir() -> None:
    VAR_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    _ensure_dir()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def create_case(
    driver_id: str,
    pattern: str,
    severity: Severity,
    confidence: float,
    evidence: dict[str, Any],
) -> FraudCase:
    case = FraudCase(
        case_id=f"case-{uuid.uuid4().hex[:10]}",
        driver_id=driver_id,
        pattern=pattern,
        severity=severity,
        confidence=confidence,
        evidence=evidence,
    )
    _append_jsonl(CASES_FILE, asdict(case))
    return case


def get_case(case_id: str) -> FraudCase:
    for record in reversed(_read_jsonl(CASES_FILE)):
        if record["case_id"] == case_id:
            return FraudCase(**record)
    raise CaseNotFoundError(f"Khong tim thay case_id={case_id}")


def list_cases(
    driver_id: str | None = None, status: Status | None = None
) -> list[FraudCase]:
    # Vi day la log append-only, mot case_id co the xuat hien nhieu lan (moi lan
    # update trang thai ghi them 1 dong) - lay ban ghi MOI NHAT cho moi case_id.
    latest: dict[str, dict[str, Any]] = {}
    for record in _read_jsonl(CASES_FILE):
        latest[record["case_id"]] = record

    cases = [FraudCase(**r) for r in latest.values()]
    if driver_id:
        cases = [c for c in cases if c.driver_id == driver_id]
    if status:
        cases = [c for c in cases if c.status == status]
    return sorted(cases, key=lambda c: c.created_at, reverse=True)


def resolve_case(
    case_id: str,
    verdict: Verdict,
    reviewer_id: str,
    note: str,
) -> CaseResolution:
    """Dong case voi verdict cua nguoi quan ly - nguon nhan chinh thuc cho Pha 5.

    verdict='confirmed' -> case.status='confirmed' (du dieu kien cho apply_action,
      xem agent/tools/apply_action.py).
    verdict='false_positive' -> case.status='cleared' (dung de hieu chinh lai
      nguong Tier A/B, KHONG dung de trung phat).
    """
    case = get_case(case_id)
    reviewed_at = datetime.now(timezone.utc).isoformat()
    new_status: Status = "confirmed" if verdict == "confirmed" else "cleared"

    updated = asdict(case) | {
        "status": new_status,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at,
        "review_note": note,
    }
    _append_jsonl(CASES_FILE, updated)  # append-only: KHONG ghi de dong cu

    resolution = CaseResolution(
        case_id=case_id,
        driver_id=case.driver_id,
        pattern=case.pattern,
        verdict=verdict,
        reviewer_id=reviewer_id,
        reviewed_at=reviewed_at,
        note=note,
        signal_snapshot=case.evidence,
    )
    _append_jsonl(RESOLUTIONS_FILE, asdict(resolution))
    return resolution


def all_resolutions() -> list[CaseResolution]:
    """Toan bo kho nhan - dung cho Tier B retrain (Pha 5)."""
    return [CaseResolution(**r) for r in _read_jsonl(RESOLUTIONS_FILE)]
