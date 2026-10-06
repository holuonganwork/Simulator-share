"""Fraud injector - xem injector.py cho tai lieu day du."""

from __future__ import annotations

from lcx_core.fraud_injector.injector import (
    DEFAULT_N_PER_SEVERITY,
    PATTERNS,
    SEVERITIES,
    SEVERITY_PARAMS,
    InjectedLabel,
    inject_all,
)
from lcx_core.fraud_injector.matching import flag_matches_label

__all__ = [
    "DEFAULT_N_PER_SEVERITY",
    "PATTERNS",
    "SEVERITIES",
    "SEVERITY_PARAMS",
    "InjectedLabel",
    "inject_all",
    "flag_matches_label",
]
