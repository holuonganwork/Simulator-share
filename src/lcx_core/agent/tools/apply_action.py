"""Tool apply_action - tool DUY NHAT co tac dong that len tai khoan tai xe.

Fail-closed theo dung docs/strategy.md muc 06: "duoc thiet ke de TU CHOI CHAY neu
thieu chu ky phe duyet cua mot quan ly con nguoi - agent co the de xuat, khong
the tu quyet". Ba bac che tai la nguyen van tu chinh sach that (KHONG bia):

    "phat tru 100% thuong tuan, tam khoa tai khoan 7 ngay hoac cham dut hop tac
    vinh vien tuy muc do vi pham"
    (GSM_SIMULATOR src/gsm_core/advisor/policy_kb.py:252)

Dieu kien fail-closed:
  1. Bat buoc co approver_id (khong rong).
  2. sanction_tier phai la 1 trong dung 3 gia tri that (khong tu dat them bac).
  3. Case lien quan PHAI co status='confirmed' (tuc da qua resolve_case voi
     verdict='confirmed' - xem labels/label_store.py) - khong the ap che tai
     tren mot case con 'open'/'reviewing'.
Ghi lai moi lan goi (ke ca bi tu choi) vao var/cases/actions.jsonl de audit.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lcx_core.agent.tools._common import ToolResult
from lcx_core.labels import CaseNotFoundError, get_case

TOOL_NAME = "apply_action"
ACTIONS_FILE = Path(__file__).resolve().parents[4] / "var" / "cases" / "actions.jsonl"

SANCTION_TIERS = (
    "clawback_100pct_weekly_bonus",  # tru 100% thuong tuan
    "suspend_7_days",                 # tam khoa tai khoan 7 ngay
    "terminate",                      # cham dut hop tac vinh vien
)


def _audit(record: dict) -> None:
    ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(
    case_id: str,
    sanction_tier: str,
    approver_id: str | None,
    reason: str,
) -> ToolResult:
    base_record = {
        "case_id": case_id,
        "sanction_tier": sanction_tier,
        "approver_id": approver_id,
        "reason": reason,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
    }

    if not approver_id:
        result = ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="missing_approver_id",
            message="Tu choi: apply_action bat buoc phai co approver_id (con nguoi phe duyet).",
        )
        _audit(base_record | {"outcome": "rejected", "reason_code": result.blocked_by})
        return result

    if sanction_tier not in SANCTION_TIERS:
        result = ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="invalid_sanction_tier",
            message=f"sanction_tier phai la mot trong {SANCTION_TIERS}.",
        )
        _audit(base_record | {"outcome": "rejected", "reason_code": result.blocked_by})
        return result

    try:
        case = get_case(case_id)
    except CaseNotFoundError:
        result = ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="case_not_found",
            message=f"Khong tim thay case_id={case_id}.",
        )
        _audit(base_record | {"outcome": "rejected", "reason_code": result.blocked_by})
        return result

    if case.status != "confirmed":
        result = ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="case_not_confirmed",
            message=(
                f"Tu choi: case {case_id} co status='{case.status}', can duoc "
                "resolve_case(verdict='confirmed') truoc khi ap che tai."
            ),
        )
        _audit(base_record | {"outcome": "rejected", "reason_code": result.blocked_by})
        return result

    record = base_record | {"driver_id": case.driver_id, "outcome": "applied"}
    _audit(record)
    return ToolResult(ok=True, tool=TOOL_NAME, data=record)
