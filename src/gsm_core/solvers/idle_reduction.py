"""Stub for idle_reduction (Solvers excluded from public simulator package)."""

IDLE_TOTAL_ALERT_MIN = 45.0
IDLE_LONGEST_ALERT_MIN = 25.0
LOW_DEMAND_MAX = 0.5

def solve(*args, **kwargs):
    raise NotImplementedError("Solvers are excluded from the standalone simulator package.")
