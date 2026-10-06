"""Tool resolve_case - dong case voi verdict cua nguoi quan ly (nguon nhan Pha 5).

verdict='confirmed' -> du dieu kien cho apply_action (xem apply_action.py).
verdict='false_positive' -> chi dung hieu chinh nguong Tier A/B, KHONG trung phat.
"""

from __future__ import annotations

from lcx_core.agent.tools._common import ToolResult
from lcx_core.labels import CaseNotFoundError, resolve_case as _resolve_case

TOOL_NAME = "resolve_case"
VALID_VERDICTS = ("false_positive", "confirmed")


def run(case_id: str, verdict: str, reviewer_id: str, note: str) -> ToolResult:
    if verdict not in VALID_VERDICTS:
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="invalid_verdict",
            message=f"verdict phai la mot trong {VALID_VERDICTS}.",
        )
    if not reviewer_id:
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="missing_reviewer_id",
            message="Can reviewer_id - khong the dong case ma khong ro ai ra quyet dinh.",
        )
    try:
        resolution = _resolve_case(case_id, verdict, reviewer_id, note)
    except CaseNotFoundError:
        return ToolResult(
            ok=False, tool=TOOL_NAME, blocked_by="case_not_found",
            message=f"Khong tim thay case_id={case_id}.",
        )
    return ToolResult(
        ok=True,
        tool=TOOL_NAME,
        data={
            "case_id": resolution.case_id,
            "verdict": resolution.verdict,
            "reviewer_id": resolution.reviewer_id,
            "reviewed_at": resolution.reviewed_at,
        },
    )
