"""Chuan hoa text tieng Viet dung chung (lowercase + bo dau, ke ca d/Ä‘ -> d).

Ham nay doc lap voi GSM_SIMULATOR (khong import gsm_core) de query_policy hoat
dong ngay ca khi package gsm-sim chua duoc cai (xem agent/tools/query_policy.py).
Logic giong het _text.normalize_vi ben GSM_SIMULATOR (mot ham chuan hoa tieng
Viet thong dung, khong phai logic nghiep vu rieng) - dam bao dem tu khoa on dinh
du dang doc doan chinh sach tu nguon nao.
"""

from __future__ import annotations

import unicodedata


def normalize_vi(s: str) -> str:
    s = s.lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")
