"""Kiểm thử và đánh giá phân phối biến thiên nhu cầu 30 ngày (Test Demand Variation 30 Days)."""

import sys
sys.path.insert(0, "src")

from gsm_sim.event import DailyDemandVariation, DayType

def run_test():
    engine = DailyDemandVariation(
        base_orders_per_day=50000.0,
        start_min=300,
        end_min=1440,
        stochastic_sigma=0.045,
        stochastic_clamp=(0.88, 1.15),
    )

    calendar = engine.evaluate_calendar(
        start_date="2026-04-20", # Bắt đầu từ thứ Hai, đi qua cả 30/4 và 1/5
        days=30,
        seed=7000
    )

    print("=" * 95)
    print(f"{'Ngày':<12} | {'Thứ':<8} | {'Phân loại':<10} | {'DOW Mult':<8} | {'Nhiễu':<8} | {'Tổng Mult':<9} | {'Nhu cầu (đơn)':<13}")
    print("-" * 95)

    dow_names = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"]
    orders_list = []

    for p in calendar:
        orders_list.append(p.effective_orders_per_day)
        print(f"{p.date:<12} | {dow_names[p.day_of_week]:<8} | {p.day_type.value:<10} | {p.dow_multiplier:<8.2f} | {p.stochastic_multiplier:<8.3f} | {p.total_multiplier:<9.4f} | {p.effective_orders_per_day:<13,}")

    print("=" * 95)
    print(f"Tổng số ngày: {len(calendar)}")
    print(f"Nhu cầu tối thiểu: {min(orders_list):,} đơn")
    print(f"Nhu cầu tối đa:    {max(orders_list):,} đơn")
    print(f"Nhu cầu trung bình: {sum(orders_list)/len(orders_list):,.1f} đơn/ngày")
    
    # Kiểm tra tính tất định CRN (chạy lại với cùng seed phải ra 100% giống hệt)
    calendar_retest = engine.evaluate_calendar("2026-04-20", 30, seed=7000)
    assert [p.effective_orders_per_day for p in calendar] == [p.effective_orders_per_day for p in calendar_retest], "Lỗi: Không bảo toàn tính tất định CRN!"
    print("Xác nhận: Tính tất định CRN (Common Random Numbers) đạt 100%!")

    # Kiểm tra tính chuẩn hóa tỷ trọng 24h
    for p in calendar[:3]:
        total_share = sum(p.hourly_shares.values())
        assert abs(total_share - 1.0) < 1e-6, f"Lỗi chuẩn hóa tỷ trọng giờ: {total_share}"
    print("Xác nhận: Tỷ trọng phân bổ giờ chuẩn hóa chính xác sum = 1.000000!")

if __name__ == "__main__":
    run_test()
