"""Buoc 3 - service mock telemetry (docs/telemetry-research.md, muc 12.3 artifact
chien luoc). Day la mo phong mot API ingest THAT (POST /telemetry/gps-ping,
POST /telemetry/trip-event) thay vi tiep tuc doc Parquet tinh nhu
data/gsm_loader.py - dung de kiem tra rang buoc do tre <100ms/ping ma
docs/architecture.md dat ra cho Tier A (Buoc 4, chua lam - buoc nay chi dung
lai o "co ha tang de do", chua noi Tier A vao).

Chay doc lap:
    ./.venv/bin/uvicorn lcx_core.telemetry_mock.service:app --port 8077

Hoac dung scripts/run_telemetry_mock_demo.py de tu dong khoi dong + chay
producer + in bao cao trong 1 lenh.
"""

from __future__ import annotations

import time

import jsonschema
from fastapi import FastAPI, HTTPException

from lcx_core.telemetry_mock.schemas import (
    validate_camera_event,
    validate_charging_session,
    validate_event,
    validate_ping,
)
from lcx_core.telemetry_mock.store import TelemetryStore

app = FastAPI(
    title="LCX Telemetry Mock",
    description=(
        "Buoc 3 cua ke hoach mock telemetry - KHONG phai API that cua GSM/VinFast. "
        "Xem docs/telemetry-research.md."
    ),
)
store = TelemetryStore()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/telemetry/gps-ping")
def ingest_ping(payload: dict) -> dict:
    t0 = time.perf_counter()
    try:
        validate_ping(payload)
    except jsonschema.ValidationError as exc:
        store.record_rejected()
        raise HTTPException(status_code=422, detail=exc.message) from exc

    latency_ms = (time.perf_counter() - t0) * 1000
    store.add_ping(payload, latency_ms)
    return {"ok": True, "latency_ms": round(latency_ms, 3)}


@app.post("/telemetry/trip-event")
def ingest_event(payload: dict) -> dict:
    t0 = time.perf_counter()
    try:
        validate_event(payload)
    except jsonschema.ValidationError as exc:
        store.record_rejected()
        raise HTTPException(status_code=422, detail=exc.message) from exc

    latency_ms = (time.perf_counter() - t0) * 1000
    store.add_event(payload, latency_ms)
    return {"ok": True, "latency_ms": round(latency_ms, 3)}


@app.post("/telemetry/camera-event")
def ingest_camera_event(payload: dict) -> dict:
    t0 = time.perf_counter()
    try:
        validate_camera_event(payload)
    except jsonschema.ValidationError as exc:
        store.record_rejected()
        raise HTTPException(status_code=422, detail=exc.message) from exc

    latency_ms = (time.perf_counter() - t0) * 1000
    store.add_camera_event(payload, latency_ms)
    return {"ok": True, "latency_ms": round(latency_ms, 3)}


@app.post("/telemetry/charging-session")
def ingest_charging_session(payload: dict) -> dict:
    t0 = time.perf_counter()
    try:
        validate_charging_session(payload)
    except jsonschema.ValidationError as exc:
        store.record_rejected()
        raise HTTPException(status_code=422, detail=exc.message) from exc

    latency_ms = (time.perf_counter() - t0) * 1000
    store.add_charging_session(payload, latency_ms)
    return {"ok": True, "latency_ms": round(latency_ms, 3)}


@app.get("/telemetry/stats")
def stats() -> dict:
    """Xem docstring TelemetryStore.stats - day la so lieu Buoc 3 can bao cao
    theo dung tieu chi thoat muc 12.3: 'log lai do tre moi lan nhan ping'.
    """
    return store.stats()
