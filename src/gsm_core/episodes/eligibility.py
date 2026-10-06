"""``DriverEligibilityProfile`` — tenure and programme eligibility as declared data.

The newbie capability was blocked because simulator archetype P4 is not a real newbie.
That is still true and this module does not weaken it.  What changed is that the block was
being enforced by *absence* — nothing carried tenure at all, so nothing could be checked.
Absence is a weak guard: the next detector that wants tenure has nothing stopping it from
reaching for a proxy.

So tenure becomes a declared record with a source and a freshness, and the guard becomes
explicit: :func:`profile_from_simulator` refuses to build anything for ``REAL``/``LIVE``,
and :func:`eligible_now` refuses to answer on a stale or absent profile.  Deriving tenure
from archetype, payout, trip count or any model output is not implemented anywhere, and
:func:`assert_not_inferred` exists so a test can state that as a requirement rather than
a convention.

Measured population in the pilot config (seeds 1000-1002, 270 actors): tenure min 8 days,
p50 320, buckets ``≤7: 0``, ``8-30: 21``, ``31-90: 27``, ``>90: 222``; 46 actors received
a ``newbie_guarantee_topup``.  The ``≤7`` bucket being empty matters — the seven-day
guarantee branch has **no sample** here, so it can be contract-tested but not
behaviour-validated, and must not be reported as verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gsm_core.episodes.contract import DataMode

# Only these modes may source a profile from the simulator.  REAL and LIVE need a
# production data contract that does not exist yet (onboarding date, programme registry,
# settlement feed) — see the roadmap in the cycle's findings.
SIMULATED_MODES = frozenset({DataMode.SIMULATED, DataMode.MOCK})

# How old a profile may be before it stops being usable.  A tenure that was true last week
# is still roughly true; an eligibility flag is not, because programmes start and end.
DEFAULT_MAX_STALENESS_MIN = 24 * 60.0

# Fields that may never be used to reconstruct tenure.  Named so the prohibition is
# machine-checkable instead of a comment.
FORBIDDEN_TENURE_PROXIES = frozenset({
    "archetype", "payout_vnd", "gross_vnd", "trips", "orders_completed",
    "points", "model_prediction", "behaviour_score",
})


class EligibilityError(ValueError):
    """Raised when eligibility is asked for without adequate provenance."""


@dataclass(frozen=True)
class DriverEligibilityProfile:
    driver_id: str
    source: str                       # who declared it, e.g. "sim.actor.tenure_days"
    observed_at_min: float
    data_mode: DataMode
    tenure_days: int | None = None
    program_id: str | None = None
    eligible: bool | None = None
    effective_from_min: float | None = None
    effective_to_min: float | None = None
    settled_benefit_vnd: int = 0
    pending_benefit_vnd: int = 0

    def freshness_min(self, at_min: float) -> float:
        return float(at_min) - float(self.observed_at_min)

    def is_fresh(self, at_min: float,
                 max_staleness_min: float = DEFAULT_MAX_STALENESS_MIN) -> bool:
        age = self.freshness_min(at_min)
        return -1e-6 <= age <= float(max_staleness_min) + 1e-6

    def to_dict(self, at_min: float | None = None) -> dict:
        payload: dict[str, Any] = {
            "driver_id": self.driver_id,
            "source": self.source,
            "observed_at_min": round(float(self.observed_at_min), 3),
            "data_mode": self.data_mode.value,
            "tenure_days": self.tenure_days,
            "program_id": self.program_id,
            "eligible": self.eligible,
            "effective_from_min": self.effective_from_min,
            "effective_to_min": self.effective_to_min,
            "settled_benefit_vnd": int(self.settled_benefit_vnd),
            "pending_benefit_vnd": int(self.pending_benefit_vnd),
        }
        if at_min is not None:
            payload["freshness_min"] = round(self.freshness_min(at_min), 3)
            payload["is_fresh"] = self.is_fresh(at_min)
        return payload


def assert_not_inferred(source: str) -> None:
    """Reject a profile whose source names one of the forbidden proxies."""
    lowered = str(source).lower()
    hit = next((field for field in sorted(FORBIDDEN_TENURE_PROXIES) if field in lowered),
               None)
    if hit is not None:
        raise EligibilityError(
            f"tenure không được suy từ {hit!r} — cần nguồn khai báo, không phải hành vi")


def profile_from_simulator(actor: Any, *, at_min: float,
                           data_mode: DataMode = DataMode.SIMULATED,
                           newbie_max_days: int = 30) -> DriverEligibilityProfile:
    """Build a profile from a simulator actor.  Refuses for REAL/LIVE data modes."""
    if data_mode not in SIMULATED_MODES:
        raise EligibilityError(
            f"data_mode {data_mode.value} cần hợp đồng dữ liệu production; "
            "simulator không phải nguồn hợp lệ cho tenure thật")
    tenure = getattr(actor, "tenure_days", None)
    if tenure is None:
        raise EligibilityError("actor không khai tenure_days — fail closed, không suy diễn")
    source = "sim.actor.tenure_days"
    assert_not_inferred(source)
    settled = int(getattr(actor, "newbie_topup_vnd", 0) or 0)
    return DriverEligibilityProfile(
        driver_id=f"d-{int(getattr(actor, 'actor_id', 0))}", source=source,
        observed_at_min=float(at_min), data_mode=data_mode, tenure_days=int(tenure),
        program_id="newbie_guarantee",
        eligible=int(tenure) <= int(newbie_max_days),
        settled_benefit_vnd=settled, pending_benefit_vnd=0)


def eligible_now(profile: DriverEligibilityProfile | None, at_min: float, *,
                 max_staleness_min: float = DEFAULT_MAX_STALENESS_MIN) -> bool:
    """Fail-closed eligibility read.

    Returns ``False`` — never ``None`` and never a guess — when the profile is missing,
    stale, undeclared or outside its effective window.  A caller that cannot tell "not
    eligible" from "we do not know" would end up showing a benefit to someone who has
    none, so the two collapse to the safe answer here and the *reason* is available
    separately via :func:`eligibility_reason`.
    """
    return eligibility_reason(profile, at_min,
                              max_staleness_min=max_staleness_min) == "eligible"


def eligibility_reason(profile: DriverEligibilityProfile | None, at_min: float, *,
                       max_staleness_min: float = DEFAULT_MAX_STALENESS_MIN) -> str:
    if profile is None:
        return "no_profile"
    if profile.data_mode not in SIMULATED_MODES:
        return "production_contract_missing"
    if not profile.is_fresh(at_min, max_staleness_min):
        return "stale_profile"
    if profile.eligible is None:
        return "eligibility_undeclared"
    if not profile.eligible:
        return "not_eligible"
    if (profile.effective_from_min is not None
            and float(at_min) < float(profile.effective_from_min) - 1e-6):
        return "before_effective_from"
    if (profile.effective_to_min is not None
            and float(at_min) > float(profile.effective_to_min) + 1e-6):
        return "after_effective_to"
    return "eligible"
