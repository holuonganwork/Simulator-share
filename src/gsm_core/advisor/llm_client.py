"""LLM client cho Composer — openai SDK (endpoint ai-box.vn OpenAI-compatible).

Fallback 4 tầng (research đợt 7 §llm-advisor-architecture):
  T1 primary (deepseek-v4-flash) + SDK retry.
  T2 parse/schema fail → reask 1 lần với hint lỗi.
  T3 fallback model (gpt-4o-mini).
  T4 raise LLMUnavailable → Composer/pipeline hạ template (fail-closed).

Client CHỈ trả `ComposerOutput` (message_template chứa {{N-id}}, KHÔNG render số).
Import openai LAZY: test-suite deterministic không cần mạng/deps. Chạy live cần
`uv sync --extra llm` + `.env` (KHÔNG commit).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from gsm_core.advisor.composer import ComposerOutput

ROOT = Path(__file__).resolve().parents[3]

# JSON schema mà LLM phải trả (nhắc trong prompt; parse + validate ở code)
_OUTPUT_HINT = (
    'Trả về DUY NHẤT một JSON object đúng dạng: '
    '{"advice_spec": null | {"action_type": string, "target_window": string|null, '
    '"target_zone_or_station": string|null}, '
    '"message_template": string (MỌI con số thay bằng token {{N1}},{{N2}}... — TUYỆT ĐỐI '
    'không tự viết chữ số), "citations": [string], "caveats": [string]}.'
)


class LLMUnavailable(RuntimeError):
    """Hết 4 tầng fallback — pipeline sẽ hạ template."""


def _strip_inline_comment(v: str) -> str:
    """Cắt chú thích `# …` ở CUỐI giá trị, tôn trọng dấu nháy.

    🔴 Vì sao cần (soi độc lập 2026-08-03, UPDATE-135 — lỗi do chính cycle đó tạo ra):
    `.env.example` viết chú thích **cùng dòng** với biến. Ai làm đúng lời dặn dòng 1 của file đó
    (*"copy thành .env và điền giá trị thật"*) sẽ nhận:

        OSRM_BASE_URL       = 'https://router.project-osrm.org   # KHÔNG cần key. Có cổng: …'
        GRAPHHOPPER_API_KEY = '# graphhopper.com Directions API, tier 2 — ⚠ ĐỔI TÊN …'

    Hậu quả đo được: URL mang khoảng trắng + chữ ⇒ `urllib` nổ `InvalidURL` và bị
    `except Exception` **nuốt** ⇒ mirror B mất **lặng lẽ**. Tệ hơn ở tầng 2: chuỗi chú thích là
    **truthy** nên nó **vượt qua** guard `if not api_key` ⇒ gọi GraphHopper thật với khoá rác ⇒
    401 sau ~5 s ⇒ tầng 2 *trông như đã cấu hình* mà chết.

    Và biến thể **thường gặp nhất**: người dùng gõ khoá ngay sau `=`, giữ chú thích lại —
    `GRAPHHOPPER_API_KEY=abc123   # graphhopper.com …` ⇒ khoá gửi đi là `'abc123   # graphhopper…'`.

    ⚠ Cắt chú thích **không được** làm hỏng giá trị chứa `#` hợp lệ (mật khẩu, fragment URL). Nên:
    chỉ cắt khi `#` đứng sau khoảng trắng (quy ước dotenv), và bỏ qua hoàn toàn nếu giá trị được
    đặt trong dấu nháy.
    """
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]                      # giá trị trong nháy: giữ nguyên, kể cả dấu #
    if v.startswith("#"):
        # `KEY=            # ghi chú` ⇒ biến để TRỐNG chờ điền. Đây là dạng phổ biến nhất và là
        # dạng nguy hiểm nhất: chuỗi chú thích truthy vượt qua mọi guard `if not key`. Ba khoá
        # (`WEATHER`/`JINA`/`STADIA`) đã mang dạng này TRƯỚC UPDATE-135 — tức bug có sẵn, chỉ chưa
        # ai copy `.env.example` nên chưa nổ.
        return ""
    m = re.search(r"\s#", v)
    return v[:m.start()].strip() if m else v


def load_env(path: Path | None = None) -> None:
    """Nạp .env vào os.environ (setdefault — không đè biến đã set).

    Cắt chú thích inline: xem `_strip_inline_comment` — thiếu bước này thì một `.env` copy từ
    `.env.example` cho ra giá trị RÁC mà mọi guard `if not x` đều cho đi qua.
    """
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), _strip_inline_comment(v))


class LLMComposerClient:
    """`.create(**kwargs) -> ComposerOutput`. Khớp interface Composer mong đợi."""

    def __init__(self, base_url=None, api_key=None, primary=None, fallback=None,
                 timeout=45.0):
        load_env()
        self.base_url = base_url or os.environ["OPENAI_BASE_URL"]
        self.api_key = api_key or os.environ["OPENAI_API_KEY"]
        self.primary = primary or os.environ.get("DEFAULT_MODEL", "deepseek-v4-flash")
        self.fallback = fallback or os.environ.get("FALLBACK_MODEL", "gpt-4o-mini")
        from openai import OpenAI  # lazy
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key,
                              max_retries=1, timeout=timeout)
        self.last_usage: dict | None = None

    def create(self, system: str, data: str, instruction: str,
               feature: str = "", repair_hint=None) -> ComposerOutput:
        user = f"{data}\n\n{instruction}\n\n{_OUTPUT_HINT}"
        if repair_hint:
            user += ("\n\nLƯU Ý SỬA LỖI (lần trước vi phạm, sửa đúng): "
                     + "; ".join(str(h) for h in repair_hint))
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]

        # T1 primary (+SDK retry) → T2 reask primary → T3 fallback model
        errs = []
        for attempt, model in ((1, self.primary), (2, self.primary), (3, self.fallback)):
            try:
                return self._call_once(model, messages)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{model}#{attempt}: {type(e).__name__}: {str(e)[:120]}")
                if attempt == 2:  # sau reask, thêm hint lỗi parse cho tầng fallback
                    messages[-1]["content"] += f"\n\n(JSON lần trước lỗi: {errs[-1]})"
        raise LLMUnavailable("; ".join(errs))

    def _call_once(self, model: str, messages: list[dict]) -> ComposerOutput:
        r = self._client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_object"}, max_tokens=1200,
        )
        self.last_usage = r.usage.model_dump() if r.usage else None
        content = r.choices[0].message.content or ""
        obj = json.loads(content)  # ném JSONDecodeError → tầng sau
        if not isinstance(obj.get("message_template"), str) or not obj["message_template"]:
            raise ValueError("thiếu message_template")
        return ComposerOutput(
            message_template=obj["message_template"],
            advice_spec=obj.get("advice_spec"),
            citations=list(obj.get("citations", [])),
            caveats=list(obj.get("caveats", [])),
        )
