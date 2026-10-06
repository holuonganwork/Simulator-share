"""Strict, tool-free provider adapter for AdviceCheckpoint language enrichment."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import threading
from typing import Any, Callable, Protocol


class ProviderError(RuntimeError):
    """Redacted provider boundary error."""


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


@dataclass(frozen=True)
class AgentRequest:
    request_type: str
    input: dict
    prompt_version: str
    schema_version: str
    policy_version: str
    model_version: str
    # Optional caller-owned chat messages.  The original proactive path lets this adapter
    # write the prompt, but a caller with its own task instruction and output schema (the
    # episode narration layer) must be able to supply both.  Left ``None``, behaviour is
    # exactly as before; the field is additive so existing callers are unaffected.
    messages: tuple[dict, ...] | None = None
    json_mode: bool = True


@dataclass(frozen=True)
class ProviderResult:
    output: Any
    model_version: str
    usage: dict
    latency_ms: float | None = None


class AdviceAgentProvider(Protocol):
    model_version: str

    def generate(self, request: AgentRequest) -> ProviderResult: ...


def build_agent_request(input_payload: dict, *, request_type: str = "proactive",
                        prompt_version: str = "advice-checkpoint-v1",
                        policy_version: str = "unknown",
                        model_version: str = "unknown") -> AgentRequest:
    """Wrap an already schema-validated allowlist payload.

    The adapter intentionally does not accept solver reports, stores or driver
    objects; callers must construct the structured input before this boundary.
    """
    if not isinstance(input_payload, dict):
        raise TypeError("agent input phải là object")
    return AgentRequest(
        request_type=request_type, input=dict(input_payload),
        prompt_version=prompt_version,
        schema_version=str(input_payload.get("schema_version", "1.0.0")),
        policy_version=policy_version, model_version=model_version)


VALID_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})


def reasoning_effort_from_env(getenv: Callable[[str, str], str] | None = None) -> str | None:
    """How hard the model may think before it answers, or ``None`` to not ask.

    One knob for every provider instance in the process, because the cost it controls is a
    property of the model rather than of any one caller.  An unrecognised value returns
    ``None`` rather than being forwarded: a typo here would otherwise reach the gateway as a
    400 on every single call, which is a worse failure than silently keeping the default.
    """
    read = getenv or (lambda key, default: os.getenv(key, default))
    value = str(read("LLM_REASONING_EFFORT", "") or "").strip().lower()
    return value if value in VALID_REASONING_EFFORTS else None


class OpenAIAdviceProvider:
    """OpenAI-compatible JSON adapter with no tool access.

    It is lazy: importing/constructing the SDK happens only on the first
    generation call.  Credentials are never included in results or errors.
    """

    def __init__(self, *, api_key: str, base_url: str | None, model: str,
                 timeout_s: float = 8.0,
                 max_output_tokens: int = 256,
                 max_calls: int | None = 20,
                 kill_switch: bool = False,
                 temperature: float | None = None,
                 reasoning_effort: str | None = None,
                 client_factory: Callable[..., Any] | None = None):
        if not api_key:
            raise ProviderUnavailable("provider credentials unavailable")
        if not model:
            raise ProviderUnavailable("provider model unavailable")
        self._api_key = api_key
        self.base_url = base_url
        self.model_version = model
        self.timeout_s = float(timeout_s)
        self.max_output_tokens = max(1, int(max_output_tokens))
        # The configured model reasons before it writes, and on a narration prompt it spent
        # 3.794 reasoning tokens and 32.3 s to produce 396 characters.  Asking for no
        # reasoning returned comparable prose in 3.2 s.  Left as None the key is omitted
        # entirely, so a provider that has never heard of it is unaffected.
        self.reasoning_effort = reasoning_effort or None
        self.max_calls = None if max_calls is None else max(0, int(max_calls))
        self.kill_switch = bool(kill_switch)
        self.temperature = temperature
        self._calls = 0
        self._budget_lock = threading.Lock()
        self._client_factory = client_factory
        self._client: Any | None = None

    @classmethod
    def from_env(cls, *, timeout_s: float = 8.0) -> "OpenAIAdviceProvider":
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ProviderUnavailable("provider credentials unavailable")
        configured_timeout = os.getenv("ADVICE_AGENT_TIMEOUT_S")
        try:
            timeout = float(configured_timeout) if configured_timeout else timeout_s
        except ValueError:
            raise ProviderUnavailable("provider timeout configuration invalid") from None
        configured_max_calls = os.getenv("ADVICE_AGENT_MAX_CALLS", "20")
        try:
            max_calls = int(configured_max_calls)
        except ValueError:
            raise ProviderUnavailable("provider budget configuration invalid") from None
        configured_max_tokens = os.getenv("ADVICE_AGENT_MAX_OUTPUT_TOKENS", "256")
        try:
            max_tokens = int(configured_max_tokens)
        except ValueError:
            raise ProviderUnavailable("provider token budget configuration invalid") from None
        return cls(api_key=key, base_url=os.getenv("OPENAI_BASE_URL"),
                   model=os.getenv("DEFAULT_MODEL", ""), timeout_s=timeout,
                   max_output_tokens=max_tokens, max_calls=max_calls,
                   reasoning_effort=reasoning_effort_from_env(),
                   kill_switch=os.getenv("ADVICE_AGENT_KILL_SWITCH", "0") == "1")

    def __repr__(self) -> str:  # never expose credential in diagnostics
        # A base URL can itself contain userinfo/query credentials in a misconfigured
        # environment; omit it rather than relying on callers to redact repr output.
        return (f"OpenAIAdviceProvider(model={self.model_version!r}, "
                f"timeout_s={self.timeout_s!r}, max_calls={self.max_calls!r})")

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        factory = self._client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except Exception as exc:  # SDK is optional until internal-live is enabled
                raise ProviderUnavailable("provider sdk unavailable") from exc
            factory = OpenAI
        try:
            kwargs = {"api_key": self._api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = factory(**kwargs)
        except Exception as exc:
            raise ProviderUnavailable("provider client unavailable") from exc
        return self._client

    @staticmethod
    def _usage(response: Any) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"input_tokens": None, "output_tokens": None, "cost_usd": None}
        def read(name: str):
            if isinstance(usage, dict):
                return usage.get(name)
            return getattr(usage, name, None)
        return {
            "input_tokens": read("prompt_tokens") or read("input_tokens"),
            "output_tokens": read("completion_tokens") or read("output_tokens"),
            "cost_usd": None,
        }

    def generate(self, request: AgentRequest) -> ProviderResult:
        import time

        with self._budget_lock:
            if self.kill_switch:
                raise ProviderUnavailable("provider kill switch enabled")
            if self.max_calls is not None and self._calls >= self.max_calls:
                raise ProviderUnavailable("provider call budget exhausted")
            self._calls += 1
        client = self._client_instance()
        body = json.dumps(request.input, ensure_ascii=False, sort_keys=True)
        if request.messages:
            messages = [dict(message) for message in request.messages]
        else:
            messages = [
                {"role": "system", "content": (
                    "Chỉ trả JSON theo schema agent_presentation_output. "
                    "Không thay đổi hành động, khung giờ, số liệu hoặc provenance.")},
                {"role": "user", "content": body},
            ]
        kwargs: dict[str, Any] = {
            "model": self.model_version,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
            "timeout": self.timeout_s,
        }
        if getattr(request, "json_mode", True):
            kwargs["response_format"] = {"type": "json_object"}
        # Only sent when configured: an unknown parameter is a hard 400 on some gateways,
        # so the default path must stay byte-identical to what it was.
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        # 🔴 NHIỆT ĐỘ — thêm 2026-08-23 sau khi Cường hỏi *"cùng một câu hỏi nhưng nhiều lần ra
        # các câu khác nhau, có lúc rơi vào CLARIFY... tìm lý do?"*
        #
        # Đo bằng cách gọi THẲNG bộ phân loại 12 lượt, cùng một câu:
        #
        #     "toi co can mac ao cua xanh khong"
        #     -> QUERY_POLICY 5 luot (conf .75-.85) · CLARIFY 7 luot (conf .50)
        #     -> raw_ok=True CA 12 LUOT
        #
        # `raw_ok=True` cả 12 loại trừ mọi giả thuyết hạ tầng: không lỗi provider, không parse
        # hỏng, không hết ngân sách. Chính **model** trả nhãn khác nhau trên đầu vào **giống hệt**.
        #
        # ⚠ Và nó giống hệt thật: `build_messages(safe)` chỉ nhận câu đã làm sạch — **không có
        # lịch sử hội thoại**. Nên giả thuyết *"context ingestion kém / vấn đề memory"* không
        # đúng ở tầng này; prompt bất biến, chỉ có phép lấy mẫu là ngẫu nhiên.
        #
        # Trước hôm nay không dòng nào trong `src/` đặt `temperature` ⇒ dùng mặc định của API
        # (thường 1.0) cho một tác vụ **phân loại vào enum đóng**, nơi sáng tạo là thuần tuý tác
        # hại.
        #
        # ⚠ Chỉ gửi khi được khai, đúng kỷ luật của dòng `reasoning_effort` ngay trên: một tham
        # số lạ là 400 cứng ở vài gateway, nên đường mặc định phải giữ nguyên từng byte.
        if self.temperature is not None:
            kwargs["temperature"] = float(self.temperature)
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            name = type(exc).__name__.lower()
            if isinstance(exc, TimeoutError) or "timeout" in name:
                raise ProviderTimeout("provider timeout") from None
            raise ProviderError("provider request failed") from None
        latency_ms = (time.perf_counter() - started) * 1000
        try:
            content = response.choices[0].message.content
        except Exception:
            raise ProviderError("provider response malformed") from None
        try:
            output = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            output = content
        return ProviderResult(output=output, model_version=self.model_version,
                              usage=self._usage(response), latency_ms=latency_ms)
