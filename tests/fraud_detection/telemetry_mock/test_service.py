"""Test service.py qua FastAPI TestClient (khong can mo cong that)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lcx_core.telemetry_mock.service import app

VALID_PING = {
    "ping_id": "ping-1",
    "driver_id": "d-1",
    "trip_id": None,
    "source_device": "vehicle_tcu_car",
    "occurred_at": "2026-09-17T10:00:00+07:00",
    "lat": 21.01,
    "lng": 105.81,
    "current_hex_h3_res9": None,
    "speed_kmh": 20.0,
    "heading_deg": None,
    "vin": None, "gear": None, "odometer_km": None,
    "battery_level_pct": 80.0, "remaining_range_km": None, "charging_status": "DISCHARGING",
    "seat_occupancy": None, "door_lock_status": None, "trunk_status": None,
    "phone_connectivity_lost": False,
}

VALID_EVENT = {
    "event_id": "evt-1",
    "trip_id": "trip-1",
    "driver_id": "d-1",
    "customer_id": None,
    "event_type": "offer_received",
    "occurred_at": "2026-09-17T10:00:00+07:00",
    "offer_countdown_seconds": 15,
    "arrival_geofence_check_passed": None,
    "arrival_geofence_radius_m": None,
    "cancel_reason": None, "cancel_actor": None,
    "via_xanhnow": False, "xanhnow_otp_code_masked": None,
    "payment_method": None,
}


VALID_CAMERA_EVENT = {
    "event_id": "cam-1",
    "driver_id": "d-1",
    "trip_id": None,
    "occurred_at": "2026-09-17T10:00:00+07:00",
    "camera_event_type": "face_mismatch",
    "detected_face_matches_account": False,
    "detected_passenger_count": None,
    "seat_sensor_passenger_count": None,
    "confidence": 0.91,
    "source_device": "vehicle_tcu_car",
}

VALID_CHARGING_SESSION = {
    "session_id": "chg-1",
    "driver_id": "d-1",
    "vehicle_vin": None,
    "station_id": "station-dd-01",
    "started_at": "2026-09-17T10:00:00+07:00",
    "ended_at": "2026-09-17T10:03:00+07:00",
    "soc_before_pct": 15.0,
    "soc_after_pct": 97.0,
    "duration_seconds": 180,
    "cost_vnd": 25000,
    "status": "completed",
}


@pytest.fixture()
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_valid_ping_accepted_with_latency(client):
    r = client.post("/telemetry/gps-ping", json=VALID_PING)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["latency_ms"] >= 0


def test_invalid_ping_rejected_422(client):
    bad = dict(VALID_PING)
    del bad["speed_kmh"]  # truong bat buoc
    r = client.post("/telemetry/gps-ping", json=bad)
    assert r.status_code == 422


def test_valid_event_accepted(client):
    r = client.post("/telemetry/trip-event", json=VALID_EVENT)
    assert r.status_code == 200


def test_event_with_null_countdown_accepted_regression():
    """Hoi quy dung cho loi 'const:15' da phat hien - mot event KHAC
    offer_received (vd trip_started) voi offer_countdown_seconds=null phai
    duoc chap nhan, khong bi tu choi oan.
    """
    client = TestClient(app)
    event = dict(VALID_EVENT, event_type="trip_started", offer_countdown_seconds=None)
    r = client.post("/telemetry/trip-event", json=event)
    assert r.status_code == 200


def test_stats_reflects_ingested_traffic():
    client = TestClient(app)
    client.post("/telemetry/gps-ping", json=VALID_PING)
    client.post("/telemetry/trip-event", json=VALID_EVENT)
    stats = client.get("/telemetry/stats").json()
    assert stats["n_pings"] >= 1
    assert stats["n_events"] >= 1
    assert stats["ping_latency"]["p50_ms"] is not None


def test_valid_camera_event_accepted(client):
    r = client.post("/telemetry/camera-event", json=VALID_CAMERA_EVENT)
    assert r.status_code == 200


def test_invalid_camera_event_rejected_422(client):
    bad = dict(VALID_CAMERA_EVENT)
    del bad["camera_event_type"]
    r = client.post("/telemetry/camera-event", json=bad)
    assert r.status_code == 422


def test_valid_charging_session_accepted(client):
    r = client.post("/telemetry/charging-session", json=VALID_CHARGING_SESSION)
    assert r.status_code == 200


def test_stats_reflects_camera_and_charging_traffic():
    client = TestClient(app)
    client.post("/telemetry/camera-event", json=VALID_CAMERA_EVENT)
    client.post("/telemetry/charging-session", json=VALID_CHARGING_SESSION)
    stats = client.get("/telemetry/stats").json()
    assert stats["n_camera_events"] >= 1
    assert stats["n_charging_sessions"] >= 1
