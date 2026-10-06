"""Nap va xac thuc payload theo dung 2 schema nhap cua Buoc 2
(schemas/telemetry/*.schema.json) - KHONG dung Pydantic model rieng de tranh
dinh nghia field o hai noi. Schema JSON la nguon su that DUY NHAT; service
(service.py) chi goi validate_ping/validate_event truoc khi luu.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "telemetry"


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    path = SCHEMA_DIR / f"{name}.schema.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_ping(payload: dict) -> None:
    """Raise jsonschema.ValidationError neu payload sai hop dong."""
    jsonschema.validate(payload, _load("vehicle_telemetry_ping"))


def validate_event(payload: dict) -> None:
    jsonschema.validate(payload, _load("trip_lifecycle_event"))


def validate_camera_event(payload: dict) -> None:
    """Hang muc 2/5 'Chien luoc lam giau du lieu' - noi luong camera_event vao
    cung service mock nay."""
    jsonschema.validate(payload, _load("camera_event"))


def validate_charging_session(payload: dict) -> None:
    """Hang muc 6/5 'Chien luoc lam giau du lieu' - noi luong charging_session_log
    vao cung service mock nay."""
    jsonschema.validate(payload, _load("charging_session_log"))
