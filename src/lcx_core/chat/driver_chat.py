"""Chat tai xe - CLI demo (docs/strategy.md muc 07).

Minh bach hoa, khong buoc toi: agent hoi truc dien nhung khong tiet lo nguong/
thuat toan phat hien, chi neu tin hieu o muc khai quat. Han giai trinh chinh
thuc 48h (schemas/l3/anomaly_alert_input.schema.json). Chay:

    python -m lcx_core.chat.driver_chat <driver_id> <pattern>
"""

from __future__ import annotations

import sys

from lcx_core.agent import orchestrator
from lcx_core.agent.tools import flag_case, notify_driver
from lcx_core.data import load_data_store


def run_session(driver_id: str, pattern: str) -> None:
    store = load_data_store()
    bundle = orchestrator.investigate(store, driver_id)

    opening = orchestrator.draft_driver_explanation_request(bundle, pattern)
    result = notify_driver.run(
        driver_id, opening, source_texts=orchestrator.source_texts(bundle)
    )
    print(f"[Agent -> {driver_id}] {opening}")
    if not result.ok:
        print(f"  (!) Tin nay bi CHAN boi guardrail: {result.blocked_by} - {result.message}")
        return

    print("\nNhap giai trinh cua tai xe (Enter de bo qua, Ctrl+D de ket thuc):")
    try:
        explanation = input("> ").strip()
    except EOFError:
        explanation = ""

    if explanation:
        print(f"[{driver_id} -> Agent] {explanation}")
        ack = (
            "Cam on anh/chi. Minh da ghi nhan giai trinh, ho so se duoc chuyen "
            "quan ly xem xet. Neu can them thong tin, minh se lien he lai."
        )
        notify_driver.run(driver_id, ack, source_texts=[explanation])
        print(f"[Agent -> {driver_id}] {ack}")

        flag_result = flag_case.run(
            driver_id=driver_id,
            pattern=pattern,
            severity="low",
            confidence=0.5,
            evidence={"driver_explanation": explanation, "bundle": orchestrator.bundle_to_dict(bundle)},
        )
        print(f"\n(he thong) flag_case -> {flag_result.data.get('case_id')}")
    else:
        print("(khong co giai trinh - case van o trang thai cho, se nhac lai truoc han 48h)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Dung: python -m lcx_core.chat.driver_chat <driver_id> <pattern>")
        sys.exit(1)
    run_session(sys.argv[1], sys.argv[2])
