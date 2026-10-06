"""Vong lap producer - goi drivers.tick() cho N tai xe ao, POST ket qua len
service mock (service.py) qua HTTP that (dung httpx) - day la diem mau chot
cua Buoc 3: mo phong dung duong di cua du lieu (network call that, co do tre
that), khong phai goi ham Python truc tiep.

Quan the tai xe: 100% Taxi Co Huu (o to dien VinFast, TCU vehicle_tcu_car).
"""

from __future__ import annotations

import random
import time

import httpx

from lcx_core.telemetry_mock.drivers import make_driver, tick

DEFAULT_PING_INTERVAL_S = 4.0  # giua [3,5] theo configs/telemetry_mock.yaml


def _driver_universe(n_drivers: int) -> list[str]:
    return [f"mock-d-{i}" for i in range(n_drivers)]


def _hex_universe_from_osrm() -> list[str] | None:
    """Tai su dung dung 316 o hex that trong ma tran OSRM cua GSM_SIMULATOR
    (research/simulation/data/osrm_matrix_dd.parquet) - dung tinh than muc
    12.3 "tai su dung toa do H3 va ma tran OSRM da co san". Tra ve None neu
    khong tim thay GSM_SIMULATOR (vd chay o may khac) - fallback duoc xu ly o
    noi goi.
    """
    try:
        from lcx_core.data.gsm_loader import load_data_store

        store = load_data_store()
        cells = store.osrm_matrix["cell_from"].unique().to_list()
        return cells if cells else None
    except Exception:
        return None


_FALLBACK_HEX_UNIVERSE = [
    "89415cb4883ffff", "89415cb48c7ffff", "89415cb4977ffff", "89415cb492bffff",
    "89415cb4c13ffff", "89415cb488fffff", "89415cb4c2bffff", "89415cb4c23ffff",
]


def run_producer(
    base_url: str,
    n_drivers: int = 30,
    duration_seconds: float = 30.0,
    ping_interval_s: float = DEFAULT_PING_INTERVAL_S,
    seed: int | None = 42,
) -> dict:
    """Chay producer trong `duration_seconds`, POST pings/events len `base_url`.
    Tra ve tom tat client-side (so request, so loi) - so lieu do tre server-side
    lay tu GET {base_url}/telemetry/stats sau khi ham nay ket thuc.
    """
    rng = random.Random(seed)
    universe = _hex_universe_from_osrm() or _FALLBACK_HEX_UNIVERSE
    station_hex = rng.choice(universe)  # 1 tram V-Green mo phong

    driver_ids = _driver_universe(n_drivers)
    drivers = [
        make_driver(driver_id=did, source_device="vehicle_tcu_car", start_hex=rng.choice(universe))
        for did in driver_ids
    ]

    n_ping_requests = 0
    n_event_requests = 0
    n_client_errors = 0

    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        t_end = time.monotonic() + duration_seconds
        while time.monotonic() < t_end:
            for driver in drivers:
                pings, events = tick(driver, universe, station_hex, rng)
                for ping in pings:
                    n_ping_requests += 1
                    try:
                        client.post("/telemetry/gps-ping", json=ping)
                    except httpx.HTTPError:
                        n_client_errors += 1
                for event in events:
                    n_event_requests += 1
                    try:
                        client.post("/telemetry/trip-event", json=event)
                    except httpx.HTTPError:
                        n_client_errors += 1
            time.sleep(ping_interval_s)

    return {
        "n_drivers": n_drivers,
        "n_cars": n_drivers,
        "n_ping_requests": n_ping_requests,
        "n_event_requests": n_event_requests,
        "n_client_errors": n_client_errors,
        "station_hex": station_hex,
    }
