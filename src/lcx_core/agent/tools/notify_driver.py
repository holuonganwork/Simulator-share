"""Tool notify_driver - gui tin nhan co kiem soat toi chat tai xe.

Hai lop kiem soat truoc khi ghi tin (khong the bo qua - fail-closed):
  1. Guardrail tu schemas/l3/anomaly_alert_input.schema.json: "KHONG ket toi,
     KHONG lo evidence_ref/nguong phat hien" -> chan tu khoa lo nguong/thuat toan.
  2. verification.verify_message: moi con so trong tin phai truy duoc nguon
     (evidence hoac trich dan chinh sach) - xem agent/verification.py.

Tin da duyet duoc ghi vao var/chat/driver_outbox.jsonl (khong co he thong nhan
tin that de goi).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lcx_core.agent.tools._common import ToolResult
from lcx_core.agent.verification import verify_message

TOOL_NAME = "notify_driver"
OUTBOX_FILE = Path(__file__).resolve().parents[4] / "var" / "chat" / "driver_outbox.jsonl"

# Tu khoa lo ky thuat phat hien - chan theo dung guardrail cua
# schemas/l3/anomaly_alert_input.schema.json ("KHONG lo evidence_ref/nguong phat hien").
BLOCKED_KEYWORDS = [
    "percentile", "z-score", "z_score", "nguong", "threshold",
    "isolation forest", "evidence_ref", "confidence=",
]


def run(driver_id: str, message: str, source_texts: list[str], case_id: str | None = None) -> ToolResult:
    lowered = message.lower()
    leaked = [kw for kw in BLOCKED_KEYWORDS if kw in lowered]
    if leaked:
        return ToolResult(
            ok=False,
            tool=TOOL_NAME,
            blocked_by="guardrail_threshold_leak",
            message=f"Tin nhan chua tu khoa lo ky thuat phat hien: {leaked}. Viet lai o muc khai quat.",
        )

    verification = verify_message(message, source_texts)
    if not verification.ok:
        return ToolResult(
            ok=False,
            tool=TOOL_NAME,
            blocked_by="unverified_numbers",
            message=verification.reason,
        )

    record = {
        "driver_id": driver_id,
        "case_id": case_id,
        "message": message,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTBOX_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return ToolResult(ok=True, tool=TOOL_NAME, data=record)
