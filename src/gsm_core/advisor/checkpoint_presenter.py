"""Side-effect-free structured presenter for immutable AdviceCheckpoints."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gsm_core.advisor._text import normalize_vi
from gsm_core.advisor.checkpoint_templates import CheckpointTemplateRegistry
from gsm_core.schema_registry import SchemaRegistry
from gsm_core.vn_format import render_number_vn


_REGISTRY = SchemaRegistry(Path(__file__).resolve().parents[3] / "schemas")
_PLACEHOLDER = re.compile(r"\{\{([FNC]\d+)\}\}")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_UNSAFE_MARKUP = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_ACTION_WORDS = {
    "PROTECT_ELIGIBILITY": ("bao ve dieu kien", "giu ty le"),
    "REST": ("nghi",),
    "SWAP": ("doi pin", "sac pin"),
    "END": ("ket ca", "dung ca"),
    "EXTEND": ("keo dai ca", "chay them"),
    "ONLINE": ("online", "tiep tuc chay"),
}

_TARGET_OR_INTERNAL_WORDS = (
    "tram", "khu vuc", "toa do", "station", "zone", "solver report",
    "prompt noi bo", "api key", "authorization",
)


@dataclass(frozen=True)
class PresentationText:
    title: str
    summary: str
    why: str
    fallback_used: bool
    verify_errors: tuple[str, ...]
    agent_output: dict | None
    shadow_output: dict | None


def build_agent_input(checkpoint: dict, *, facts: list[dict], numbers: list[dict],
                      caveats: list[dict], locale: str = "vi-VN") -> dict:
    current_action = checkpoint.get("current_action") or {
        "code": "NO_ACTION", "label_id": "action.no_action"}
    payload = {
        "schema_version": "1.1.0",
        "checkpoint_id": checkpoint["checkpoint_id"],
        "surface": checkpoint["surface"],
        "locale": locale,
        "topic": checkpoint["topic"],
        # Both names are intentional: canonical_action is the public invariant;
        # current_action makes the current/future boundary explicit to the model.
        "canonical_action": current_action,
        "current_action": current_action,
        "future_plan": checkpoint.get("future_plan") or [],
        "action_window": checkpoint.get("action_window"),
        "facts": facts,
        "numbers": [{k: item[k] for k in ("id", "value", "unit", "source")}
                    for item in numbers],
        "confidence_band": checkpoint["confidence_band"],
        "caveats": caveats,
        "summary_max_chars": 120,
        "why_max_chars": 280,
    }
    errors = _REGISTRY.validate("agent_presentation_input", payload)
    if errors:
        raise ValueError(f"agent presentation input không hợp lệ: {errors}")
    return payload


def build_explanation_input(checkpoint: dict, *, display_id: str,
                            facts: list[dict], numbers: list[dict],
                            caveats: list[dict], presentation: dict,
                            checkpoint_status: str, is_historical: bool,
                            locale: str = "vi-VN") -> dict:
    """Build the separate, closed lazy-why context (no solver/store access)."""
    current_action = checkpoint.get("current_action") or {
        "code": "NO_ACTION", "label_id": "action.no_action"}
    payload = {
        "schema_version": "1.0.0", "request_type": "explain_why",
        "checkpoint_id": checkpoint["checkpoint_id"], "display_id": display_id,
        "surface": checkpoint.get("surface", "nudge"), "locale": locale,
        "topic": checkpoint.get("topic", "policy_info"),
        "canonical_action": current_action, "current_action": current_action,
        "future_plan": checkpoint.get("future_plan") or [],
        "action_window": checkpoint.get("action_window"), "facts": facts,
        "numbers": [{k: item[k] for k in ("id", "value", "unit", "source")}
                    for item in numbers],
        "confidence_band": checkpoint.get("confidence_band", "medium"),
        "caveats": caveats,
        "presentation_text": {
            "title": str(presentation.get("title", "")),
            "summary": str(presentation.get("summary", "")),
        },
        "checkpoint_status": checkpoint_status,
        "is_historical": bool(is_historical),
        "summary_max_chars": 120, "why_max_chars": 280,
    }
    errors = _REGISTRY.validate("agent_explanation_input", payload)
    if errors:
        raise ValueError(f"agent explanation input không hợp lệ: {errors}")
    return payload


def verify_agent_output(raw: Any, checkpoint: dict, *, facts: list[dict],
                        numbers: list[dict], caveats: list[dict]
                        ) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if isinstance(raw, str):
        try:
            output = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            return None, [f"malformed_json:{exc}"]
    elif isinstance(raw, dict):
        output = dict(raw)
    else:
        return None, ["output_not_object"]

    errors.extend(_REGISTRY.validate("agent_presentation_output", output))
    if errors:
        return None, errors
    if output["checkpoint_id"] != checkpoint["checkpoint_id"]:
        errors.append("checkpoint_id_mismatch")

    registries = {
        "F": {item["id"] for item in facts},
        "N": {item["id"] for item in numbers},
        "C": {item["id"] for item in caveats},
    }
    used = {
        "F": set(output["used_fact_ids"]),
        "N": set(output["used_number_ids"]),
        "C": set(output["used_caveat_ids"]),
    }
    text = output["reason_template"] + " " + output["why_template"]
    placeholders = set(_PLACEHOLDER.findall(text))
    for prefix in ("F", "N", "C"):
        referenced = {value for value in placeholders if value.startswith(prefix)}
        if not used[prefix] <= registries[prefix]:
            errors.append(f"unknown_{prefix.lower()}_id")
        if referenced != used[prefix]:
            errors.append(f"used_{prefix.lower()}_ids_mismatch")

    without_placeholders = _PLACEHOLDER.sub("", text)
    if _UNSAFE_MARKUP.search(without_placeholders):
        errors.append("unsafe_markup_or_control_char")
    if re.search(r"\d", without_placeholders):
        errors.append("fabricated_number")
    if _CJK.search(text):
        errors.append("cjk_not_allowed")

    normalized = normalize_vi(without_placeholders)
    if any(phrase in normalized for phrase in
           ("cuoc nay", "nhan cuoc", "tu choi cuoc", "huy cuoc")):
        errors.append("specific_trip_advice")
    if any(phrase in normalized for phrase in
           ("chac chan", "dam bao", "se kiem", "cam ket thu nhap")):
        errors.append("income_promise")
    if checkpoint.get("urgency_band") not in {"high", "critical"} and any(
            phrase in normalized for phrase in ("lap tuc", "khan cap", "ngay bay gio")):
        errors.append("urgency_conflict")
    if any(phrase in normalized for phrase in (
            "truoc cuoi ca", "sau cuoi ca", "trong khung", "trong vong",
            "hom nay", "ngay mai")):
        errors.append("window_conflict")

    canonical = (checkpoint.get("current_action") or {}).get("code")
    future_codes = {
        str(item.get("code")) for item in (checkpoint.get("future_plan") or [])
        if item.get("code")
    }
    for action, phrases in _ACTION_WORDS.items():
        if action not in ({canonical} | future_codes) and any(
                phrase in normalized for phrase in phrases):
            errors.append("action_conflict")
            break
        if action != canonical and action in future_codes and any(
                phrase in normalized for phrase in phrases):
            # A future action may be explained, but it cannot be rewritten as
            # an instruction for the current moment.
            if not any(marker in normalized for marker in ("sap toi", "ke tiep", "sau do")):
                errors.append("current_future_conflict")
                break
    if any(phrase in normalized for phrase in _TARGET_OR_INTERNAL_WORDS):
        errors.append("target_or_internal_detail")
    return (None, errors) if errors else (output, [])


def verify_explanation_output(raw: Any, checkpoint: dict, *, display_id: str,
                              facts: list[dict], numbers: list[dict],
                              caveats: list[dict]) -> tuple[dict | None, list[str]]:
    """Verify lazy-why output while reusing the canonical text safety checks."""
    if isinstance(raw, str):
        try:
            output = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            return None, [f"malformed_json:{exc}"]
    elif isinstance(raw, dict):
        output = dict(raw)
    else:
        return None, ["output_not_object"]
    errors = _REGISTRY.validate("agent_explanation_output", output)
    if errors:
        return None, errors
    if output["checkpoint_id"] != checkpoint["checkpoint_id"]:
        return None, ["checkpoint_id_mismatch"]
    if output["display_id"] != display_id:
        return None, ["display_id_mismatch"]
    converted = {
        "schema_version": "1.0.0", "checkpoint_id": checkpoint["checkpoint_id"],
        "reason_template": output["explanation_template"],
        "why_template": output["explanation_template"],
        "used_fact_ids": output["used_fact_ids"],
        "used_number_ids": output["used_number_ids"],
        "used_caveat_ids": output["used_caveat_ids"],
    }
    verified, safety_errors = verify_agent_output(
        converted, checkpoint, facts=facts, numbers=numbers, caveats=caveats)
    if verified is None:
        return None, safety_errors
    return output, []


def _render(output: dict, facts: list[dict], numbers: list[dict],
            caveats: list[dict]) -> dict:
    values = {item["id"]: item["value"] for item in [*facts, *caveats]}
    values.update({item["id"]: render_number_vn(item["value"], item["unit"])
                   for item in numbers})

    def replace(template: str) -> str:
        return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)

    return {"summary": replace(output["reason_template"]),
            "why": replace(output["why_template"])}


class CheckpointPresenter:
    def __init__(self, *, agent=None, mode: str = "template"):
        if mode not in {"template", "shadow", "internal_live"}:
            raise ValueError("presentation_mode chỉ nhận template|shadow|internal_live")
        self.agent = agent
        self.mode = mode
        self.templates = CheckpointTemplateRegistry()

    def present(self, checkpoint: dict, *, facts: list[dict], numbers: list[dict],
                caveats: list[dict], locale: str = "vi-VN") -> PresentationText:
        template = self.templates.resolve(
            checkpoint, facts=facts, numbers=numbers, caveats=caveats,
            locale=locale, surface=checkpoint.get("surface", "nudge"))
        title, summary, why = template.title, template.summary, template.why
        errors: list[str] = []
        accepted = None
        shadow = None
        if self.mode == "shadow" and self.agent is not None:
            payload = build_agent_input(
                checkpoint, facts=facts, numbers=numbers, caveats=caveats, locale=locale)
            try:
                raw = self.agent.generate(payload)
                accepted, errors = verify_agent_output(
                    raw, checkpoint, facts=facts, numbers=numbers, caveats=caveats)
                if accepted is None and hasattr(self.agent, "repair"):
                    repaired = self.agent.repair(payload, errors)
                    accepted, errors = verify_agent_output(
                        repaired, checkpoint, facts=facts, numbers=numbers, caveats=caveats)
                if accepted is not None:
                    shadow = _render(accepted, facts, numbers, caveats)
            except Exception as exc:  # provider boundary: model failure never escapes to card path
                errors = [f"agent_error:{type(exc).__name__}"]
                accepted = None
        return PresentationText(
            title=title, summary=summary, why=why, fallback_used=True,
            verify_errors=tuple(errors), agent_output=accepted,
            shadow_output=shadow)
