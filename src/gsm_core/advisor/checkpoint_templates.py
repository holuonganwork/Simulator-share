"""Deterministic, versioned AdviceCheckpoint templates.

Templates own wording for the common/repeated path.  They never choose a
canonical action and they only interpolate values from the typed number
registry supplied by checkpoint orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from gsm_core.vn_format import render_number_vn


# v2 (UPDATE-147): SWAP/REST render action_window thật ("trong khung HH:MM–HH:MM")
# khi record 1.2.0 mang window; wording không window giữ nguyên v1.
TEMPLATE_VERSION = "checkpoint-template-v3"


@dataclass(frozen=True)
class TemplateRender:
    title: str
    summary: str
    why: str
    template_key: str
    template_version: str
    required_fact_ids: tuple[str, ...] = ()
    required_number_ids: tuple[str, ...] = ()
    required_caveat_ids: tuple[str, ...] = ()
    fallback_reason: str | None = None


_ACTION_LABELS = {
    "PROTECT_ELIGIBILITY": "bảo vệ điều kiện thưởng",
    "ONLINE": "tiếp tục online",
    "REST": "nghỉ",
    "SWAP": "đổi pin",
    "END": "kết ca",
    "EXTEND": "kéo dài ca",
    "REPOSITION_SIM_ONLY": "tái định vị (chỉ mô phỏng)",
    "NO_ACTION": "giữ nguyên trạng thái",
}


def _number_map(numbers: Iterable[dict]) -> dict[str, dict]:
    return {str(item["id"]): item for item in numbers if item.get("id")}


def _semantic_number_map(numbers: Iterable[dict]) -> dict[str, dict]:
    return {str(item["key"]): item for item in numbers if item.get("key")}


def _number_text(item: dict) -> str:
    return render_number_vn(item["value"], str(item["unit"]))


def _clock(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except (TypeError, ValueError):
        return None


def _window_text(window: dict | None) -> str | None:
    if not window:
        return None
    start = _clock(window.get("start"))
    end = _clock(window.get("end"))
    if start and end:
        return f"{start}–{end}"
    return None


class CheckpointTemplateRegistry:
    """Resolve a closed template key from canonical checkpoint context."""

    def resolve(self, checkpoint: dict, *, facts: list[dict],
                numbers: list[dict], caveats: list[dict], locale: str,
                surface: str) -> TemplateRender:
        del locale, surface  # reserved in the key contract; vi-VN/nudge is MVP.
        action = str((checkpoint.get("current_action") or {}).get("code") or "NO_ACTION")
        future = checkpoint.get("future_plan") or []
        future_action = str((future[0] if future else {}).get("code") or "")
        number_by_id = _number_map(numbers)
        number_by_key = _semantic_number_map(numbers)

        if checkpoint.get("reason_code") == "idea.IC-01":
            now_label = _ACTION_LABELS.get(action, action.lower())
            future_label = _ACTION_LABELS.get(future_action, future_action.lower())
            future_window = _window_text((future[0] or {}).get("window")) if future else None
            next_text = (f" Bước kế tiếp: {future_label}"
                         + (f" trong khung {future_window}." if future_window else ".")
                         if future_action else "")
            soc = number_by_key.get("soc_pct")
            soc_text = f" Mức pin ghi nhận: {_number_text(soc)}." if soc else ""
            required = (str(soc["id"]),) if soc else ()
            return TemplateRender(
                "Kế hoạch đầu ca",
                f"Bây giờ: {now_label}.{next_text}",
                "Lý do: brief được mở khi đã có cả kế hoạch S2, trạng thái năng lượng "
                f"và policy/S1 còn hiệu lực.{soc_text} Chưa dùng so sánh cùng khung giờ "
                "của các ngày trước khi chưa có baseline intraday đủ chuẩn.",
                "OPENING_SHIFT_BRIEF", TEMPLATE_VERSION,
                required_number_ids=required)

        if (checkpoint.get("reason_code") == "episode.energy_pressure_building"
                and action == "ONLINE" and future_action == "SWAP"):
            window = _window_text((future[0] or {}).get("window"))
            skip = number_by_key.get("soc_skip_count")
            observed = (f" Hệ thống đã ghi nhận {_number_text(skip)} hành trình không "
                        "phù hợp mức pin hiện tại." if skip else "")
            next_step = (f"Chuẩn bị đổi pin trong khung {window}." if window
                         else "Chuẩn bị cho bước đổi pin sắp tới trong kế hoạch.")
            required = (str(skip["id"]),) if skip else ()
            return TemplateRender(
                "Chuẩn bị đổi pin theo kế hoạch",
                f"Bây giờ: Tiếp tục trực tuyến. Sắp tới: {next_step}",
                "Lý do:" + observed + " Kế hoạch còn hiệu lực đã chuyển bước kế tiếp "
                "sang đổi pin; hệ thống sẽ cập nhật nếu cửa sổ thay đổi.",
                "EPISODE_ENERGY_PRESSURE", TEMPLATE_VERSION,
                required_number_ids=required)

        if action == "PROTECT_ELIGIBILITY":
            number = next((item for item in numbers
                           if item.get("unit") in {"trips", "points"}), None)
            if number is not None:
                value = _number_text(number)
                return TemplateRender(
                    "Bảo vệ điều kiện thưởng",
                    f"Mốc hiện tại còn thiếu {value}. Theo dõi điều kiện của mốc trong "
                    "cửa sổ đang còn hiệu lực.",
                    "Lý do: solver đánh giá mốc vẫn còn khả thi theo phiên chính sách "
                    "đang áp dụng; đây không phải lời khuyên nhận một cuốc cụ thể.",
                    "S1_BONUS_PROGRESS", TEMPLATE_VERSION,
                    required_number_ids=(str(number["id"]),))
            return TemplateRender(
                "Bảo vệ điều kiện thưởng",
                "Mốc thưởng vẫn còn khả thi trong cửa sổ hiện tại.",
                "Lý do: solver đã đánh giá theo chính sách hiện hành nhưng chưa có số "
                "gap đủ nghĩa để hiển thị.",
                "S1_BONUS_PROGRESS", TEMPLATE_VERSION)

        if action == "ONLINE" and future_action == "SWAP":
            window = _window_text((future[0] or {}).get("window"))
            upcoming = (f" Sắp tới: Đổi pin trong khung {window}."
                        if window else " Sắp tới: Cân nhắc đổi pin trong khung kế tiếp.")
            return TemplateRender(
                "Tiếp tục online",
                "Bây giờ: Tiếp tục online." + upcoming,
                "Lý do: kế hoạch hiện tại giữ trạng thái online trước khi đổi pin.",
                "S2_ONLINE_NOW_SWAP_LATER", TEMPLATE_VERSION)

        action_window = _window_text(checkpoint.get("action_window"))
        swap_summary = (f"Đổi pin trong khung {action_window}." if action_window
                        else "Đổi pin trong cửa sổ được đề xuất.")
        rest_summary = (f"Nghỉ trong khung {action_window}." if action_window
                        else "Nghỉ trong cửa sổ đang còn hiệu lực.")
        templates = {
            "SWAP": ("Chuẩn bị đổi pin", swap_summary,
                      "Lý do: action hiện tại của kế hoạch là SWAP trong cửa sổ đang "
                      "hiển thị; hệ thống không suy thêm nguyên nhân ngoài solver.",
                      "S2_SWAP_NOW"),
            "REST": ("Nghỉ trong khung này", rest_summary,
                     "Lý do: kế hoạch đã tính trạng thái nghỉ và thời gian trong ca.",
                     "S7_REST_WINDOW"),
            "END": ("Kết ca", "Kết ca theo ranh giới kế hoạch hiện tại.",
                    "Lý do: chạy thêm không cải thiện kế hoạch hiện tại.", "S2_END_SHIFT"),
            "EXTEND": ("Cân nhắc kéo dài ca", "Kéo dài trong giới hạn đang hiển thị.",
                       "Lý do: mốc kế tiếp còn nằm trong trần kéo dài đã khóa.",
                       "S2_EXTEND_SHIFT"),
            "REPOSITION_SIM_ONLY": (
                "Tái định vị (chỉ mô phỏng)",
                "Bây giờ: Tái định vị trong mô phỏng.",
                "Lý do: đây là tín hiệu positioning chỉ dùng cho simulator.",
                "S4_RELOCATE"),
            "ONLINE": ("Tiếp tục online", "Bây giờ: Tiếp tục online.",
                        "Lý do: đây là trạng thái duy trì, không phải chỉ định vị trí.",
                        "S2_ONLINE_NOW"),
        }
        if action in templates:
            title, summary, why, key = templates[action]
            return TemplateRender(title, summary, why, key, TEMPLATE_VERSION)

        # Keep the registry strict: unused inputs are intentionally not folded into
        # free-form text, and unsupported actions cannot accidentally become advice.
        del facts, caveats, number_by_id, number_by_key
        return TemplateRender(
            "Gợi ý cho ca hiện tại", "Bây giờ: Giữ nguyên trạng thái.",
            "Lý do: chưa có mẫu diễn giải an toàn cho khuyến nghị này.",
            "FALLBACK_NO_ACTION", TEMPLATE_VERSION,
            fallback_reason="unknown_or_unsupported_template")
