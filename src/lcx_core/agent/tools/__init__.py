"""12 tool cua agent (docs/strategy.md muc 06) - moi tool la 1 module co ham run()."""

from __future__ import annotations

from lcx_core.agent.tools import (
    apply_action,
    check_location_integrity,
    compare_route_to_optimal,
    flag_case,
    get_driver_risk_profile,
    get_incentive_activity,
    map_match_trace,
    notify_driver,
    pull_evidence_bundle,
    query_device_account_graph,
    query_policy,
    resolve_case,
)

TOOLS = {
    "get_driver_risk_profile": get_driver_risk_profile.run,
    "check_location_integrity": check_location_integrity.run,
    "compare_route_to_optimal": compare_route_to_optimal.run,
    "map_match_trace": map_match_trace.run,
    "query_device_account_graph": query_device_account_graph.run,
    "get_incentive_activity": get_incentive_activity.run,
    "query_policy": query_policy.query_policy,
    "pull_evidence_bundle": pull_evidence_bundle.run,
    "flag_case": flag_case.run,
    "resolve_case": resolve_case.run,
    "notify_driver": notify_driver.run,
    "apply_action": apply_action.run,
}

__all__ = ["TOOLS"]
