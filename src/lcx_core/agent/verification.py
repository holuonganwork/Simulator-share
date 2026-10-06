"""Cong xac minh truoc khi gui tin nhan (Pha 3) - "KHONG message nao chua so lieu
khong truy duoc nguon" (tieu chi thoat Pha 3, docs/strategy.md muc 08).

Trong GSM_SIMULATOR, advice_agent.py la "Strict, tool-free provider adapter":
LLM chi nhan fact da tinh san de dien dat thanh template voi placeholder {{N1}},
KHONG tu viet so. Module nay ap dung dung triet ly do cho agent Tier D: MOI con
so xuat hien trong message cuoi cung PHAI trich duoc tu (a) evidence cua case
hoac (b) noi dung trich dan chinh sach da lay tu query_policy - neu khong, tu
choi gui va yeu cau sinh lai.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER_RE = re.compile(r"\d[\d.,]*\d|\d")


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    unverified_numbers: list[str]
    reason: str | None = None


def _numbers_in(text: str) -> set[str]:
    return {m.group(0) for m in _NUMBER_RE.finditer(text)}


def verify_message(message: str, source_texts: list[str]) -> VerificationResult:
    """Kiem tra moi con so trong `message` co xuat hien (nguyen van chuoi so) o
    it nhat mot trong `source_texts` (evidence + policy excerpt da dung de sinh
    message do) hay khong.

    Day la kiem tra CHUOI KY TU (khong parse gia tri so hoc) - co chu dich: muc
    tieu la bat truong hop LLM/template "bia" mot con so KHONG co trong bat ky
    nguon nao, khong phai kiem tra tinh dung dan toan hoc.
    """
    msg_numbers = _numbers_in(message)
    if not msg_numbers:
        return VerificationResult(ok=True, unverified_numbers=[])

    available: set[str] = set()
    for text in source_texts:
        available |= _numbers_in(text)

    unverified = sorted(n for n in msg_numbers if n not in available)
    if unverified:
        return VerificationResult(
            ok=False,
            unverified_numbers=unverified,
            reason=(
                f"Tu choi gui: cac con so {unverified} xuat hien trong message nhung "
                "khong tim thay trong evidence/trich dan chinh sach da dung."
            ),
        )
    return VerificationResult(ok=True, unverified_numbers=[])
