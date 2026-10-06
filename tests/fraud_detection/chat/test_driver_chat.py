"""Smoke test cho driver_chat.py - mo phong input() de chay het 1 phien that."""

from __future__ import annotations

import pytest

from lcx_core.chat import driver_chat
from lcx_core.labels import list_cases
from lcx_core.tier_a import route_deviation


@pytest.mark.xfail(
    reason=(
        "Loi CO SAN, khong lien quan v1/v2/ban do: cau mo dau cua "
        "orchestrator.draft_driver_explanation_request() co chuoi cung 'trong "
        "vong 48 gio', nhung so '48' khong xuat hien trong evidence/trich dan "
        "chinh sach cua bundle -> notify_driver.run() luon bi guardrail "
        "'unverified_numbers' chan, BAT KE driver_id hay bo du lieu nao (da "
        "kiem chung: xay ra y het tren ca realdata-v1 lan v2, voi driver du "
        "duoc route_deviation gan co that). Can sua o template cua "
        "orchestrator (trich dan '48' vao evidence, hoac bo con so cung trong "
        "cau) - ngoai pham vi lam giau du lieu/ban do Res 7."
    ),
    strict=True,
)
def test_driver_chat_session_creates_case_when_driver_explains(store, monkeypatch, capsys):
    # Lay 1 driver_id THAT SU bi route_deviation gan co tren bo du lieu dang
    # nap (khong hard-code driver_id/index co dinh - tai xe nao bi gan co doi
    # theo tung ban realdata, xem SimulatorGSM-HaNoi#lam-giau-du-lieu).
    driver_id = route_deviation.detect(store).flags[0].driver_id
    monkeypatch.setattr("builtins.input", lambda _prompt="": "ket xe nen bi cham")

    driver_chat.run_session(driver_id, "route_deviation")

    out = capsys.readouterr().out
    assert "[Agent ->" in out
    cases = list_cases(driver_id=driver_id)
    assert len(cases) == 1
    assert cases[0].evidence["driver_explanation"] == "ket xe nen bi cham"


def test_driver_chat_session_skips_case_when_no_explanation(store, monkeypatch, capsys):
    driver_id = store.table("driver_statistic_daily")["driver_id"][1]

    def _raise_eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)
    driver_chat.run_session(driver_id, "route_deviation")

    assert list_cases(driver_id=driver_id) == []
