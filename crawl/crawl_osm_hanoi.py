"""Bộ công cụ cào dữ liệu địa lý OpenStreetMap (OSM) qua Overpass API cho toàn thành phố Hà Nội.
Đặt trực tiếp trong GSM_SIMULATOR-raw/crawl/ để mở rộng dữ liệu cho Simulator.

ĐẶC ĐIỂM:
- 100% MIỄN PHÍ: Không cần API Key, không cần tài khoản, không mất phí.
- Sử dụng Bounding Box toàn Hà Nội: Truy vấn cực nhanh (chỉ vài giây).
- Tích hợp 3 máy chủ Overpass API dự phòng: Tự động chuyển server nếu nghẽn mạng.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
import requests

# Đảm bảo in tiếng Việt chuẩn UTF-8 trên Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Danh sách máy chủ Overpass API công cộng (Miễn phí & Không cần API Key)
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]

CRAWL_DIR = Path(__file__).resolve().parent
DATA_OUTPUT_DIR = CRAWL_DIR / "data_hanoi"
SIM_DATA_DIR = CRAWL_DIR.parent / "research" / "simulation" / "data"

# Bounding Box toàn bộ địa bàn thành phố Hà Nội (gồm cả nội thành và ngoại thành)
# Cú pháp: (Vĩ độ Nam, Kinh độ Tây, Vĩ độ Bắc, Kinh độ Đông)
HANOI_BBOX = "20.75,105.55,21.35,106.10"

# 1. Truy vấn Tủ đổi pin xe máy điện & Trụ sạc ô tô VinFast toàn Hà Nội
QUERY_BATTERY = f"""
[out:json][timeout:60];
(
  node["amenity"="vending_machine"]["battery_swap"="yes"]({HANOI_BBOX});
  node["battery_swap"="yes"]["brand"="VinFast"]({HANOI_BBOX});
  node["amenity"="charging_station"]["brand"="VinFast"]({HANOI_BBOX});
  node["amenity"="charging_station"]["operator"="VinFast"]({HANOI_BBOX});
  node["amenity"="charging_station"]["operator"="V-Green"]({HANOI_BBOX});
);
out body;
>;
out skel qt;
"""

# 2. Truy vấn các điểm tập trung nhu cầu đặt xe (POI) khớp với cấu hình GSM Simulator
QUERY_POI = f"""
[out:json][timeout:90];
(
  node["amenity"="hospital"]({HANOI_BBOX});
  node["amenity"="university"]({HANOI_BBOX});
  node["amenity"="college"]({HANOI_BBOX});
  node["amenity"="marketplace"]({HANOI_BBOX});
  node["amenity"="bus_station"]({HANOI_BBOX});
  node["shop"="mall"]({HANOI_BBOX});
);
out body;
>;
out skel qt;
"""

# 3. Truy vấn đa giác ranh giới hành chính các Quận/Phường Hà Nội (admin_level=6 hoặc admin_level=4)
QUERY_BOUNDARY = f"""
[out:json][timeout:120];
(
  relation["boundary"="administrative"]["admin_level"="6"]({HANOI_BBOX});
);
out body geom;
"""



def send_overpass_query(query: str, description: str) -> dict:
    """Gửi request tới Overpass API (kèm User-Agent hợp lệ theo chính sách Fair Use của OSM)."""
    print(f"\n[+] Đang tải: {description}...")
    headers = {
        "User-Agent": "GSM_Simulator_DataHarvester/1.0 (contact: student_research@edu.vn)",
        "Accept": "*/*"
    }

    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        print(f"    -> Đang kết nối máy chủ: {endpoint}")
        try:
            start_time = time.time()
            resp = requests.post(
                endpoint,
                data={"data": query},
                headers=headers,
                timeout=120,
            )
            elapsed = time.time() - start_time

            if resp.status_code == 200:
                data = resp.json()
                n_elements = len(data.get("elements", []))
                print(f"    ✔ Thành công ({elapsed:.1f}s)! Nhận được {n_elements:,} phần tử OSM.")
                return data
            elif resp.status_code == 429:
                print(f"    ⚠ Máy chủ phản hồi 429 (bận), tự động đổi sang server khác...")
                time.sleep(2)
            else:
                print(f"    ⚠ Server trả về mã HTTP {resp.status_code}, đang thử server khác...")
        except Exception as e:
            print(f"    ✖ Lỗi kết nối: {e}")
            last_error = e
            time.sleep(1)

    raise RuntimeError(f"Không thể tải '{description}' từ các máy chủ Overpass. Lỗi: {last_error}")


def crawl_battery_stations(out_dir: Path) -> Path:
    """Cào trạm sạc & tủ pin xe điện."""
    data = send_overpass_query(QUERY_BATTERY, "Trạm sạc & Tủ đổi pin VinFast Hà Nội")
    out_file = out_dir / "batt_hanoi.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"    -> Đã lưu: {out_file.name} ({out_file.stat().st_size / 1024:.1f} KB)")
    return out_file


def crawl_poi(out_dir: Path) -> Path:
    """Cào các điểm đón trả nhu cầu cao (Bệnh viện, Trường học, Bến xe, Chợ, TTTM)."""
    data = send_overpass_query(QUERY_POI, "Điểm nhu cầu khách (Hospital, Univ, Mall, Market, Bus)")
    out_file = out_dir / "poi_hanoi.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"    -> Đã lưu: {out_file.name} ({out_file.stat().st_size / 1024:.1f} KB)")
    return out_file


def crawl_boundary(out_dir: Path) -> Path:
    """Cào đa giác ranh giới các quận/huyện Hà Nội."""
    data = send_overpass_query(QUERY_BOUNDARY, "Đa giác ranh giới các quận/huyện Hà Nội")
    out_file = out_dir / "hanoi_geom.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"    -> Đã lưu: {out_file.name} ({out_file.stat().st_size / 1024:.1f} KB)")
    return out_file


def copy_to_simulator(data_dir: Path):
    """Sao chép các file vừa cào vào thư mục research/simulation/data của Simulator."""
    if not SIM_DATA_DIR.exists():
        print(f"\n[!] Thư mục {SIM_DATA_DIR} không tồn tại.")
        return

    print(f"\n[+] Đang bổ sung dữ liệu vào Simulator ({SIM_DATA_DIR})...")
    for fname in ["batt_hanoi.json", "poi_hanoi.json", "hanoi_geom.json"]:
        src = data_dir / fname
        if src.exists():
            dst = SIM_DATA_DIR / fname
            dst.write_bytes(src.read_bytes())
            print(f"    ✔ Đã bổ sung file mới: {dst.name} ({dst.stat().st_size / 1024:.1f} KB)")


def main():
    parser = argparse.ArgumentParser(description="Tool cào dữ liệu OSM toàn Hà Nội cho GSM Simulator")
    parser.add_argument("--all", action="store_true", help="Cào tất cả (Trạm pin, POI, Ranh giới)")
    parser.add_argument("--battery", action="store_true", help="Chỉ cào Trạm đổi pin / Trụ sạc")
    parser.add_argument("--poi", action="store_true", help="Chỉ cào Điểm nhu cầu khách (POI)")
    parser.add_argument("--boundary", action="store_true", help="Chỉ cào Ranh giới đa giác quận/huyện")
    parser.add_argument("--sync", action="store_true", help="Tự động đồng bộ vào research/simulation/data/")
    args = parser.parse_args()

    crawl_all = args.all or (not args.battery and not args.poi and not args.boundary)

    DATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("====================================================================")
    print("   GSM SIMULATOR: CÀO DỮ LIỆU ĐỊA LÝ OPENSTREETMAP TOÀN HÀ NỘI     ")
    print("   (Nguồn: Overpass API công cộng - Miễn phí 100% - Không cần API Key) ")
    print("====================================================================")
    print(f"Thư mục lưu: {DATA_OUTPUT_DIR.resolve()}")

    try:
        if crawl_all or args.battery:
            crawl_battery_stations(DATA_OUTPUT_DIR)

        if crawl_all or args.poi:
            crawl_poi(DATA_OUTPUT_DIR)

        if crawl_all or args.boundary:
            crawl_boundary(DATA_OUTPUT_DIR)

        if args.sync or crawl_all:
            copy_to_simulator(DATA_OUTPUT_DIR)

        print("\n====================================================================")
        print("                 CÀO TOÀN BỘ DỮ LIỆU THÀNH CÔNG!                    ")
        print("====================================================================")
        print("Các file đã sẵn sàng phục vụ mở rộng mô phỏng toàn Hà Nội.")
    except Exception as e:
        print(f"\n[X] Lỗi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
