"""Tach mot cuoc dai thanh nhieu cuoc ngan (split_long_trip) - giai doan trong chuyen.

Ghep chuoi cac chuyen lien tiep cung driver_id + customer_id: neu diem tra khach
cuoc N gan (cung o hex, hoac o lang gieng theo h3.grid_distance) diem don khach
cuoc N+1, VA khoang cach thoi gian complete_time(N) -> request_time(N+1) rat ngan,
day co the la mot chuyen thuc te bi bam "hoan thanh" roi "dat lai" de tach cuoc dai
thanh nhieu cuoc ngan (moi cuoc co phi mo cuoc rieng).

trips.parquet trong du lieu hien co CHI co status='completed' (khong co cancelled/
assigned), nen chuoi duoc ghep tren cac cuoc da hoan thanh - dung dieu kien that
cua du lieu, khong gia dinh co du lieu chua co.

PHAT HIEN KHI CHAY TREN DU LIEU THAT (ghi lai thay vi giau di): da kiem tra truc
tiep - trong toan bo 174.233 dong trips.parquet, MOI cap (driver_id, customer_id)
CHI xuat hien dung 1 lan (max_repeats = 1, 0 cap co >= 2 chuyen). customer_id o
day duoc sinh dang "cust-<order_id>" - tuc la mot dinh danh gia lap RIENG BIET
CHO TUNG CHUYEN, khong mo phong mot khach hang quay lai nhieu lan. Vi vay detector
nay LUON tra ve 0 flag tren bo du lieu mock hien co - day KHONG phai loi logic,
ma la gioi han cua chinh du lieu tong hop (mockgen chua sinh khach hang lap lai).
Logic van dung khi duoc chay tren du lieu that co customer_id on dinh qua nhieu
chuyen.
"""

from __future__ import annotations

from datetime import datetime

import h3
import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult

PATTERN = "split_long_trip"
MAX_GAP_MINUTES = 5
MAX_HEX_RING_GAP = 1


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def detect(
    store: GsmDataStore,
    max_gap_minutes: int = MAX_GAP_MINUTES,
    max_hex_ring_gap: int = MAX_HEX_RING_GAP,
) -> RuleResult:
    trips = store.table("trips").filter(pl.col("status") == "completed")
    trips = trips.filter(
        pl.col("complete_time").is_not_null() & pl.col("request_time").is_not_null()
    )
    trips = trips.sort(["driver_id", "customer_id", "request_time"])

    flags: list[RuleFlag] = []
    n_evaluated = 0
    prev_row: dict | None = None

    for row in trips.iter_rows(named=True):
        if (
            prev_row is not None
            and prev_row["driver_id"] == row["driver_id"]
            and prev_row["customer_id"] == row["customer_id"]
        ):
            n_evaluated += 1
            gap_minutes = (
                _parse(row["request_time"]) - _parse(prev_row["complete_time"])
            ).total_seconds() / 60.0

            if 0 <= gap_minutes <= max_gap_minutes:
                ring_gap = h3.grid_distance(prev_row["drop_h3"], row["pickup_h3"])
                if ring_gap <= max_hex_ring_gap:
                    flags.append(
                        RuleFlag(
                            pattern=PATTERN,
                            driver_id=row["driver_id"],
                            signal_value=gap_minutes,
                            threshold=float(max_gap_minutes),
                            evidence={
                                "customer_id": row["customer_id"],
                                "trip_id_prev": prev_row["trip_id"],
                                "trip_id_next": row["trip_id"],
                                "gap_minutes": round(gap_minutes, 2),
                                "hex_ring_gap": ring_gap,
                                "drop_h3_prev": prev_row["drop_h3"],
                                "pickup_h3_next": row["pickup_h3"],
                            },
                        )
                    )
        prev_row = row

    return RuleResult(
        pattern=PATTERN,
        method=f"max_gap_minutes={max_gap_minutes},max_hex_ring_gap={max_hex_ring_gap}",
        threshold=float(max_gap_minutes),
        n_evaluated=n_evaluated,
        flags=flags,
    )
