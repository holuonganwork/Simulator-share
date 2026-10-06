"""Context pack — 1 RENDERER DUY NHẤT cho cả Composer prompt lẫn Verifier check.

Research đợt 7: Markdown-KV + XML tags; numbers canonical mỗi số 1 dòng [N-id];
system static byte-identical đầu (DeepSeek prefix cache); data trước, instruction
CUỐI (+30% Anthropic docs); budget ≤4K tokens. numbers_registry = nguồn sự thật
cho placeholder render + verify (faithfulness = bất biến cấu trúc).
"""

from __future__ import annotations

# MỘT nguồn định dạng VN dùng chung với solver (tránh 2 kiểu format cùng số)
from gsm_core.vn_format import render_number_vn  # noqa: F401 (re-export)

PROMPT_VERSION = "cp-v1"
MAX_PACK_CHARS = 16000  # ~4K tokens

# System prompt STATIC — byte-identical mọi request (prefix cache). KHÔNG đổi
# tùy driver/feature; per-feature instruction inject ở CUỐI user message.
SYSTEM_PROMPT = """Bạn là trợ lý tư vấn thu nhập cho tài xế Xanh SM Bike (xe máy điện) tại Hà Nội.

QUY TẮC CỨNG (không được vi phạm):
1. TUYỆT ĐỐI KHÔNG tự viết bất kỳ con số nào (tiền, điểm, giờ, phần trăm). Mọi số phải dùng placeholder dạng {{N1}}, {{N2}}... tham chiếu đúng [N-id] trong dữ liệu.
2. KHÔNG hứa hẹn hay đảm bảo mức thu nhập. Luôn nói rõ đây là ước tính có điều kiện.
3. KHÔNG khuyên nhận/từ chối/hủy bất kỳ đơn hàng cụ thể nào.
4. Trả lời chính sách phải kèm nguồn [P-id]. Không bịa chính sách.
5. Xưng hô "anh/chị", giọng thân thiện ngắn gọn của trợ lý cho tài xế xe ôm công nghệ.
6. Chỉ viết tiếng Việt (không chèn tiếng Trung/ký tự lạ).

ĐỊNH DẠNG TRẢ LỜI: JSON đúng schema được yêu cầu, message dùng placeholder {{N-id}} cho MỌI con số.

VÍ DỤ 1 (kế hoạch ca):
{"advice_spec": {"action_type": "rest_window", "target_window": "14:00-15:30"}, "message_template": "Chào anh/chị! Khung 14h-15h30 thường vắng khách, anh/chị tranh thủ nghỉ rồi chạy dồn khung tối sẽ hiệu quả hơn. Hiện anh/chị còn thiếu {{N1}} nữa là đạt mốc thưởng {{N2}} — hoàn toàn khả thi nếu chạy đủ khung 17h-20h. Lưu ý: số cuốc thực tế phụ thuộc nhu cầu, không đảm bảo trước được.", "citations": [], "caveats": ["Ước tính theo dữ liệu lịch sử, không phải cam kết"]}

VÍ DỤ 2 (hỏi chính sách):
{"advice_spec": null, "message_template": "Theo chính sách hiện hành [P1], anh/chị thuộc nhóm Platform sẽ được tính thưởng khi đạt đủ điểm ngày và giữ tỷ lệ nhận cuốc trên ngưỡng. Hiện anh/chị cần thêm {{N1}} để chạm mốc {{N2}}. Chi tiết anh/chị xem tại nguồn [P1] nhé.", "citations": ["P1"], "caveats": ["Chính sách có thể thay đổi — kiểm tra bản mới nhất trên app"]}
"""

_FEATURE_INSTRUCTION = {
    "F0": "NHIỆM VỤ: Trả lời câu hỏi chính sách của tài xế dựa trên trích đoạn [P-id] và số liệu [N-id]. Bắt buộc cite [P-id] cho mọi claim chính sách.",
    "F1": "NHIỆM VỤ: Tư vấn kế hoạch ca (mục tiêu điểm/thưởng + lịch chạy-nghỉ-sạc) từ báo cáo solver. Một thông điệp gọn, nêu next step rõ.",
    "F2": "NHIỆM VỤ: Nêu hành động hiện tại từ schedule[0] trước; chỉ nêu next_action như bước sắp tới nếu khác bucket. Không gọi next_action là hành động hiện tại. Ngắn gọn 2-3 câu.",
    "F3": "NHIỆM VỤ: Tổng kết ca + nêu ĐÚNG MỘT điểm cải thiện nổi bật nhất (top_pattern). Không phán xét, giọng khích lệ.",
}


def build_context_pack(feature: str, solver_reports: list[dict],
                       kb_excerpts: list[dict], driver_id: str) -> dict:
    """Trả {system_prompt, prompt_data, instruction, numbers_registry, citations_registry}."""
    registry: dict[str, dict] = {}
    cite_reg: dict[str, dict] = {}
    blocks: list[str] = []
    n_idx = 0

    for rep in solver_reports:
        lines = [f'<solver_report solver="{rep["solver"]}">',
                 f"tóm_tắt: {rep['problem_digest']}"]
        sol = rep.get("solution", {})
        # whitelist key được đưa vào prompt. S5/S6 (PI-5a) thêm key riêng — nếu không
        # khai ở đây thì LLM/verifier KHÔNG thấy nội dung khoán tuần / mini-task.
        for k in ("feasible", "schedule", "next_action", "top_pattern", "herding_avoided",
                  "quota_available", "gap_revenue_vnd", "clawback_risk_vnd",
                  "expected_reward_vnd", "chosen_missions",
                  "total_idle_min", "idle_share", "worst_window", "reposition_mission",
                  "total_deducted_vnd", "penalty_count", "open_count", "top_severity"):
            if k in sol and sol[k] is not None:
                v = sol[k]
                if k == "schedule" and isinstance(v, list):
                    v = "; ".join(
                        f"{item.get('bucket')}: {item.get('action')}"
                        for item in v if isinstance(item, dict)
                    )
                elif isinstance(v, dict):
                    v = v.get("action", v.get("type", ""))
                elif isinstance(v, list):  # chosen_missions → tên nhiệm vụ (số ở [N-id])
                    v = ", ".join(str(x.get("name") or x.get("mission_id")) for x in v) or "không có"
                lines.append(f"{k}: {v}")
        lines.append("số_liệu (chỉ dùng các [N-id] này, KHÔNG tự viết số):")
        for n in rep.get("numbers", []):
            n_idx += 1
            nid = f"N{n_idx}"
            registry[nid] = dict(n)
            lines.append(f"- [{nid}] {render_number_vn(n['value'], n['unit'])} "
                         f"(nguồn: {n['source']})")
        for c in rep.get("caveats", []):
            lines.append(f"lưu_ý: {c}")
        if rep.get("infeasible_reason"):
            lines.append(f"không_khả_thi_vì: {rep['infeasible_reason']}")
        lines.append("</solver_report>")
        blocks.append("\n".join(lines))

    for i, ex in enumerate(kb_excerpts, start=1):
        pid = f"P{i}"
        cite_reg[pid] = {"source_id": ex["source_id"], "url": ex["source_url"],
                          "title": ex["title"]}
        blocks.append(f'<policy_excerpt id="{pid}" url="{ex["source_url"]}">\n'
                      f'tiêu_đề: {ex["title"]}\n{ex["excerpt"]}\n</policy_excerpt>')

    prompt_data = "\n\n".join(blocks)
    # budget: cắt excerpt trước (không bao giờ cắt numbers)
    if len(prompt_data) > MAX_PACK_CHARS:
        prompt_data = prompt_data[:MAX_PACK_CHARS] + "\n[...đã cắt bớt trích đoạn...]"

    return {
        "system_prompt": SYSTEM_PROMPT,
        "prompt_data": prompt_data,
        "instruction": _FEATURE_INSTRUCTION.get(feature, _FEATURE_INSTRUCTION["F1"]),
        "numbers_registry": registry,
        "citations_registry": cite_reg,
        "prompt_version": PROMPT_VERSION,
    }
