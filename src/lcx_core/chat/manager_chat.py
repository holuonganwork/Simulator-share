"""Chat quan ly - CLI demo (docs/strategy.md muc 07).

Quyen truy cap cao hon chat tai xe: xem toan bo evidence, la noi DUY NHAT co the
kich hoat apply_action. Chay:

    python -m lcx_core.chat.manager_chat <driver_id>

Roi lam theo huong dan tren man hinh (resolve/apply/quit).
"""

from __future__ import annotations

import sys

from lcx_core.agent import orchestrator
from lcx_core.agent.tools import apply_action, resolve_case
from lcx_core.data import load_data_store
from lcx_core.labels import list_cases


def run_session(driver_id: str) -> None:
    store = load_data_store()
    bundle = orchestrator.investigate(store, driver_id)

    print(orchestrator.draft_manager_summary(bundle))
    print()

    cases = list_cases(driver_id=driver_id, status="open") + list_cases(
        driver_id=driver_id, status="reviewing"
    )
    if not cases:
        print("(khong co case dang mo cho driver nay)")
        return

    for case in cases:
        print(f"\nCase {case.case_id} [{case.pattern}] severity={case.severity} status={case.status}")
        print(f"  evidence: {case.evidence}")

    print(
        "\nLenh: resolve <case_id> <false_positive|confirmed> <ghi_chu>\n"
        "      apply <case_id> <clawback_100pct_weekly_bonus|suspend_7_days|terminate> <ly_do>\n"
        "      quit"
    )
    reviewer_id = input("Nhap manager_id cua ban: ").strip() or "mgr-unknown"

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line or line == "quit":
            break

        parts = line.split(maxsplit=2)
        cmd = parts[0]

        if cmd == "resolve" and len(parts) == 3:
            case_id, rest = parts[1], parts[2]
            verdict, _, note = rest.partition(" ")
            result = resolve_case.run(case_id, verdict, reviewer_id, note or "(khong ghi chu)")
            print(result.to_dict())
        elif cmd == "apply" and len(parts) == 3:
            case_id, rest = parts[1], parts[2]
            tier, _, reason = rest.partition(" ")
            result = apply_action.run(case_id, tier, reviewer_id, reason or "(khong ly do)")
            print(result.to_dict())
        else:
            print("Lenh khong hop le. Xem huong dan o tren.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Dung: python -m lcx_core.chat.manager_chat <driver_id>")
        sys.exit(1)
    run_session(sys.argv[1])
