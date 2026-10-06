"""Script tính toán ma trận cự ly và thời gian di chuyển đường bộ OSRM cho lưới H3 Res 8 toàn Hà Nội.
Lưu kết quả trực tiếp ra file Parquet để GSM Simulator đọc offline lúc chạy mô phỏng.

NGUYÊN LÝ HOẠT ĐỘNG:
1. Lấy danh sách các ô lục giác H3 (Res 8) của bản đồ Hà Nội.
2. Tính tọa độ tâm (Centroid: lat, lon) của từng ô bằng hàm h3.cell_to_latlng().
3. Gửi tọa độ theo từng cụm (Batch) lên OSRM Table Service để đo khoảng cách đường bộ (mét)
   và thời gian di chuyển (giây).
4. Lưu thành file Parquet chuẩn 4 cột: [cell_from, cell_to, road_km, duration_s].
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
import h3
import polars as pl
import requests

# Đảm bảo console Windows in tiếng Việt UTF-8 không lỗi
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Đảm bảo import được module gsm_sim trong src/
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from gsm_sim import geo

OSRM_PUBLIC_URL = "https://router.project-osrm.org/table/v1/driving"
DATA_DIR = ROOT_DIR / "research" / "simulation" / "data"


def get_hanoi_res8_cells(geom_path: Path, limit: int | None = None) -> list[str]:
    """Lấy danh sách các ô H3 Res 8 từ đa giác ranh giới Hà Nội."""
    print(f"[1/4] Đang dựng lưới H3 Res 8 từ file ranh giới: {geom_path.name}...")
    boundary = geo.load_boundary(geom_path)
    cells: set[str] = set()
    for poly in boundary.geoms:
        cells |= geo._polygon_to_cells(poly, res=8)
    
    cell_list = sorted(list(cells))
    if limit and limit > 0:
        cell_list = cell_list[:limit]
        print(f"      ✔ Đã chọn mẫu {len(cell_list)} ô để tính toán nhanh.")
    else:
        print(f"      ✔ Tổng cộng tìm thấy {len(cell_list):,} ô H3 Res 8 phủ kín Hà Nội.")
    return cell_list


def query_osrm_batch(cells_batch: list[str], osrm_url: str) -> list[dict]:
    """Gửi một mảng tọa độ tâm lên OSRM Table API và trả về danh sách các cặp cự ly."""
    # 1. Lấy tâm các ô: (lat, lon) -> định dạng OSRM: {lon},{lat}
    coords = [h3.cell_to_latlng(c) for c in cells_batch]
    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)

    url = f"{osrm_url}/{coord_str}?annotations=distance,duration"
    headers = {
        "User-Agent": "GSM_Simulator_MatrixGenerator/1.0 (contact: student_research@edu.vn)",
        "Accept": "application/json"
    }

    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"OSRM API trả mã lỗi HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM trả mã lỗi logic: {data.get('code')}")

    distances = data["distances"]  # mảng 2D khoảng cách tính bằng mét
    durations = data["durations"]  # mảng 2D thời gian tính bằng giây

    rows = []
    n = len(cells_batch)
    for i in range(n):
        c_from = cells_batch[i]
        for j in range(n):
            c_to = cells_batch[j]
            dist_m = distances[i][j]
            dur_s = durations[i][j]

            # Bỏ qua nếu OSRM không tìm được đường hoặc giá trị null
            if dist_m is None or dur_s is None:
                continue

            rows.append({
                "cell_from": c_from,
                "cell_to": c_to,
                "road_km": round(float(dist_m) / 1000.0, 4),
                "duration_s": round(float(dur_s), 1)
            })

    return rows


def generate_osrm_matrix(
    geom_file: str = "hanoi_geom.json",
    out_file: str = "osrm_matrix_hanoi.parquet",
    batch_size: int = 50,
    limit_cells: int | None = None,
    osrm_url: str = OSRM_PUBLIC_URL
):
    """Quy trình tạo ma trận đường bộ OSRM tự động."""
    geom_path = DATA_DIR / geom_file
    out_path = DATA_DIR / out_file

    print("====================================================================")
    print("      TÍNH TOÁN MA TRẬN ĐƯỜNG BỘ OSRM CHO HÀ NỘI (H3 RES 8)        ")
    print("====================================================================")
    print(f"Nguồn ranh giới : {geom_path}")
    print(f"Đầu ra Parquet  : {out_path}")
    print(f"Kích thước Batch: {batch_size} ô/lần gọi API (tương đương {batch_size * batch_size:,} cặp)")
    print(f"Máy chủ OSRM    : {osrm_url}")

    # 1. Lấy danh sách ô Res 8
    cells = get_hanoi_res8_cells(geom_path, limit=limit_cells)

    # 2. Chia thành các batch để gọi API (tránh URL quá dài)
    print(f"\n[2/4] Bắt đầu tính toán cự ly qua OSRM...")
    all_rows: list[dict] = []
    total_batches = math.ceil(len(cells) / batch_size)

    start_all = time.time()
    for b_idx in range(total_batches):
        batch = cells[b_idx * batch_size : (b_idx + 1) * batch_size]
        print(f"    -> Đang xử lý cụm {b_idx + 1}/{total_batches} ({len(batch)} ô)...", end="", flush=True)

        try:
            t0 = time.time()
            rows = query_osrm_batch(batch, osrm_url)
            elapsed = time.time() - t0
            all_rows.extend(rows)
            print(f" ✔ Xong ({elapsed:.1f}s, +{len(rows):,} cặp)")
            # Nghỉ 0.5s giữa các request để tuân thủ chính sách sử dụng của máy chủ cộng đồng
            time.sleep(0.5)
        except Exception as e:
            print(f" ✖ Lỗi: {e}")
            print("       Tạm dừng 3s rồi tiếp tục...")
            time.sleep(3)

    print(f"\n[3/4] Hoàn tất tính toán ({time.time() - start_all:.1f}s). Tổng số cặp O-D: {len(all_rows):,}")

    # 3. Xuất file Parquet chuẩn Polars
    print(f"\n[4/4] Đang ghi dữ liệu vào {out_path.name}...")
    df = pl.DataFrame(all_rows, schema={
        "cell_from": pl.String,
        "cell_to": pl.String,
        "road_km": pl.Float64,
        "duration_s": pl.Float64
    })
    
    # Loại bỏ các cặp trùng lặp nếu có
    df = df.unique(subset=["cell_from", "cell_to"])
    df.write_parquet(out_path)
    
    size_kb = out_path.stat().st_size / 1024
    print(f"      ✔ Đã ghi thành công {len(df):,} dòng ({size_kb:.1f} KB)!")
    print("====================================================================")
    print(f"File {out_path.name} đã sẵn sàng trong: {out_path.parent}")


def main():
    parser = argparse.ArgumentParser(description="Tool tính toán OSRM Matrix cho bản đồ Hà Nội Res 8")
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn số ô để chạy test nhanh (ví dụ: --limit 100)")
    parser.add_argument("--batch-size", type=int, default=50, help="Số ô trong 1 lượt query OSRM (mặc định 50)")
    parser.add_argument("--out", type=str, default="osrm_matrix_hanoi.parquet", help="Tên file parquet đầu ra")
    parser.add_argument("--url", type=str, default=OSRM_PUBLIC_URL, help="URL OSRM service")
    args = parser.parse_args()

    generate_osrm_matrix(
        geom_file="hanoi_geom.json",
        out_file=args.out,
        batch_size=args.batch_size,
        limit_cells=args.limit,
        osrm_url=args.url
    )


if __name__ == "__main__":
    main()
