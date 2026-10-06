"""Fixture dung chung: `store` tro thang vao du lieu that cua GSM_SIMULATOR.

Cac test trong repo nay KHONG dung du lieu gia lap rieng - chung chay truc tiep
tren data/mock/realdata-v1 cua GSM_SIMULATOR (session-scoped de khong doc lai
file Parquet moi test, vi public_driver_hex_tracking co 1.48 trieu dong).
"""

from __future__ import annotations

import pytest

from lcx_core.data.gsm_loader import load_data_store


@pytest.fixture(scope="session")
def store():
    return load_data_store()


@pytest.fixture(autouse=True)
def _isolated_var_dir(tmp_path, monkeypatch):
    """Moi test flag_case/resolve_case/apply_action ghi vao var/ RIENG (tmp_path)
    thay vi var/cases that cua repo - tranh test lam ban du lieu case that.
    """
    import lcx_core.labels.label_store as label_store

    cases_dir = tmp_path / "cases"
    monkeypatch.setattr(label_store, "VAR_DIR", cases_dir)
    monkeypatch.setattr(label_store, "CASES_FILE", cases_dir / "cases.jsonl")
    monkeypatch.setattr(label_store, "RESOLUTIONS_FILE", cases_dir / "resolutions.jsonl")

    import lcx_core.agent.tools.apply_action as apply_action_mod

    monkeypatch.setattr(apply_action_mod, "ACTIONS_FILE", cases_dir / "actions.jsonl")

    import lcx_core.agent.tools.notify_driver as notify_driver_mod

    monkeypatch.setattr(notify_driver_mod, "OUTBOX_FILE", tmp_path / "chat" / "driver_outbox.jsonl")

    yield
