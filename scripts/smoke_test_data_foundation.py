#!/usr/bin/env python3
"""Smoke Test: Kiểm tra toàn diện Nền Tảng Dữ Liệu & Simulator (Data Foundation & Simulator).

Script độc lập dành cho kỹ sư tiếp nhận hệ thống:
1. Kiểm tra môi trường thư viện cốt lõi (simpy, h3, polars, pyarrow, shapely, pyyaml, scipy, jsonschema).
2. Kiểm tra Hệ thống Data Schemas (schemas/ l0, l1, l1r, l2, l3).
3. Kiểm tra Kho Dữ liệu Nền tảng (13 bảng Parquet trong data/mock/realdata-v2/).
4. Kiểm tra Dữ liệu Không gian & Hạ tầng (research/simulation/data/).
5. Chạy mô phỏng thế giới tự nhiên (Baseline Environment, seed=42) và lưu dữ liệu.
6. Xác thực tự động dữ liệu đầu ra (events.parquet, actors.parquet, manifest.json, định luật bảo toàn cuốc).

Cách chạy:
    python scripts/smoke_test_data_foundation.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Đảm bảo in tiếng Việt trên Windows console không lỗi charmap
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Đảm bảo src/ nằm trong PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log_step(name: str):
    print(f"\n{BOLD}{BLUE}▶ {name}{RESET}")


def log_pass(msg: str):
    print(f"  {GREEN}✔ [PASS]{RESET} {msg}")


def log_warn(msg: str):
    print(f"  {YELLOW}⚠ [WARN]{RESET} {msg}")


def log_fail(msg: str):
    print(f"  {RED}✖ [FAIL]{RESET} {msg}")


def check_dependencies() -> bool:
    log_step("Bước 1: Kiểm tra môi trường & thư viện cần thiết")
    required = [
        "simpy", "h3", "numpy", "polars", "pyarrow", "shapely", "yaml", "scipy", "jsonschema"
    ]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
            log_pass(f"Thư viện: {pkg}")
        except ImportError:
            missing.append(pkg)
            log_fail(f"Thiếu thư viện: {pkg}")

    if missing:
        print(f"\n{RED}Vui lòng cài đặt các thư viện còn thiếu:{RESET}")
        print(f"  pip install {' '.join(missing)}")
        return False
    return True


def check_schemas() -> bool:
    log_step("Bước 2: Kiểm tra Hệ thống Data Schemas (schemas/)")
    schemas_dir = ROOT / "schemas"
    if not schemas_dir.is_dir():
        log_fail(f"Không tìm thấy thư mục: {schemas_dir}")
        return False

    subdirs = ["l0", "l1", "l1r", "l2", "l3"]
    all_ok = True
    total_schemas = 0

    for sub in subdirs:
        p = schemas_dir / sub
        if p.is_dir():
            files = list(p.glob("*.schema.json"))
            total_schemas += len(files)
            log_pass(f"Tầng schemas/{sub}: tìm thấy {len(files)} schema hợp đồng")
        else:
            log_warn(f"Chưa thấy thư mục: schemas/{sub}")

    # Kiểm tra cú pháp JSON của các schema
    schema_errors = 0
    for sfile in schemas_dir.rglob("*.schema.json"):
        try:
            with open(sfile, "r", encoding="utf-8") as f:
                json.load(f)
        except Exception as err:
            log_fail(f"Lỗi cú pháp JSON tại {sfile.name}: {err}")
            schema_errors += 1

    if schema_errors == 0:
        log_pass(f"Đã xác thực cú pháp JSON cho toàn bộ {total_schemas} tệp schema.")
    else:
        all_ok = False

    return all_ok


def check_foundational_datasets() -> bool:
    log_step("Bước 3: Kiểm tra Kho Dữ liệu Nền tảng (13 Bảng Parquet)")
    realdata_dir = ROOT / "data" / "mock" / "realdata-v2"
    if not realdata_dir.is_dir():
        log_fail(f"Không tìm thấy thư mục: {realdata_dir}")
        return False

    expected_tables = [
        "trips.parquet",
        "public_driver_hex_tracking.parquet",
        "driver_statistic_daily.parquet",
        "driver_income_daily.parquet",
        "driver_orders_rush_hours.parquet",
        "driver_online_hours_sap_id.parquet",
        "driver_bike_stoppoints.parquet",
        "kpi_driver_platform_calculator_gbq.parquet",
        "public_mission.parquet",
        "public_mission_earn_history.parquet",
        "public_user_mission_progress.parquet",
        "driver_penalization_ATA.parquet",
        "public_frauds.parquet",
    ]

    import polars as pl

    all_exist = True
    for tbl in expected_tables:
        fpath = realdata_dir / tbl
        if fpath.is_file():
            size_mb = fpath.stat().st_size / (1024 * 1024)
            log_pass(f"Bảng {tbl:40s} ({size_mb:6.2f} MB)")
        else:
            log_fail(f"Thiếu bảng: {tbl}")
            all_exist = False

    # Đọc thử một bảng trọng yếu
    try:
        trips_df = pl.read_parquet(realdata_dir / "trips.parquet")
        log_pass(f"Đọc thử trips.parquet thành công: {len(trips_df):,} dòng, {len(trips_df.columns)} cột.")
    except Exception as err:
        log_fail(f"Không thể đọc trips.parquet: {err}")
        all_exist = False

    return all_exist


def check_spatial_data() -> bool:
    log_step("Bước 4: Kiểm tra Dữ liệu Không gian & Hạ tầng (research/simulation/data/)")
    geo_dir = ROOT / "research" / "simulation" / "data"
    if not geo_dir.is_dir():
        log_fail(f"Không tìm thấy thư mục: {geo_dir}")
        return False

    required_geo = [
        ("hanoi_geom.json", "Đa giác ranh giới địa bàn H3 toàn Hà Nội"),
        ("batt_hanoi.json", "Tọa độ 399 trạm đổi pin / sạc xe điện GSM VinFast"),
        ("poi_hanoi.json", "199 Điểm tập trung nhu cầu (Bệnh viện, ĐH, TTTM)"),
        ("osrm_matrix_hanoi.parquet", "Ma trận khoảng cách và thời gian đường bộ OSRM toàn Hà Nội"),
    ]

    all_ok = True
    for fname, desc in required_geo:
        p = geo_dir / fname
        if p.is_file():
            size_kb = p.stat().st_size / 1024
            log_pass(f"{fname:25s} ({size_kb:7.1f} KB) - {desc}")
        else:
            log_fail(f"Thiếu file dữ liệu: {fname}")
            all_ok = False
    return all_ok


def run_simulator_and_verify() -> bool:
    log_step("Bước 5: Chạy Simulator Mô phỏng Thế giới Tự nhiên & Kiểm chứng Dữ liệu")

    from gsm_sim.config import Config
    from gsm_sim.logging_ev import write_run
    from gsm_sim.metrics import summarize
    from gsm_sim.runner import run_once
    import polars as pl

    config_path = ROOT / "configs" / "pilot_hanoi.yaml"
    if not config_path.is_file():
        log_fail(f"Không tìm thấy file cấu hình: {config_path}")
        return False

    cfg = Config.load(str(config_path))
    # Đảm bảo cờ advice tắt để chạy ở chế độ Môi trường tự nhiên (Baseline Natural Environment)
    if "advice" in cfg.data:
        cfg.data["advice"]["enabled"] = False

    seed = 42
    print(f"  Đang chạy mô phỏng thế giới tự nhiên (Seed={seed}, Window 05:00 - 24:00)...")
    t0 = time.perf_counter()
    result = run_once(cfg, seed=seed)
    elapsed = time.perf_counter() - t0
    log_pass(f"Mô phỏng hoàn tất sau {elapsed:.2f} giây.")

    # Ghi dữ liệu ra thư mục runs/
    out_root = ROOT / "runs"
    run_dir = write_run(result, cfg, out_root)
    log_pass(f"Dữ liệu được lưu tại: {run_dir.relative_to(ROOT)}")

    # Kiểm tra tính toàn vẹn của các file xuất ra
    ev_file = run_dir / "events.parquet"
    ac_file = run_dir / "actors.parquet"
    mf_file = run_dir / "manifest.json"

    if not ev_file.is_file() or not ac_file.is_file() or not mf_file.is_file():
        log_fail("Không tìm thấy đủ 3 file đầu ra (events, actors, manifest)!")
        return False

    events_df = pl.read_parquet(ev_file)
    actors_df = pl.read_parquet(ac_file)
    with open(mf_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    log_pass(f"events.parquet: {len(events_df):,} dòng log sự kiện, {len(events_df.columns)} cột.")
    log_pass(f"actors.parquet: {len(actors_df)} tài xế mô phỏng.")
    log_pass(f"manifest.json : run_id_deterministic={manifest.get('run_id_deterministic', 'N/A')}")

    # Kiểm tra định luật bảo toàn: tổng số cuốc của các tài xế = số đơn hoàn thành của hệ thống
    total_actor_trips = int(actors_df["trips"].sum())
    system_completed = int(manifest["metrics"]["orders_completed"])

    if total_actor_trips == system_completed:
        log_pass(f"Định luật bảo toàn cuốc xe [THỎA MÃN]: sum(actor.trips)={total_actor_trips:,} == system_completed={system_completed:,}")
    else:
        log_fail(f"Lệch định luật bảo toàn cuốc: actor={total_actor_trips} != system={system_completed}")
        return False

    # In bảng tóm tắt chỉ số vận hành
    m = manifest["metrics"]
    payout_ft = m.get('payout_fulltime_median', 0)
    print(f"\n{BOLD}=== BẢNG CHỈ SỐ MÔI TRƯỜNG MÔ PHỎNG (MOCK SEED {seed}) ==={RESET}")
    print(f"  Tổng số đơn khách đặt (orders_total)    : {m.get('orders_total', 0):,}")
    print(f"  Số đơn phục vụ thành công (completed)  : {m.get('orders_completed', 0):,}")
    print(f"  Tỷ lệ phục vụ (served_rate)             : {m.get('served_rate', 0):.1%}")
    print(f"  Thu nhập trung vị Fulltime (VND)        : {payout_ft:,} đ")
    print(f"  Thời gian đón khách trung vị (phút)     : {m.get('pickup_eta_median', 0):.1f} min")
    print(f"  Số lượt đổi pin tại trạm (swap_events)  : {m.get('swap_events', 0)}")


    return True


def print_code_snippet():
    print(f"\n{BOLD}{GREEN}====================================================================")
    print("           TẤT CẢ CÁC BƯỚC KIỂM TRA ĐÃ QUA THÀNH CÔNG! (ALL PASS)")
    print("====================================================================\n" + RESET)
    print(f"{BOLD}💡 Gợi ý cho đồng nghiệp - 3 dòng code Polars đọc dữ liệu ngay:{RESET}")
    print("""```python
import polars as pl
from pathlib import Path

# Đọc bảng cuốc xe thực tế
trips = pl.read_parquet("data/mock/realdata-v2/trips.parquet")

# Đọc bảng sự kiện vừa mô phỏng
latest_run = sorted(Path("runs").glob("*_seed*"))[-1]
events = pl.read_parquet(latest_run / "events.parquet")
print(f"Cuốc xe mẫu: {len(trips):,} | Sự kiện mô phỏng: {len(events):,}")
```""")
    print(f"\n{BLUE}Chi tiết tài liệu xem tại: docs/handoff/DATA_FOUNDATION_AND_SIMULATOR_HANDOFF.md{RESET}\n")


def main() -> int:
    print(f"{BOLD}====================================================================")
    print("  SMOKE TEST: NỀN TẢNG DỮ LIỆU & SIMULATOR MÔI TRƯỜNG (DATA FOUNDATION)")
    print("====================================================================" + RESET)

    if not check_dependencies():
        return 1
    if not check_schemas():
        return 1
    if not check_foundational_datasets():
        return 1
    if not check_spatial_data():
        return 1
    if not run_simulator_and_verify():
        return 1

    print_code_snippet()
    return 0


if __name__ == "__main__":
    sys.exit(main())
