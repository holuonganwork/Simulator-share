"""Phan tich do thi thiet bi/tai khoan (Tier C) - da tai khoan & cau ket.

STUB CO CHU DICH - KHONG BIA DU LIEU. Da grep toan bo schemas/l0 va schemas/l1
cua GSM_SIMULATOR: KHONG co truong device_id, IMEI, IP, hay BSSID o bat ky bang
nao trong 13 bang L1R hien co (xac nhan lai dung nhu docs/gsm-simulator-alignment.md
muc "Can dieu chinh truoc khi code" da neu). Day la khoang trong DUY NHAT khong
the tu mock hop ly tu nhung gi repo dang co - can du lieu that tu doi GSM.

Interface duoi day mo ta INPUT/OUTPUT du kien de cac phan con lai cua he thong
(agent/tools/query_device_account_graph.py) co the goi ngay khi du lieu ve, ma
khong phai sua lai chu ky ham o noi khac.
"""

from __future__ import annotations

from dataclasses import dataclass

from lcx_core.data.gsm_loader import GsmDataStore


class MissingDataError(NotImplementedError):
    """Nem loi ro rang thay vi tra ve ket qua rong/gia."""


@dataclass(frozen=True)
class DeviceAccountCluster:
    """Du kien: mot cum tai khoan/thiet bi lien quan nhau."""

    cluster_id: str
    driver_ids: list[str]
    shared_signal: str          # vd "device_id", "ip_subnet", "bssid"
    shared_value: str
    confidence: float


def query_cluster_for_driver(
    store: GsmDataStore, driver_id: str
) -> list[DeviceAccountCluster]:  # noqa: ARG001
    raise MissingDataError(
        "Tier C (do thi thiet bi/tai khoan) chua the trien khai: 13 bang L1R hien co "
        "khong chua device_id/IMEI/IP/BSSID o bat ky schema nao (schemas/l0, schemas/l1). "
        "Can de xuat bo sung du lieu nay voi doi GSM truoc (xem docs/appendix-pending-data.md, "
        "muc 'Du lieu can xin them tu GSM' - dong dau tien: Da tai khoan / thue tai khoan)."
    )
