"""Kieu tra ve chung cho moi agent tool.

Theo mau da co san trong GSM_SIMULATOR (src/gsm_sim/hil/commands.py): "moi lenh
bi chan phai tra ve ten tang da chan, khong throw exception im lang". Ap dung
dung nguyen tac do cho tool apply_action va cac tool phu thuoc du lieu con thieu
(Tier C) - agent/orchestrator.py va hai chat UI dua vao `ok`/`blocked_by` de
quyet dinh buoc tiep theo thay vi bat exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool: str
    data: dict[str, Any] = field(default_factory=dict)
    blocked_by: str | None = None   # ten tang/ly do chan, vd "missing_data_tier_c"
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "data": self.data,
            "blocked_by": self.blocked_by,
            "message": self.message,
        }
