"""``DriverBriefMemory`` — carrying an observation from one shift into the next, prior-only.

Not to be confused with ``gsm_sim.multiday.DriverMemory``, which is the simulator's own
cross-day state feeding the solvers (acceptance history, points per hour, weekly totals).
This one is the *driver-facing* half: at most one sentence, in the pre-shift brief, with an
expiry and a revalidation step. Keeping them separate matters because the simulator's
memory may hold anything the solvers need, while this one may only hold what a driver is
allowed to be told.

The multiday probe reproduced 84.44% carry-over, but a probe is an offline measurement,
not a flow.  This module is the flow: day N produces a candidate, the candidate is
validated before it is stored, day N+1 reads it into the pre-shift brief, and the new
day's own state revalidates it before anything is shown.

The invariant that makes this safe is ``valid_from``: a memory produced from day N carries
``valid_from`` at the *start of day N+1*, and :func:`memories_for_brief` refuses anything
whose ``valid_from`` has not arrived.  That is what stops the brief for today being built
out of today — the failure that would make the whole feature circular.

Two things this deliberately cannot express:

* **Causation across days.**  A memory records what was observed, and the copy contract
  keeps it that way.  "Yesterday was quiet at this hour so today will be too" is a
  forecast from one sample, and :data:`FORBIDDEN_MEMORY_CLAIMS` blocks the shape of it.
* **Cohort inference.**  A memory belongs to one driver.  Nothing here reads other
  drivers' days, so a stereotype cannot leak in through the back door.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gsm_core.episodes.contract import DataMode, EvidenceMode

MINUTES_PER_DAY = 1440.0

# A single day is an anecdote.  Below this, a memory may be stored but is marked
# ``OBSERVATION_ONLY`` and may not be described as a pattern.
MIN_DAYS_FOR_PATTERN = 3

# Memories go stale: a wait pattern from three weeks ago says little about tomorrow.
DEFAULT_EXPIRY_DAYS = 7

FORBIDDEN_MEMORY_CLAIMS: tuple[str, ...] = (
    "nên hôm nay", "vì vậy hôm nay sẽ", "hôm nay cũng sẽ", "chắc chắn hôm nay",
    "dự báo hôm nay", "sẽ lại ít đơn", "sẽ lại đông",
)


class MemoryKind(str, Enum):
    OBSERVATION_ONLY = "OBSERVATION_ONLY"   # one comparable day
    PATTERN = "PATTERN"                     # >= MIN_DAYS_FOR_PATTERN comparable days


class MemoryError_(ValueError):
    """Raised when a memory would be built from the present or from another driver."""


@dataclass(frozen=True)
class DriverBriefMemory:
    driver_id: str
    key: str                            # e.g. "longest_wait_hour"
    value: object
    kind: MemoryKind
    produced_on_day: int
    valid_from_min: float               # absolute minute; start of the *next* day
    expires_after_day: int
    comparable_days: int
    confidence: float
    data_mode: DataMode
    evidence_mode: EvidenceMode
    source_day_refs: tuple[str, ...]

    def is_live_on(self, day_index: int) -> bool:
        return self.produced_on_day < day_index <= self.expires_after_day

    def to_dict(self) -> dict:
        return {
            "driver_id": self.driver_id, "key": self.key, "value": self.value,
            "kind": self.kind.value, "produced_on_day": int(self.produced_on_day),
            "valid_from_min": round(float(self.valid_from_min), 3),
            "expires_after_day": int(self.expires_after_day),
            "comparable_days": int(self.comparable_days),
            "confidence": round(float(self.confidence), 3),
            "data_mode": self.data_mode.value,
            "evidence_mode": self.evidence_mode.value,
            "source_day_refs": list(self.source_day_refs),
        }


def _longest_wait_hour(view) -> tuple[int, float] | None:
    """Hour of day holding the driver's single longest recorded wait, and its length."""
    waits = [b for b in view.timeline if b.kind == "idle"]
    if not waits:
        return None
    longest = max(waits, key=lambda b: b.minutes)
    if longest.minutes <= 0:
        return None
    return int((longest.t0 // 60) % 24), round(float(longest.minutes), 1)


def candidate_from_day(view, *, day_index: int,
                       comparable_days: int = 1) -> DriverBriefMemory | None:
    """Produce at most one candidate from a *finished* day.

    Called after the day is over, so reading the whole view is not a leak — the leak this
    module has to prevent is on the *consumption* side, and ``valid_from_min`` is what
    prevents it.
    """
    found = _longest_wait_hour(view)
    if found is None:
        return None
    hour, minutes = found
    kind = (MemoryKind.PATTERN if comparable_days >= MIN_DAYS_FOR_PATTERN
            else MemoryKind.OBSERVATION_ONLY)
    return DriverBriefMemory(
        driver_id=str(view.driver_id), key="longest_wait_hour",
        value={"hour": hour, "minutes": minutes}, kind=kind,
        produced_on_day=int(day_index),
        valid_from_min=(int(day_index) + 1) * MINUTES_PER_DAY,
        expires_after_day=int(day_index) + DEFAULT_EXPIRY_DAYS,
        comparable_days=int(comparable_days),
        # One day is one day.  Confidence rises with comparable days and is capped well
        # below certainty because the quantity itself is a reconstruction.
        confidence=min(0.7, 0.25 + 0.15 * int(comparable_days)),
        data_mode=view.data_mode, evidence_mode=EvidenceMode.INFERRED,
        source_day_refs=(f"{view.run_id}#day{int(day_index)}",))


def validate_candidate(memory: DriverBriefMemory, *, produced_for_driver: str) -> DriverBriefMemory:
    """Reject a memory that belongs to someone else or claims today from today."""
    if memory.driver_id != produced_for_driver:
        raise MemoryError_(
            f"memory của {memory.driver_id!r} không được gán cho {produced_for_driver!r} — "
            "không dùng khuôn mẫu nhóm")
    if memory.valid_from_min <= memory.produced_on_day * MINUTES_PER_DAY + 1e-6:
        raise MemoryError_(
            "valid_from không được nằm trong chính ngày sinh ra nó — advice đầu ngày "
            "không được dựng từ dữ liệu của ngày đó")
    if memory.expires_after_day <= memory.produced_on_day:
        raise MemoryError_("memory hết hạn trước khi có hiệu lực")
    return memory


def memories_for_brief(store: list[DriverBriefMemory], *, driver_id: str, day_index: int,
                       now_min: float) -> list[DriverBriefMemory]:
    """The prior-only read.  Anything not yet valid, expired or foreign is dropped."""
    return sorted(
        (m for m in store
         if m.driver_id == driver_id
         and m.is_live_on(day_index)
         and float(now_min) >= float(m.valid_from_min) - 1e-6),
        key=lambda m: (m.produced_on_day, m.key))


def revalidate_against_today(memory: DriverBriefMemory, today_view) -> tuple[bool, str]:
    """Check a memory against the new day's own state before it is shown.

    Returns ``(still_usable, reason)``.  A memory about an hour the driver is not working
    today is not wrong, it is *irrelevant*, and the two are reported separately so the
    rejection rate can be read honestly.
    """
    value = memory.value if isinstance(memory.value, dict) else {}
    hour = value.get("hour")
    if hour is None:
        return False, "malformed_memory"
    start_hour = int((float(today_view.shift_start_min) // 60) % 24)
    end_hour = int((float(today_view.shift_end_min) // 60) % 24)
    span = ((end_hour - start_hour) % 24) or 24
    offset = (int(hour) - start_hour) % 24
    if offset > span:
        return False, "hour_outside_today_shift"
    return True, "carried_over"


def brief_line(memory: DriverBriefMemory) -> str:
    """The one sentence a memory is allowed to become.  Fails closed on a causal claim."""
    value = memory.value if isinstance(memory.value, dict) else {}
    hour = int(value.get("hour", 0))
    text = (f"Ca trước có khoảng chờ dài nhất trong khung {hour:02d}:00. "
            "Kế hoạch hôm nay sẽ xem xét thông tin này và kiểm tra lại bằng trạng thái mới.")
    lowered = text.lower()
    hit = next((claim for claim in FORBIDDEN_MEMORY_CLAIMS if claim in lowered), None)
    if hit is not None:                  # unreachable for this template; guards edits
        raise MemoryError_(f"câu brief chứa suy luận nhân quả bị cấm: {hit!r}")
    return text
