"""Build one evidence-gated opening brief from the start-of-shift S2 plan and SOC.

The brief does not run a solver and does not read the future.  S1 policy/bonus context is
included when it already exists, but it cannot be a hard gate: simulation evidence shows
S1 is often first produced hours after ``go_online``.  Requiring it made a start-of-shift
brief structurally impossible.  Historical same-hour advice remains absent until a
trusted intraday baseline exists.
"""

from __future__ import annotations

from datetime import datetime
import hashlib

from gsm_core.advisor.checkpoint_intents import SignalObservation, enrich_candidate_intent
from gsm_core.lifecycle.checkpoint import checkpoint_fingerprint


def _at_or_before(record: dict, at_iso: str) -> bool:
    try:
        return datetime.fromisoformat(record["created_at"]) <= datetime.fromisoformat(at_iso)
    except (KeyError, TypeError, ValueError):
        return False


def _snapshot_soc(snapshot_ref: str | None, artifacts: list[dict]) -> tuple[float, str] | None:
    artifact = next((item for item in artifacts
                     if item.get("artifact_id") == snapshot_ref), None)
    payload = (artifact or {}).get("payload") or {}
    value = payload.get("soc_pct")
    if value is None:
        return None
    return float(value), str(snapshot_ref)


def opening_brief_candidate(*, driver_id: str, checkpoints: list[dict],
                            artifacts: list[dict], at_iso: str) -> dict | None:
    """Return the one start-of-shift brief candidate, or ``None`` if evidence is incomplete."""
    owned = [record for record in checkpoints if record.get("driver_id") == driver_id]
    s1_records = [record for record in owned
                  if "S1" in (record.get("solver_set") or ())
                  and _at_or_before(record, at_iso)]
    s2_records = [record for record in owned
                  if "S2" in (record.get("solver_set") or ())
                  and _at_or_before(record, at_iso)]
    selected_s2 = None
    for right in sorted(s2_records, key=lambda record: record["created_at"]):
        boundary = (right.get("validity") or {}).get("valid_until")
        try:
            record_is_live = datetime.fromisoformat(boundary) > datetime.fromisoformat(at_iso)
        except (TypeError, ValueError):
            record_is_live = False
        if record_is_live and _snapshot_soc(right.get("snapshot_ref"), artifacts) is not None:
            selected_s2 = right
            break
    if selected_s2 is None:
        return None
    s2 = selected_s2
    live_s1 = []
    for record in s1_records:
        boundary = (record.get("validity") or {}).get("valid_until")
        try:
            if datetime.fromisoformat(boundary) > datetime.fromisoformat(at_iso):
                live_s1.append(record)
        except (TypeError, ValueError):
            continue
    s1 = max(live_s1, key=lambda record: record["created_at"]) if live_s1 else None
    valid_until = (s2.get("validity") or {}).get("valid_until")
    soc = _snapshot_soc(s2.get("snapshot_ref"), artifacts)
    assert soc is not None

    created_at = s2["created_at"]
    policy_caveat = ("Mốc thưởng đã có kết luận S1 tại đầu ca."
                     if s1 is not None
                     else "Mốc thưởng chưa có kết luận S1 tại thời điểm bắt đầu ca.")
    candidate = {
        "driver_id": driver_id,
        "topic": "shift_timing",
        "surface": "brief",
        "trigger_type": "state_change",
        "current_action": s2.get("current_action"),
        "future_plan": list(s2.get("future_plan") or []),
        "action_window": s2.get("action_window"),
        "validity": {
            "valid_from": created_at,
            "valid_until": valid_until,
            "freshness_deadline": valid_until,
        },
        # This is not emergency urgency; the band ensures a one-time opening brief can
        # merge the simultaneous S1 checkpoint instead of being silently displaced by it.
        "urgency_band": "high",
        "material_revision": "1",
        "reason_code": "idea.IC-01",
        "numbers": [{"key": "soc_pct", "value": round(soc[0], 1), "unit": "%",
                     "source": "sim.observer_snapshot"}],
        "caveats": [
            "Brief này chỉ dùng dữ kiện ca hiện tại.",
            policy_caveat,
            "Chưa dùng so sánh cùng khung giờ của các ngày trước khi chưa có baseline intraday đủ chuẩn.",
        ],
        "confidence": min(0.9, 0.8 if s2.get("confidence_band") == "high" else 0.65),
        "confidence_band": "medium",
        "snapshot_ref": s2["snapshot_ref"],
        "solver_artifact_ref": s2["solver_artifact_ref"],
        "source_decision_id": f"opening:{s2.get('source_decision_id')}",
        "run_id": s2.get("run_id") or (s1 or {}).get("run_id"),
        "solver_input_refs": list(dict.fromkeys([
            *((s1 or {}).get("solver_input_refs") or ()), *(s2.get("solver_input_refs") or ())])),
        "solver_report_refs": list(dict.fromkeys([
            *((s1 or {}).get("solver_report_refs") or ()), *(s2.get("solver_report_refs") or ())])),
        "solver_set": (["S1", "S2"] if s1 is not None else ["S2"]),
        "solver_status": "optimal",
        "maintenance": False,
        "data_mode": s2.get("data_mode") or "synthetic",
        "is_mock": bool((s1 or {}).get("is_mock") or s2.get("is_mock")),
        "created_at": created_at,
        "source_episode_id": None,
        "episode_type": None,
        "decision_key": f"{driver_id}:shift:{created_at}:opening_brief",
        "evidence_refs": list(dict.fromkeys([
            ref for ref in (
                soc[1], (s1 or {}).get("checkpoint_id"), s2["checkpoint_id"],
                *(s1 or {}).get("evidence_refs", ()),
                *s2.get("evidence_refs", ()),
            ) if ref
        ])),
    }
    available = ["plan", "energy", *( ["policy"] if s1 is not None else [])]
    candidate = enrich_candidate_intent(candidate, observations={
        name: SignalObservation(name) for name in available})
    candidate["fingerprint"] = checkpoint_fingerprint(candidate)
    identity = hashlib.sha256(
        f"{candidate['decision_key']}:{candidate['fingerprint']}".encode("utf-8")
    ).hexdigest()[:24]
    candidate["checkpoint_id"] = f"ckpt-{identity}"
    return candidate
