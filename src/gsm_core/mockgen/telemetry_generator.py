"""Bộ sinh dữ liệu giả lập Telemetry xe điện VinFast (Vehicle Telemetry Mock Generator).

Triển khai Bước 3 của kế hoạch Telemetry Research (docs/telemetry-research.md),
xuất bản dữ liệu viễn thám chuẩn xe điện VinFast (VF5, VF6, VF e34, VF8)
theo đúng hợp đồng: schemas/telemetry/vehicle_telemetry_ping.schema.json.

Các tính chất cốt lõi:
- Đọc và stream tín hiệu trực tiếp từ CAN-bus/TCU của xe ô tô điện:
  * Số công-tơ-mét phần cứng (odometer_km) tăng lũy kế chính xác.
  * Mức pin (battery_level_pct) sụt giảm theo km và hồi phục khi sạc/đổi pin.
  * Cần số thực tế (gear: P, R, N, D).
  * Cảm biến áp lực tải trọng ghế (seat_occupancy: driver, any_passenger_seat).
  * Vận tốc thực tế (speed_kmh) và hướng di chuyển (heading_deg).

NGUYÊN TẮC THOÁI TỘI (EXONERATION FOR CRUISE / LUNCH BREAK):
- Khi tài xế đang chạy rỗng tìm khách (relocate / cruise), lái xe đi nghỉ trưa (lunch break),
  hoặc chạy xe đi trạm sạc: xe di chuyển (speed > 0) NHƯNG cảm biến ghế phụ/ghế sau
  ghi nhận KHÔNG CÓ NGƯỜI (any_passenger_seat = False).
- Hệ thống Lá Chắn Cuốc Xe sẽ căn cứ vào đây để THOÁI TỘI HOÀN TOÀN cho tài xế,
  tuyệt đối không bị ghi nhận oan là chở khách ngoài app (F1).
"""

from __future__ import annotations

import math
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Đảm bảo src luôn có trong sys.path
SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import h3
import polars as pl

from lcx_core.frauds._common import haversine_meters, make_valid_vin

TZ = timezone(timedelta(hours=7))


def _iso(date: str, t_min: float) -> str:
    """Chuyển phút-sim sang chuỗi ISO-8601 UTC+7."""
    base = datetime.fromisoformat(date).replace(tzinfo=TZ)
    return (base + timedelta(minutes=float(t_min))).isoformat()


def _calculate_heading(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    """Tính góc hướng la bàn (0 - 360 độ) giữa 2 điểm tọa độ GPS."""
    if abs(lat1 - lat2) < 1e-6 and abs(lon1 - lon2) < 1e-6:
        return None
    d_lon = math.radians(lon2 - lon1)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    y = math.sin(d_lon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lon)
    bearing = math.degrees(math.atan2(y, x))
    return round((bearing + 360.0) % 360.0, 1)


def generate_vehicle_telemetry_pings(
    segments: list[dict[str, Any]],
    actors: list[Any],
    events: list[Any],
    seed: int,
    date: str,
    ping_interval_min: float = 0.5,  # 0.5 phút = 30 giây/ping (chuẩn telemetry xe điện GSM)
) -> list[dict[str, Any]]:
    """Sinh danh sách bản ghi vehicle_telemetry_ping từ kết quả mô phỏng.

    Parameters:
    -----------
    segments : danh sách phân đoạn di chuyển (s['actor_id'], s['t0'], s['t1'], s['kind'], ...)
    actors : danh sách tác tử tài xế (Actor)
    events : danh sách sự kiện trong sim (order_matched, dropoff, swap_done...)
    seed : seed ngẫu nhiên của ngày mô phỏng
    date : chuỗi ngày dạng YYYY-MM-DD
    ping_interval_min : khoảng thời gian giữa 2 lần ping (mặc định 0.5 phút = 30s)

    Returns:
    --------
    Danh sách các bản ghi dictionary khớp 100% với vehicle_telemetry_ping.schema.json
    """
    actors_map = {a.actor_id: a for a in actors}

    # Khởi tạo trạng thái vật lý thực tế của từng xe điện VinFast
    # (mỗi xe có ODO gốc và mức pin ban đầu độc lập)
    vehicle_states: dict[int, dict[str, Any]] = {}
    for a in actors:
        aid = a.actor_id
        # ODO ban đầu từ 12.000 đến 35.000 km tùy xe
        init_odo = 12000.0 + (aid * 37.3) % 20000.0
        # Mức pin đầu ngày: 88% - 98%
        init_soc = 88.0 + (aid * 3.7) % 10.0
        vin = make_valid_vin(f"d-{aid}")
        vehicle_states[aid] = {
            "odometer_km": round(init_odo, 2),
            "battery_level_pct": round(init_soc, 1),
            "vin": vin,
            "current_trip_id": None,
        }

    # Bản đồ sự kiện khớp lệnh đơn để biết order_id nào đang active
    matched_orders: dict[int, int] = {}  # actor_id -> current order_id
    for e in events:
        if e.kind == "order_matched" and "order_id" in e.detail:
            matched_orders[e.actor_id] = e.detail["order_id"]
        elif e.kind == "dropoff":
            matched_orders.pop(e.actor_id, None)

    telemetry_pings: list[dict[str, Any]] = []
    ping_seq = 0

    sorted_segments = sorted(segments, key=lambda x: (x["actor_id"], x["t0"]))

    for s in sorted_segments:
        actor_id = s["actor_id"]
        if actor_id not in vehicle_states:
            continue

        state = vehicle_states[actor_id]
        dur_min = s["t1"] - s["t0"]
        if dur_min <= 0:
            continue

        kind = s.get("kind", "wait")
        from_lat, from_lon = s["from_lat"], s["from_lon"]
        to_lat, to_lon = s["to_lat"], s["to_lon"]

        dist_meters = haversine_meters(from_lat, from_lon, to_lat, to_lon)
        dist_km = dist_meters / 1000.0

        # Số lượng bản ghi ping nội suy trong segment này
        n_pings = max(2, int(math.ceil(dur_min / ping_interval_min)))
        # Giới hạn tối đa 300 pings cho các segment idle quá dài
        n_pings = min(300, n_pings)

        # Tính vận tốc trung bình của đoạn di chuyển
        dur_hours = dur_min / 60.0
        avg_speed = round(dist_km / dur_hours, 1) if dur_hours > 0 else 0.0
        avg_speed = min(85.0, avg_speed)  # khống chế vận tốc tối đa nội thành

        # Xác định góc hướng xe
        heading = _calculate_heading(from_lat, from_lon, to_lat, to_lon)

        # Xác định trạng thái cuốc xe (trip_id)
        order_id = s.get("order_id")
        if order_id is not None:
            trip_ref = f"o-{seed}-{order_id}"
        elif kind == "trip" and actor_id in matched_orders:
            trip_ref = f"o-{seed}-{matched_orders[actor_id]}"
        else:
            trip_ref = None

        # ======================================================================
        # NGUYÊN TẮC CẢM BIẾN TẢI TRỌNG GHẾ (SEAT OCCUPANCY & EXONERATION):
        # 1. Khi kind == 'trip': Đang phục vụ khách cuốc xe -> any_passenger_seat = True.
        # 2. Khi kind in ('relocate', 'pickup', 'wait', 'rest', 'swap', 'home'):
        #    Tài xế đang chạy xe không tìm khách, đi đón khách hoặc đi nghỉ trưa:
        #    any_passenger_seat = False (ghế phụ/sau hoàn toàn trống).
        #    -> Đây là điều kiện THOÁI TỘI cốt lõi được bảo đảm 100%.
        # ======================================================================
        is_passenger_on_board = (kind == "trip")

        # Cần số VinFast
        if avg_speed > 1.0:
            gear = "D"
        else:
            gear = "P"

        # Trạng thái sạc / xả pin
        if kind == "swap":
            charging_status = "CHARGING"
        elif avg_speed > 1.0:
            charging_status = "DISCHARGING"
        else:
            charging_status = "IDLE"

        km_per_step = dist_km / max(1, n_pings - 1)

        for k in range(n_pings):
            frac = k / (n_pings - 1) if n_pings > 1 else 0.0
            lat = round(from_lat + (to_lat - from_lat) * frac, 6)
            lng = round(from_lon + (to_lon - from_lon) * frac, 6)
            t_curr = s["t0"] + dur_min * frac

            # Cập nhật số ODO tích lũy
            current_odo = round(state["odometer_km"] + (dist_km * frac), 2)

            # Cập nhật pin (xe điện VinFast tiêu hao ~0.16% pin / 1 km di chuyển)
            if kind == "swap":
                # Nạp pin nhảy vọt lên 95% - 98%
                current_soc = min(98.0, 75.0 + frac * 22.0)
            else:
                consumed_soc = (dist_km * frac) * 0.16
                current_soc = max(12.0, round(state["battery_level_pct"] - consumed_soc, 1))

            remaining_range = max(30.0, round(current_soc * 3.5, 1))

            # Xác định cell H3 res 9
            cell_h3 = h3.latlng_to_cell(lat, lng, 9)

            ping_rec: dict[str, Any] = {
                "ping_id": f"tp-{seed}-{actor_id}-{ping_seq}",
                "driver_id": f"d-{actor_id}",
                "trip_id": trip_ref,
                "source_device": "vehicle_tcu_car",
                "occurred_at": _iso(date, t_curr),
                "lat": lat,
                "lng": lng,
                "current_hex_h3_res9": cell_h3,
                "speed_kmh": avg_speed if (k < n_pings - 1 and avg_speed > 0) else (avg_speed if k > 0 else 0.0),
                "heading_deg": heading,
                "vin": state["vin"],
                "gear": gear,
                "odometer_km": current_odo,
                "battery_level_pct": current_soc,
                "remaining_range_km": remaining_range,
                "charging_status": charging_status,
                "seat_occupancy": {
                    "driver": True,
                    "any_passenger_seat": is_passenger_on_board,
                },
                "door_lock_status": "LOCKED" if avg_speed > 15.0 else "UNLOCKED",
                "trunk_status": "OPEN" if (k == 0 and is_passenger_on_board) else "CLOSED",
                "phone_connectivity_lost": False,
            }

            telemetry_pings.append(ping_rec)
            ping_seq += 1

        # Cập nhật lại trạng thái cuối của xe sau khi xong segment
        state["odometer_km"] = round(state["odometer_km"] + dist_km, 2)
        if kind == "swap":
            state["battery_level_pct"] = 98.0
        else:
            state["battery_level_pct"] = max(12.0, round(state["battery_level_pct"] - (dist_km * 0.16), 1))

    # Sắp xếp toàn bộ ping theo thời gian phát sinh
    telemetry_pings.sort(key=lambda p: (p["driver_id"], p["occurred_at"]))
    return telemetry_pings


def generate_telemetry_from_l1_dir(
    l1_dir: Path | str,
    out_file: Path | str | None = None,
    max_drivers: int | None = None,
) -> pl.DataFrame:
    """Nâng cấp bộ dữ liệu L1 (gps_ping + trip_record) hoặc L1R (public_driver_hex_tracking + trips)
    thành vehicle_telemetry_ping chuẩn xe điện VinFast.

    Hỗ trợ 100% toàn bộ 3.000 xe ô tô điện VinFast (d-0 đến d-2999) sinh từ simulation `realdata`.
    Tự động nhận diện cả định dạng L1 cổ điển lẫn 15 bảng L1R thật.
    """
    import bisect
    import json
    import gc

    l1_path = Path(l1_dir)
    trips_path = l1_path / "trip_record.parquet"
    if not trips_path.exists():
        trips_path = l1_path / "trips.parquet"

    gps_path = l1_path / "gps_ping.parquet"
    hex_path = l1_path / "public_driver_hex_tracking.parquet"
    profile_path = l1_path / "driver_profile.parquet"

    has_gps = gps_path.exists()
    has_hex = hex_path.exists()

    if not trips_path.exists() or (not has_gps and not has_hex):
        raise FileNotFoundError(
            f"Không tìm thấy bảng cuốc xe (trips/trip_record) hoặc bảng định vị (gps_ping/public_driver_hex_tracking) tại {l1_path}"
        )

    trips_df = pl.read_parquet(trips_path)

    # Chuẩn hóa tên cột cuốc xe giữa L1 (trip_record) và L1R (trips)
    if "trip_id" in trips_df.columns and "order_id" not in trips_df.columns:
        trips_df = trips_df.with_columns(pl.col("trip_id").alias("order_id"))
    if "pickup_time" in trips_df.columns and "t_pickup" not in trips_df.columns:
        trips_df = trips_df.with_columns(pl.col("pickup_time").alias("t_pickup"))
    if "complete_time" in trips_df.columns and "t_complete" not in trips_df.columns:
        trips_df = trips_df.with_columns(pl.col("complete_time").alias("t_complete"))

    # Lấy danh sách toàn bộ tài xế (ưu tiên từ driver_profile để bảo đảm đúng 3.000 xe)
    all_drivers: list[str] = []
    if profile_path.exists():
        prof_df = pl.read_parquet(profile_path)
        if "driver_id" in prof_df.columns:
            all_drivers = prof_df["driver_id"].unique().to_list()

    if not all_drivers:
        if has_gps:
            all_drivers = pl.read_parquet(gps_path)["driver_id"].unique().to_list()
        elif has_hex:
            all_drivers = pl.read_parquet(hex_path)["driver_id"].unique().to_list()

    if max_drivers is not None:
        selected_drivers = set(all_drivers[:max_drivers])
    else:
        selected_drivers = set(all_drivers)

    # Lọc cuốc xe của các tài xế được chọn
    if selected_drivers:
        trips_df = trips_df.filter(pl.col("driver_id").is_in(list(selected_drivers)))

    # Xây dựng danh sách khoảng thời gian cuốc xe của từng tài xế (dùng bisect tra cứu O(log N))
    driver_trip_intervals: dict[str, list[tuple[str, str, str]]] = {}
    driver_trip_starts: dict[str, list[str]] = {}
    for row in trips_df.iter_rows(named=True):
        d_id = str(row["driver_id"])
        o_id = str(row.get("order_id") or row.get("trip_id") or "")
        t_pick = row.get("t_pickup")
        t_comp = row.get("t_complete")
        if t_pick is not None and t_comp is not None:
            t_pick_str = str(t_pick)
            t_comp_str = str(t_comp)
            driver_trip_intervals.setdefault(d_id, []).append((t_pick_str, t_comp_str, o_id))

    for d_id in driver_trip_intervals:
        driver_trip_intervals[d_id].sort(key=lambda x: x[0])
        driver_trip_starts[d_id] = [x[0] for x in driver_trip_intervals[d_id]]

    # Khởi tạo trạng thái phần cứng của từng tài xế (3.000 xe)
    hardware_state: dict[str, dict[str, Any]] = {}
    for d_id in selected_drivers:
        d_seed = abs(hash(str(d_id))) % 10000
        hardware_state[str(d_id)] = {
            "odometer_km": 15000.0 + d_seed * 1.5,
            "battery_pct": 92.0 + (d_seed % 8),
            "vin": make_valid_vin(str(d_id)),
            "last_t": None,
            "last_lat": None,
            "last_lng": None,
        }

    # Thiết lập ParquetWriter nếu có out_file để streaming tiết kiệm RAM
    writer = None
    pq_schema = None
    total_written = 0
    records_buffer: list[dict[str, Any]] = []
    chunk_size = 50000

    def _flush_buffer():
        nonlocal writer, pq_schema, total_written, records_buffer
        if not records_buffer:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        df_chunk = pl.DataFrame(records_buffer)
        t_chunk = df_chunk.to_arrow()
        if writer is None and out_file is not None:
            pq_schema = t_chunk.schema
            out_p = Path(out_file)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            writer = pq.ParquetWriter(out_p, pq_schema, compression="snappy")
        if writer is not None:
            writer.write_table(t_chunk)
        total_written += len(records_buffer)
        records_buffer.clear()
        gc.collect()

    ping_seq = 0

    if has_gps:
        # Đường dẫn 1: Có gps_ping.parquet (L1 format)
        gps_df = pl.read_parquet(gps_path)
        if selected_drivers:
            gps_df = gps_df.filter(pl.col("driver_id").is_in(list(selected_drivers)))
        sorted_gps = gps_df.sort(["driver_id", "t"])

        for row in sorted_gps.iter_rows(named=True):
            d_id = str(row["driver_id"])
            t_str = str(row["t"])
            loc = row.get("location")
            if isinstance(loc, str):
                try:
                    loc = json.loads(loc)
                except Exception:
                    loc = {}
            elif not isinstance(loc, dict):
                loc = {}

            lat = float(loc.get("lat", 21.0285))
            lng = float(loc.get("lon", 105.8542))
            h3_res9 = str(loc.get("h3", h3.latlng_to_cell(lat, lng, 9)))

            if d_id not in hardware_state:
                hardware_state[d_id] = {
                    "odometer_km": 15000.0, "battery_pct": 90.0,
                    "vin": make_valid_vin(d_id), "last_t": None, "last_lat": None, "last_lng": None,
                }
            hw = hardware_state[d_id]

            if hw["last_lat"] is not None and hw["last_lng"] is not None:
                dist_m = haversine_meters(hw["last_lat"], hw["last_lng"], lat, lng)
                dist_km = dist_m / 1000.0
            else:
                dist_km = 0.0

            hw["odometer_km"] = round(hw["odometer_km"] + dist_km, 2)
            hw["battery_pct"] = max(10.0, round(hw["battery_pct"] - (dist_km * 0.16), 1))
            hw["last_lat"] = lat
            hw["last_lng"] = lng

            raw_speed = row.get("speed_kmh")
            if raw_speed is not None and float(raw_speed) >= 0:
                speed = float(raw_speed)
            else:
                speed = min(60.0, round(dist_km * 120.0, 1)) if dist_km > 0.01 else 0.0

            # Tra cứu cuốc xe active bằng bisect O(log N)
            current_order = None
            if d_id in driver_trip_starts:
                starts = driver_trip_starts[d_id]
                idx = bisect.bisect_right(starts, t_str) - 1
                if idx >= 0:
                    tp, tc, oid = driver_trip_intervals[d_id][idx]
                    if tp <= t_str <= tc:
                        current_order = oid

            is_passenger = current_order is not None
            gear = "D" if speed > 1.0 else "P"
            rem_range = max(30.0, round(hw["battery_pct"] * 3.5, 1))

            ping_rec = {
                "ping_id": f"tp-l1-{d_id}-{ping_seq}",
                "driver_id": d_id,
                "trip_id": current_order,
                "source_device": "vehicle_tcu_car",
                "occurred_at": t_str,
                "lat": lat,
                "lng": lng,
                "current_hex_h3_res9": h3_res9,
                "speed_kmh": round(speed, 1),
                "heading_deg": None,
                "vin": hw["vin"],
                "gear": gear,
                "odometer_km": hw["odometer_km"],
                "battery_level_pct": hw["battery_pct"],
                "remaining_range_km": rem_range,
                "charging_status": "DISCHARGING" if speed > 1.0 else "IDLE",
                "seat_occupancy": json.dumps({
                    "driver": True,
                    "any_passenger_seat": is_passenger,
                }),
                "door_lock_status": "LOCKED" if speed > 15.0 else "UNLOCKED",
                "trunk_status": "CLOSED",
                "phone_connectivity_lost": False,
            }
            records_buffer.append(ping_rec)
            ping_seq += 1

            if out_file is not None and len(records_buffer) >= chunk_size:
                _flush_buffer()

    else:
        # Đường dẫn 2: Có public_driver_hex_tracking.parquet (L1R realdata format)
        trk_df = pl.read_parquet(hex_path)
        if selected_drivers:
            trk_df = trk_df.filter(pl.col("driver_id").is_in(list(selected_drivers)))
        sorted_trk = trk_df.sort(["driver_id", "entered_current_hex_at"])

        for row in sorted_trk.iter_rows(named=True):
            d_id = str(row["driver_id"])
            curr_hex = str(row["current_hex"])
            t_enter = str(row["entered_current_hex_at"])
            t_last = str(row.get("last_seen_at") or t_enter)
            dur_s = int(row.get("stay_duration_seconds") or 0)
            status = str(row.get("tracking_status") or "moving")

            lat, lng = h3.cell_to_latlng(curr_hex)
            lat = round(lat, 6)
            lng = round(lng, 6)

            if d_id not in hardware_state:
                hardware_state[d_id] = {
                    "odometer_km": 15000.0, "battery_pct": 90.0,
                    "vin": make_valid_vin(d_id), "last_t": None, "last_lat": None, "last_lng": None,
                }
            hw = hardware_state[d_id]

            if hw["last_lat"] is not None and hw["last_lng"] is not None:
                dist_m = haversine_meters(hw["last_lat"], hw["last_lng"], lat, lng)
                dist_km = dist_m / 1000.0
            else:
                dist_km = 0.0

            hw["odometer_km"] = round(hw["odometer_km"] + dist_km, 2)
            hw["battery_pct"] = max(10.0, round(hw["battery_pct"] - (dist_km * 0.16), 1))
            hw["last_lat"] = lat
            hw["last_lng"] = lng

            speed = 28.0 if status == "moving" else 0.0

            # Tra cứu cuốc xe active bằng bisect O(log N)
            current_order = None
            if d_id in driver_trip_starts:
                starts = driver_trip_starts[d_id]
                idx = bisect.bisect_right(starts, t_enter) - 1
                if idx >= 0:
                    tp, tc, oid = driver_trip_intervals[d_id][idx]
                    if tp <= t_enter <= tc:
                        current_order = oid

            is_passenger = current_order is not None
            gear = "D" if speed > 1.0 else "P"
            rem_range = max(30.0, round(hw["battery_pct"] * 3.5, 1))

            ping_rec = {
                "ping_id": f"tp-l1r-{d_id}-{ping_seq}",
                "driver_id": d_id,
                "trip_id": current_order,
                "source_device": "vehicle_tcu_car",
                "occurred_at": t_enter,
                "lat": lat,
                "lng": lng,
                "current_hex_h3_res9": curr_hex,
                "speed_kmh": round(speed, 1),
                "heading_deg": None,
                "vin": hw["vin"],
                "gear": gear,
                "odometer_km": hw["odometer_km"],
                "battery_level_pct": hw["battery_pct"],
                "remaining_range_km": rem_range,
                "charging_status": "DISCHARGING" if speed > 1.0 else "IDLE",
                "seat_occupancy": json.dumps({
                    "driver": True,
                    "any_passenger_seat": is_passenger,
                }),
                "door_lock_status": "LOCKED" if speed > 15.0 else "UNLOCKED",
                "trunk_status": "CLOSED",
                "phone_connectivity_lost": False,
            }
            records_buffer.append(ping_rec)
            ping_seq += 1

            if out_file is not None and len(records_buffer) >= chunk_size:
                _flush_buffer()

    # Flush nốt phần còn lại vào Parquet file
    if out_file is not None:
        _flush_buffer()
        if writer is not None:
            writer.close()
        # Trả về DataFrame mẫu gọn nhẹ
        return pl.read_parquet(out_file)
    else:
        return pl.DataFrame(records_buffer)


if __name__ == "__main__":
    import argparse
    import sys
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Vehicle Telemetry Mock Generator (VinFast VF5/VF6/e34/VF8)")
    parser.add_argument("--l1-dir", type=str, default="data/mock/test_out", help="Thu muc chua L1 parquet")
    parser.add_argument("--out-file", type=str, default="data/mock/test_out/vehicle_telemetry_ping.parquet", help="Duong dan file parquet dau ra")
    parser.add_argument("--max-drivers", type=int, default=None, help="Gioi han so tai xe xu ly")
    args = parser.parse_args()

    print(f"[*] Generating vehicle_telemetry_ping from {args.l1_dir}...")
    df = generate_telemetry_from_l1_dir(args.l1_dir, args.out_file, max_drivers=args.max_drivers)
    print(f"[+] Success! Generated {len(df)} telemetry records at {args.out_file}")

