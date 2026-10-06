# Kien truc

Bon tang xu ly, dung dung mo hinh 4-tier trong chien luoc goc (artifact "La Chan
Cuoc Xe"), duoc trien khai that thay vi chi mo ta:

```
GsmDataStore (src/lcx_core/data/gsm_loader.py)
        |
        v
Tier A (src/lcx_core/tier_a/) -- 8 luat nguong percentile + 2 stub
        |
        v
Tier B (src/lcx_core/tier_b/) -- IsolationForest tren feature gop tu Tier A
        |
        v
Tier C (src/lcx_core/tier_c/) -- STUB: thieu du lieu device/IP
        |
        v
Agent Tier D (src/lcx_core/agent/) -- pipeline tat dinh, goi 4 tool theo thu tu
        |                              co dinh (orchestrator.PIPELINE_STEPS)
        v
Chat (src/lcx_core/chat/) -- driver_chat.py, manager_chat.py (CLI demo)
        |
        v
Label store (src/lcx_core/labels/) -- var/cases/*.jsonl, nguon nhan cho Pha 5
```

## Vi sao khong co LLM API that

Khong co API key nao duoc cau hinh trong repo nay. `agent/orchestrator.py`
`draft_manager_summary`/`draft_driver_explanation_request` la HAM TEMPLATE Python
thuan, dien fact da tinh san (tu cac tool) vao cau - dung vai tro "presentation
layer tool-free" ma chinh GSM_SIMULATOR da ap dung
(`src/gsm_core/advisor/advice_agent.py`: "Strict, tool-free provider adapter").
Khi gan LLM that vao, no CHI duoc dung de viet lai van phong - moi ban nhap van
phai qua `agent/verification.py::verify_message` truoc khi goi `notify_driver`.

## Pipeline tat dinh, khong phai agent "tu do goi tool"

`orchestrator.investigate()` goi DUNG 4 tool theo mot thu tu CO DINH cho MOI
driver (`PIPELINE_STEPS`), khong co nhanh re dua tren noi dung hoi thoai. Day la
diem khac biet co chu dich voi mot kien truc "LLM tu quyet dinh goi tool nao" -
khop voi khuyen nghi Pha 3 trong chien luoc goc: "pipeline tat dinh goi tool,
LLM chi dien dat".

## Fail-closed

`apply_action` (tool duy nhat co tac dong that) tu choi chay neu:
- thieu `approver_id`
- `sanction_tier` khong phai 1 trong 3 bac that
- case chua duoc `resolve_case(verdict="confirmed")`

`notify_driver` tu choi gui neu tin nhan chua tu khoa lo ky thuat phat hien
hoac chua con so khong truy duoc nguon. Xem `tests/agent/test_case_lifecycle.py`
cho bo test day du cac duong tu choi nay.
