# Do luong va vong phan hoi

## Chi so can theo doi khi van hanh

| Chi so | Cach tinh trong repo nay |
|---|---|
| Precision/Recall theo mau hinh | Can nhan `confirmed` tu `resolve_case` (Pha 5) - chua co du lieu that de tinh, chi co ha tang (`labels/label_store.py::all_resolutions`) |
| False positive rate | Ty le case co verdict `false_positive` / tong case da resolve - `list_cases(status="cleared")` |
| Time-to-resolution | `reviewed_at - created_at` cho tung case trong `var/cases/cases.jsonl` |
| Flag rate theo mau hinh | Da co san: `RuleResult.flag_rate` cho ca 8 detector Tier A (xem `scripts/run_tier_a_scan.py`) |
| Ty le tu dong dong case qua chat tai xe | So case co `driver_explanation` trong evidence / tong case gui qua `driver_chat.py` |

## Vi sao chua co so lieu Precision/Recall that TREN DU LIEU SAN XUAT

He thong moi co 1 nguon nhan doc lap thuc su tu san xuat (`abnormal_cancel`, tu
chinh `public_frauds.parquet` cua GSM_SIMULATOR). Cac mau hinh con lai chua co
case nao duoc `resolve_case(verdict="confirmed")` boi mot quan ly that -
Precision/Recall THAT (tren du lieu san xuat that) tren nhung mau hinh do van
chua co y nghia thong ke. Day la ly do Tier B (`tier_b/risk_scorer.py`) dung
IsolationForest (khong giam sat) thay vi mot mo hinh phan loai - xem docstring
cua file do.

**Cap nhat (Pha A - fraud injector, 2026-09-18):** rieng cho MUC DICH KIEM THU
kha nang phat hien (khong thay the nhan san xuat that o tren), 7 mau hinh con
lai gio da co Precision/Recall do duoc tren du lieu co hanh vi gian lan CO CHU
DICH duoc tiem co tham so (`src/lcx_core/fraud_injector/`, xem ket qua day du
va cac phat hien quan trong - dac biet hieu ung "self-masking" cua
`fake_ride_for_kpi` - tai `docs/injector-eval-results.md`). Day la buoc dau
tien de tra loi cau hoi "luat co bat dung khong" ma truoc Pha A khong the tra
loi cho 6/7 mau hinh (`split_long_trip` la ngoai le - da biet tra ve 0 tren du
lieu that vi gioi han cua chinh du lieu, xem docstring file do).

## Buoc tiep theo de co so lieu that

1. Van hanh chat quan ly (`manager_chat.py`) tren case that mot thoi gian.
2. Chay `labels.all_resolutions()` de lay tap nhan doc lap.
3. Viet `scripts/retrain_tier_b.py` phien ban giam sat khi co it nhat vai chuc
   case `confirmed` cho moi mau hinh (hien script nay kiem tra dieu kien nay va
   tu choi chay neu chua du, xem noi dung script).
