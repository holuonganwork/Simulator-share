"""Chuẩn hoá text tiếng Việt — 1 nguồn sự thật cho router / policy_kb / verifier.

BUG-C6-01 (fix): đ/Đ (U+0111/U+0110) là CHỮ CÁI riêng, KHÔNG phải base+combining,
nên `unicodedata.normalize("NFD")` KHÔNG tách được — phải map tay đ→d trước khi
strip combining marks. Nếu bỏ sót, "đơn"→"đon", "điểm"→"điem", "đổi pin"→"đoi pin"
và mọi keyword có 'd' sẽ ÂM THẦM không match (F0 KB, router intent, verifier blocklist).
"""

from __future__ import annotations

import unicodedata


def normalize_vi(s: str) -> str:
    """Lowercase + bỏ dấu (kể cả đ→d) để keyword match tiếng Việt bền vững."""
    s = s.lower().replace("đ", "d")  # Đ đã thành đ sau lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")
