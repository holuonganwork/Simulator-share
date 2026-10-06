"""Composer — LLM #1 theo cơ chế PLACEHOLDER-FIRST (research đợt 7, arxiv 2601.03640).

LLM CHỈ viết message_template chứa token {{N-id}}; CODE thay bằng số format VN từ
numbers_registry. Faithfulness trở thành BẤT BIẾN CẤU TRÚC — LLM không bao giờ chạm
vào chữ số. Composer tự verify (tầng 1 subset) + repair ≤1; vẫn fail → trả cờ
fallback_used để pipeline hạ về template (fail-closed).

Suite test dùng MockLLM (không mạng). Live client ở `llm_client.py` (lazy openai).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gsm_core.advisor.context_pack import render_number_vn
from gsm_core.advisor import verifier as V

# token {{N1}} / {{ N12 }} — chỉ chấp nhận [A-Za-z]+ theo digits (N-id/P-id)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z]+\d+)\s*\}\}")

# baseline confidence cho advice do LLM soạn (chưa qua LLM-judge — EXP-001)
_LLM_CONFIDENCE = 0.75


@dataclass
class ComposerOutput:
    """Cái LLM trả về (instructor JSON mode / mock). LLM KHÔNG render số."""
    message_template: str
    advice_spec: dict | None = None
    citations: list = field(default_factory=list)
    caveats: list = field(default_factory=list)


def render_placeholders(template: str, numbers_registry: dict) -> str:
    """CODE thay {{N-id}} bằng số format VN. Placeholder lạ → KeyError (repair-able)."""
    def _repl(m: re.Match) -> str:
        nid = m.group(1)
        if nid not in numbers_registry:
            raise KeyError(nid)
        n = numbers_registry[nid]
        return render_number_vn(n["value"], n["unit"])
    return _PLACEHOLDER_RE.sub(_repl, template)


class Composer:
    """Điều phối 1 LLM call + repair ≤1. `llm` có method `.create(**kwargs)->ComposerOutput`."""

    def __init__(self, llm):
        self.llm = llm

    def compose(self, pack: dict, feature: str) -> dict:
        registry = pack["numbers_registry"]
        cite_reg = pack.get("citations_registry") or {}
        used_kb = bool(cite_reg)
        cited_texts = [c.get("title", "") for c in cite_reg.values()]

        try:
            out = self._call(pack, feature, repair_hint=None)
        except Exception:  # noqa: BLE001 — LLM/mạng chết → template (fail-closed)
            return self._giveup()
        res = self._render_and_check(out, registry, feature, used_kb, cited_texts)
        if res["ok"]:
            return self._finish(res, fallback=False)

        # repair ≤1: đưa rule ID lỗi làm hint cho lần reask
        try:
            out2 = self._call(pack, feature, repair_hint=res["errors"])
        except Exception:  # noqa: BLE001
            return self._giveup()
        res2 = self._render_and_check(out2, registry, feature, used_kb, cited_texts)
        # vẫn fail → cờ fallback; pipeline verify lại & hạ template (fail-closed)
        return self._finish(res2, fallback=not res2["ok"])

    # --- internal ---

    def _call(self, pack: dict, feature: str, repair_hint):
        return self.llm.create(
            system=pack["system_prompt"],
            data=pack["prompt_data"],
            instruction=pack["instruction"],
            feature=feature,
            repair_hint=repair_hint,
        )

    def _render_and_check(self, out: ComposerOutput, registry: dict,
                          feature: str, used_kb: bool, cited_texts=None) -> dict:
        try:
            message = render_placeholders(out.message_template, registry)
        except KeyError as e:
            return {"ok": False, "errors": [f"unknown placeholder {e}"],
                    "message": out.message_template, "out": out}
        rendered = [render_number_vn(n["value"], n["unit"])
                    for n in registry.values()]
        vres = V.verify(message, out.advice_spec, out.citations, feature,
                        used_kb=used_kb, rendered_numbers=rendered,
                        cited_texts=cited_texts)
        return {"ok": vres["passed"], "errors": vres["errors"],
                "message": message, "out": out}

    def _giveup(self) -> dict:
        """LLM không dùng được → trả cờ fallback; pipeline sẽ hạ template."""
        return {"message": "", "citations": [], "advice_spec": None,
                "caveats": [], "confidence": _LLM_CONFIDENCE, "fallback_used": True}

    def _finish(self, res: dict, fallback: bool) -> dict:
        out = res["out"]
        return {
            "message": res["message"],
            "citations": list(out.citations),
            "advice_spec": out.advice_spec,
            "caveats": list(out.caveats),
            "confidence": _LLM_CONFIDENCE,
            "fallback_used": fallback,
        }
