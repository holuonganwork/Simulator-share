"""Code-side verifier for model-written narration.  It decides; the model does not.

Every rule here answers one question: *could this sentence have been written from the
FactPack alone?*  A rule that cannot be decided from the pack is not a rule — it is a
preference, and preferences belong in the prompt.

The verifier is deliberately allowed to be strict.  A false rejection costs one template
sentence, which is the sentence we would have shipped anyway.  A false acceptance ships a
number, a cause or a health claim we cannot defend to a driver.  The asymmetry is the
whole design, and it is why ``verify`` returns the reasons rather than a repaired string:
this module never edits model output, because an edited claim is still the model's claim
with our name on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gsm_core.advisor._text import normalize_vi as _norm
from gsm_core.episodes.factpack import (
    ACTION_PHRASES,
    MAX_REVIEW_POINTS,
    MAX_REVIEW_WORDS,
    MIN_REVIEW_POINTS,
    PRESCRIPTIVE_ACTION_PHRASES,
    PRESCRIPTIVE_MARKERS,
    TASK_REVIEW,
    FactPack,
)
from gsm_core.episodes.templates import BANNED_PHRASES

# §5: fields that would let the model author a decision instead of describing one.
FORBIDDEN_OUTPUT_FIELDS: frozenset[str] = frozenset({
    "recommended_action", "action_window", "estimated_income", "target",
    "suggested_location", "objective_priority", "lifecycle_command",
})

# Causal connectives.  "sau khi" (after) is allowed and "vì sao" is the question itself, so
# neither appears here: the rule targets assertions that X produced Y.
_CAUSAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bdan den\b", "dẫn đến"),
    (r"\bgay ra\b", "gây ra"),
    (r"\bkhien (cho )?\b", "khiến"),
    (r"\bnho (do|vay)\b", "nhờ đó"),
    (r"\bla nguyen nhan\b", "là nguyên nhân"),
    (r"\bdo (do|vay) (ma )?\b", "do đó"),
    (r"\bboi vi\b", "bởi vì"),
    (r"\bnguyen nhan la\b", "nguyên nhân là"),
    (r"\bvi .{0,40}\bnen\b", "vì … nên"),
    (r"\bdo .{0,40}\bnen\b", "do … nên"),
)

# Applied to **model-written prose only** — see :func:`check_causal`.
#
# The paired forms above were covered; the bare consequent "X nên Y" was not, and it is
# the form a model reaches for first: "Bạn đã chờ 30 phút nên kế hoạch bị đổi" passed the
# entire verifier until the P2 slice walked a wait through it end to end.
#
# Two exclusions, both load-bearing:
#
# * ``nên`` is also the ordinary word for "should".  The lookbehinds drop the prescriptive
#   sense, which is not being let through — it is rejected by the *action* rule (N2), the
#   one that actually describes it.  Double-flagging would also make the causal counter
#   useless as evidence of how often the model asserts causation.
# * A consequent about the assistant itself is not a claim about the driver's world.
#   "Chưa đủ chi phí đã biết để tính thu nhập ròng, nên mình không hiện số đó" is the
#   system explaining its own silence, and it is both true and verifiable.  Nine of this
#   repo's deterministic refusal strings have exactly that shape; flagging them would turn
#   honest copy into SAFE_FALLBACK, which is how a refusal stops being readable.
_SYSTEM_SUBJECT = r"(?:minh|he thong|day la|phan nay|khong (?:hien|dua|tinh|doan|tu)|chua)"
_MODEL_CAUSAL_PATTERNS: tuple[tuple[str, str], ...] = _CAUSAL_PATTERNS + (
    (rf"[^.!?]{{12,}}(?<!biet )(?<!ban )(?<!hay )(?<!can )(?<!luan )\bnen\b "
     rf"(?!{_SYSTEM_SUBJECT}\b)\S",
     "… nên (nhân quả)"),
    # 🔴 `R27` (2026-08-21) — cụm SUY LUẬN, không phải nhân quả cổ điển.
    #
    # Đo được, mẫu sai `M5`: hỏi *"tủ pin nào trống"*, model trả *"bác vẫn đang ở trên
    # ngưỡng đổi pin, **CÓ NGHĨA LÀ** tủ pin không trống"*. Mức pin của XE không nói gì
    # về chỗ trống ở TỦ — hai dữ kiện từ hai nguồn khác nhau, nối bằng một mệnh đề bịa.
    #
    # Verifier canh **con số** rất chặt nhưng **không canh mệnh đề**: mọi con số trong
    # câu ấy đều có nguồn, chỉ mối quan hệ giữa chúng là bịa. Câu như thế đọc lên **chắc
    # chắn hơn** một câu bịa số, vì nó nghe như suy luận.
    #
    # ⚠ Chỉ áp cho văn MODEL: bản mẫu tất định dùng "nghĩa là" để ĐỊNH NGHĨA khái niệm
    # (*"khoán tuần nghĩa là…"*) — việc hợp lệ.
    (rf"[^.!?]{{12,}}\bco nghia la\b (?!{_SYSTEM_SUBJECT}\b)\S",
     "… có nghĩa là (suy luận)"),
    (r"[^.!?]{12,}\bdieu (nay|do) cho thay\b", "điều này cho thấy (suy luận)"),
    (rf"[^.!?]{{12,}}\btuc la\b (?!{_SYSTEM_SUBJECT}\b)\S",
     "… tức là (suy luận)"),
)

_INCOME_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bkiem them\b", "kiếm thêm"),
    (r"\bkiem duoc\b", "kiếm được"),
    (r"\btang thu nhap\b", "tăng thu nhập"),
    (r"\bbu lai\b", "bù lại"),
    (r"\bthu nhap se\b", "thu nhập sẽ"),
    (r"\bse (co|dat|kiem)\b.{0,20}\b(dong|trieu|nghin)\b", "hứa mức tiền"),
    (r"\bdam bao\b.{0,20}\bthu nhap\b", "đảm bảo thu nhập"),
    (r"\bchac chan\b.{0,20}\b(kiem|dat|co)\b", "chắc chắn kiếm/đạt"),
    # 🔴 `T-RECAP-03` — thêm 2026-08-24 sau khi đo bản tổng kết ca thật.
    #
    # Model **tự thêm** cụm *"nhằm tối đa hoá thu nhập"* vào một câu về tiền, và cả tám luật
    # cũ đều cho qua: nó không hứa một **MỨC** nào, không có chữ số, không có "sẽ".
    #
    # Nhưng nó vẫn là một **lời hứa về kết quả** — *"làm X để tối đa hoá thu nhập"* nói rằng
    # X dẫn tới nhiều tiền hơn, mà ta **không có** dữ liệu nào cho phép khẳng định điều đó.
    # Cùng họ với `tang thu nhap` ngay ở trên; khác mỗi cách nói.
    #
    # ⚠ Vì sao chỉ chặn dạng ĐỘNG TỪ, không chặn cụm danh từ *"thu nhập tối đa"*: bài chính
    # sách có thể **trích dẫn** cụm ấy, và `_trong_trich_dan_nguyen_van` chỉ tha cho trích dẫn
    # nguyên văn. Chặn hẹp thì trích dẫn không bị vạ lây.
    (r"\b(toi da hoa|toi uu hoa|toi da hoa) (duoc )?thu nhap\b", "tối đa hoá thu nhập"),
    (r"\b(de|nham|giup) (toi da hoa|tang toi da|toi uu) .{0,12}thu nhap\b",
     "hứa tối ưu thu nhập"),
)

_ORDER_ADVICE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(nen|hay) (nhan|huy|tu choi|bo)\b.{0,12}\b(don|cuoc)\b", "khuyên nhận/huỷ cuốc"),
    (r"\b(nhan|huy|tu choi) (don|cuoc) nay\b", "khuyên về cuốc cụ thể"),
)

_LOCATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bnen (den|toi|di toi|chay ve)\b", "gợi ý nơi đến"),
    (r"\bdiem nong\b", "điểm nóng"),
    (r"\bkhu vuc\b.{0,20}\b(dong khach|nhieu khach|nen)\b", "gợi ý khu vực"),
    (r"\bchuyen sang khu\b", "gợi ý chuyển khu"),
)

_SETTLED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bda chot\b", "đã chốt"),
    (r"\bda thanh toan\b", "đã thanh toán"),
    (r"\bda vao tai khoan\b", "đã vào tài khoản"),
    (r"\bchac chan nhan duoc\b", "chắc chắn nhận được"),
)

_ADHERENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(da )?lam theo\b", "làm theo"),
    (r"\btuan thu\b", "tuân thủ"),
    (r"\bnghe loi\b", "nghe lời"),
    (r"\bbo qua loi khuyen\b", "bỏ qua lời khuyên"),
    (r"\bkhong lam theo\b", "không làm theo"),
)

_INFEASIBLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bkhong con kha thi\b", "không còn khả thi"),
    (r"\bkhong the dat\b", "không thể đạt"),
    (r"\bkhong dat duoc\b", "không đạt được"),
    (r"\bbo muc\b", "bỏ mốc"),
)

# Present-tense framing.  Used only to test whether a *future* action is being described as
# something happening right now.
_PRESENT_MARKERS = ("hien tai", "bay gio", "dang", "luc nay", "hien dang")

_NUMBER_RE = re.compile(r"\d[\d.,:]*")
_URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Verdict:
    passed: bool
    errors: tuple[str, ...] = ()
    checked_chars: int = 0

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(error.split(":", 1)[0] for error in self.errors)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "errors": list(self.errors),
                "reason_codes": list(self.reason_codes),
                "checked_chars": self.checked_chars}


def _digits(token: str) -> str:
    return re.sub(r"[^\d]", "", token)


def check_numbers(text: str, pack: FactPack) -> list[str]:
    """N1 — every numeral must trace to ``validated_numbers`` or an action window.

    Thousand separators make the same amount look like several tokens, so a digits-only
    comparison is accepted as well.  That is a deliberate loosening for *formatting*, not
    for value: "1.234.567" and "1234567" are the same number, "1.2" and "12" both reduce to
    "12" and the pack would have to contain one of them for either to pass.
    """
    allowed = set(pack.allowed_number_tokens)
    allowed_digits = {_digits(token) for token in allowed if _digits(token)}
    stripped = _URL_RE.sub(" ", text)
    errors: list[str] = []
    for match in _NUMBER_RE.finditer(stripped):
        token = match.group().strip(".,")
        if not token:
            continue
        if token in allowed:
            continue
        if _digits(token) and _digits(token) in allowed_digits:
            continue
        errors.append(f"N1: số '{token}' không có trong validated_numbers")
    return errors


# Negation directly in front of a prescriptive marker turns a plan into the absence of a
# plan.  "Chưa có kế hoạch đổi pin còn hiệu lực" is our own A1 copy and prescribes nothing.
_NEGATION_RE = re.compile(r"\b(chua|khong|chang)\b[^,.;:!?]{0,20}$")


def _is_prescriptive(q: str, position: int) -> bool:
    """Is the action at ``position`` being recommended or scheduled, rather than recounted?

    This distinction is the whole of rule N2.  Driver-facing copy legitimately *narrates*
    actions the driver already took — "ghi nhận bắt đầu đổi pin lúc 15:44" is a record, and
    an early version of this rule rejected it as an unauthorised recommendation.  What must
    never appear is an action *proposed* by the narrator that no solver put in the plan.
    """
    window = q[max(0, position - 40):position]
    marker = next((m for m in PRESCRIPTIVE_MARKERS if m in window), None)
    if marker is None:
        return False
    before_marker = window[:window.rindex(marker)]
    return not _NEGATION_RE.search(before_marker)


def _action_hits(text: str) -> list[tuple[str, int]]:
    """Locate action mentions that are framed as recommendations or scheduled steps.

    ``ACTION_PHRASES`` are unambiguous names for an action; ``PRESCRIPTIVE_ACTION_PHRASES``
    are ordinary words ("nghỉ") that only denote one in context.  Both groups still require
    prescriptive framing, because naming an action is not the same as proposing it.
    """
    q = _norm(text)
    hits: list[tuple[str, int]] = []
    for source in (ACTION_PHRASES, PRESCRIPTIVE_ACTION_PHRASES):
        for code, phrases in source.items():
            for phrase in phrases:
                position = q.find(_norm(phrase))
                if position >= 0 and _is_prescriptive(q, position):
                    hits.append((code, position))
                    break
    return hits


def check_actions(text: str, pack: FactPack) -> list[str]:
    """N2/N3 — no unknown action, and no planned action spoken of as current."""
    q = _norm(text)
    errors: list[str] = []
    allowed = pack.allowed_action_codes
    future_only = pack.future_only_action_codes
    for code, position in _action_hits(text):
        if code not in allowed:
            errors.append(f"N2: nhắc tới hành động {code} không có trong FactPack")
            continue
        if code in future_only:
            window = q[max(0, position - 32):position]
            marker = next((m for m in _PRESENT_MARKERS if m in window), None)
            if marker is not None:
                errors.append(
                    f"N3: hành động {code} thuộc kế hoạch sắp tới nhưng được mô tả như "
                    f"đang diễn ra ('{marker}')")
    return errors


# A negation token that directly governs a match turns an assertion into a disclaimer.
# Our own episode caveats depend on this: "không khẳng định đổi pin làm tăng thu nhập" is
# the sentence that *prevents* the causal reading, and an early version of this verifier
# rejected it for containing the phrase it exists to deny.
_ASSERTION_NEGATION = re.compile(
    r"\b(khong phai|khong|chua|chang|tranh|dung)\b[^,.;:!?]{0,32}$")


def _negated(q: str, start: int) -> bool:
    return bool(_ASSERTION_NEGATION.search(q[max(0, start - 48):start]))


def _scan(text: str, patterns, code: str) -> list[str]:
    """Report assertion patterns, skipping ones a negation defuses."""
    q = _norm(text)
    errors = []
    for pattern, label in patterns:
        match = re.search(pattern, q)
        if match and not _negated(q, match.start()):
            errors.append(f"{code}: {label}")
    return errors


def check_causal(text: str, pack: FactPack) -> list[str]:
    """N4 on model prose, which is the strict variant.

    ``gsm_core.agent.verifier`` deliberately imports the shared ``_CAUSAL_PATTERNS`` tuple
    instead of calling this: the agent's own composer strings are authored, reviewed and
    pinned by tests, so they cannot drift at runtime the way a generated sentence can.
    The bare-consequent rule is aimed at the surface that can.
    """
    del pack
    return _scan(text, _MODEL_CAUSAL_PATTERNS, "N4")


def check_income(text: str, pack: FactPack) -> list[str]:
    del pack
    return _scan(text, _INCOME_PATTERNS, "N5")


# Health vocabulary is never defusable by negation.  "Bạn không mệt" is still a statement
# about the driver's body, and this system does not measure that variable at all (spec
# §1.2b).  Every other banned phrase is a causal or income claim, where a disclaimer that
# names the claim in order to deny it is exactly the sentence we want to keep.
HEALTH_PHRASES: frozenset[str] = frozenset({
    "nợ nghỉ", "bạn mệt", "chưa nghỉ đủ", "cần nghỉ ngay", "nên nghỉ ngay",
    "sức khỏe đang bị ảnh hưởng", "sức khoẻ đang bị ảnh hưởng", "quá sức",
    "bạn đang mệt", "bạn chưa nghỉ",
})

DEFUSABLE_PHRASES: tuple[str, ...] = tuple(
    phrase for phrase in BANNED_PHRASES if phrase not in HEALTH_PHRASES)


def check_health(text: str, pack: FactPack) -> list[str]:
    """N6 — the health boundary (spec §1.2b) applies to model output verbatim."""
    del pack
    lowered = str(text).lower()
    errors = [f"N6: cụm sức khoẻ bị cấm '{phrase}'" for phrase in sorted(HEALTH_PHRASES)
              if phrase in lowered]
    q = _norm(text)
    for phrase in DEFUSABLE_PHRASES:
        position = q.find(_norm(phrase))
        if position >= 0 and not _negated(q, position):
            errors.append(f"N6: cụm bị cấm '{phrase}'")
    return errors


def check_settled(text: str, pack: FactPack) -> list[str]:
    if pack.income_settled:
        return []
    return _scan(text, _SETTLED_PATTERNS, "N7")


def check_adherence(text: str, pack: FactPack) -> list[str]:
    del pack
    return _scan(text, _ADHERENCE_PATTERNS, "N8")


def check_location(text: str, pack: FactPack) -> list[str]:
    del pack
    return (_scan(text, _LOCATION_PATTERNS, "N9")
            + _scan(text, _ORDER_ADVICE_PATTERNS, "N9"))


_TARGET_VERDICT_TYPE = "stop_chasing_infeasible_target"


def check_infeasible(text: str, pack: FactPack) -> list[str]:
    """N10 — "this target is gone" is a solver verdict, never a narrator's conclusion.

    The verdict is present when the pack is built from the A7 episode itself, or — in a
    shift review — when one of the outcome facts names that episode type.  ``episode_refs``
    holds ids, not types, so it is not the place to look.
    """
    if pack.episode_type == _TARGET_VERDICT_TYPE:
        return []
    if any(_TARGET_VERDICT_TYPE in fact.text or "infeasible" in fact.text.lower()
           for fact in pack.facts):
        return []
    return _scan(text, _INFEASIBLE_PATTERNS, "N10")


def check_cjk(text: str, pack: FactPack) -> list[str]:
    del pack
    for char in text:
        code_point = ord(char)
        if 0x4E00 <= code_point <= 0x9FFF or 0x3400 <= code_point <= 0x4DBF:
            return [f"N11: ký tự CJK {char!r}"]
    return []


_SNAKE_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def check_internal_identifiers(text: str, pack: FactPack) -> list[str]:
    """N17 — no internal name reaches a driver.

    The pack hands the model machine-readable keys so it can cite them
    (``stop_chasing_infeasible_target``, ``settled_source_count``), and a model that is
    describing its evidence will sometimes quote one.  Observed in evaluation: a served
    review contained "trạng thái đuổi mốc (stop_chasing_infeasible_target) đã đóng".  It
    was factually correct and completely unreadable, which no other rule detects because
    every other rule asks whether the sentence is *true*.

    The check is on the *shape* rather than on a list of known keys.  An earlier version
    only rejected identifiers present in the pack, which missed the case that matters most:
    a model recalling a key from somewhere else.  Vietnamese prose contains no
    ``word_word`` tokens, so the shape is a reliable signal on its own.
    """
    del pack
    hits = sorted(set(_SNAKE_TOKEN.findall(text.lower())))
    return [f"N17: lộ định danh nội bộ {hit!r}" for hit in hits[:3]]


# Sentences that exist only because a template or a model had a slot to fill.  "Ghi nhận 0
# lần huỷ" is true, carries nothing, and is named in the product spec as copy not to write.
_EMPTY_COUNT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b0 (lan|cuoc|nguon|khoang|buoc)\b", "đếm bằng 0"),
    (r"\bla 0\b", "giá trị là 0"),
    (r"\bkhong co (lan|cuoc|buoc) nao\b", "không có gì để nói"),
    (r"\bchua co buoc (tiep theo|ke tiep)\b", "chưa có bước tiếp theo"),
    (r"\bbay gio: —", "ô trống"),
)


def check_empty_statements(text: str, pack: FactPack) -> list[str]:
    """N18 — a zero is not a finding.

    Unlike every other rule here this one rejects *true* sentences.  It is a quality gate,
    not a safety gate, and it exists because "0 lần huỷ" survived the entire evaluation:
    faithful, provenance-clean, and worth nothing to the person reading it.
    """
    del pack
    return _scan(text, _EMPTY_COUNT_PATTERNS, "N18")


CONTENT_RULES = (check_numbers, check_actions, check_causal, check_income,
                 check_health, check_settled, check_adherence, check_location,
                 check_infeasible, check_cjk, check_internal_identifiers,
                 check_empty_statements)


def verify_structure(output: dict, pack: FactPack) -> list[str]:
    """N12–N15 — shape, budget and citations, before a single content rule runs."""
    errors: list[str] = []
    if not isinstance(output, dict):
        return ["N12: output không phải JSON object"]
    present_forbidden = sorted(FORBIDDEN_OUTPUT_FIELDS & set(output))
    if present_forbidden:
        errors.append(f"N13: output chứa field bị cấm {present_forbidden}")

    summary = output.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("N12: thiếu summary")

    used = output.get("used_fact_ids")
    if not isinstance(used, list) or not used:
        errors.append("N14: thiếu used_fact_ids")
    else:
        unknown = sorted({str(item) for item in used} - set(pack.fact_ids))
        if unknown:
            errors.append(f"N14: used_fact_ids không có trong FactPack: {unknown[:5]}")

    if pack.task_type == TASK_REVIEW:
        points = output.get("key_points")
        if not isinstance(points, list):
            errors.append("N12: thiếu key_points")
        elif not MIN_REVIEW_POINTS <= len(points) <= MAX_REVIEW_POINTS:
            errors.append(
                f"N15: key_points phải có {MIN_REVIEW_POINTS}–{MAX_REVIEW_POINTS} ý, "
                f"nhận {len(points)}")
        elif any(not isinstance(point, str) or not point.strip() for point in points):
            errors.append("N12: key_points có phần tử rỗng")
        caveat = output.get("caveat")
        if caveat is not None and not isinstance(caveat, str):
            errors.append("N12: caveat phải là chuỗi hoặc null")
    return errors


def rendered_text(output: dict, pack: FactPack) -> str:
    """Everything the driver would read, joined for content scanning.

    Scanning the joined text rather than each field means a claim cannot be split across
    ``summary`` and a bullet to slip past a rule that reads one of them.
    """
    parts = [str(output.get("summary") or "")]
    if pack.task_type == TASK_REVIEW:
        parts.extend(str(point) for point in (output.get("key_points") or []))
        if output.get("caveat"):
            parts.append(str(output["caveat"]))
    return " ".join(part for part in parts if part).strip()


def check_length(text: str, pack: FactPack) -> list[str]:
    errors: list[str] = []
    if len(text) > pack.max_output_chars:
        errors.append(f"N15: dài {len(text)} ký tự > {pack.max_output_chars}")
    if pack.task_type == TASK_REVIEW:
        words = len(text.split())
        if words > MAX_REVIEW_WORDS:
            errors.append(f"N15: {words} từ > {MAX_REVIEW_WORDS}")
    return errors


def verify(output: dict, pack: FactPack) -> Verdict:
    """Full verdict for one model output against the pack it was built from."""
    errors = verify_structure(output, pack)
    text = rendered_text(output, pack) if isinstance(output, dict) else ""
    if text:
        errors.extend(check_length(text, pack))
        for rule in CONTENT_RULES:
            errors.extend(rule(text, pack))
    elif not errors:
        errors.append("N12: output rỗng")
    # De-duplicate while preserving order: one banned phrase repeated three times is one
    # defect to report, not three.
    unique = list(dict.fromkeys(errors))
    return Verdict(passed=not unique, errors=tuple(unique), checked_chars=len(text))


def verify_text(text: str, pack: FactPack) -> Verdict:
    """Content-only verdict, used to hold the template baseline to the same rules.

    A baseline that fails is a template bug, and it should surface in tests rather than be
    excused because it did not come from a model.
    """
    errors: list[str] = list(check_length(text, pack))
    for rule in CONTENT_RULES:
        errors.extend(rule(text, pack))
    unique = list(dict.fromkeys(errors))
    return Verdict(passed=not unique, errors=tuple(unique), checked_chars=len(text))
