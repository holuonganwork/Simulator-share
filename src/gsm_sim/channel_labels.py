"""MỘT nguồn nhãn tiếng Việt + giải thích cho các kênh advisor — cho MỌI bề mặt hiển thị.

Vì sao tồn tại (Cường 2026-08-06): *"các phần tên nên được đặt để dễ hiểu — swap_early và
station_choice có thể sẽ hơi khó hiểu để stakeholder theo dõi"*. Nguyên tắc:

- **ID nội bộ KHÔNG đổi** (`swap_early`, `station_choice`, …) — chúng nằm trong registry,
  contract, event log, dữ liệu đã lưu; đổi tên là phá contract (bài học QĐ-4: hợp nhất
  THẨM QUYỀN, không đổi tên). Chỉ NHÃN HIỂN THỊ là tiếng Việt dễ hiểu.
- Dashboard/web/Flutter đều đọc từ đây (hoặc chép đúng nguyên văn) — hai bảng nhãn lệch nhau
  là họ lỗi "hai nguồn sự thật".
"""
from __future__ import annotations

# ID kênh -> (nhãn ngắn cho checkbox/bảng, giải thích một câu cho tooltip/caption)
#
# ⚠ QUY TẮC CHỮ UI (Cường 2026-08-06): đây là BẢN CUỐI cho stakeholder — KHÔNG mã hiệu nội bộ
# (ĐA-07, D-M3-*, V-28, seed, arm...) trong nhãn/tooltip. Ngữ cảnh nội bộ để ở comment:
#   · positioning: kênh duy nhất bật mặc định — duyệt ĐA-08 n=100.
#   · shift_plan: ĐA-07 bác (Δ âm SIG khi thêm trên nền positioning) — chỉ nghiên cứu.
#   · shift_extend: 3 lan can sức khoẻ D-QD4-03; rest_window: cam kết D-M3-04-FIX, khuyên mềm.
#   · station_choice: số −66% từ artifact e01-station-30.json (n=30, thăm dò).
CHANNEL_VN: dict[str, tuple[str, str]] = {
    "positioning": (
        "Gợi ý vị trí đứng chờ",
        "Khi tài xế định đứng chờ tại chỗ, gợi ý khu vực có nhu cầu tốt hơn. Kênh đang bật "
        "mặc định — đã kiểm chứng lợi ích trên mô phỏng lớn."),
    "shift_plan": (
        "Lịch ca tổng thể",
        "Lịch chạy/nghỉ/đổi pin cả ca do bộ tối ưu tính. Phép đo trước đây cho thấy kênh này "
        "làm giảm hiệu quả — chỉ dùng để nghiên cứu."),
    "accept_lift": (
        "Cảnh báo tỷ lệ nhận sát ngưỡng thưởng",
        "Nhắc khi tỷ lệ nhận cuốc sắp rơi dưới ngưỡng đủ điều kiện thưởng ngày và vẫn còn "
        "kịp cải thiện."),
    "shift_extend": (
        "Kéo ca khi sát mốc thưởng",
        "Chỉ gợi ý chạy thêm khi mốc thưởng kế không kịp trong ca và phần chạy thêm không "
        "đẩy tài xế quá ngưỡng mệt."),
    "rest_window": (
        "Hẹn giờ nghỉ vào khung vắng khách",
        "Thay vì bỏ giờ nghỉ, hẹn một giờ nghỉ cụ thể vào lúc vắng khách — tới giờ là nghỉ "
        "thật. Loại gợi ý nhẹ nhàng, không theo dõi mức làm theo."),
    "swap_early": (
        "Đổi pin sớm lúc rảnh, trạm vắng",
        "Pin sắp tới lúc phải đổi — tranh thủ đổi khi đang rảnh và trạm không phải chờ, "
        "thay vì để cạn giữa lúc đông khách."),
    "station_choice": (
        "Chọn trạm đổi pin ít phải chờ",
        "Chọn trạm theo tổng thời gian thật (đường đi + hàng đợi + pin sẵn) thay vì cứ tới "
        "trạm gần nhất. Mô phỏng cho thấy thời gian chờ trạm giảm khoảng 2/3."),
}


def label(channel_id: str) -> str:
    """Nhãn hiển thị; fallback về chính ID nếu kênh chưa khai (đừng che kênh mới)."""
    return CHANNEL_VN.get(channel_id, (channel_id, ""))[0]


def help_text(channel_id: str) -> str:
    return CHANNEL_VN.get(channel_id, (channel_id, ""))[1]
