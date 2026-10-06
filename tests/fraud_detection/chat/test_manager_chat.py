"""Smoke test cho manager_chat.py - mo phong input() de chay het resolve+apply."""

from __future__ import annotations

from lcx_core.agent.tools import flag_case
from lcx_core.chat import manager_chat
from lcx_core.labels import get_case


def test_manager_chat_resolve_and_apply_flow(store, monkeypatch, capsys):
    driver_id = store.table("driver_statistic_daily")["driver_id"][0]
    case = flag_case.run(driver_id, "route_deviation", "medium", 0.7, {"x": 1})
    case_id = case.data["case_id"]

    inputs = iter(
        [
            "mgr-test",
            f"resolve {case_id} confirmed xac nhan qua bang chung",
            f"apply {case_id} suspend_7_days vi pham xac nhan",
            "quit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

    manager_chat.run_session(driver_id)

    out = capsys.readouterr().out
    assert "'outcome': 'applied'" in out
    assert get_case(case_id).status == "confirmed"
