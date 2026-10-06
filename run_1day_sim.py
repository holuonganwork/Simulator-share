"""Script chạy mô phỏng 1 ngày GSM_SIMULATOR-check.
Tích hợp đầy đủ:
- Cơ chế Biến thiên Nhu cầu theo ngày (DailyDemandVariation)
- Động lực Thời tiết & Ngập lụt (WeatherEventEngine)
- Vận tốc tài xế thực tế (Cao điểm: 25 km/h, Thường: 35 km/h, Đêm: 45 km/h)
- 100% Bộ điều phối Bipartite Hungarian Batch Matching chuẩn (không PPO).
"""

import sys
import io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

# Thêm đường dẫn src vào sys.path
base_dir = Path(__file__).resolve().parent
if str(base_dir / "src") not in sys.path:
    sys.path.insert(0, str(base_dir / "src"))
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

import argparse
from gsm_sim.config import Config
from gsm_sim.runner import build_world, result_from_world
from gsm_sim.metrics import summarize


def main():
    parser = argparse.ArgumentParser(description="Chạy mô phỏng 1 ngày GSM_SIMULATOR-check (Batch Dispatcher)")
    parser.add_argument("--config", type=str, default="configs/pilot_hanoi.yaml", help="Đường dẫn file config YAML")
    parser.add_argument("--seed", type=int, default=7000, help="Hạt giống ngẫu nhiên (seed chuẩn 7000 hoặc 42)")
    parser.add_argument("--date", type=str, default=None, help="Ngày mô phỏng YYYY-MM-DD (mặc định lấy start_date từ config)")
    parser.add_argument("--export-parquet", action="store_true", help="Xuất toàn bộ hệ thống bảng Parquet vào thư mục data/mock/1day")
    args = parser.parse_args()

    if args.export_parquet:
        print(f"\n[+] Khởi chạy mô phỏng và xuất bộ dữ liệu Parquet 1 ngày (seed={args.seed})...")
        from gsm_core.mockgen.realdata import generate_realdata
        out_dir = base_dir / "data" / "mock" / "1day"
        res = generate_realdata(days=1, seed_base=args.seed, out_dir=out_dir)
        print(f"[✓] Đã xuất xong {len(res['tables'])} bảng Parquet vào: {out_dir}")
        return

    print(f"\n[+] Đang nạp cấu hình từ {args.config} (seed={args.seed})...")
    cfg = Config.load(args.config)
    
    date_str = args.date or str(cfg.get("event_dynamics.start_date", "2026-07-01"))
    print(f"[+] Khởi tạo thế giới mô phỏng ngày {date_str}...")
    world, deps = build_world(cfg, seed=args.seed, date_str=date_str)
    env = world.envctx

    if env and hasattr(env, "weather_profile") and env.weather_profile:
        wp = env.weather_profile
        dp = env.demand_profile
        print(f"  • Thời tiết trong ngày:        {wp.weather_type.value.upper()} ({wp.description})")
        print(f"  • Hệ số vận tốc thời tiết:     x{wp.speed_multiplier:.2f}")
        print(f"  • Cầu kỳ vọng sau biến thiên:  {dp.effective_orders_per_day:,} đơn (gốc {cfg.get('demand.orders_per_day'):,})")
        if wp.num_blocked_cells > 0:
            print(f"  • Ô đường bị ngập sâu cấm xe:  {wp.num_blocked_cells} ô")
            print(f"  • Ô đường ngập vừa giảm tốc:   {wp.num_severe_cells} ô")

    print(f"[+] Bắt đầu chạy mô phỏng 1 ngày (05:00 - 24:00)...")
    world.run()
    
    result = result_from_world(world, deps)
    summary = summarize(result)

    print("\n" + "=" * 65)
    print("=== KẾT QUẢ MÔ PHỎNG 1 NGÀY VẬN HÀNH (GSM_SIMULATOR-check) ===")
    print("=" * 65)
    print(f"Ngày mô phỏng:                   {date_str}")
    print(f"Tổng số đơn phát sinh:           {summary.get('orders_total'):,}")
    print(f"Số cuốc phục vụ thành công:      {summary.get('orders_completed'):,}")
    print(f"Số đơn hết hạn kiên nhẫn:        {summary.get('orders_expired'):,}")
    print(f"Số lượt tài xế từ chối:          {summary.get('orders_declined'):,}")
    print(f"Tỷ lệ phục vụ (Served Rate):     {summary.get('served_rate', 0)*100:.2f}%")
    print(f"Thu nhập trung vị của tài xế:    {summary.get('payout_per_actor_median', 0):,.0f} VND")
    print(f"Số cuốc trung vị mỗi tài xế:     {summary.get('trips_per_actor_median', 0)}")
    print(f"Số cuốc trung bình mỗi tài xế:   {summary.get('trips_per_actor_mean', 0):.2f}")
    print(f"Thời gian đón khách trung vị:    {summary.get('pickup_eta_median', 0):.2f} phút")
    print(f"Thời gian đón khách P90:         {summary.get('pickup_eta_p90', 0):.2f} phút")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
