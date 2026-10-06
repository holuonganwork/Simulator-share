"""Cửa DUY NHẤT đọc 13 bảng GSM (`l1r`) — từ PostgreSQL, không còn parquet.

## Vì sao không giữ đường parquet làm dự phòng

Cường chốt 2026-08-19: cắt hẳn. Một hệ thống có hai nguồn cho cùng một con số là đúng lỗi
`D-M3-17` (UI tự tính tầm pin lệch engine 1,76×) — và lần đó cái sai sống sót lâu chính vì
đường thứ hai vẫn "chạy được". Postgres chết thì phải NỔ, không được âm thầm trả số cũ.

## Hai cách đọc, hai mục đích

- `bang(ten)` trả TRỌN bảng dạng polars DataFrame — cho ~16 call-site đã viết bằng biểu thức
  polars (`ui/backend/app/adapters/*`). Chỉ dùng cho bảng nhỏ.
- `cua_tai_xe(ten, driver_id, tu_ngay)` đẩy filter XUỐNG DB — cho đường nóng. Đây là chỗ ăn
  tiền: `public_driver_hex_tracking` có 1,48 triệu dòng, một lượt hỏi chỉ cần vài nghìn.

## 🔴 Hai phép chuẩn hoá ở biên, đừng bỏ

**Tên bảng hạ chữ.** `driver_penalization_ATA` là tên GSM thật, nhưng Postgres hạ chữ mọi
identifier không nháy ⇒ bảng thật tên `driver_penalization_ata`. Dùng `load_pg.ten_pg` để
không có bản ánh xạ thứ hai.

**Cột ngày trả về dạng CHUỖI.** Trong Postgres chúng là `DATE` thật (bắt buộc — nếu để TEXT thì
`WHERE local_date >= …` so theo thứ tự chữ cái). Nhưng parquet vốn giữ chúng là chuỗi
`"2026-04-03"`, và call-site cũ so `pl.col("local_date") < ngay` với một chuỗi Python. Đổi kiểu
ở đây là bắt 16 chỗ phải sửa cùng lúc để đổi lấy đúng con số không. Nên: **DB giữ kiểu đúng,
biên này trả hình dạng cũ.**
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
from typing import Any

from gsm_core.schema_registry import L1R_ENTITIES

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {}

BANG_HOP_LE = frozenset(L1R_ENTITIES)

# Bảng nào dùng cột ngày nào để cắt theo thời gian. Thiếu ở đây ⇒ `tu_ngay` bị bỏ qua.
_COT_NGAY = {
    "driver_income_daily": "order_date",
    "kpi_driver_platform_calculator_gbq": "week_start",
}
_COT_NGAY_MAC_DINH = "local_date"


def dsn() -> str:
    v = os.getenv("L1R_DSN", "").strip()
    if not v:
        try:
            from gsm_core.advisor.llm_client import load_env

            load_env()
            v = os.getenv("L1R_DSN", "").strip()
        except Exception:  # noqa: BLE001 - không có .env là trạng thái bình thường
            pass
    if not v:
        raise RuntimeError(
            "L1R_DSN chưa đặt. Dữ liệu GSM nay nằm ở PostgreSQL, không còn đọc parquet — "
            "xem docs/setup/postgres-local.md")
    return v


def reset_cache_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()


def _kiem_ten(ten: str) -> str:
    if ten not in BANG_HOP_LE:
        raise ValueError(f"{ten}: không thuộc 13 bảng l1r")
    return ten


def _ten_pg(ten: str) -> str:
    """Tên bảng phía Postgres — LUÔN chữ thường.

    ⚠ Bản sao CÓ CHỦ Ý của `scripts/load_pg.py::ten_pg`. Merge 2026-08-20 (nhánh RAG) đã inline
    nó ở đây, và inline là ĐÚNG: `src/` không được import `scripts/`. Nhưng docstring của bản
    gốc nói *"ánh xạ tên GSM ↔ tên Postgres nằm gọn trong hàm này"*, nên inline làm mất điểm neo
    đó — nếu ánh xạ một ngày không còn chỉ là hạ chữ, chỗ này sẽ không đi theo.

    Đã đối chiếu 2026-08-20: hai bên **đúng bằng nhau** (`ten_bang.lower()`). Đổi một bên thì
    phải đổi bên kia; `test_ten_bang_pg_khop_hai_ben` canh điều đó.
    """
    return ten.lower()


def bang(ten: str):
    """Trọn bảng, dạng polars DataFrame với ngày là CHUỖI (khuôn cũ của parquet)."""
    import polars as pl
    import psycopg

    _kiem_ten(ten)
    with _LOCK:
        if ten in _CACHE:
            return _CACHE[ten]
    with psycopg.connect(dsn()) as conn:
        cur = conn.execute(f"SELECT * FROM l1r.{_ten_pg(ten)}")
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    df = pl.DataFrame(
        [dict(zip(cols, _chuoi_hoa_ngay(r))) for r in rows],
        infer_schema_length=None) if rows else pl.DataFrame({c: [] for c in cols})
    with _LOCK:
        _CACHE[ten] = df
    return df


def cua_tai_xe(ten: str, driver_id: str, tu_ngay: str | None = None) -> list[dict]:
    """Lát cắt của MỘT tài xế. Filter chạy ở DB, không ở Python.

    Bảng không có cột `driver_id` (vd `public_mission` là catalog dùng chung) ⇒ trả trọn bảng,
    vì đó đúng là thứ deriver cần.
    """
    import psycopg
    from psycopg.rows import dict_row

    _kiem_ten(ten)
    cols = set(bang(ten).columns)
    if "driver_id" not in cols:
        return bang(ten).to_dicts()

    cot_ngay = _COT_NGAY.get(ten, _COT_NGAY_MAC_DINH)
    sql = f"SELECT * FROM l1r.{_ten_pg(ten)} WHERE driver_id = %s"
    params: list[Any] = [driver_id]
    if tu_ngay and cot_ngay in cols:
        sql += f' AND "{cot_ngay}" >= %s'
        params.append(tu_ngay)
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [{k: _chuoi_neu_ngay(v) for k, v in r.items()} for r in rows]


def _chuoi_neu_ngay(v: Any) -> Any:
    """`date`/`datetime` → chuỗi ISO. Giữ nguyên hình dạng mà parquet vẫn trả về."""
    if isinstance(v, _dt.datetime):
        return v.isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    return v


def _chuoi_hoa_ngay(row: Any) -> list:
    return [_chuoi_neu_ngay(v) for v in row]
