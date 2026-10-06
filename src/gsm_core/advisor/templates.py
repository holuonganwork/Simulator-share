"""Template fallback deterministic — đường LLM-off BẮT BUỘC (guardrail).

Render advice thuần code từ SolverReport + KB excerpt. Luôn chạy được.
"""

from __future__ import annotations

from gsm_core.advisor.context_pack import render_number_vn


def _fmt(reg: dict, nid: str) -> str:
    n = reg.get(nid)
    return render_number_vn(n["value"], n["unit"]) if n else ""


def _rep(solver_reports: list[dict], solver: str) -> dict | None:
    return next((r for r in solver_reports if r["solver"] == solver), None)


# AUDIT A3 LAYEROUT-1 (UPDATE-070): tolerance PHẢI theo unit. Bản cũ dùng 0.5 tuyệt đối
# cho mọi unit ⇒ với `ratio` (thang [0,1]) mọi giá trị đều "khớp" nhau: ngưỡng policy 0.85
# bị render thành tỷ lệ hiện tại 0.739 → message nói "mức tối thiểu 74%" (sai chính sách).
_UNIT_TOL = {"ratio": 0.005, "hours": 0.05, "points": 0.5, "trips": 0.5,
             "vnd": 0.5, "minutes": 0.5, "count": 0.5, "vnd_per_order": 0.5,
             "points_per_hour": 0.05, "vnd_per_hour": 0.5}


def _vn(reg: dict, value, unit: str) -> str:
    """Chuỗi số VN cho (value, unit) CHỈ KHI có trong numbers_registry.

    Bắt buộc tra registry (không tự format từ solution) → mọi số hiển thị đều trace
    được về `SolverReport.numbers[]`; verifier V1 (số trần) vì thế không bị vi phạm.
    Khớp theo tolerance RIÊNG của unit và chọn entry GẦN NHẤT (không phải entry đầu).
    """
    if value is None:
        return ""
    tol = _UNIT_TOL.get(unit, 0.5)
    best, best_d = None, None
    for n in reg.values():
        if n["unit"] != unit:
            continue
        d = abs(float(n["value"]) - float(value))
        if d < tol and (best_d is None or d < best_d):
            best, best_d = n, d
    return render_number_vn(best["value"], best["unit"]) if best else ""


def _khoan_sentence(solver_reports: list[dict], reg: dict) -> str:
    """Tiến độ khoán tuần + cảnh báo truy thu (ngôn ngữ ĐIỀU KIỆN, không doạ/hứa)."""
    r = _rep(solver_reports, "weekly_khoan")
    if not r:
        return ""
    sol = r.get("solution", {})
    if not sol.get("quota_available"):
        return " Hiện chưa có số khoán tuần chính thức để đối chiếu."
    # BUG-PI5a-02: TRƯỚC ĐÂY kiểm tra CHUỖI đã render ("0đ" là truthy!) ⇒ tài xế đã đạt
    # khoán vẫn bị nói "còn thiếu 0đ … có thể bị truy thu 0đ". Phải xét GIÁ TRỊ SỐ.
    gap_val = sol.get("gap_revenue_vnd") or 0
    claw_val = sol.get("clawback_risk_vnd") or 0
    if gap_val <= 0:
        return " Tuần này anh/chị đã đạt khoán doanh số."
    gap = _vn(reg, gap_val, "vnd")
    if not gap:
        return ""  # không trace được số → không nói (thà thiếu còn hơn bịa)

    # C2 §1c (UPDATE-076): PHẢI đọc `feasible`. Trước đây câu này chỉ nhìn `gap_revenue_vnd`
    # nên khi solver kết luận KHÔNG khả thi (quỹ giờ tuần không đủ / thiếu ngày hoạt động) nó
    # vẫn nói "còn thiếu Xđ để đạt khoán … có thể bị truy thu Yđ" — vừa ngụ ý với tới được,
    # vừa treo doạ truy thu, đẩy tài xế đuổi theo thứ không thể đạt.
    # Đây ĐÚNG nguyên tắc AUDIT A3 LAYEROUT-2 đã áp cho `_gap_sentence` (S1) nhưng bỏ sót S5.
    if not sol.get("feasible", True):
        cons = sol.get("constraints") or {}
        if not cons.get("enough_hours", True):
            why = " vì quỹ giờ còn lại trong tuần không đủ"
        elif not cons.get("ok_active_days", True):
            why = " vì số ngày hoạt động không đạt yêu cầu"
        else:
            why = ""
        s = f" Tuần này còn thiếu {gap} doanh số, nhưng khoán tuần khó đạt{why}."
        claw_s = _vn(reg, claw_val, "vnd") if claw_val > 0 else ""
        if claw_s:
            s += f" Phần chưa đạt có thể bị truy thu khoảng {claw_s}."
        return s + " Giữ nhịp bền và tỷ lệ tốt cho tuần sau vẫn hơn cố quá sức."

    s = f" Tuần này còn thiếu {gap} doanh số để đạt khoán."
    claw = _vn(reg, claw_val, "vnd") if claw_val > 0 else ""
    if claw:
        s += f" Nếu không đạt, phần chưa đạt có thể bị truy thu khoảng {claw}."
    return s


def _mission_sentence(solver_reports: list[dict], reg: dict) -> str:
    """Mini-task nên làm (UC8) — thưởng chỉ nhận khi hoàn thành đủ điều kiện."""
    r = _rep(solver_reports, "mission_knapsack")
    if not r:
        return ""
    chosen = (r.get("solution") or {}).get("chosen_missions") or []
    if not chosen:
        return ""
    reward = _vn(reg, (r["solution"] or {}).get("expected_reward_vnd"), "vnd")
    names = ", ".join(str(c.get("name") or c.get("mission_id")) for c in chosen)
    s = f" Nhiệm vụ nên làm: {names}"
    if reward:
        s += f" (thưởng tối đa {reward} nếu hoàn thành đủ điều kiện)"
    return s + "."


def _idle_sentence(solver_reports: list[dict], reg: dict) -> str:
    """UC5 idle (D-004b): CHỈ khuyên MỨC THỜI GIAN — không chỉ định ô/khu vực đứng.

    Khu vực chỉ nhắc lại nhiệm vụ CHÍNH THỨC của hãng (nếu data có). Cảnh báo tỷ lệ
    nhận + nhãn ước lượng nằm ở `caveats` của SolverReport (điều kiện 3 & 4).
    """
    r = _rep(solver_reports, "idle_reduction")
    if not r:
        return ""
    sol = r.get("solution") or {}
    if not sol.get("notable"):
        return ""  # không bịa vấn đề khi tài xế không chờ nhiều
    total = _vn(reg, sol.get("total_idle_min"), "minutes")
    if not total:
        return ""
    s = f" Hôm nay anh/chị chờ tổng {total}"
    w = sol.get("worst_window")
    if w and w.get("hour") is not None:
        s += (f", nhiều nhất quanh khung {int(w['hour']):02d}h — khung này nhu cầu thường "
              "thấp, anh/chị có thể dồn nghỉ/đổi pin vào đó")
    s += "."
    if sol.get("reposition_mission"):
        s += f" Ngoài ra, {sol['reposition_mission']}."
    return s


def _penalty_sentence(solver_reports: list[dict], reg: dict) -> str:
    """UC6: nêu khoản trừ + cách TUÂN THỦ (không dạy lách, không phán xét)."""
    r = _rep(solver_reports, "penalty_explain")
    if not r:
        return ""
    sol = r.get("solution") or {}
    if not sol.get("notable"):
        return ""
    total = _vn(reg, sol.get("total_deducted_vnd"), "vnd")
    s = ""
    if total and sol.get("penalty_count"):
        # AUDIT A1 S8S9-1 (UPDATE-065): count phải neo registry như mọi số khác
        # (bài học BUG-PI5d-01) — không trace được thì bỏ "(N khoản)", giữ tổng.
        cnt = _vn(reg, sol["penalty_count"], "count")
        s += (f" Kỳ này anh/chị bị trừ tổng {total} ({cnt} khoản)." if cnt
              else f" Kỳ này anh/chị bị trừ tổng {total}.")
    for risk in (sol.get("risks") or [])[:1]:
        state = "đang dưới" if risk["state"] == "below" else "đang sát"
        th = _vn(reg, risk["threshold"], "ratio")
        if th:
            s += f" {risk['metric'].capitalize()} {state} mức tối thiểu {th} theo chính sách."
        else:
            s += f" {risk['metric'].capitalize()} {state} mức tối thiểu theo chính sách."
    acts = sol.get("actions") or []
    if acts:
        s += f" Để cải thiện: {acts[0]}."
    return s


def _anomaly_sentence(solver_reports: list[dict], reg: dict) -> str:
    """UC7: heads-up KHÔNG kết tội — chỉ 'ghi nhận dấu hiệu' + khuyến nghị kiểm tra."""
    r = _rep(solver_reports, "anomaly_alert")
    if not r:
        return ""
    sol = r.get("solution") or {}
    if not sol.get("notable"):
        return ""  # không có cờ mở → im lặng
    items = sol.get("items") or []
    if not items:
        return ""
    top = items[0]
    lvl = top.get("official_level")
    # dùng ĐÚNG thang app ("Mức độ cảnh báo gian lận": Không/Thấp/Cao/Rất cao) để khớp
    # cái tài xế đang nhìn thấy (research đợt 4 §F-2)
    lvl_txt = f" (mức cảnh báo trên app: {lvl})" if lvl else ""
    s = (f" Lưu ý: hệ thống ghi nhận dấu hiệu cần xem lại — {top['description']}{lvl_txt}. "
         "Đây chưa phải kết luận vi phạm; anh/chị kiểm tra lại thông tin chuyến "
         "liên quan hoặc liên hệ hỗ trợ nếu thấy chưa chính xác.")
    # hạn giải trình 48h (official 15/12/2025) — nhu cầu GẤP nhất khi bị gắn cờ
    left = top.get("explain_hours_left")
    if left is not None:
        if top.get("explain_overdue"):
            s += (" Anh/chị có thể giải trình trực tuyến trên app; thời hạn có thể đã qua "
                  "— nên liên hệ hỗ trợ sớm.")
        else:
            # BUG-PI5d-01: TRƯỚC ĐÂY template tự format f"{left:.0f} giờ" → KHÔNG khớp
            # chuỗi render từ registry ⇒ verifier V1 coi là SỐ TRẦN và veto cả advice.
            # Mọi số hiển thị PHẢI đi qua _vn() (neo registry).
            hrs = _vn(reg, left, "hours")
            s += (f" Anh/chị còn khoảng {hrs} để giải trình trực tuyến trên app." if hrs
                  else " Anh/chị nên giải trình trực tuyến trên app sớm.")
    return s


def _gap_sentence(solver_reports: list[dict], reg: dict, n1: str, n2: str) -> str:
    """AUDIT A3 LAYEROUT-2 (UPDATE-070): câu 'còn thiếu X để đạt mốc Y' CHỈ được nói khi
    solver bảo KHẢ THI. Bản cũ chỉ kiểm 'render được số' ⇒ hứa mốc trong khi S1 kết luận
    infeasible (đối lập cả với UI adapter vốn nói thật 'khó khả thi hôm nay')."""
    r = _rep(solver_reports, "bonus_feasibility")
    if not r or not (n1 and n2):
        return ""
    sol = r.get("solution") or {}
    # KHÔNG chèn `infeasible_reason` thô: chuỗi đó chứa số chưa neo registry ⇒ V1 veto
    # (bài học BUG-PI5d-01). Diễn giải lý do bằng NHÃN cấu trúc từ constraints.
    cons = sol.get("constraints") or {}

    if sol.get("already_maxed"):
        # C2 (UPDATE-076): `already_maxed` KHÔNG còn là nhánh sớm. Kịch mốc điểm mà tỷ lệ dưới
        # ngưỡng thì chính sách trả **0đ** — trấn an lúc đó là nói sai với tài xế đang mất tiền
        # và VẪN CÒN CỨU ĐƯỢC. Xem hồ sơ `08-parity-sim-vs-ui.md` §1b.
        if sol.get("feasible"):
            return " Anh/chị đã đạt mốc thưởng cao nhất hôm nay."
        if not cons.get("ok_acceptance", True):
            return (" Anh/chị đã đủ điểm mốc cao nhất, nhưng tỷ lệ NHẬN đang dưới ngưỡng chính "
                    "sách — giữ nguyên tới cuối ngày thì phần thưởng sẽ không được trả.")
        if not cons.get("ok_completion", True):
            return (" Anh/chị đã đủ điểm mốc cao nhất, nhưng tỷ lệ HOÀN THÀNH đang dưới ngưỡng "
                    "chính sách — giữ nguyên tới cuối ngày thì phần thưởng sẽ không được trả.")
        return (" Anh/chị đã đủ điểm mốc cao nhất, nhưng điều kiện tỷ lệ của chính sách chưa "
                "đạt — phần thưởng có thể không được trả.")

    if sol.get("feasible"):
        return f" Anh/chị còn thiếu {n1} để chạm mốc thưởng {n2}."
    if not cons.get("enough_hours", True):
        why = " vì quỹ giờ còn lại không đủ"
    elif not cons.get("ok_acceptance", True):
        why = " vì tỷ lệ nhận đang dưới ngưỡng chính sách"
    elif not cons.get("ok_completion", True):
        why = " vì tỷ lệ hoàn thành đang dưới ngưỡng chính sách"
    else:
        why = ""
    return (f" Mốc thưởng {n2} hôm nay khó khả thi{why} — anh/chị còn thiếu {n1}. "
            "Giữ tỷ lệ tốt cho ngày mai vẫn hơn cố quá sức.")


def _advice_spec(action: str | None, bucket: str | None) -> dict:
    """advice_spec hợp schema: action_type thường-hoá (adherence taxonomy), KHÔNG
    kèm `expiry` khi rỗng (schema: expiry là string date-time, không nullable)."""
    return {"action_type": (action or "online").lower(),
            "target_window": bucket, "target_zone_or_station": None}


def _shift_now_and_future(solution: dict) -> tuple[dict | None, dict | None]:
    """Tách action của bucket hiện tại khỏi bước tiếp theo trong kế hoạch S2.

    `schedule[0]` là action đang có hiệu lực. `next_action` là action đầu tiên
    cần làm trong tương lai theo solver. Với report cũ chưa có schedule, giữ
    backward compatibility bằng cách coi `next_action` là action hiện tại.
    """
    schedule = solution.get("schedule") or []
    current = schedule[0] if schedule else solution.get("next_action")
    future = solution.get("next_action") if schedule else None
    if current and future and current.get("action") == future.get("action") \
            and current.get("bucket") == future.get("bucket"):
        future = None
    return current, future


def render_template(feature: str, solver_reports: list[dict], kb_excerpts: list[dict],
                    numbers_registry: dict) -> dict:
    reg = numbers_registry
    # BUG-PI5a-01: TRƯỚC ĐÂY n1/n2 lấy theo VỊ TRÍ trong registry (nids[0], nids[1]).
    # Khi thêm S5/S6, thứ tự đổi ⇒ render câu VÔ NGHĨA, vd "mốc thưởng 35585.2
    # vnd_per_hour" (tốc độ doanh số bị gán nhãn mốc thưởng). Nay CHỈ neo theo
    # GIÁ TRỊ của `bonus_feasibility`; không có solver đó ⇒ để RỖNG và bỏ câu,
    # KHÔNG lấy bừa số khác (thà thiếu còn hơn sai ngữ nghĩa).
    _s1 = _rep(solver_reports, "bonus_feasibility")
    _sol1 = (_s1 or {}).get("solution") or {}
    n1 = _vn(reg, _sol1.get("gap_points"), "points")
    # ⭐ P1a (2026-08-07) — dùng phần KIẾM THÊM, không dùng TỔNG mốc.
    #
    # `day_bonus_tiers` là thang **THAY THẾ** (`policy.bonus_at` ghi đè `bonus = tier_vnd`, không
    # `+=`) ⇒ với tài xế ĐÃ CHỐT một mốc, `tier_vnd` là **tên mốc**, không phải phần đổi được
    # bằng công sức thêm. Câu render dưới đây (`"cần thêm {n1} để đạt mốc thưởng {n2}"`) đặt số
    # ngay cạnh cụm NỖ LỰC ⇒ dùng TỔNG là hứa gấp đôi. Đo trên đường v1: **131/426 thẻ = 30,75%**
    # rơi vào ca này, tiền bị thổi **4.440.000đ**, bội số **2,00×**.
    #
    # `tier_delta_vnd` do `bonus_feasibility` tính sẵn; fallback về `tier_vnd` cho report CŨ
    # (chưa có khoá) — không có nó thì câu này im lặng đổi nghĩa khi gặp artifact cũ.
    n2 = _vn(reg, _sol1.get("tier_delta_vnd", _sol1.get("tier_vnd")), "vnd")

    disclaimer = " Lưu ý: đây là ước tính theo dữ liệu lịch sử, không phải cam kết thu nhập."
    citations: list[str] = []
    advice_spec = None

    if feature == "F0":
        cite_txt = ""
        if kb_excerpts:
            citations = [e["source_url"] for e in kb_excerpts[:2]]
            cite_txt = f" Nguồn: {kb_excerpts[0]['title']}."
        msg = (f"Chào anh/chị! Theo chính sách hiện hành, anh/chị cần thêm {n1} "
               f"để đạt mốc thưởng {n2}.{cite_txt}{disclaimer}")
    elif feature == "F1":
        # digest S2 là câu dẫn (solver-authored, deterministic) nếu có
        dp = next((r for r in solver_reports if r["solver"] == "shift_dp"), None)
        plan = (dp["problem_digest"].strip() + " ") if dp else ""
        gap_txt = _gap_sentence(solver_reports, reg, n1, n2).strip()
        # UC3 khoán tuần + UC8 mini-task (PI-5a)
        msg = (f"{plan}{gap_txt}{_khoan_sentence(solver_reports, reg)}"
               f"{_mission_sentence(solver_reports, reg)}{disclaimer}")
        na = (dp or {}).get("solution", {}).get("next_action") if dp else None
        if na:
            advice_spec = _advice_spec(na.get("action"), na.get("bucket"))
    elif feature == "F2":
        dp = next((r for r in solver_reports if r["solver"] == "shift_dp"), None)
        now_action, future_action = _shift_now_and_future(
            ((dp or {}).get("solution") or {}) if dp else {})
        act_vn = {"REST": "nghỉ một lúc", "SWAP": "đi đổi pin", "ONLINE": "tiếp tục chạy",
                  "END": "cân nhắc kết ca"}.get(
                      (now_action or {}).get("action", ""), "tiếp tục chạy")
        future_act_vn = {"REST": "nghỉ một lúc", "SWAP": "đi đổi pin", "ONLINE": "tiếp tục chạy",
                         "END": "cân nhắc kết ca"}.get(
                             (future_action or {}).get("action", ""), "tiếp tục chạy")
        now_reason = (now_action or {}).get("reason", "")
        now_reason_txt = f" — {now_reason}" if now_reason else ""
        future_reason = (future_action or {}).get("reason", "")
        future_reason_txt = f" — {future_reason}" if future_reason else ""
        future_bucket = (future_action or {}).get("bucket")
        future_line = (f" Sắp tới {future_bucket} nên {future_act_vn}{future_reason_txt}."
                       if future_action and future_bucket else "")
        progress = _gap_sentence(solver_reports, reg, n1, n2)
        msg = (f"Bây giờ: anh/chị nên {act_vn}{now_reason_txt}.{future_line}{progress}"
               f"{_idle_sentence(solver_reports, reg)}"
               f"{_mission_sentence(solver_reports, reg)}{disclaimer}")
        if now_action:
            advice_spec = _advice_spec(now_action.get("action"), now_action.get("bucket"))
    else:  # F3
        f3 = next((r for r in solver_reports if r["solver"] == "f3_patterns"), None)
        top = (f3 or {}).get("solution", {}).get("top_pattern") if f3 else None
        progress = f" Ca này anh/chị đạt thêm {n1}, hướng tới mốc {n2}." if n1 and n2 else ""
        khoan = _khoan_sentence(solver_reports, reg)  # UC3 tiến độ tuần (PI-5a)
        if top:
            msg = (f"Tổng kết ca: {top['heuristic_note']}{progress}{khoan}"
                   f"{_idle_sentence(solver_reports, reg)} "
                   f"Ca sau anh/chị thử điều chỉnh điểm này nhé."
                   f"{_penalty_sentence(solver_reports, reg)}"
                   f"{_anomaly_sentence(solver_reports, reg)}{disclaimer}")
        else:
            msg = (f"Tổng kết ca:{(progress + ' ') if progress else ' '}"
                   f"không có điểm nào cần điều chỉnh rõ rệt "
                   f"— anh/chị chạy ổn định.{khoan}"
                   f"{_idle_sentence(solver_reports, reg)}"
                   f"{_penalty_sentence(solver_reports, reg)}"
                   f"{_anomaly_sentence(solver_reports, reg)}{disclaimer}")

    return {
        "message": msg.strip(),
        "citations": citations,
        "advice_spec": advice_spec,
        "caveats": ["template fallback (không dùng LLM)"],
        "fallback_used": True,
    }
