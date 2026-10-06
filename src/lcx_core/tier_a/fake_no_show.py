"""Bao "khach khong den" gia (fake_no_show) - giai doan cho cuoc.

STUB CO CHU DICH - KHONG BIA LOGIC. Da kiem tra truc tiep tren du lieu that:

  - trips.status chi co enum {"completed", "cancelled", "assigned"}
    (schemas/l1r/trips.schema.json) va TRONG DU LIEU HIEN CO 100% la 'completed'
    (174.233/174.233 dong) - khong co ban ghi cancelled/assigned nao de doi chieu.
  - Khong co truong nao danh dau su kien "no-show" rieng biet o bat ky bang nao
    trong 13 bang (driver_penalization_ATA.penalty_type cung chi co gia tri
    'clawback_khoan' trong du lieu hien co, khong co loai lien quan no-show).

Viet logic "phat hien" dua tren cac truong khong ton tai se la khang dinh mot
nang luc khong co that (dung tinh than cua chinh
src/gsm_core/mockgen/realdata.py trong GSM_SIMULATOR). Xem docs/appendix-pending-data.md
va docs/data-matrix.md - day la 1 trong 3 mau hinh "thieu han" du lieu.

CAP NHAT (docs/telemetry-research.md): quan sat truc tiep app Xanh SM Driver
xac nhan trang thai "Da den diem don" (arrived_at_pickup) TON TAI THAT trong
san pham that, kem kiem tra GPS trong ban kinh cho phep truoc khi cho bam - xem
schemas/telemetry/trip_lifecycle_event.schema.json (truong
arrival_geofence_check_passed). Day la thay doi quan trong: KHONG con la "chua
co bang chung", ma la "GSM_SIMULATOR chua mock truong nay". Mot khi service
mock telemetry (Buoc 3, chua build) tao ra du lieu arrived_at_pickup, detector
nay CO THE lam that duoc bang cach doi chieu vi tri bao cao no-show voi
arrival_geofence_check_passed va thoi luong dung that giua arrived_at_pickup va
no_show_reported/trip_cancelled.

Du lieu can xin bo sung tu GSM (theo docs/strategy.md phu luc muc 08), NEU
khong tu mock duoc qua telemetry:
  - Vi tri thuc te luc tai xe bam "bao khach khong den" (de doi chieu voi diem don)
  - Thoi gian tai xe da dung cho that (khong chi suy tu stoppoints tong hop)
"""

from __future__ import annotations

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleResult

PATTERN = "fake_no_show"


class MissingDataError(NotImplementedError):
    """Nem loi ro rang thay vi tra ve ket qua rong/gia."""


def detect(store: GsmDataStore) -> RuleResult:  # noqa: ARG001 - giu chu ky nhat quan
    raise MissingDataError(
        f"[{PATTERN}] Chua the trien khai: khong co truong no-show/vi-tri-bao-cao "
        "trong 13 bang hien co. Xem docstring module nay va docs/appendix-pending-data.md."
    )
