#!/usr/bin/env python3
"""Buoc 3 - demo 1 lenh: khoi dong service mock telemetry, chay producer,
in bao cao do tre. Day la bang chung cho tieu chi thoat muc 12.3: "service
mock chay duoc, log lai do tre moi lan nhan ping".

Dung:
    ./.venv/bin/python3 scripts/run_telemetry_mock_demo.py \
        --n-drivers 30 --duration 30 --port 8077
"""

from __future__ import annotations

import argparse
import json
import threading
import time

import httpx
import uvicorn

from lcx_core.telemetry_mock.producer import run_producer
from lcx_core.telemetry_mock.service import app


def _wait_healthy(base_url: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=0.5)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Service khong san sang sau {timeout_s}s tai {base_url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-drivers", type=int, default=30)
    parser.add_argument("--duration", type=float, default=30.0, help="giay")
    parser.add_argument("--ping-interval", type=float, default=4.0, help="giay")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_healthy(base_url)
        print(f"[OK] Service mock telemetry dang chay tai {base_url}")
        print(f"Chay producer: {args.n_drivers} tai xe ao, {args.duration}s, ping moi {args.ping_interval}s...")

        t0 = time.monotonic()
        client_summary = run_producer(
            base_url,
            n_drivers=args.n_drivers,
            duration_seconds=args.duration,
            ping_interval_s=args.ping_interval,
            seed=args.seed,
        )
        elapsed = time.monotonic() - t0

        server_stats = httpx.get(f"{base_url}/telemetry/stats", timeout=5.0).json()

        print(f"\n=== Tom tat client (chay trong {elapsed:.1f}s) ===")
        print(json.dumps(client_summary, indent=2, ensure_ascii=False))

        print("\n=== Do tre server-side (tieu chi thoat Buoc 3) ===")
        print(json.dumps(server_stats, indent=2, ensure_ascii=False))

        ping_p99 = (server_stats.get("ping_latency") or {}).get("p99_ms")
        if ping_p99 is not None:
            verdict = "DAT" if ping_p99 < 100 else "CHUA DAT"
            print(f"\nRang buoc <100ms/ping (muc 03 chien luoc): p99={ping_p99:.2f}ms -> {verdict}")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
