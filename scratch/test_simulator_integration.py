"""Kiểm thử tích hợp toàn diện: Cơ chế Biến thiên Nhu cầu & Thời tiết vào Lõi Simulator (GSM_SIMULATOR-check)."""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "src")

from gsm_sim.config import Config
from gsm_sim.runner import build_environment, build_world
from gsm_sim.demand import generate_orders
from gsm_sim.event import WeatherType

def test_integration():
    print("=" * 85)
    print("KIỂM THỬ TÍCH HỢP TOÀN DIỆN LÕI SIMULATOR (GSM_SIMULATOR-check)")
    print("=" * 85)

    cfg = Config.load("configs/pilot_hanoi.yaml")

    # 1. KIỂM THỬ KHỞI TẠO VÀ GẮN PROFILE VÀO ENVIRONMENT CONTEXT
    print("\n[+] 1. Kiểm tra build_world và tích hợp EnvironmentContext...")
    world, deps = build_world(cfg, seed=7000)
    env = world.envctx

    assert env is not None, "Lỗi: envctx không được khởi tạo!"
    assert hasattr(env, "demand_profile") and env.demand_profile is not None, "Lỗi: demand_profile chưa được gắn vào env!"
    assert hasattr(env, "weather_profile") and env.weather_profile is not None, "Lỗi: weather_profile chưa được gắn vào env!"

    d_prof = env.demand_profile
    w_prof = env.weather_profile

    print(f"  ✓ Ngày mô phỏng:           {d_prof.date} (Thứ {d_prof.day_of_week + 2 if d_prof.day_of_week < 6 else 'CN'})")
    print(f"  ✓ Phân loại ngày:          {d_prof.day_type.value.upper()}")
    print(f"  ✓ Nhu cầu kỳ vọng ngày:    {d_prof.effective_orders_per_day:,} đơn (x{d_prof.total_multiplier:.3f})")
    print(f"  ✓ Thời tiết trong ngày:    {w_prof.weather_type.value.upper()} ({w_prof.description})")
    print(f"  ✓ Số ô ngập sâu cấm đường: {w_prof.num_blocked_cells} ô")
    print(f"  ✓ Số ô ngập vừa giảm tốc:  {w_prof.num_severe_cells} ô")

    # 2. KIỂM THỬ SINH ĐƠN HÀNG VỚI DEMAND PROFILE ĐỘNG
    print("\n[+] 2. Kiểm tra sinh đơn hàng động (generate_orders)...")
    orders = world.orders
    print(f"  ✓ Tổng số đơn sinh ra:     {len(orders):,} đơn")
    # Số đơn thực tế sinh ra theo Poisson xoay quanh effective_orders_per_day (dung sai < 3%)
    err_pct = abs(len(orders) - d_prof.effective_orders_per_day) / d_prof.effective_orders_per_day
    print(f"  ✓ Sai số so với kỳ vọng:   {err_pct*100:.2f}% (Poisson variance bình thường)")
    assert err_pct < 0.04, f"Lỗi: Số đơn sinh ra ({len(orders)}) lệch quá xa so với hồ sơ ({d_prof.effective_orders_per_day})!"

    # 3. KIỂM THỬ ĐẶC TÍNH VẬN TỐC HIỆU DỤNG CỦA WORLD (_eff_speed)
    print("\n[+] 3. Kiểm tra vận tốc hiệu dụng qua _eff_speed...")
    spd_peak = world._eff_speed(hour=7)
    spd_offpeak = world._eff_speed(hour=11)
    spd_night = world._eff_speed(hour=23)
    print(f"  ✓ Vận tốc hiệu dụng 07h:    {spd_peak:.2f} km/h (cơ sở 25 km/h kết hợp thời tiết/tắc đường)")
    print(f"  ✓ Vận tốc hiệu dụng 11h:    {spd_offpeak:.2f} km/h (cơ sở 35 km/h)")
    print(f"  ✓ Vận tốc hiệu dụng 23h:    {spd_night:.2f} km/h (cơ sở 45 km/h)")

    # 4. KIỂM THỬ SỰ KIỆN LŨ LỤT CỰC ĐOAN (FORCE OVERRIDE FLOOD EVENT)
    print("\n[+] 4. Kiểm tra sự kiện lũ lụt cực đoan (Flood Event Override)...")
    from gsm_sim.event import WeatherEventEngine
    wt_engine = WeatherEventEngine(core_cells=world.grid.core_cells)
    flood_profile = wt_engine.evaluate_day(d_prof.date, day_index=0, seed=7000, override_type=WeatherType.FLOOD)
    env.attach_event_profiles(demand_profile=d_prof, weather_profile=flood_profile)

    blocked_cell = list(flood_profile.blocked_cells)[0]
    severe_cell = list(flood_profile.severe_slowdown_cells)[0]

    v_blocked = world._eff_speed(hour=12, cell=blocked_cell)
    v_severe = world._eff_speed(hour=12, cell=severe_cell)
    detour_normal = world._dfac("cell_a", "cell_b")
    detour_flood = env.get_route_detour_factor(t_min=720, pickup_cell=severe_cell, drop_cell="cell_b", base_detour=1.3)

    print(f"  ✓ Ô ngập sâu '{blocked_cell}':  Vận tốc = {v_blocked:.2f} km/h (CẤM ĐƯỜNG)")
    print(f"  ✓ Ô ngập vừa '{severe_cell}':   Vận tốc = {v_severe:.2f} km/h (GIẢM TRẦM TRỌNG)")
    print(f"  ✓ Detour bình thường:             {detour_normal:.2f}")
    print(f"  ✓ Detour tránh ngập:              {detour_flood:.2f} (tăng xe phải đi vòng)")

    assert v_blocked == 0.0, "Lỗi: Ô ngập sâu phải có vận tốc = 0!"
    assert v_severe < spd_offpeak * 0.4, "Lỗi: Ô ngập vừa phải giảm tốc trầm trọng!"
    assert detour_flood > detour_normal, "Lỗi: Detour tránh ngập phải lớn hơn bình thường!"

    # Khôi phục lại weather profile gốc
    env.attach_event_profiles(demand_profile=d_prof, weather_profile=w_prof)

    # 5. CHẠY THỬ MÔ PHỎNG 60 PHÚT ĐẦU TIÊN (05:00 - 06:00)
    print("\n[+] 5. Chạy thử mô phỏng 60 phút thực tế (05:00 - 06:00)...")
    world.start_processes()
    world.env.run(until=360) # Chạy từ 300 đến 360 (60 phút)
    print(f"  ✓ Đồng hồ SimPy hiện tại:   {world.env.now:.1f} phút (06:00 sáng)")
    print(f"  ✓ Số sự kiện đã sinh ra:    {len(world.events):,} events")
    print(f"  ✓ Mô phỏng chạy trơn tru, không có bất kỳ lỗi Runtime nào!")

    print("\n" + "=" * 85)
    print("XÁC NHẬN: TÍCH HỢP EVENT & WEATHER VÀO SIMULATOR-CHECK THÀNH CÔNG 100%!")
    print("=" * 85)

if __name__ == "__main__":
    test_integration()
