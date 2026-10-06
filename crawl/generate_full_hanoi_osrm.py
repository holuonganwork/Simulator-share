"""Script tính toán Ma trận Đường bộ OSRM Toàn phần (All-to-All) cho toàn bộ 925 ô H3 Res 7 của Hà Nội.
Thiết kế cho các phiên chạy ổn định với tính năng TỰ ĐỘNG LƯU TIẾN ĐỘ & RESUME.

TỔNG QUAN VỚI H3 RES 7:
- Tổng số ô toàn thành phố: Đúng 925 ô lục giác Res 7.
- Kích thước 1 block: 50 ô -> Chỉ có 19 x 19 = 361 nhiệm vụ.
- Thời gian chạy dự kiến: Chỉ khoảng 6 - 8 phút là hoàn thành 100%!
- Hỗ trợ Resume tự động: Bấm Ctrl+C dừng và gõ lại lệnh sẽ chạy tiếp bình thường.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
from pathlib import Path
import h3
import polars as pl
import requests

# Đảm bảo in UTF-8 không lỗi trên Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from gsm_sim import geo

OSRM_DEFAULT_URL = "https://router.project-osrm.org/table/v1/driving"
DATA_DIR = ROOT_DIR / "research" / "simulation" / "data"
CHUNKS_DIR = DATA_DIR / "osrm_chunks"


def get_all_cells(geom_path: Path, res: int = 7) -> list[str]:
    """Lấy toàn bộ danh sách các ô H3 của Hà Nội (mặc định Res 7)."""
    boundary = geo.load_boundary(geom_path)
    cells: set[str] = set()
    for poly in boundary.geoms:
        cells |= geo._polygon_to_cells(poly, res=res)
    return sorted(list(cells))


def query_osrm_block_pair(
    src_cells: list[str],
    dst_cells: list[str],
    osrm_url: str,
    max_retries: int = 5
) -> list[dict]:
    """Gửi truy vấn OSRM giữa nhóm ô nguồn (src) và nhóm ô đích (dst)."""
    is_same = (src_cells == dst_cells)
    
    if is_same:
        all_cells = src_cells
        coords = [h3.cell_to_latlng(c) for c in all_cells]
        coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
        url = f"{osrm_url}/{coord_str}?annotations=distance,duration"
    else:
        all_cells = src_cells + dst_cells
        coords = [h3.cell_to_latlng(c) for c in all_cells]
        coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
        src_indices = ";".join(str(i) for i in range(len(src_cells)))
        dst_indices = ";".join(str(len(src_cells) + j) for j in range(len(dst_cells)))
        url = f"{osrm_url}/{coord_str}?sources={src_indices}&destinations={dst_indices}&annotations=distance,duration"

    headers = {
        "User-Agent": "GSM_Simulator_MatrixWorker/2.0 (contact: research@gsm.internal)",
        "Accept": "application/json"
    }

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok":
                    distances = data["distances"]
                    durations = data["durations"]
                    rows = []
                    for i, c_from in enumerate(src_cells):
                        for j, c_to in enumerate(dst_cells):
                            d_m = distances[i][j]
                            t_s = durations[i][j]
                            if d_m is not None and t_s is not None:
                                rows.append({
                                    "cell_from": c_from,
                                    "cell_to": c_to,
                                    "road_km": round(float(d_m) / 1000.0, 4),
                                    "duration_s": round(float(t_s), 1)
                                })
                    return rows
                else:
                    last_err = RuntimeError(f"OSRM Logic Code: {data.get('code')}")
            elif resp.status_code == 429:
                wait_time = attempt * 3
                print(f" [Rate-limit 429, chờ {wait_time}s...] ", end="", flush=True)
                time.sleep(wait_time)
            else:
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                time.sleep(2)
        except Exception as e:
            last_err = e
            time.sleep(attempt * 2)

    raise RuntimeError(f"Thất bại sau {max_retries} lần thử: {last_err}")


def merge_chunks_to_parquet(out_parquet_path: Path):
    """Hợp nhất toàn bộ các file chunk parquet trong osrm_chunks thành 1 file duy nhất."""
    chunk_files = sorted(glob.glob(str(CHUNKS_DIR / "chunk_*.parquet")))
    if not chunk_files:
        print(f"\n[!] Không tìm thấy file chunk nào trong {CHUNKS_DIR}")
        return

    print(f"\n[+] Đang hợp nhất {len(chunk_files):,} file chunks vào {out_parquet_path.name}...")
    start_merge = time.time()
    
    lf = pl.scan_parquet(chunk_files)
    df = lf.unique(subset=["cell_from", "cell_to"]).collect()
    
    df.write_parquet(out_parquet_path)
    elapsed = time.time() - start_merge
    size_mb = out_parquet_path.stat().st_size / (1024 * 1024)
    print(f"✔ ĐÃ HỢP NHẤT XONG ({elapsed:.1f}s)!")
    print(f"  - File đầu ra : {out_parquet_path}")
    print(f"  - Tổng số cặp : {len(df):,} cặp O-D")
    print(f"  - Dung lượng  : {size_mb:.2f} MB")


def main():
    parser = argparse.ArgumentParser(description="Tính toán toàn bộ OSRM Matrix Hà Nội Res 7 (Có Resume & Checkpoint)")
    parser.add_argument("--res", type=int, default=7, help="Độ phân giải H3 (mặc định: 7)")
    parser.add_argument("--batch-size", type=int, default=50, help="Kích thước block (mặc định: 50 ô/block)")
    parser.add_argument("--merge-only", action="store_true", help="Chỉ chạy bước hợp nhất các chunk đã tải")
    parser.add_argument("--url", type=str, default=OSRM_DEFAULT_URL, help="URL máy chủ OSRM")
    parser.add_argument("--out", type=str, default="osrm_matrix_hanoi.parquet", help="Tên file parquet cuối cùng")
    args = parser.parse_args()

    out_parquet = DATA_DIR / args.out
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    if args.merge_only:
        merge_chunks_to_parquet(out_parquet)
        return

    geom_path = DATA_DIR / "hanoi_geom.json"
    if not geom_path.exists():
        print(f"[X] Không tìm thấy file ranh giới: {geom_path}")
        sys.exit(1)

    print("====================================================================")
    print(f"   GSM SIMULATOR: TÍNH TOÁN TOÀN PHẦN OSRM TOÀN HÀ NỘI (RES {args.res})      ")
    print("   (Hỗ trợ tự động Resume - Tốc độ nhanh ~6-8 phút là xong 100%)    ")
    print("====================================================================")

    # 1. Dựng danh sách ô Res 7
    cells = get_all_cells(geom_path, res=args.res)
    total_cells = len(cells)
    bs = args.batch_size
    n_blocks = math.ceil(total_cells / bs)
    total_tasks = n_blocks * n_blocks

    print(f"Tổng số ô H3 Res {args.res}  : {total_cells:,} ô")
    print(f"Kích thước 1 block  : {bs} ô")
    print(f"Số lượng blocks     : {n_blocks} x {n_blocks} = {total_tasks:,} nhiệm vụ")
    print(f"Dự kiến tổng số cặp : khoảng {total_cells * total_cells:,} cặp")
    print(f"Thư mục lưu chunks  : {CHUNKS_DIR.resolve()}")
    print("--------------------------------------------------------------------")

    # 2. Quét các chunks đã có sẵn để resume
    completed_chunks = set()
    for f in CHUNKS_DIR.glob("chunk_*.parquet"):
        name = f.stem
        parts = name.split("_")
        if len(parts) == 3:
            completed_chunks.add((int(parts[1]), int(parts[2])))

    print(f"Tiến độ hiện tại: Đã hoàn thành {len(completed_chunks):,}/{total_tasks:,} block tasks ({len(completed_chunks)/total_tasks*100:.1f}%)")
    if completed_chunks:
        print("✔ Đang tự động RESUME tiếp tục từ vị trí chưa chạy...")

    # 3. Vòng lặp duyệt qua từng cặp block (i, j)
    task_count = 0
    start_time = time.time()

    try:
        for i in range(n_blocks):
            src_block = cells[i * bs : (i + 1) * bs]
            for j in range(n_blocks):
                task_count += 1
                if (i, j) in completed_chunks:
                    continue

                dst_block = cells[j * bs : (j + 1) * bs]
                chunk_file = CHUNKS_DIR / f"chunk_{i:03d}_{j:03d}.parquet"

                pct = (task_count / total_tasks) * 100
                print(f"[{task_count:,}/{total_tasks:,} | {pct:4.1f}%] Block ({i:02d}, {j:02d})...", end="", flush=True)

                t0 = time.time()
                rows = query_osrm_block_pair(src_block, dst_block, args.url)
                
                df_chunk = pl.DataFrame(rows, schema={
                    "cell_from": pl.String,
                    "cell_to": pl.String,
                    "road_km": pl.Float64,
                    "duration_s": pl.Float64
                })
                df_chunk.write_parquet(chunk_file)

                elapsed = time.time() - t0
                print(f" ✔ Xong ({elapsed:.1f}s, {len(rows):,} cặp)")

                completed_chunks.add((i, j))
                time.sleep(0.3)

        print("\n====================================================================")
        print("          ĐÃ HOÀN TẤT TÍNH TOÁN 100% CÁC BLOCK OSRM RES 7!          ")
        print("====================================================================")
        merge_chunks_to_parquet(out_parquet)

    except KeyboardInterrupt:
        print("\n\n[!] BẠN ĐÃ TẠM DỪNG TIẾN TRÌNH.")
        print(f"    -> Tiến độ đã được lưu an toàn tại {CHUNKS_DIR.name}/ ({len(completed_chunks):,} block tasks).")
        print("    -> Khi bạn chạy lại lệnh, script sẽ tự động chạy tiếp mà không mất dữ liệu.")
        print("    -> Nếu muốn gộp các block đã có thành file parquet ngay, thêm cờ `--merge-only`.\n")


if __name__ == "__main__":
    main()
