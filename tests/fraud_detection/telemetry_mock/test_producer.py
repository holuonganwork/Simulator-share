"""Test tich hop that: mo mot uvicorn server that (cong rieng cho test), chay
producer that qua HTTP that trong vai giay - day chinh la con duong du lieu
Buoc 3 can chung minh (khong phai goi ham Python truc tiep). Giu ngan
(duration=2s) de bo test khong bi cham.
"""

from __future__ import annotations

import threading
import time

import httpx
import uvicorn

from lcx_core.telemetry_mock.producer import run_producer
from lcx_core.telemetry_mock.service import app

TEST_PORT = 8199


def _wait_healthy(base_url: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("service test khong san sang kip thoi")


def test_producer_sends_valid_traffic_over_real_http():
    base_url = f"http://127.0.0.1:{TEST_PORT}"
    config = uvicorn.Config(app, host="127.0.0.1", port=TEST_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_healthy(base_url)

        result = run_producer(
            base_url, n_drivers=3, duration_seconds=2.0, ping_interval_s=0.5, seed=99,
        )
        assert result["n_client_errors"] == 0
        assert result["n_ping_requests"] > 0

        stats = httpx.get(f"{base_url}/telemetry/stats", timeout=5.0).json()
        assert stats["n_rejected"] == 0
        assert stats["n_pings"] == result["n_ping_requests"]
        assert stats["ping_latency"]["p99_ms"] < 100  # rang buoc muc 03
    finally:
        server.should_exit = True
        thread.join(timeout=5)
