"""``why_no_relocation`` — the input contract and the closed door in front of it.

This capability is **BLOCKED**, and unlike the others in this cycle the block is real: it
needs live supply, live capacity and an S4 allocation authority that production does not
have.  Nothing here unblocks it.  What it does is make the block *enforced* rather than
merely intended, and make the unlock path concrete.

The failure mode this guards against is specific and tempting.  Every input below has a
plausible-looking substitute in the simulator — ``market_state`` has per-cell idle counts,
``capacity_alloc`` produces allocations, a cell's driver count could stand in for supply.
Wiring those together would produce a screen that looks right and is not: it would answer
"where should I go" from a model of the world rather than the world, and the driver cannot
tell the difference.  So :func:`render_decision` refuses on any missing or stale input and
:func:`can_render` is the single place an owner would ever flip.

Never rendered under any circumstances, per the product boundary: the best cell, expected
income, "go to X", or live weather/traffic.  :data:`FORBIDDEN_OUTPUT_KEYS` states that as
data so a test can assert it.
"""

from __future__ import annotations

from dataclasses import dataclass

from gsm_core.episodes.contract import Authority, DataMode

# Every field the decision needs, and who would have to produce it.  This *is* the
# dependency map; keeping it as data means the readiness report cannot drift from the code.
REQUIRED_INPUTS: dict[str, str] = {
    "zone_id": "S4 allocation authority (production)",
    "demand_estimate": "demand model, versioned, with a confidence",
    "current_supply": "live driver positions — not available",
    "incoming_supply": "en-route/assigned drivers — not available",
    "capacity": "zone capacity registry — not available",
    "allocation_decision": "S4 solver report, persisted",
    "herding_avoided": "S4 allocation invariant",
    "observed_at": "freshness stamp of the supply snapshot",
    "data_source": "provenance of the environment reading",
}

# Inputs whose absence is what currently keeps the capability closed.
MISSING_IN_PRODUCTION = frozenset({
    "current_supply", "incoming_supply", "capacity", "allocation_decision",
})

# How old a supply reading may be and still describe "now".  Supply moves in minutes.
MAX_SUPPLY_STALENESS_MIN = 5.0

FORBIDDEN_OUTPUT_KEYS = frozenset({
    "best_zone", "best_cell", "recommended_zone", "expected_income_vnd",
    "expected_earnings", "go_to", "weather", "traffic", "eta_to_zone",
})


class RelocationAuthorityError(ValueError):
    """Raised when a relocation decision is requested without adequate authority."""


@dataclass(frozen=True)
class RelocationInput:
    zone_id: str
    demand_estimate: float | None
    current_supply: int | None
    incoming_supply: int | None
    capacity: int | None
    allocation_decision: str | None
    herding_avoided: bool | None
    observed_at_min: float | None
    data_source: str
    authority: Authority
    data_mode: DataMode

    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name in sorted(REQUIRED_INPUTS)
                     if getattr(self, _attr_for(name), None) in (None, ""))


def _attr_for(name: str) -> str:
    return {"observed_at": "observed_at_min"}.get(name, name)


@dataclass(frozen=True)
class RelocationDecision:
    """The only shape this capability is ever allowed to emit."""

    applicable: bool
    reason_not_to_relocate: str
    zone_id: str
    authority: Authority
    observed_at_min: float
    freshness_min: float
    data_mode: DataMode
    source_report_refs: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = {
            "applicable": bool(self.applicable),
            "reason_not_to_relocate": self.reason_not_to_relocate,
            "zone_id": self.zone_id,
            "authority": self.authority.value,
            "observed_at_min": round(float(self.observed_at_min), 3),
            "freshness_min": round(float(self.freshness_min), 3),
            "data_mode": self.data_mode.value,
            "source_report_refs": list(self.source_report_refs),
        }
        leaked = sorted(FORBIDDEN_OUTPUT_KEYS & set(payload))
        if leaked:                       # unreachable by construction; asserted anyway
            raise RelocationAuthorityError(f"output chứa khoá bị cấm: {leaked}")
        return payload


def readiness_report() -> dict:
    """What exists, what does not, and what would unlock it.  Consumed by the audit doc."""
    return {
        "capability": "why_no_relocation",
        "status": "BLOCKED",
        "required_inputs": dict(REQUIRED_INPUTS),
        "missing_in_production": sorted(MISSING_IN_PRODUCTION),
        "driver_facing": False,
        "unlock_path": [
            "S4 phát hành solver report có persist cho quyết định phân bổ",
            "nguồn supply thật (vị trí tài xế đang rảnh) kèm freshness ≤ 5 phút",
            "registry sức chứa theo zone, có version",
            "provenance nguồn môi trường; KHÔNG mock thời tiết/giao thông",
            "quyết định của Product Owner về việc có được nói lý do KHÔNG đi hay không",
        ],
        "forbidden_outputs": sorted(FORBIDDEN_OUTPUT_KEYS),
    }


def can_render(payload: RelocationInput | None, at_min: float) -> bool:
    """Single gate.  Returns ``False`` today for every input the repo can construct."""
    try:
        render_decision(payload, at_min)
    except RelocationAuthorityError:
        return False
    return True


def render_decision(payload: RelocationInput | None, at_min: float) -> RelocationDecision:
    """Build a decision, or refuse with the specific reason.

    Ordered so the most fundamental failure is reported first: no input at all, then wrong
    authority, then missing fields, then staleness.  A caller that logs the message gets a
    usable diagnosis rather than a generic "not available".
    """
    if payload is None:
        raise RelocationAuthorityError("không có input S4 — fail closed")
    if payload.authority is not Authority.SOLVER:
        raise RelocationAuthorityError(
            f"quyết định định vị cần authority SOLVER, nhận {payload.authority.value}")
    missing = payload.missing_fields()
    if missing:
        raise RelocationAuthorityError(
            f"thiếu input bắt buộc: {list(missing)} — không render khi thiếu authority")
    freshness = float(at_min) - float(payload.observed_at_min)
    if freshness < -1e-6 or freshness > MAX_SUPPLY_STALENESS_MIN:
        raise RelocationAuthorityError(
            f"supply snapshot cũ {freshness:.1f} phút (trần {MAX_SUPPLY_STALENESS_MIN}) — "
            "không mô tả hiện tại")
    if not payload.data_source:
        raise RelocationAuthorityError("thiếu provenance nguồn môi trường")
    return RelocationDecision(
        applicable=True,
        reason_not_to_relocate=str(payload.allocation_decision),
        zone_id=payload.zone_id, authority=payload.authority,
        observed_at_min=float(payload.observed_at_min), freshness_min=freshness,
        data_mode=payload.data_mode,
        source_report_refs=(str(payload.allocation_decision),))
