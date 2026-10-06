# Vong doi chuyen di theo du lieu THAT (khac voi ly thuyet mo ta trong chien luoc goc)

Chien luoc goc mo ta vong doi day du:
`TimTaiXe -> DaGhepCuoc -> DaDenDiemDon -> DangChay -> HoanThanh`, voi cac
nhanh huy o nhieu diem va mot trang thai `KhongGapKhach` (no-show).

**Da kiem tra truc tiep tren `trips.parquet`:** enum `status` chi co
`["completed", "cancelled", "assigned"]` (`schemas/l1r/trips.schema.json`), va
trong du lieu mock hien co (`data/mock/realdata-v1`), **100% cac dong la
'completed'** - khong co dong nao o trang thai `cancelled` hay `assigned`.

Hieu ung thuc te:

- Khong the phan biet "huy truoc ghep", "huy sau ghep", "huy sau khi gap
  khach" tu du lieu hien co - ca ba deu can trang thai `cancelled` (0 dong).
- Khong co trang thai trung gian `DaDenDiemDon` (arrived) - tool
  `off_app_cash_pressure` va `fake_no_show` phu thuoc truc tiep vao khoang
  trong nay (xem `docs/appendix-pending-data.md`).
- Cac tool/detector trong repo nay chi hoat dong tren doan
  `DaGhepCuoc -> HoanThanh` cua vong doi - dung phan CO du lieu.

Neu GSM cap them du lieu trang thai chi tiet hon (vd `assigned`, `arrived`,
`cancelled` voi timestamp), hai stub `tier_a/fake_no_show.py` va
`tier_a/off_app_cash_pressure.py` da co san chu ky ham (`detect(store)`) de
cai logic that vao ma khong phai sua noi nao khac.
