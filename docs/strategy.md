# Chien luoc - tom tat va ban do tai lieu

Repo nay trien khai chien luoc "La Chan Cuoc Xe": AI Agent goi tool phat hien
gian lan tai xe o hai giai doan xung yeu (cho cuoc, trong chuyen), tren 4 tang
xu ly (luat cung -> ML -> do thi quan he -> agent), voi hai giao dien chat
(tai xe, quan ly). Ban chien luoc goc day du nam trong artifact chia se dau du
an - tai lieu o day la ban DA DOI CHIEU VOI CODE THAT, khong phai ban sao chep.

## Ban do tai lieu

| Tai lieu | Noi dung |
|---|---|
| `docs/architecture.md` | Kien truc 4 tang, luong du lieu, ly do thiet ke tool-free/fail-closed |
| `docs/fraud-taxonomy.md` | 15 mau hinh gian lan theo 2 giai doan, tin hieu phat hien |
| `docs/data-matrix.md` | Tinh trang tung mau hinh tren DU LIEU THAT, phat hien phu khi trien khai |
| `docs/appendix-pending-data.md` | 3 mau hinh chua the lam vi thieu du lieu, can xin gi tu GSM |
| `docs/phase-status.md` | Tien do theo 6 pha cua chien luoc goc |
| `docs/metrics.md` | Chi so do luong de van hanh he thong |
| `docs/risks.md` | Rui ro van hanh va gioi han cua thiet ke hien tai |

## Nguyen tac cot loi (nhac lai, ap dung xuyen suot code)

1. Tach hai toc do xu ly: Tier A/B/C real-time-ish, Agent (Tier D) chi xu ly
   case da duoc gan co, khong duyet tung diem GPS.
2. Agent la tang dieu tra + hoi thoai, KHONG phai tang chan - moi khoa/tam
   ngung tai khoan di qua `apply_action` va bat buoc `approver_id` la con nguoi.
3. Khong bia du lieu - mau hinh khong co tin hieu that la stub co tai lieu.
4. Nguong tu du lieu (percentile), khong hardcode so tuy y.
