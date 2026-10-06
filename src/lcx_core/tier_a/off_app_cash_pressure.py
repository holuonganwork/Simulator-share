"""Ep huy chuyen tien mat (off_app_cash_pressure) - giai doan trong chuyen.

STUB CO CHU DICH - KHONG BIA LOGIC. Tin hieu that su can la "ty le huy SAU KHI
DA GAP KHACH (arrived)" - nhung da kiem tra truc tiep:

  - trips.schema.json khong co trang thai 'arrived' (chi co request/assign/
    pickup/complete_time) - khong the phan biet "huy truoc khi gap" voi "huy sau
    khi gap khach" tu du lieu hien co.
  - trips.parquet hien co 100% status='completed', khong co dong 'cancelled' nao.
  - driver_penalization_ATA.penalty_type trong du lieu hien co CHI co gia tri
    'clawback_khoan' (truy thu doanh so tuan) - khong co loai phat lien quan huy-
    sau-khi-gap-khach hay giao dich tien mat ngoai app.

Day la 1 trong 3 mau hinh "thieu han" theo docs/appendix-pending-data.md. Du lieu
can xin bo sung: trang thai chi tiet hon cho trip (vd 'arrived_at_pickup') va/hoac
log huy cuoc kem thoi diem so voi pickup_time.

CAP NHAT (docs/telemetry-research.md): trang thai 'arrived_at_pickup' TON TAI
THAT trong app Xanh SM Driver (xac nhan qua quan sat UI, kem kiem tra GPS ban
kinh) - xem schemas/telemetry/trip_lifecycle_event.schema.json. Ngoai ra
telemetry xe hoi (seat_occupancy, gear) cung cap tin hieu MANH HON de phat
hien dung mau hinh nay: cuoc bi bao 'no_show_reported'/'trip_cancelled' nhung
seat_occupancy=true va xe van di chuyen ve huong diem den ban dau - dung
NGUYEN VAN bang anh xa trong docs/telemetry-research.md. Van la STUB cho toi
khi service mock telemetry (Buoc 3) san sang - GSM_SIMULATOR (nguon du lieu
hien tai cua module nay) khong co ca hai loai truong tren.
"""

from __future__ import annotations

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleResult

PATTERN = "off_app_cash_pressure"


class MissingDataError(NotImplementedError):
    """Nem loi ro rang thay vi tra ve ket qua rong/gia."""


def detect(store: GsmDataStore) -> RuleResult:  # noqa: ARG001 - giu chu ky nhat quan
    raise MissingDataError(
        f"[{PATTERN}] Chua the trien khai: khong co trang thai 'arrived' hay ban ghi "
        "huy cuoc nao trong du lieu hien co. Xem docstring module nay va "
        "docs/appendix-pending-data.md."
    )
