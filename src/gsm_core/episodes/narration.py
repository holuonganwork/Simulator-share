"""FactPack → LLM → verifier → fallback, with a cache and a hard call policy.

This is the only place in the repo that turns a validated FactPack into driver-facing
prose, and it is built so that the interesting failure — "the model said something we
cannot defend" — costs nothing:

    baseline = template(pack)          # computed first, always available
    candidate = provider(pack)         # may time out, may be malformed, may be wrong
    return candidate if verify(candidate) else baseline

Three properties follow from that ordering and are worth stating because each one was a
way this feature could have gone wrong:

* **A provider outage is invisible to the driver.**  The template answer was already
  computed before the call was made, so there is no state in which the surface is empty.
* **The model never gets a second chance at the same claim.**  There is no repair loop: a
  rejected answer is replaced, not renegotiated, because re-prompting until something
  passes optimises the wording against the verifier rather than against the facts.
* **Rejections are cached too.**  A pack that produced a violation will produce it again;
  paying for that twice teaches us nothing and costs the demo its latency budget.

Calls happen only on an explicit user tap.  Nothing in the replay loop reaches this module,
which is what keeps stepping the cursor a zero-token operation.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from gsm_core.episodes.factpack import (
    MAX_REVIEW_POINTS,
    MAX_REVIEW_WORDS,
    MIN_REVIEW_POINTS,
    TASK_HIL_PAUSE,
    TASK_REVIEW,
    TASK_WHY,
    FactPack,
)
from gsm_core.episodes.narration_verifier import Verdict, verify

# Presentation modes.  ``off`` never calls a provider; ``shadow`` calls it, verifies it and
# logs it but still shows the template; ``live`` shows a passing model answer.
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_LIVE = "live"
VALID_MODES = frozenset({MODE_OFF, MODE_SHADOW, MODE_LIVE})

PROMPT_VERSION = "episode-narration-v1"

_SYSTEM = (
    "Bạn viết phần diễn giải ngắn cho ứng dụng tài xế Xanh SM, bằng tiếng Việt.\n"
    "Bạn KHÔNG phải người ra quyết định. Mọi hành động, khung giờ và con số đã được hệ "
    "thống tính sẵn và nằm trong FactPack; việc của bạn là chọn ra vài dữ kiện quan trọng "
    "nhất và diễn đạt cho dễ hiểu.\n"
    "Quy tắc bắt buộc:\n"
    "- Chỉ dùng dữ kiện có trong FactPack. Không thêm dữ kiện mới.\n"
    "- Chỉ viết con số xuất hiện trong validated_numbers (dùng đúng trường display). Con số "
    "nào không có trong đó thì viết bằng chữ hoặc bỏ hẳn — tuyệt đối không ước lượng, "
    "không làm tròn, không tự cộng trừ.\n"
    # The model reliably invents ids when it is not told where they live: it either
    # enumerates a contiguous f1..fN range (the fact ids are NOT contiguous — a derived fact
    # sits in inferred_facts, so observed_facts skips its number) or it answers with n* ids
    # from validated_numbers, which are a different namespace entirely.  Measured: 27 of 100
    # cases failed this way once reasoning was switched off.
    "- used_fact_ids: CHÉP NGUYÊN VĂN trường fact_id của những dữ kiện bạn thực sự dùng, "
    "lấy từ observed_facts và inferred_facts. Không suy ra dãy liên tiếp, không tự đặt id "
    "mới, và KHÔNG dùng number_id (n1, n2…) — đó là danh sách khác.\n"
    "- Mô tả theo THỨ TỰ THỜI GIAN, không khẳng định việc này gây ra việc kia.\n"
    "- Việc trong future_plan là việc SẮP TỚI, không được viết như đang diễn ra.\n"
    "- Dữ kiện có evidence_mode INFERRED phải nói dè dặt ('theo ghi nhận', 'có thể').\n"
    "- Văn phong bình thường, thân thiện, không hô hào, không xin lỗi, không hỏi lại.\n"
    "- Trả về DUY NHẤT một JSON object, không kèm giải thích nào khác."
)

_WHY_INSTRUCTION = (
    "Nhiệm vụ: giải thích ngắn gọn cho tài xế điều gì vừa thay đổi trong kế hoạch và hiện "
    "tại kế hoạch là gì.\n"
    "Tối đa 2–3 câu, đọc hết trong vài giây. Không kể lại toàn bộ log.\n"
    "Không viết ý rỗng kiểu '0 lần huỷ', 'chưa có gì thay đổi' hay 'chưa có bước tiếp "
    "theo' — nếu không có gì đáng nói thì nói ngắn hơn.\n"
    'Trả JSON: {"summary": "<2-3 câu>", "used_fact_ids": [<các fact_id chép từ FactPack>]}'
)

_REVIEW_INSTRUCTION = (
    # 🔴 Cường 2026-08-24: *"cần chi tiết để tài xế có thể hiểu được cái gì làm ổn rồi, cái gì
    # chưa ổn, vân vân"*. Bản cũ chỉ bảo *"chọn điều đáng chú ý"* — model liệt kê số, không
    # đánh giá. Nay nói rõ HAI CHIỀU: cái được và cái chưa được.
    #
    # ⚠ "Đánh giá" ở đây là so số của ca với ngưỡng ĐÃ CHO, không phải phán xét bác tài. Ranh
    # giới §5 giữ nguyên: không hứa thu nhập, không nhận xét sức khoẻ, không khuyên nhận/bỏ
    # một cuốc cụ thể.
    "Nhiệm vụ: viết nhận xét cuối ca. Chọn "
    f"{MIN_REVIEW_POINTS}–{MAX_REVIEW_POINTS} điều đáng chú ý nhất trong ca, bỏ qua phần "
    "không có thông tin gì.\n"
    f"Tổng độ dài tối đa {MAX_REVIEW_WORDS} từ. Một câu tổng quan, rồi các ý ngắn.\n"
    "Không viết ý rỗng kiểu '0 lần huỷ' hay 'chưa có bước tiếp theo'.\n"
    "Nói rõ HAI CHIỀU: điều gì trong ca này ĐÃ ỔN, và điều gì CHƯA ỔN — mỗi bên kèm con số "
    "làm căn cứ. Nếu một chỉ số dưới ngưỡng chính sách thì nêu cả số hiện tại lẫn ngưỡng.\n"
    "Được viết dài. Ưu tiên bác tài HIỂU hơn là câu ngắn.\n"
    'Trả JSON: {"summary": "<1 câu>", "key_points": ["<ý>", ...], '
    '"used_fact_ids": [<các fact_id chép từ FactPack>], '
    '"caveat": null hoặc "<một câu lưu ý>"}'
)

# 🔴 VIẾT LẠI 2026-08-23 — Cường: *"tôi muốn TA LÀ TÀI XẾ, không nhắc gì tới thói quen hay
# mô phỏng → ta hành động, AI advisor cho lời khuyên, hết. Không thói quen."*
#
# Bản cũ dặn model đúng ba điều Cường vừa cấm:
#
#     "cho NGƯỜI ĐANG ĐÓNG VAI tài xế trong MÔ PHỎNG"   -> nói người dùng đang diễn
#     "BẢN NĂNG MÔ PHỎNG nghiêng về phía nào"           -> nói về thói quen của actor sim
#     "Đây là ROLE-PLAY: NGƯỜI CHƠI quyết định"         -> gọi tài xế là người chơi
#
# ## Vì sao "bản năng mô phỏng" nguy hiểm hơn hai cái kia
#
# Hai cụm đầu chỉ phá nhập vai. Cụm thứ ba **mượn uy tín**: `choose_idle_action` là tham số
# hành vi của actor SIM, không phải phán quyết của solver nào. Đặt nó cạnh một nút làm theo
# thì tài xế đọc thành lời khuyên có căn cứ — trong khi nó chỉ là một hằng số mô hình.
# Cùng lớp lỗi mà `_idle_lines` đã phải sửa (chỉ nói khi `hint.source == "advisor"`), nhưng
# ở đây nó nằm trong PROMPT nên không cổng nào của tầng dữ kiện bắt được.
#
# ⚠ Ràng buộc "chỉ thuật, không tự thêm lời khuyên" GIỮ NGUYÊN — nó là ranh giới §5, không
# phải chuyện nhập vai. Chỉ đổi cách gọi người đọc và bỏ nguồn "bản năng".
_HIL_INSTRUCTION = (
    "Nhiệm vụ: thuật lại điểm dừng hiện tại cho bác tài — chuyện gì đang chờ bác quyết định, "
    "kèm căn cứ.\n"
    "Tối đa 1–2 câu. Bác tài là người quyết định, bạn chỉ thuật lại. Không thêm lời "
    "khuyên mới ngoài gợi ý đã có trong FactPack, không hứa hẹn thu nhập, không nhận xét "
    "sức khoẻ hay mức mệt.\n"
    "TUYỆT ĐỐI không nhắc tới mô phỏng, dữ liệu giả, đóng vai, người chơi, hay thói quen/"
    "bản năng của bác tài. Bác tài đang chạy ca của chính mình.\n"
    'Trả JSON: {"summary": "<1-2 câu>", "used_fact_ids": [<các fact_id chép từ FactPack>]}'
)

INSTRUCTIONS = {TASK_WHY: _WHY_INSTRUCTION, TASK_REVIEW: _REVIEW_INSTRUCTION,
                TASK_HIL_PAUSE: _HIL_INSTRUCTION}


def _ms(value: float | None) -> int | None:
    return None if value is None else int(round(float(value)))


@dataclass(frozen=True)
class NarrationResult:
    """What the surface renders, plus everything needed to audit how it got there."""

    task_type: str
    episode_id: str
    material_revision: int
    summary: str
    key_points: tuple[str, ...]
    caveat: str | None
    source: str                       # "template" | "llm"
    fallback_reason: str | None
    verdict: Verdict | None
    cache_hit: bool
    model_version: str | None
    provider_latency_ms: float | None
    verifier_latency_ms: float | None
    total_latency_ms: float | None
    usage: dict = field(default_factory=dict)
    used_fact_ids: tuple[str, ...] = ()
    baseline: str = ""
    fingerprint: str = ""

    @property
    def rendered_text(self) -> str:
        """Exactly what the driver reads, whichever branch produced it.

        Tests and the evaluation hold the template fallback to the same verifier as a model
        answer, and this is the string they check.  It is deliberately not
        ``pack.template_baseline``: the baseline lists every episode, while the surface
        renders a head plus at most four bullets.
        """
        parts = [self.summary, *self.key_points]
        if self.caveat:
            parts.append(self.caveat)
        return " ".join(part for part in parts if part).strip()

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "episode_id": self.episode_id,
            "material_revision": int(self.material_revision),
            "summary": self.summary,
            "key_points": list(self.key_points),
            "caveat": self.caveat,
            "presentation_source": self.source,
            "fallback_reason": self.fallback_reason,
            "verdict": None if self.verdict is None else self.verdict.to_dict(),
            "cache_hit": bool(self.cache_hit),
            "model_version": self.model_version,
            # Rounded here, not in the browser.  The client is not allowed to derive a
            # displayed value, and that rule does not get an exception for telemetry.
            "latency": {
                "provider_ms": _ms(self.provider_latency_ms),
                "verifier_ms": _ms(self.verifier_latency_ms),
                "total_ms": _ms(self.total_latency_ms),
            },
            "usage": dict(self.usage),
            "used_fact_ids": list(self.used_fact_ids),
            "fingerprint": self.fingerprint,
        }


class NarrationCache:
    """Bounded LRU keyed by ``FactPack.cache_key``.

    The key includes the material revision *and* the pack fingerprint.  The revision alone
    is not enough — two revisions can be materially identical — and the fingerprint alone
    would not express that a revision bump is what the product considers a new statement.
    """

    def __init__(self, max_entries: int = 256):
        self.max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[tuple, NarrationResult] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, pack: FactPack) -> NarrationResult | None:
        key = pack.cache_key
        if key not in self._entries:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return self._entries[key]

    def put(self, pack: FactPack, result: NarrationResult) -> None:
        key = pack.cache_key
        self._entries[key] = result
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._entries)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"entries": len(self._entries), "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0}


def _fit_points(head: str, points: tuple[str, ...], caveat: str | None = None
                ) -> tuple[str, ...]:
    """Take whole bullets while the note still fits the review word budget.

    The fallback is held to the same limit as a model answer, so it has to make the same
    trade-off: a shift with seven verbose episodes gets fewer bullets rather than a longer
    screen.  Bullets are never truncated mid-sentence — half a claim is worse than one
    fewer claim — and at least one is always kept so the surface is never empty.

    The caveat counts against the budget because the driver reads it too; leaving it out of
    the arithmetic was how the first version produced notes 3–33 words over the limit.
    """
    chosen: list[str] = []
    used = len(head.split()) + len((caveat or "").split())
    for point in points[:MAX_REVIEW_POINTS]:
        cost = len(point.split())
        if chosen and used + cost > MAX_REVIEW_WORDS:
            break
        chosen.append(point)
        used += cost
    return tuple(chosen)


def template_result(pack: FactPack, *, reason: str, cache_hit: bool = False,
                    total_ms: float | None = None,
                    verdict: Verdict | None = None) -> NarrationResult:
    """The deterministic answer, shaped exactly like a model answer."""
    if pack.task_type == TASK_REVIEW:
        # The baseline lists every episode; the surface renders a head plus a few bullets.
        # The head still states the true total, so dropping bullets shortens the note
        # without hiding that the shift had more in it.
        head = (f"Ca này ghi nhận {len(pack.baseline_points)} diễn biến."
                if pack.baseline_points else pack.template_baseline)
        caveat = pack.caveats[0] if pack.caveats else None
        points = _fit_points(head, pack.baseline_points, caveat)
        return NarrationResult(
            task_type=pack.task_type, episode_id=pack.episode_id,
            material_revision=pack.material_revision, summary=head,
            key_points=points, caveat=caveat,
            source="template", fallback_reason=reason, verdict=verdict,
            cache_hit=cache_hit, model_version=None, provider_latency_ms=None,
            verifier_latency_ms=None, total_latency_ms=total_ms,
            baseline=pack.template_baseline, fingerprint=pack.fingerprint)
    return NarrationResult(
        task_type=pack.task_type, episode_id=pack.episode_id,
        material_revision=pack.material_revision,
        summary=pack.template_baseline, key_points=(),
        caveat=(pack.caveats[0] if pack.caveats else None),
        source="template", fallback_reason=reason, verdict=verdict,
        cache_hit=cache_hit, model_version=None, provider_latency_ms=None,
        verifier_latency_ms=None, total_latency_ms=total_ms,
        baseline=pack.template_baseline, fingerprint=pack.fingerprint)


def build_messages(pack: FactPack) -> list[dict]:
    """System + user messages.  The user turn is the pack, never a raw trace."""
    instruction = INSTRUCTIONS.get(pack.task_type)
    if instruction is None:
        raise ValueError(f"task_type không hỗ trợ: {pack.task_type!r}")
    body = json.dumps(pack.model_payload(), ensure_ascii=False, sort_keys=True)
    return [{"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"FactPack:\n{body}\n\n{instruction}"}]


def _parse(output: Any) -> dict | None:
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def narrate(pack: FactPack, *, provider: Any = None, mode: str = MODE_OFF,
            cache: NarrationCache | None = None,
            now: Callable[[], float] = time.perf_counter) -> NarrationResult:
    """Produce the narration for one pack, never raising to the caller.

    A caller is a tap on a phone.  Every provider failure mode — unavailable, timeout,
    malformed JSON, rejected content — resolves to the template answer with the reason
    recorded, so the surface always has something true to show.
    """
    started = now()
    if mode not in VALID_MODES:
        return template_result(pack, reason="invalid_mode",
                               total_ms=(now() - started) * 1000)
    if mode == MODE_OFF or provider is None:
        return template_result(
            pack, reason=("llm_disabled" if mode == MODE_OFF else "provider_unavailable"),
            total_ms=(now() - started) * 1000)

    if cache is not None:
        cached = cache.get(pack)
        if cached is not None:
            return NarrationResult(**{**cached.__dict__, "cache_hit": True})

    from gsm_core.advisor.advice_agent import AgentRequest, ProviderError

    request = AgentRequest(
        request_type=pack.task_type, input=pack.model_payload(),
        prompt_version=PROMPT_VERSION, schema_version=pack.schema_version,
        policy_version="episode-contract-1.1.0",
        model_version=getattr(provider, "model_version", "unknown"),
        messages=tuple(build_messages(pack)))

    call_started = now()
    try:
        provider_result = provider.generate(request)
    except ProviderError as exc:
        reason = type(exc).__name__
        result = template_result(pack, reason=f"provider_error:{reason}",
                                 total_ms=(now() - started) * 1000)
        if cache is not None:
            cache.put(pack, result)
        return result
    except Exception:  # noqa: BLE001 - a tap must not surface a stack trace
        result = template_result(pack, reason="provider_error:unexpected",
                                 total_ms=(now() - started) * 1000)
        if cache is not None:
            cache.put(pack, result)
        return result
    provider_ms = (getattr(provider_result, "latency_ms", None)
                   or (now() - call_started) * 1000)

    parsed = _parse(getattr(provider_result, "output", None))
    if parsed is None:
        # Keep the provider's latency and token usage on the fallback.  A malformed answer
        # still cost time and money, and an evaluation that dropped those rows would report
        # the feature as cheaper and faster than it is.
        result = template_result(pack, reason="malformed_output",
                                 total_ms=(now() - started) * 1000)
        result = NarrationResult(**{
            **result.__dict__, "model_version": provider_result.model_version,
            "provider_latency_ms": provider_ms,
            "usage": dict(getattr(provider_result, "usage", {}) or {})})
        if cache is not None:
            cache.put(pack, result)
        return result

    verify_started = now()
    verdict = verify(parsed, pack)
    verifier_ms = (now() - verify_started) * 1000

    if not verdict.passed or mode == MODE_SHADOW:
        reason = "verifier_rejected" if not verdict.passed else "shadow_mode"
        result = template_result(pack, reason=reason, verdict=verdict,
                                 total_ms=(now() - started) * 1000)
        result = NarrationResult(**{
            **result.__dict__, "model_version": provider_result.model_version,
            "provider_latency_ms": provider_ms, "verifier_latency_ms": verifier_ms,
            "usage": dict(getattr(provider_result, "usage", {}) or {})})
        if cache is not None:
            cache.put(pack, result)
        return result

    points = tuple(str(point) for point in (parsed.get("key_points") or ())
                   ) if pack.task_type == TASK_REVIEW else ()
    result = NarrationResult(
        task_type=pack.task_type, episode_id=pack.episode_id,
        material_revision=pack.material_revision,
        summary=str(parsed.get("summary") or "").strip(),
        key_points=points,
        caveat=(str(parsed["caveat"]) if parsed.get("caveat") else None),
        source="llm", fallback_reason=None, verdict=verdict, cache_hit=False,
        model_version=provider_result.model_version,
        provider_latency_ms=provider_ms, verifier_latency_ms=verifier_ms,
        total_latency_ms=(now() - started) * 1000,
        usage=dict(getattr(provider_result, "usage", {}) or {}),
        used_fact_ids=tuple(str(item) for item in (parsed.get("used_fact_ids") or ())),
        baseline=pack.template_baseline, fingerprint=pack.fingerprint)
    if cache is not None:
        cache.put(pack, result)
    return result


def _load_dotenv_once() -> None:
    """Best-effort ``.env`` load.  ``load_env`` uses ``setdefault``, so it never overrides.

    Without this the mode is decided by import order: nothing in the FastAPI app loads
    ``.env`` explicitly, so ``mode_from_env()`` sees whatever happens to be in
    ``os.environ`` at the moment ``DemoSessionService`` is constructed.  It currently reads
    ``live`` only because some other module in the import chain happens to call
    ``load_env`` first.  That is an accident, and the day it moves, narration silently
    turns off with no error anywhere.
    """
    try:
        from gsm_core.advisor.llm_client import load_env
        load_env()
    except Exception:  # noqa: BLE001 - no .env is a normal state, not a failure
        pass


def mode_from_env(getenv: Callable[[str, str], str] | None = None) -> str:
    """Resolve the narration mode, failing closed on anything unrecognised."""
    import os

    if getenv is None:
        _load_dotenv_once()
    read = getenv or os.getenv
    if read("ADVICE_AGENT_KILL_SWITCH", "0") == "1":
        return MODE_OFF
    mode = str(read("EPISODE_NARRATION_MODE", MODE_OFF) or MODE_OFF).strip().lower()
    return mode if mode in VALID_MODES else MODE_OFF


# Narration needs a far larger completion budget than the older advice agent, and every
# number here is measured rather than guessed.  The configured model reasons before it
# answers, and the reasoning — not the answer — is what consumes the budget:
#
#   WHY    (2.761 input tokens): reasoning 1.232 / 3.511 / 3.907 / 4.131, 4 of 4 answered
#   REVIEW (3.552 input tokens): reasoning 4.401 / 4.683 / 6.708 / 8.001, 3 of 4 answered
#
# At the advice agent's 256-token default every single call returns
# ``finish_reason=length`` with an empty string, which the orchestrator would faithfully
# report as ``malformed_output`` — a real failure with a completely misleading name.
#
# 8.000 was too close to the working distribution to be a safe ceiling.  Measured over 100
# cases on 2026-08-07: successful REVIEW answers ran to 7.705 output tokens, a 4 % margin,
# and *every* malformed answer in the run stopped at exactly 8.000 — the model had spent the
# whole budget reasoning and never emitted the closing brace.  It was silently costing 5 %
# of REVIEW answers before anyone changed the prompt, and a slightly longer prompt took it
# to 15 %.  16.000 puts the cliff clear of the distribution instead of inside its tail.
NARRATION_MAX_TOKENS = 16000
# Latency follows the same distribution: 13.8–40.2 s for WHY, 37.6–69.8 s for REVIEW.
# The timeout sits above the observed maximum so a slow answer is served rather than
# discarded after paying for it.
NARRATION_TIMEOUT_S = 120.0
NARRATION_MAX_CALLS = 40


def _int_env(read, name: str, default: int) -> int:
    try:
        return int(read(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def provider_from_env(getenv: Callable[..., Any] | None = None, *,
                      mode: str | None = None):
    """Build the narration provider, or ``None`` when it is not configured.

    Returning ``None`` rather than raising is deliberate: every caller's fallback for "no
    provider" is the template answer, so a missing key is a degraded surface, never a 500.
    Credentials are read here and never returned, logged or sent to the client.

    ``mode`` cho phép một bề mặt khác (HIL) dùng cờ bật/tắt CỦA RIÊNG NÓ thay vì
    ``EPISODE_NARRATION_MODE`` — không có nó, bật narration HIL nghĩa là bật luôn narration
    của episode, hai bề mặt bị trói vào một công tắc. ``None`` giữ nguyên hành vi cũ;
    giá trị lạ fail-closed về ``off``.
    """
    import os

    read = getenv or os.getenv
    resolved = mode_from_env(read) if mode is None else (
        mode if mode in VALID_MODES else MODE_OFF)
    if resolved == MODE_OFF:
        return None
    try:
        from gsm_core.advisor.llm_client import load_env
        load_env()
    except Exception:  # noqa: BLE001 - a missing .env is not an error, just no provider
        pass
    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("DEFAULT_MODEL", "")
    if not api_key or not model:
        return None
    try:
        from gsm_core.advisor.advice_agent import (
            OpenAIAdviceProvider,
            reasoning_effort_from_env,
        )
        return OpenAIAdviceProvider(
            api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"), model=model,
            timeout_s=float(_int_env(read, "EPISODE_NARRATION_TIMEOUT_S",
                                     int(NARRATION_TIMEOUT_S))),
            max_output_tokens=_int_env(read, "EPISODE_NARRATION_MAX_TOKENS",
                                       NARRATION_MAX_TOKENS),
            max_calls=_int_env(read, "EPISODE_NARRATION_MAX_CALLS", NARRATION_MAX_CALLS),
            reasoning_effort=reasoning_effort_from_env(read),
            kill_switch=os.getenv("ADVICE_AGENT_KILL_SWITCH", "0") == "1")
    except Exception:  # noqa: BLE001 - SDK absent or misconfigured: degrade to template
        return None
