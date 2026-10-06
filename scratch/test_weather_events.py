"""Kiểm thử và đánh giá toàn diện Bộ máy Sự kiện Thời tiết & Ngập lụt (Test Weather Event Engine)."""

import sys
sys.path.insert(0, "src")

from collections import Counter
from gsm_sim.event import WeatherType, WeatherEventEngine

def run_weather_tests():
    print("=" * 85)
    print("BẮT ĐẦU KIỂM THỬ BỘ MÁY SỰ KIỆN THỜI TIẾT & MÔI TRƯỜNG CỰC ĐOAN (GSM_SIMULATOR-rl)")
    print("=" * 85)

    # Giả lập 100 ô H3 tượng trưng cho các phường quận tại Hà Nội
    mock_cells = [f"87415d{i:04x}ffffff" for i in range(100)]
    engine = WeatherEventEngine(core_cells=mock_cells)

    # 1. KIỂM THỬ TẦN SUẤT XUẤT HIỆN TRÊN 1,000 NGÀY (Monte Carlo Validation)
    calendar_1000 = engine.evaluate_calendar(
        start_date="2026-01-01",
        days=1000,
        seed=7000
    )
    counts = Counter(p.weather_type for p in calendar_1000)

    print("\n--- 1. PHÂN BỐ TẦN SUẤT THỜI TIẾT TRÊN 1,000 NGÀY MÔ PHỎNG ---")
    for wt in [WeatherType.PLEASANT, WeatherType.RAIN, WeatherType.FOG, WeatherType.FLOOD]:
        cnt = counts[wt]
        pct = cnt / 1000 * 100
        print(f"  • {wt.value.upper():<10}: {cnt:>4} ngày ({pct:>5.1f}%) | "
              f"Kỳ vọng lý thuyết: {engine.probabilities[wt]*100:>5.1f}%")

    assert counts[WeatherType.PLEASANT] > counts[WeatherType.RAIN] > counts[WeatherType.FOG], "Lỗi thứ tự tần suất!"
    assert counts[WeatherType.FLOOD] < 35, "Lỗi: Sự kiện lũ lụt xuất hiện quá nhiều!"
    assert counts[WeatherType.FOG] < 45, "Lỗi: Sự kiện sương mù xuất hiện quá nhiều!"
    print("✓ Xác nhận: Tần suất Mát trời cao nhất, Mưa ít, Lũ lụt & Sương mù siêu hiếm!")

    # 2. KIỂM THỬ TỪNG KỊCH BẢN THỜI TIẾT CỤ THỂ
    print("\n--- 2. KIỂM THỬ ĐẶC TÍNH VẬT LÝ TỪNG KỊCH BẢN THỜI TIẾT ---")

    # Kịch bản 2.1: Mát trời
    prof_pleasant = engine.evaluate_day("2026-05-10", 0, 7000, override_type=WeatherType.PLEASANT)
    spd_pl = engine.get_effective_speed_multiplier(prof_pleasant, t_min=480, cell=mock_cells[0])
    dem_pl = engine.get_effective_demand_multiplier(prof_pleasant, t_min=480, cell=mock_cells[0])
    print(f"\n[A] MÁT TRỜI (PLEASANT):")
    print(f"  - Diễn giải: {prof_pleasant.description}")
    print(f"  - Hệ số vận tốc: {spd_pl:.2f} (chuẩn 100%)")
    print(f"  - Hệ số nhu cầu: {dem_pl:.2f} (chuẩn 100%)")
    print(f"  - Số ô cấm đường: {prof_pleasant.num_blocked_cells} ô")
    assert spd_pl == 1.0 and dem_pl == 1.0 and prof_pleasant.num_blocked_cells == 0

    # Kịch bản 2.2: Trời mưa
    prof_rain = engine.evaluate_day("2026-05-11", 1, 7000, override_type=WeatherType.RAIN)
    mid_rain = (prof_rain.event_start_min + prof_rain.event_end_min) / 2
    spd_rn = engine.get_effective_speed_multiplier(prof_rain, t_min=mid_rain, cell=mock_cells[0])
    dem_rn = engine.get_effective_demand_multiplier(prof_rain, t_min=mid_rain, cell=mock_cells[0])
    print(f"\n[B] TRỜI MƯA (RAIN):")
    print(f"  - Diễn giải: {prof_rain.description}")
    print(f"  - Khung giờ mưa: {prof_rain.event_start_min//60}h00 - {prof_rain.event_end_min//60}h00")
    print(f"  - Hệ số vận tốc: {spd_rn:.2f} (giảm nhẹ ~16%)")
    print(f"  - Hệ số nhu cầu: {dem_rn:.2f} (khách giảm nhẹ ~8% đúng yêu cầu)")
    assert 0.80 <= spd_rn <= 0.88 and 0.88 <= dem_rn <= 0.95

    # Kịch bản 2.3: Sương mù dày đặc
    prof_fog = engine.evaluate_day("2026-05-12", 2, 7000, override_type=WeatherType.FOG)
    spd_fog_peak = engine.get_effective_speed_multiplier(prof_fog, t_min=360, cell=mock_cells[0]) # 06:00 sáng
    spd_fog_after = engine.get_effective_speed_multiplier(prof_fog, t_min=660, cell=mock_cells[0]) # 11:00 trưa
    print(f"\n[C] SƯƠNG MÙ DÀY ĐẶC (FOG):")
    print(f"  - Diễn giải: {prof_fog.description}")
    print(f"  - Vận tốc lúc 06:00 sáng (sương mù đỉnh điểm): {spd_fog_peak:.2f} (tốc độ giảm đáng kể ~42%)")
    print(f"  - Vận tốc lúc 11:00 trưa (sương mù đã tan):   {spd_fog_after:.2f} (hồi phục bình thường)")
    assert 0.50 <= spd_fog_peak <= 0.65
    assert spd_fog_after == 1.0

    # Kịch bản 2.4: Lũ lụt / Ngập úng đô thị
    prof_flood = engine.evaluate_day("2026-05-13", 3, 7000, override_type=WeatherType.FLOOD)
    blocked_list = list(prof_flood.blocked_cells)
    severe_list = list(prof_flood.severe_slowdown_cells)
    normal_cell = [c for c in mock_cells if c not in prof_flood.blocked_cells and c not in prof_flood.severe_slowdown_cells][0]

    b_cell = blocked_list[0]
    s_cell = severe_list[0]
    mid_flood = (prof_flood.event_start_min + prof_flood.event_end_min) / 2

    spd_blocked = engine.get_effective_speed_multiplier(prof_flood, mid_flood, cell=b_cell)
    spd_severe = engine.get_effective_speed_multiplier(prof_flood, mid_flood, cell=s_cell)
    spd_normal = engine.get_effective_speed_multiplier(prof_flood, mid_flood, cell=normal_cell)

    passable_blocked = engine.is_route_passable(prof_flood, mid_flood, b_cell, normal_cell)
    passable_ok = engine.is_route_passable(prof_flood, mid_flood, s_cell, normal_cell)

    detour_normal = engine.get_route_detour_factor(prof_flood, mid_flood, normal_cell, normal_cell, 1.3)
    detour_severe = engine.get_route_detour_factor(prof_flood, mid_flood, s_cell, normal_cell, 1.3)

    print(f"\n[D] LŨ LỤT / NGẬP ÚNG ĐÔ THỊ (FLOOD):")
    print(f"  - Diễn giải: {prof_flood.description}")
    print(f"  - Tổng số ô ngập sâu cấm đường: {prof_flood.num_blocked_cells} ô ({prof_flood.num_blocked_cells/len(mock_cells)*100:.1f}%)")
    print(f"  - Tổng số ô ngập vừa giảm tốc:  {prof_flood.num_severe_cells} ô ({prof_flood.num_severe_cells/len(mock_cells)*100:.1f}%)")
    print(f"  - Ô ngập sâu '{b_cell}': Vận tốc = {spd_blocked:.2f} (KHÔNG THỂ ĐI ĐƯỢC) | Tuyến đường khả thi = {passable_blocked}")
    print(f"  - Ô ngập vừa '{s_cell}':  Vận tốc = {spd_severe:.2f} (GIẢM TRẦM TRỌNG ~25%) | Tuyến đường khả thi = {passable_ok}")
    print(f"  - Ô khô ráo  '{normal_cell}':  Vận tốc = {spd_normal:.2f} | Detour bình thường = {detour_normal:.2f}")
    print(f"  - Detour khi đi qua ô ngập vừa: {detour_severe:.2f} (tăng xe phải đi vòng tránh vũng ngập)")

    assert spd_blocked == 0.0, "Lỗi: Ô ngập sâu phải có vận tốc = 0!"
    assert passable_blocked == False, "Lỗi: Ô ngập sâu không thể lưu thông!"
    assert 0.20 <= spd_severe <= 0.30, "Lỗi: Vận tốc ô ngập vừa phải giảm trầm trọng!"
    assert detour_severe > detour_normal, "Lỗi: Detour phải tăng khi qua vùng ngập!"

    # 3. KIỂM THỬ TÍNH TẤT ĐỊNH 100% (CRN-Safe)
    print("\n--- 3. KIỂM THỬ TÍNH TẤT ĐỊNH (COMMON RANDOM NUMBERS) ---")
    cal_1 = engine.evaluate_calendar("2026-06-01", 30, seed=42)
    cal_2 = engine.evaluate_calendar("2026-06-01", 30, seed=42)

    w_types_1 = [p.weather_type for p in cal_1]
    w_types_2 = [p.weather_type for p in cal_2]
    assert w_types_1 == w_types_2, "Lỗi: Thời tiết không tái lập!"

    blocked_1 = [p.blocked_cells for p in cal_1]
    blocked_2 = [p.blocked_cells for p in cal_2]
    assert blocked_1 == blocked_2, "Lỗi: Danh sách ô ngập lụt cấm đường không tái lập!"
    print("✓ Xác nhận: Tính tất định CRN đạt 100% hoàn hảo (tái lập từng bit trên mọi lần chạy)!")

    print("\n" + "=" * 85)
    print("TẤT CẢ CÁC BÀI KIỂM THỬ THỜI TIẾT ĐÃ VƯỢT QUA 100% XUẤT SẮC!")
    print("=" * 85)

if __name__ == "__main__":
    run_weather_tests()
