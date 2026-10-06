"""Router — direct mapping zero-ML (research đợt 7: <15 intents không cần classifier).

Feature từ AdviceRequest (UI trigger đã biết) → solver set. Free-text → keyword
layer tiếng Việt xác nhận; không match → out_of_taxonomy (R5, từ chối lịch sự).
"""

from __future__ import annotations

from gsm_core.advisor._text import normalize_vi as _norm

# UC của GSM map VÀO feature F0-F3 của ta (advice_request.feature là enum ĐÓNG):
#   UC3 khoán tuần → weekly_khoan (F1 kế hoạch, F3 tổng kết)
#   UC8 mini-task  → mission_knapsack (F1 kế hoạch, F2 trong ca)
FEATURE_SOLVERS = {
    "F0": {"solvers": ["bonus_feasibility"], "use_kb": True},
    "F1": {"solvers": ["bonus_feasibility", "shift_dp", "weekly_khoan", "mission_knapsack"],
           "use_kb": False},
    "F2": {"solvers": ["shift_dp", "capacity_alloc", "mission_knapsack", "idle_reduction"],
           "use_kb": False},
    "F3": {"solvers": ["f3_patterns", "weekly_khoan", "idle_reduction",
                       "penalty_explain", "anomaly_alert"], "use_kb": False},
}

# keyword tiếng Việt (đã bỏ dấu, đ→d) per intent — dict viết tay, fixture test
_INTENT_KEYWORDS = {
    "policy_bonus": ["thuong", "moc", "diem", "chinh sach", "chuyen", "cuoc",
                     "thu nhap", "phi", "pin", "doi pin", "tai khoan", "quyen loi"],
    "shift_plan": ["ca", "lich", "gio", "nghi", "sac", "chay luc nao", "khung gio"],
    "session_review": ["tong ket", "ca vua", "sao thu nhap", "toi uu",
                       "thu nhap hom nay", "ket qua ca"],
    # UC8 mini-task
    "mission_task": ["nhiem vu", "mini task", "mission", "lam them viec",
                     "thuong nhiem vu", "task"],
    # UC5 chờ/ế khách
    "idle_wait": ["dung cho", "cho lau", "e khach", "khong co cuoc", "vang khach",
                  "cho mai", "it don"],
    # UC6 vì sao bị trừ tiền
    "penalty_why": ["bi tru tien", "vi sao bi phat", "tru vi", "mat thuong",
                    "bi phat", "tru tien"],
    # UC7 cảnh báo bất thường (chỉ hiện ở F3 — không bắn giữa ca)
    "anomaly_check": ["bat thuong", "canh bao", "khoa tai khoan", "lech tuyen",
                      "bi nghi ngo"],
    # UC3 khoán tuần / chỉ tiêu
    "weekly_target": ["khoan", "doanh so tuan", "chi tieu tuan", "kpi tuan",
                      "truy thu", "muc tieu tuan"],
}


def route(feature: str, free_text_query: str | None) -> dict:
    base = FEATURE_SOLVERS.get(feature)
    if base is None:
        return {"intent": "out_of_taxonomy", "solvers": [], "use_kb": False,
                "feature": feature}
    out = {"feature": feature, "solvers": list(base["solvers"]),
           "use_kb": base["use_kb"], "intent": f"{feature}_default"}
    if base["use_kb"]:
        out["solvers"] = out["solvers"] + ["policy_kb"]
    if free_text_query:
        q = _norm(free_text_query)
        # BUG-PI5c-01: TRƯỚC ĐÂY lấy intent ĐẦU TIÊN khớp bất kỳ keyword nào → sai với
        # ĐỒNG ÂM tiếng Việt sau khi bỏ dấu: "bất thường"→"bat thuong" CHỨA "thuong"
        # (= "thưởng"/bonus) ⇒ câu hỏi cảnh báo bị định tuyến sang hỏi thưởng.
        # Nay: KEYWORD DÀI NHẤT thắng (cụm cụ thể hơn thắng từ đơn); hoà → thứ tự dict.
        matched, best_len = None, 0
        for intent, kws in _INTENT_KEYWORDS.items():
            hit = max((len(kw) for kw in kws if kw in q), default=0)
            if hit > best_len:
                matched, best_len = intent, hit
        if matched is None:
            return {"intent": "out_of_taxonomy", "solvers": [], "use_kb": False,
                    "feature": feature}
        out["intent"] = matched
    return out
