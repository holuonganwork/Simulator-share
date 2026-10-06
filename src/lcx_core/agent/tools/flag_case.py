"""Tool flag_case - tao case co the audit trong label_store."""

from __future__ import annotations

from typing import Any

from lcx_core.agent.tools._common import ToolResult
from lcx_core.labels import create_case

TOOL_NAME = "flag_case"


def run(
    driver_id: str,
    pattern: str,
    severity: str,
    confidence: float,
    evidence: dict[str, Any],
) -> ToolResult:
    if severity not in ("low", "medium", "high"):
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="invalid_severity",
            message="severity phai la 'low', 'medium' hoac 'high'.",
        )
    case = create_case(driver_id, pattern, severity, confidence, evidence)
    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={"case_id": case.case_id, "status": case.status, "created_at": case.created_at},
    )
