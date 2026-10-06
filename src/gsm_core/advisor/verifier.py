"""Verifier — 3 tầng plain code (research đợt 7: KHÔNG framework, validator hub
degrade tiếng Việt). Tầng 1 CODE có quyền veto; LLM-judge (tầng 2) advisory-only,
flag off default v1 (EXP-001). Repair ≤1 vòng ở composer; verifier chỉ verdict.

Rules tầng 1 (mỗi rule = 1 hàm thuần có ID + test):
  V1 số trần ngoài placeholder/registry (faithfulness cấu trúc)
  V2 blocklist tiếng Việt: hứa thu nhập / khuyên đơn cụ thể / thiếu disclaimer
  V3 ký tự CJK (language-mixing model gốc Trung)
  V4 F0 thiếu citation khi dùng KB
"""

from __future__ import annotations

import re

from gsm_core.advisor._text import normalize_vi as _norm


# V2 blocklist (đã bỏ dấu, đ→d) — tự viết cho tiếng Việt, không dùng validator hub.
# QUAN TRỌNG (BUG-C6-02): promise = HỨA KẾT QUẢ KIẾM TIỀN CỦA TÀI XẾ (động từ
# "kiếm được/đạt được" hoặc mức tiền cụ thể), KHÔNG phải sự đồng hiện của "đảm bảo"
# + "thu nhập". Chính sách GSM tên là "Chính Sách ĐẢM BẢO THU NHẬP" — nếu bắt theo
# co-occurrence sẽ báo nhầm mọi câu F0 trích nguồn đó là "hứa thu nhập".
_PROMISE_PATTERNS = [
    r"chac chan .{0,15}\b(kiem|dat|duoc|co)\b",          # chắc chắn kiếm/đạt/được/có
    r"(dam bao|cam ket) .{0,25}\b(kiem duoc|dat duoc|kiem it nhat)\b",  # đảm bảo … kiếm được
    r"(dam bao|cam ket) .{0,15}\d[\d.]* ?(nghin|trieu|dong)\b",  # đảm bảo 500 nghìn
    r"\bkiem duoc it nhat\b",
]
_ORDER_ADVICE_PATTERNS = [
    r"(nhan|huy|tu choi) (don|cuoc) nay", r"don (hang )?nay (nen|phai)",
    r"(nen|hay) (nhan|huy|bo) (ngay )?(don|cuoc)\b",
]
_DISCLAIMER_HINTS = ["uoc tinh", "khong dam bao", "khong phai cam ket",
                     "phu thuoc", "co the thay doi", "khong chac chan",
                     "khong hua"]


def _strip_spans(text: str, spans) -> str:
    """Gỡ các đoạn trích VERBATIM nguồn chính thức (tiêu đề policy) khỏi text —
    guardrail chỉ soi CLAIM của agent, không soi text official được trích dẫn.
    BUG-C6-03: tiêu đề "...Áp dụng từ 05/06/2026" / "Đảm bảo thu nhập" là DATA,
    không phải agent hứa hẹn hay bịa số."""
    for s in spans or []:
        if s:
            text = text.replace(s, " ")
    return text


def check_bare_numbers(message: str, rendered_numbers: list[str],
                       cited_texts=None) -> list[str]:
    """V1: mọi chuỗi số trong message phải thuộc tập số ĐÃ RENDER từ registry.
    Whitelist: số trong URL, giờ dạng HH:MM, số trong [N-id]/[P-id], và số nằm
    trong tiêu đề nguồn trích verbatim (cited_texts)."""
    errors = []
    text = _strip_spans(message, cited_texts)             # bỏ tiêu đề nguồn trích
    text = re.sub(r"https?://\S+", "", text)             # bỏ URL
    text = re.sub(r"\[[NP]\d+\]", "", text)               # bỏ ref tags
    text = re.sub(r"\b\d{1,2}[:h]\d{0,2}\b", "", text)   # bỏ giờ 17h/14:30
    allowed = set()
    for rn in rendered_numbers:
        for tok in re.findall(r"[\d.,]+", rn):
            allowed.add(tok.strip(".,"))
    for m in re.finditer(r"[\d][\d.,]*", text):
        tok = m.group().strip(".,")
        if tok and tok not in allowed:
            errors.append(f"V1: số trần '{tok}' không trace được về numbers_registry")
    return errors


# AUDIT A3 VBYPASS-3 (UPDATE-070): bản cũ substring-match tập ("khong phai","khong",
# "chang","dung","chua") trong 15 ký tự trước ⇒ (a) "dung" là substring của "ung dung"/
# "su dung"/"dung" (đúng) nên câu quảng cáo "Ứng dụng này chắc chắn giúp anh kiếm được
# nhiều hơn" LỌT; (b) "khong" bổ nghĩa cho từ khác ("Khong sai dau, chac chan anh kiem
# duoc...") cũng defuse. Nay: phủ định phải là TOKEN nguyên và phải đứng NGAY TRƯỚC
# trigger (≤ 12 ký tự, không có dấu ngắt câu chen giữa) — nghĩa là thật sự chi phối nó.
_NEGATION_TOKENS = ("khong phai", "khong", "chang", "chua")
_NEG_RE = re.compile(r"\b(" + "|".join(_NEGATION_TOKENS) + r")\b[^,.;:!?]{0,12}$")


def _negated(q: str, start: int) -> bool:
    """True khi có phủ định TOKEN chi phối trực tiếp match (không qua dấu ngắt câu).

    Ví dụ giữ nguyên hành vi đúng: "Con so nay khong chac chan, kiem duoc bao nhieu
    phu thuoc thi truong." → disclaimer hợp lệ."""
    prefix = q[max(0, start - 24):start]
    return bool(_NEG_RE.search(prefix))


def check_blocklist(message: str, advice_spec, cited_texts=None) -> list[str]:
    """V2: hứa thu nhập / khuyên đơn cụ thể / thiếu disclaimer khi có advice_spec.
    Câu phủ định ('không phải cam kết...') là disclaimer hợp lệ — bỏ qua.
    Tiêu đề nguồn trích (cited_texts) bị gỡ trước khi soi (không phải claim agent)."""
    errors = []
    q = _strip_spans(_norm(message), [_norm(c) for c in (cited_texts or [])])
    for pat in _PROMISE_PATTERNS:
        m = re.search(pat, q)
        if m and not _negated(q, m.start()):
            errors.append(f"V2a: hứa hẹn thu nhập (pattern '{pat}')")
    for pat in _ORDER_ADVICE_PATTERNS:
        m = re.search(pat, q)
        if m and not _negated(q, m.start()):
            errors.append(f"V2b: khuyên nhận/hủy đơn cụ thể (pattern '{pat}')")
    if advice_spec is not None and not any(h in q for h in _DISCLAIMER_HINTS):
        errors.append("V2c: advice có action nhưng thiếu disclaimer bất định")
    return errors


def check_cjk(message: str) -> list[str]:
    """V3: reject ký tự CJK (model gốc Trung có thể trộn)."""
    for ch in message:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            return [f"V3: ký tự CJK '{ch}' trong message"]
    return []


def check_f0_citation(feature: str, used_kb: bool, citations: list) -> list[str]:
    """V4: F0 dùng KB phải có ≥1 citation."""
    if feature == "F0" and used_kb and not citations:
        return ["V4: F0 dùng policy KB nhưng không có citation"]
    return []


def verify(message: str, advice_spec, citations: list, feature: str,
           used_kb: bool, rendered_numbers: list[str],
           cited_texts=None) -> dict:
    """Tầng 1 verdict. Trả {passed, errors[]}. Verifier không sửa — chỉ verdict.
    `cited_texts`: tiêu đề nguồn official được trích verbatim (gỡ khỏi phạm vi soi
    số trần/hứa hẹn — chúng là DATA, không phải claim của agent)."""
    errors = (check_bare_numbers(message, rendered_numbers, cited_texts)
              + check_blocklist(message, advice_spec, cited_texts)
              + check_cjk(message)
              + check_f0_citation(feature, used_kb, citations))
    return {"passed": not errors, "errors": errors}
