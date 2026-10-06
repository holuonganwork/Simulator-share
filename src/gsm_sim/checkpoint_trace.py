"""AdviceCheckpoint trace helpers for simulator runs.

The helpers in this module are deliberately side-effect free during the world
tick.  Segment identities are attached after a run and trace records are only
written when an explicit export is requested.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from gsm_core.lifecycle.checkpoint import (
    checkpoint_record,
    evaluate_checkpoint,
    normalize_solver_decision,
)
from gsm_core.lifecycle.checkpoint_store import (
    InMemoryCheckpointJournal,
    build_artifact_record,
)


_BASE_DATE = date(2026, 7, 1)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _iso(minute: float) -> str:
    whole_minute = int(minute)
    day = _BASE_DATE + timedelta(days=whole_minute // 1440)
    minute_of_day = whole_minute % 1440
    return (f"{day.isoformat()}T{minute_of_day // 60:02d}:"
            f"{minute_of_day % 60:02d}:00+07:00")


def _state_value(value: Any) -> Any:
    return getattr(value, "value", value)


def actor_snapshot(actor: Any, now_min: float, run_id: str,
                   validity_hints: dict[str, float] | None = None) -> dict[str, Any]:
    """Return the auditable, non-PII state visible to a simulator solver.

    ``validity_hints`` mang các boundary THẬT của producer theo phút sim:
    ``freshness_deadline_min`` (chu kỳ refresh thật của producer — vd interval consult),
    ``bucket_end_min`` (S2), ``rest_window_end_min`` (S7), ``allocation_bucket_end_min``
    (S4). Trước UPDATE-147 freshness bị bịa ``now+1`` ⇒ mọi checkpoint chỉ sống 1 phút
    và ~40% card chết `expired` tại presentation (đo funnel seed 1000). Fallback +1'
    chỉ còn cho fixture cũ không truyền hints.
    """
    hints = validity_hints or {}
    observed_at = _iso(now_min)
    snapshot: dict[str, Any] = {
        "run_id": run_id,
        "driver_id": f"d-{actor.actor_id}",
        "now_min": float(now_min),
        "observed_at": observed_at,
        "freshness_deadline": _iso(hints.get("freshness_deadline_min", now_min + 1)),
        "actor_state": _state_value(getattr(actor, "state", None)),
        "zone_h3": getattr(actor, "cell", None),
        "soc_pct": getattr(actor, "soc_pct", None),
        "points": getattr(actor, "points", 0),
        "orders_offered": getattr(actor, "orders_offered", 0),
        "orders_accepted": getattr(actor, "orders_accepted", 0),
        "orders_completed": getattr(actor, "orders_completed", 0),
        "online_min": getattr(actor, "online_min", 0.0),
        "rest_taken_min": getattr(actor, "rest_min", 0.0),
        "shift_start_min": getattr(actor, "shift_start_min", None),
        "shift_end_min": getattr(actor, "shift_end_min", None),
        "shift_end": _iso(getattr(actor, "shift_end_min", now_min)),
        "source": "SIMULATOR",
        "data_mode": "synthetic",
        "is_mock": True,
    }
    for hint_key, snapshot_key in (("bucket_end_min", "bucket_end"),
                                   ("rest_window_end_min", "rest_window_end"),
                                   ("allocation_bucket_end_min", "allocation_bucket_end")):
        if hints.get(hint_key) is not None:
            snapshot[snapshot_key] = _iso(float(hints[hint_key]))
    return snapshot


def annotate_segment_ids(run_id: str, segments: Iterable[dict]) -> list[dict]:
    """Copy segments and attach stable identities without consuming sim RNG."""
    annotated: list[dict] = []
    for ordinal, original in enumerate(segments):
        segment = deepcopy(original)
        identity_content = {k: v for k, v in segment.items() if k != "segment_id"}
        identity = {
            "run_id": run_id,
            "actor_id": segment.get("actor_id"),
            "ordinal": ordinal,
            "content": identity_content,
        }
        segment["segment_id"] = "seg-" + _digest(identity).split(":", 1)[1][:24]
        annotated.append(segment)
    return annotated


def _lifecycle_event(checkpoint: dict, event_type: str, occurred_at: str,
                     *, reason: str | None = None, payload: dict | None = None,
                     relation_type: str | None = None,
                     confidence: float | None = None) -> dict:
    material = {
        "checkpoint_id": checkpoint["checkpoint_id"],
        "event_type": event_type,
        "occurred_at": occurred_at,
        "reason": reason,
        "payload": payload or {},
    }
    event_id = f"{event_type}:" + _digest(material).split(":", 1)[1][:24]
    return {
        "schema_version": "1.1.0",
        "event_id": event_id,
        "checkpoint_id": checkpoint["checkpoint_id"],
        "driver_id": checkpoint["driver_id"],
        "display_id": None,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "actor": "observer" if event_type == "execution_observed" else "advisor",
        "origin": "simulator",
        "reason_code": reason,
        "relation_type": relation_type,
        "confidence": confidence,
        "payload": payload or {},
    }


class CheckpointTraceSink:
    """RAM-only sink attached after existing solver calls.

    It never invokes a solver and owns no simulation RNG.  Repeated material
    recommendations are deduplicated by checkpoint identity, while the first
    exact snapshot/input/report bundle remains immutable and replayable.
    """

    def __init__(self, *, enabled: bool, run_id: str):
        self.enabled = bool(enabled)
        self.run_id = run_id
        self._journal = InMemoryCheckpointJournal()
        self._pending: list[dict[str, Any]] = []
        self._execution_links: list[dict[str, Any]] = []

    @property
    def artifacts(self) -> list[dict]:
        return self._journal.artifacts()

    @property
    def checkpoints(self) -> list[dict]:
        return self._journal.checkpoints()

    @property
    def events(self) -> list[dict]:
        return self._journal.all_events()

    @property
    def pending_execution_observations(self) -> list[dict]:
        return [dict(item) for item in self._pending]

    @property
    def execution_links(self) -> list[dict]:
        return [dict(item) for item in self._execution_links]

    def capture(self, solver_name: str, actor: Any, now_min: float,
                solver_input: dict, solver_report: dict,
                source_decision_id: str,
                validity_hints: dict[str, float] | None = None) -> str | None:
        if not self.enabled:
            return None
        snapshot = actor_snapshot(actor, now_min, self.run_id, validity_hints)
        created_at = snapshot["observed_at"]
        artifacts = [
            build_artifact_record("state_snapshot", snapshot,
                                  created_at=created_at),
            build_artifact_record("solver_input", deepcopy(solver_input),
                                  created_at=created_at),
            build_artifact_record("solver_report", deepcopy(solver_report),
                                  created_at=created_at),
        ]
        solver_artifact = build_artifact_record("solver_artifact", {
            "solver_name": str(solver_name).upper(),
            "solver_input_ref": artifacts[1]["artifact_id"],
            "solver_report_ref": artifacts[2]["artifact_id"],
        }, created_at=created_at)
        artifacts.append(solver_artifact)

        candidate = normalize_solver_decision(
            solver_name, snapshot, solver_input, solver_report, source_decision_id)
        candidate["snapshot_ref"] = artifacts[0]["artifact_id"]
        candidate["solver_input_refs"] = [artifacts[1]["artifact_id"]]
        candidate["solver_report_refs"] = [artifacts[2]["artifact_id"]]
        candidate["solver_artifact_ref"] = solver_artifact["artifact_id"]
        checkpoint = checkpoint_record(candidate)

        # Same material checkpoint is intentionally one immutable record.  A
        # later poll must not rewrite its trace refs or lifecycle.
        if self._journal.checkpoint(checkpoint["checkpoint_id"]) is not None:
            return checkpoint["checkpoint_id"]

        self._journal.create_checkpoint_bundle(artifacts, checkpoint)
        policy = evaluate_checkpoint(candidate, [], {}, created_at, is_driving=False)
        event = _lifecycle_event(
            checkpoint, policy.verdict, created_at, reason=policy.reason)
        self._journal.append_event(event)
        current = checkpoint.get("current_action") or {}
        # Execution matching is meaningful only for advice that could actually be
        # presented and asks for a change.  ONLINE/NO_ACTION are maintenance states; a
        # later ``go_online`` event is not evidence that the driver followed a card.
        if (policy.verdict in {"ready", "queued"}
                and current.get("code") not in {None, "ONLINE", "NO_ACTION"}):
            self._pending.append({
                "checkpoint_id": checkpoint["checkpoint_id"],
                "driver_id": checkpoint["driver_id"],
                "actor_id": int(getattr(actor, "actor_id")),
                "solver_name": str(solver_name).upper(),
                "action": current.get("code"),
                "observed_min": float(now_min),
                "source_decision_id": source_decision_id,
                "run_id": self.run_id,
            })
        return checkpoint["checkpoint_id"]

    def finalize_execution_links(self, segments: list[dict],
                                 legacy_events: Iterable[Any]) -> list[dict]:
        """Match observations after dynamics are fixed; never infer acceptance."""
        if not self.enabled:
            return []
        for pending in self._pending:
            observation = self._match_observation(pending, segments, legacy_events)
            if observation is None:
                continue
            identity = {
                "checkpoint_id": pending["checkpoint_id"],
                "source_decision_id": pending["source_decision_id"],
                **observation,
            }
            link_id = "exec-" + _digest(identity).split(":", 1)[1][:24]
            link = {
                "execution_link_id": link_id,
                "checkpoint_id": pending["checkpoint_id"],
                "source_decision_id": pending["source_decision_id"],
                "segment_id": observation.get("segment_id"),
                "observed_event_id": observation.get("observed_event_id"),
                "relation_type": "coincident",
                "confidence": 0.6,
            }
            self._execution_links.append(link)
            checkpoint = self._journal.checkpoint(pending["checkpoint_id"])
            assert checkpoint is not None
            occurred_at = _iso(float(observation["observed_min"]))
            self._journal.append_event(_lifecycle_event(
                checkpoint, "execution_observed", occurred_at,
                payload={"execution_link_id": link_id,
                         "segment_id": observation.get("segment_id"),
                         "observed_event_id": observation.get("observed_event_id")},
                relation_type="coincident", confidence=0.6))
        return self.execution_links

    @staticmethod
    def _match_observation(pending: dict, segments: list[dict],
                           legacy_events: Iterable[Any]) -> dict | None:
        action = pending["action"]
        actor_id = pending["actor_id"]
        observed = pending["observed_min"]
        segment_kinds = {
            "REST": {"rest"},
            "SWAP": {"charge"},
            "REPOSITION_SIM_ONLY": {"relocate"},
        }.get(action, set())
        for segment in segments:
            if (segment.get("actor_id") == actor_id
                    and segment.get("kind") in segment_kinds
                    and float(segment.get("t0", -1)) >= observed):
                if (action == "REPOSITION_SIM_ONLY"
                        and segment.get("reason") != "standby"):
                    continue
                return {"segment_id": segment.get("segment_id"),
                        "observed_event_id": None,
                        "observed_min": float(segment.get("t0", observed))}

        event_kinds = {
            "PROTECT_ELIGIBILITY": {"advice_bonus_gate"},
            "END": {"end_shift"},
            "EXTEND": {"advice_shift_extend"},
            "REST": {"advice_rest_window"},
        }.get(action, set())
        for ordinal, event in enumerate(legacy_events):
            event_actor = getattr(event, "actor_id", None)
            event_kind = getattr(event, "kind", None)
            event_min = float(getattr(event, "t_min", -1))
            if event_actor == actor_id and event_kind in event_kinds and event_min >= observed:
                event_id = "sim-event-" + _digest({
                    "run_id": pending.get("run_id"), "ordinal": ordinal,
                    "actor_id": event_actor, "kind": event_kind,
                    "t_min": event_min,
                }).split(":", 1)[1][:24]
                return {"segment_id": None, "observed_event_id": event_id,
                        "observed_min": event_min}
        return None


def _jsonl_bytes(records: Iterable[dict]) -> tuple[bytes, int]:
    rows = [_canonical_json(record) for record in records]
    payload = (("\n".join(rows) + "\n") if rows else "").encode("utf-8")
    return payload, len(rows)


def export_checkpoint_trace(result: Any, output_dir: str | Path) -> dict[str, Any]:
    """Export post-run trace collections plus count/digest manifest."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    collections = {
        "advice_artifacts.jsonl": getattr(result, "advice_artifacts", []),
        "advice_checkpoints.jsonl": getattr(result, "advice_checkpoints", []),
        "advice_checkpoint_events.jsonl": getattr(
            result, "advice_checkpoint_events", []),
        "execution_links.jsonl": getattr(result, "execution_links", []),
    }
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "generated_at": None,
        "files": {},
    }
    for filename, records in collections.items():
        payload, count = _jsonl_bytes(records)
        (destination / filename).write_bytes(payload)
        manifest["files"][filename] = {
            "count": count,
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
    (destination / "checkpoint_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def evaluate_presenters_post_run(result: Any, presenters: dict[str, Any]) -> dict[str, list[dict]]:
    """Evaluate multiple presenters after the trajectory is fixed."""
    artifact_by_id = {artifact["artifact_id"]: artifact
                      for artifact in getattr(result, "advice_artifacts", [])}
    outputs: dict[str, list[dict]] = {name: [] for name in presenters}
    for checkpoint in getattr(result, "advice_checkpoints", []):
        report_ref = (checkpoint.get("solver_report_refs") or [None])[0]
        report = (artifact_by_id.get(report_ref) or {}).get("payload") or {}
        numbers = [{"id": f"N{index + 1}", **number}
                   for index, number in enumerate(report.get("numbers") or [])]
        facts = [{"id": "F1", "value": str(
            checkpoint.get("reason_code") or "solver recommendation").replace("_", " ")}]
        caveats = [{"id": f"C{index + 1}", "value": value}
                   for index, value in enumerate(report.get("caveats") or [])]
        for name, presenter in presenters.items():
            rendered = presenter.present(
                checkpoint, facts=facts, numbers=numbers, caveats=caveats)
            outputs[name].append({"checkpoint_id": checkpoint["checkpoint_id"],
                                  **asdict(rendered)})
    return outputs


def checkpoint_metrics(result: Any) -> dict[str, Any]:
    """Report checkpoint and legacy adherence units without conflating them."""
    checkpoints = getattr(result, "advice_checkpoints", [])
    checkpoint_events = getattr(result, "advice_checkpoint_events", [])
    links = getattr(result, "execution_links", [])
    event_types: dict[str, list[str]] = {}
    for event in checkpoint_events:
        event_types.setdefault(event["checkpoint_id"], []).append(event["event_type"])
    offered = {checkpoint_id for checkpoint_id, kinds in event_types.items()
               if "offered" in kinds}
    accepted = {checkpoint_id for checkpoint_id, kinds in event_types.items()
                if "accepted" in kinds}
    executed = {link["checkpoint_id"] for link in links}

    legacy_events = getattr(result, "events", [])
    decision_adherence = None
    event_adherence = None
    if legacy_events:
        from gsm_core.lifecycle.projections import adherence_view, sim_events_to_lifecycle
        view = adherence_view(sim_events_to_lifecycle(legacy_events))
        decided = sum(row["decided"] for row in view.values())
        followed = sum(row["followed"] for row in view.values())
        event_decided = sum(row["event_decided"] for row in view.values())
        event_followed = sum(row["event_followed"] for row in view.values())
        decision_adherence = followed / decided if decided else None
        event_adherence = event_followed / event_decided if event_decided else None

    relations: dict[str, int] = {}
    confidences = []
    for link in links:
        relation = link.get("relation_type") or "unknown"
        relations[relation] = relations.get(relation, 0) + 1
        if link.get("confidence") is not None:
            confidences.append(float(link["confidence"]))
    counts = {name: sum(name in kinds for kinds in event_types.values())
              for name in ("queued", "expired", "superseded")}
    counts["duplicate"] = sum(
        event.get("reason_code") == "duplicate" for event in checkpoint_events)
    return {
        "candidate_volume": len(checkpoints),
        "checkpoint_volume": len(checkpoints),
        **counts,
        "retained_expected_impact": None,
        "decision_adherence": decision_adherence,
        "event_adherence": event_adherence,
        "accept_rate": len(accepted) / len(offered) if offered else None,
        "execution_rate": len(executed) / len(offered) if offered else None,
        "execution_relation": relations,
        "execution_confidence": (sum(confidences) / len(confidences)
                                 if confidences else None),
    }
