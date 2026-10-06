"""Proprietary Solvers Stub (Excluded from Simulator Handoff Package).

NOTE: All optimization solvers (S1-S9: shift_dp, bonus_feasibility, idle_reduction,
capacity_alloc, etc.) are proprietary algorithms and are excluded from this
standalone environment simulation and data foundation repository.

The simulator operates as a pure Environment Digital Twin & Data Provider
under natural baseline mode (advice.enabled: false).
"""

def bonus_feasibility(*args, **kwargs):
    raise NotImplementedError("Solvers are excluded from the simulator package.")

def idle_reduction(*args, **kwargs):
    raise NotImplementedError("Solvers are excluded from the simulator package.")

def shift_dp(*args, **kwargs):
    raise NotImplementedError("Solvers are excluded from the simulator package.")

def capacity_alloc(*args, **kwargs):
    raise NotImplementedError("Solvers are excluded from the simulator package.")
