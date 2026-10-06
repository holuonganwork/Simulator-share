"""Luu tru va do do tre cho service mock telemetry (Buoc 3).

Trong bo doc luong THAT, day la vai tro cua mot message queue/feature store
(xem docs/architecture.md - "GsmDataStore" cho du lieu tinh, con o day la du
lieu SONG). Voi quy mo demo/nghien cuu, mot buffer trong bo nho + ghi JSONL la
du - muc tieu chinh cua Buoc 3 la CHUNG MINH duoc do tre ingest, khong phai
xay mot he thong nhan tin that.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

VAR_DIR = Path(__file__).resolve().parents[3] / "var" / "telemetry"
PINGS_FILE = VAR_DIR / "pings.jsonl"
EVENTS_FILE = VAR_DIR / "events.jsonl"
CAMERA_EVENTS_FILE = VAR_DIR / "camera_events.jsonl"
CHARGING_SESSIONS_FILE = VAR_DIR / "charging_sessions.jsonl"

MAX_LATENCY_SAMPLES = 5000  # tranh phinh bo nho vo han khi chay lau


@dataclass
class LatencyTracker:
    """Giu toi da MAX_LATENCY_SAMPLES gia tri gan nhat (giay giu deque, khong
    phai vi hieu nang ma vi day la con so THAT muon bao cao, khong phai uoc
    luong tren toan bo lich su vo han).
    """

    samples: deque[float] = field(default_factory=lambda: deque(maxlen=MAX_LATENCY_SAMPLES))

    def record(self, latency_ms: float) -> None:
        self.samples.append(latency_ms)

    def percentile(self, p: float) -> float | None:
        if not self.samples:
            return None
        ordered = sorted(self.samples)
        idx = min(int(len(ordered) * p), len(ordered) - 1)
        return ordered[idx]

    def summary(self) -> dict:
        return {
            "count": len(self.samples),
            "p50_ms": self.percentile(0.50),
            "p95_ms": self.percentile(0.95),
            "p99_ms": self.percentile(0.99),
            "max_ms": max(self.samples) if self.samples else None,
        }


class TelemetryStore:
    """Singleton-per-process. Thread-safe o muc du dung cho uvicorn 1 worker."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.ping_latency = LatencyTracker()
        self.event_latency = LatencyTracker()
        self.camera_event_latency = LatencyTracker()
        self.charging_session_latency = LatencyTracker()
        self.n_pings = 0
        self.n_events = 0
        self.n_camera_events = 0
        self.n_charging_sessions = 0
        self.n_rejected = 0
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        # "w" thay vi "a": moi lan khoi dong service la MOT PHIEN mock moi,
        # khong tron du lieu demo cu voi phien dang chay (khac voi label_store
        # that o labels/label_store.py - noi lich su case can giu vinh vien).
        self._pings_fh = open(PINGS_FILE, "w", encoding="utf-8")
        self._events_fh = open(EVENTS_FILE, "w", encoding="utf-8")
        self._camera_events_fh = open(CAMERA_EVENTS_FILE, "w", encoding="utf-8")
        self._charging_sessions_fh = open(CHARGING_SESSIONS_FILE, "w", encoding="utf-8")

    def add_ping(self, payload: dict, latency_ms: float) -> None:
        with self._lock:
            self.n_pings += 1
            self.ping_latency.record(latency_ms)
            self._pings_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._pings_fh.flush()

    def add_event(self, payload: dict, latency_ms: float) -> None:
        with self._lock:
            self.n_events += 1
            self.event_latency.record(latency_ms)
            self._events_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._events_fh.flush()

    def add_camera_event(self, payload: dict, latency_ms: float) -> None:
        with self._lock:
            self.n_camera_events += 1
            self.camera_event_latency.record(latency_ms)
            self._camera_events_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._camera_events_fh.flush()

    def add_charging_session(self, payload: dict, latency_ms: float) -> None:
        with self._lock:
            self.n_charging_sessions += 1
            self.charging_session_latency.record(latency_ms)
            self._charging_sessions_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._charging_sessions_fh.flush()

    def record_rejected(self) -> None:
        with self._lock:
            self.n_rejected += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                "n_pings": self.n_pings,
                "n_events": self.n_events,
                "n_camera_events": self.n_camera_events,
                "n_charging_sessions": self.n_charging_sessions,
                "n_rejected": self.n_rejected,
                "ping_latency": self.ping_latency.summary(),
                "event_latency": self.event_latency.summary(),
                "camera_event_latency": self.camera_event_latency.summary(),
                "charging_session_latency": self.charging_session_latency.summary(),
            }


def now_ms() -> float:
    return time.perf_counter() * 1000
