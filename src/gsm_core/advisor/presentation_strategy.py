"""Pure deterministic choice between silent, template and Agent presentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Strategy = Literal["SILENT", "TEMPLATE", "LLM"]


@dataclass(frozen=True)
class PresentationDecision:
    strategy: Strategy
    reason_code: str


_KNOWN_ACTIONS = {
    "PROTECT_ELIGIBILITY", "ONLINE", "REST", "SWAP", "END", "EXTEND",
    "REPOSITION_SIM_ONLY", "NO_ACTION",
}


def decide_presentation(checkpoint: dict | None, *, facts: list[dict],
                        numbers: list[dict], caveats: list[dict],
                        mode: str = "template", provider_enabled: bool = False,
                        is_driving: bool = False,
                        user_requested_why: bool = False) -> PresentationDecision:
    """Select a presentation strategy without calling a model or reading state.

    Complexity is an explicit, code-owned property (or a small typed registry
    shape), never a question delegated to an LLM.  The default remains template.
    """
    del numbers  # typed numbers inform the caller's template/input, not this gate.
    if checkpoint is None:
        return PresentationDecision("SILENT", "no_checkpoint")
    if is_driving:
        return PresentationDecision("SILENT", "unsafe_while_moving")

    # Strategy is called only after primary selection in the normal path, but it
    # remains a public pure boundary.  Never turn a non-presentable lifecycle
    # record into a card when a replay/consumer calls it directly.
    status = str(checkpoint.get("status") or checkpoint.get("state") or "").lower()
    if status in {"suppressed", "queued", "expired", "superseded", "dismissed"}:
        return PresentationDecision("SILENT", "checkpoint_not_presentable")

    action = str((checkpoint.get("current_action") or {}).get("code") or "NO_ACTION")
    if action not in _KNOWN_ACTIONS:
        return PresentationDecision("TEMPLATE", "simple_known_template")

    complexity = str(checkpoint.get("presentation_complexity") or "")
    reason = str(checkpoint.get("reason_code") or "")
    if user_requested_why:
        if mode in {"shadow", "internal_live"} and provider_enabled:
            return PresentationDecision("LLM", "user_requested_why")
        return PresentationDecision(
            "TEMPLATE", "llm_disabled" if mode not in {"shadow", "internal_live"}
            else "provider_unavailable")

    if reason in {"repeated_advice", "simple_known_template", "soc_low", "rest_window",
                  "bonus_gap", "shift_boundary", "solver_recommendation"} \
            and not complexity and not caveats:
        return PresentationDecision(
            "TEMPLATE", "repeated_advice" if reason == "repeated_advice"
            else "simple_known_template")

    if complexity == "complex" or len(facts) > 2:
        reason_code = "complex_multi_fact"
    elif caveats or complexity == "caveat":
        reason_code = "complex_caveat"
    elif complexity == "current_future":
        reason_code = "current_future_explanation"
    else:
        return PresentationDecision("TEMPLATE", "simple_known_template")

    if mode not in {"shadow", "internal_live"}:
        return PresentationDecision("TEMPLATE", "llm_disabled")
    if not provider_enabled:
        return PresentationDecision("TEMPLATE", "provider_unavailable")
    return PresentationDecision("LLM", reason_code)
