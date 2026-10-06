"""Giu trang thai online "ao" (virtual_online_status) - hang muc 4 "Chien luoc
lam giau du lieu". Truoc day KHONG co tin hieu chuyen dong doc lap voi trang
thai online (xem docs/fraud-taxonomy.md - danh dau "CHUA lam") - event_type
`went_online`/`went_offline` da co khung trong trip_lifecycle_event.schema.json
nhung producer Buoc 3 chua bao gio mo phong.

Tin hieu: trong MOT phien (tu went_online den went_offline gan nhat cua CUNG
driver), khoang cach thoi gian giua 2 su kien LIEN TIEP BAT KY (app_foreground/
app_background/driver_break/manual_location_toggle/...) vuot percentile p99
cua chinh phan phoi do - dung logic HOAN TOAN giong tier_a/location_dropout.py
(percentile tu hieu chuan, khong dat so cung) nhung o tang SU KIEN thay vi
tang GPS hex.
"""

from __future__ import annotations

from datetime import datetime

from lcx_core.event_sim.store import EventDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult, percentile_threshold

PATTERN = "virtual_online_status"
DEFAULT_PERCENTILE = 0.99


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def detect(store: EventDataStore, percentile: float = DEFAULT_PERCENTILE) -> RuleResult:
    ordered = store.events.sort(["driver_id", "occurred_at"])

    gaps: list[tuple[str, float, dict]] = []
    open_since_event: dict | None = None
    prev_row: dict | None = None

    for row in ordered.iter_rows(named=True):
        if prev_row is not None and prev_row["driver_id"] != row["driver_id"]:
            open_since_event = None

        if row["event_type"] == "went_online":
            open_since_event = row
        elif (
            open_since_event is not None
            and prev_row is not None
            and prev_row["driver_id"] == row["driver_id"]
        ):
            gap_seconds = (_parse(row["occurred_at"]) - _parse(prev_row["occurred_at"])).total_seconds()
            gaps.append(
                (
                    row["driver_id"],
                    gap_seconds,
                    {
                        "prev_event_type": prev_row["event_type"],
                        "prev_event_id": prev_row["event_id"],
                        "next_event_type": row["event_type"],
                        "next_event_id": row["event_id"],
                        "toggle_off_event_id": prev_row["event_id"]
                        if prev_row["event_type"] == "manual_location_toggle"
                        else None,
                        "gap_seconds": gap_seconds,
                        "went_online_at": open_since_event["occurred_at"],
                    },
                )
            )

        if row["event_type"] == "went_offline":
            open_since_event = None
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
        method=f"percentile_p{int(percentile * 100)}_within_open_session",
        threshold=threshold,
        n_evaluated=len(gaps),
        flags=flags,
    )
