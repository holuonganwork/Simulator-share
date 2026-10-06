"""Ngat dinh vi giua chuyen (location_dropout) - giai doan trong chuyen.

Tin hieu: khoang trong thoi gian giua hai ban ghi public_driver_hex_tracking
lien tiep cung mot driver (dung entered_current_hex_at lam moc thoi gian). Neu
khoang trong nay vuot percentile p99 cua chinh phan phoi do (sau khi loai cac
khoang trong "tu nhien" do tan suat log thap - min_gap_seconds), day la dau hieu
tai xe tat dinh vi tam thoi.

Luu y do phan giai (docs/gsm-simulator-alignment.md): day la mau hinh duoc chinh
tai lieu chien luoc goi la "THO - do phan giai phut kho tach bach voi nhieu binh
thuong". Nguong percentile giup giam nguy co bao dong gia so voi mot nguong co dinh.
"""

from __future__ import annotations

from datetime import datetime

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult, percentile_threshold

PATTERN = "location_dropout"
DEFAULT_PERCENTILE = 0.99
MIN_GAP_SECONDS = 300


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def detect(
    store: GsmDataStore,
    percentile: float = DEFAULT_PERCENTILE,
    min_gap_seconds: int = MIN_GAP_SECONDS,
) -> RuleResult:
    hex_t = store.table("public_driver_hex_tracking").sort(
        ["driver_id", "entered_current_hex_at"]
    )

    gaps: list[tuple[str, float, dict]] = []
    prev_row: dict | None = None

    for row in hex_t.iter_rows(named=True):
        if prev_row is not None and prev_row["driver_id"] == row["driver_id"]:
            gap_seconds = (
                _parse(row["entered_current_hex_at"])
                - _parse(prev_row["entered_current_hex_at"])
            ).total_seconds()
            if gap_seconds >= min_gap_seconds:
                gaps.append(
                    (
                        row["driver_id"],
                        gap_seconds,
                        {
                            "from_hex": prev_row["current_hex"],
                            "to_hex": row["current_hex"],
                            "gap_seconds": gap_seconds,
                            "dropout_start": prev_row["entered_current_hex_at"],
                            "dropout_end": row["entered_current_hex_at"],
                        },
                    )
                )
        prev_row = row

    values = [g[1] for g in gaps]
    threshold = percentile_threshold(values, percentile)

    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=driver_id,
            signal_value=gap_seconds,
            threshold=threshold,
            evidence=evidence,
        )
        for driver_id, gap_seconds, evidence in gaps
        if gap_seconds > threshold
    ]

    return RuleResult(
        pattern=PATTERN,
        method=f"percentile_p{int(percentile * 100)}_above_min_gap={min_gap_seconds}s",
        threshold=threshold,
        n_evaluated=len(gaps),
        flags=flags,
    )
