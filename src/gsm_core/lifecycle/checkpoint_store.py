"""SQLite append-only store for AdviceCheckpoint shadow lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gsm_core.lifecycle.checkpoint import project_checkpoint_events
from gsm_core.schema_registry import SchemaRegistry


def _registry() -> SchemaRegistry:
    return SchemaRegistry(Path(__file__).resolve().parents[3] / "schemas")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def build_artifact_record(artifact_type: str, payload: dict,
                          data_mode: str = "synthetic", is_mock: bool = True,
                          created_at: str | None = None) -> dict:
    """Build one content-addressed artifact record without persisting it."""
    canonical = _json(payload)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema_version": "1.1.0",
        "artifact_id": f"{artifact_type}:{digest}",
        "digest": digest,
        "artifact_type": artifact_type,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "data_mode": data_mode,
        "is_mock": is_mock,
        "payload": payload,
    }


def _created_event(checkpoint: dict) -> dict:
    return {
        # The event is its own entity and carries its own schema version, which must not be
        # taken from the checkpoint record's version or validation fails loudly.
        "schema_version": "1.1.0",
        "event_id": f"created:{checkpoint['checkpoint_id']}",
        "checkpoint_id": checkpoint["checkpoint_id"],
        "driver_id": checkpoint["driver_id"],
        "display_id": None,
        "event_type": "created",
        "occurred_at": checkpoint["created_at"],
        "actor": "system",
        "origin": "checkpoint",
        "reason_code": None,
        "relation_type": None,
        "confidence": None,
        "payload": {},
    }


def _la_postgres(db_path) -> bool:
    """The Postgres path, taken when the path itself is a
    connection string or the shared environment variable is set.

    The environment is read here rather than at every call site because a dozen callers pass a file
    path and neither know nor need to know which dialect is running.
    """
    from gsm_core.agent.sql_backend import is_dsn

    return is_dsn(str(db_path)) or bool(os.getenv("AGENT_STORE_DSN", "").strip())


class CheckpointStore:
    """Immutable checkpoint records plus append-only lifecycle events.

    No update/delete methods are intentionally exposed.  Replacing a material
    recommendation is represented by a new event (`superseded`) and a new
    checkpoint, never by mutating an old record.
    """

    def __init__(self, db_path: str | Path, *, ns: str = ""):
        """The namespace isolates one replay session when running
        on PostgreSQL.

        On SQLite the namespace is recorded but unused, deliberately: each session is already its
        own file, so isolation comes from the filesystem. Two different mechanisms, one invariant,
        and a test guards them against drifting apart.

        On Postgres the namespace is set as a session variable. Every table's namespace column
        defaults to it and the row-level security policy filters on it, so not one SQL statement in
        this file has to change, including every insert.
        """
        self.ns = str(ns or "")
        self._la_pg = _la_postgres(db_path)
        if self._la_pg:
            from gsm_core.agent.sql_backend import backend_for

            # Rows are read positionally here, because this file was written for the
            # embedded database, whose driver returns tuples. The agent store reads by name.
            self.db = backend_for(db_path, dict_rows=False)
            # No schema or table creation here: the application role deliberately has no
            # such rights, and an application process creating tables on a shared database is how
            # two people end up with two different schemas without knowing. The schema comes from a
            # migration.
            self.db.execute("SET search_path TO lifecycle, public")
            # Set for the whole connection rather than only the current transaction.
            self.db.execute("SELECT set_config('app.ns', ?, false)", (self.ns,))
            # The demo-run marker is the scope a single demonstration's reset deletes. The
            # column defaults to this session variable, and without setting it every row carries
            # an empty marker and the reset never finds anything — measured with a real smoke test
            # after the unit tests had all passed. It is set here because this is the only place
            # that knows a connection has just been opened.
            self.db.execute("SELECT set_config('app.demo_run', ?, false)",
                            (os.getenv("DEMO_RUN", ""),))
            self._registry = _registry()
            return
        self.db = sqlite3.connect(str(db_path))
        self.db.execute("PRAGMA busy_timeout = 2000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS advice_artifacts (
            digest TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS advice_checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS advice_checkpoint_events (
            event_id TEXT PRIMARY KEY,
            checkpoint_id TEXT NOT NULL,
            record TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_checkpoint_events_checkpoint
            ON advice_checkpoint_events(checkpoint_id);
        CREATE TABLE IF NOT EXISTS advice_presentation_leases (
            checkpoint_id TEXT PRIMARY KEY,
            display_id TEXT NOT NULL UNIQUE,
            record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS advice_generation_claims (
            cache_key TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS advice_generation_cache (
            cache_key TEXT PRIMARY KEY,
            expires_at TEXT NOT NULL,
            record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS advice_explanation_requests (
            client_request_id TEXT PRIMARY KEY,
            context_digest TEXT NOT NULL,
            record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS advice_presentation_metrics (
            metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            checkpoint_id TEXT NOT NULL,
            record TEXT NOT NULL
        );
        """)
        self.db.commit()
        self._registry = _registry()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "CheckpointStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def put_artifact(self, artifact_type: str, payload: dict,
                     data_mode: str = "synthetic", is_mock: bool = True) -> bool:
        """Persist content-addressed artifact; same payload is idempotent."""
        record = build_artifact_record(artifact_type, payload, data_mode, is_mock)
        self._validate("advice_artifact", record)
        cur = self.db.execute(
            "INSERT OR IGNORE INTO advice_artifacts(digest,artifact_id,artifact_type,record) "
            "VALUES(?,?,?,?)", (record["digest"], record["artifact_id"], artifact_type,
                                 _json(record)))
        self.db.commit()
        return cur.rowcount == 1

    def create_checkpoint(self, checkpoint: dict) -> bool:
        return self.create_checkpoint_bundle([], checkpoint)

    def create_checkpoint_bundle(self, artifacts: list[dict], checkpoint: dict) -> bool:
        """Atomically persist artifact refs, checkpoint and its `created` event."""
        self._validate("advice_checkpoint", checkpoint)
        for artifact in artifacts:
            self._validate("advice_artifact", artifact)
            expected = build_artifact_record(
                artifact["artifact_type"], artifact["payload"], artifact["data_mode"],
                artifact["is_mock"], artifact["created_at"])
            if artifact["digest"] != expected["digest"]:
                raise ValueError("artifact digest không khớp payload")
        checkpoint_id = checkpoint["checkpoint_id"]
        existing = self.db.execute(
            "SELECT record FROM advice_checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        if existing:
            if json.loads(existing[0]) != checkpoint:
                raise ValueError(
                    f"checkpoint_id {checkpoint_id!r} xuất hiện với payload khác nhau")
            return False
        created = _created_event(checkpoint)
        self._validate("advice_checkpoint_event", created)
        with self.db:
            for artifact in artifacts:
                row = self.db.execute(
                    "SELECT artifact_id,artifact_type FROM advice_artifacts WHERE digest = ?",
                    (artifact["digest"],),
                ).fetchone()
                if row:
                    if row != (artifact["artifact_id"], artifact["artifact_type"]):
                        raise ValueError(
                            f"artifact digest {artifact['digest']!r} có identity khác")
                    continue
                self.db.execute(
                    "INSERT INTO advice_artifacts(digest,artifact_id,artifact_type,record) "
                    "VALUES(?,?,?,?)",
                    (artifact["digest"], artifact["artifact_id"], artifact["artifact_type"],
                     _json(artifact)),
                )
            self.db.execute("INSERT INTO advice_checkpoints(checkpoint_id,record) VALUES(?,?)",
                            (checkpoint_id, _json(checkpoint)))
            self.db.execute(
                "INSERT INTO advice_checkpoint_events(event_id,checkpoint_id,record) "
                "VALUES(?,?,?)", (created["event_id"], checkpoint_id, _json(created)))
        return True

    def append_event(self, event: dict) -> bool:
        self._validate("advice_checkpoint_event", event)
        checkpoint_id = event["checkpoint_id"]
        if not self.db.execute("SELECT 1 FROM advice_checkpoints WHERE checkpoint_id = ?",
                               (checkpoint_id,)).fetchone():
            raise ValueError(f"checkpoint không tồn tại: {checkpoint_id}")
        existing = self.db.execute(
            "SELECT record FROM advice_checkpoint_events WHERE event_id = ?",
            (event["event_id"],),
        ).fetchone()
        if existing:
            if json.loads(existing[0]) != event:
                raise ValueError(
                    f"event_id {event['event_id']!r} xuất hiện với payload khác nhau")
            return False
        projected = project_checkpoint_events(self.events(checkpoint_id) + [event])
        del projected  # projection above is the transition validator
        with self.db:
            self.db.execute(
                "INSERT INTO advice_checkpoint_events(event_id,checkpoint_id,record) "
                "VALUES(?,?,?)", (event["event_id"], checkpoint_id, _json(event)))
        return True

    def events(self, checkpoint_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT record FROM advice_checkpoint_events WHERE checkpoint_id = ? "
            "ORDER BY rowid", (checkpoint_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def checkpoint(self, checkpoint_id: str) -> dict | None:
        row = self.db.execute("SELECT record FROM advice_checkpoints WHERE checkpoint_id = ?",
                              (checkpoint_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def state(self, checkpoint_id: str) -> dict:
        if self.checkpoint(checkpoint_id) is None:
            raise ValueError(f"checkpoint không tồn tại: {checkpoint_id}")
        return project_checkpoint_events(self.events(checkpoint_id))

    def acquire_presentation_lease(self, checkpoint_id: str, leased_at: str,
                                   display_id_factory=None,
                                   presentation: dict | None = None) -> dict:
        """Atomically create the one presentation lease and its `offered` event."""
        factory = display_id_factory or (lambda: str(uuid.uuid4()))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT record FROM advice_presentation_leases WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            if row:
                self.db.commit()
                return json.loads(row[0])
            checkpoint = self.checkpoint(checkpoint_id)
            if checkpoint is None:
                raise KeyError(checkpoint_id)
            state = project_checkpoint_events(self.events(checkpoint_id))["state"]
            if state not in {"ready", "generated", "generation_failed"}:
                raise ValueError(
                    f"checkpoint {checkpoint_id!r} không thể offered từ state {state!r}")
            display_id = str(factory())
            lease = {
                "checkpoint_id": checkpoint_id,
                "display_id": display_id,
                "leased_at": leased_at,
            }
            if presentation:
                # The lease stores immutable presentation identity/version metadata.  The
                # actual artifact is content-addressed by the orchestration layer before
                # this transaction; retrying an existing lease never re-renders text.
                lease.update(dict(presentation))
            event = {
                # As above: the event entity's own version, not the checkpoint record's.
                "schema_version": "1.1.0",
                "event_id": f"offered:{checkpoint_id}",
                "checkpoint_id": checkpoint_id,
                "driver_id": checkpoint["driver_id"],
                "display_id": display_id,
                "event_type": "offered",
                "occurred_at": leased_at,
                "actor": "advisor",
                "origin": "product",
                "reason_code": None,
                "relation_type": None,
                "confidence": None,
                "payload": {},
            }
            self._validate("advice_checkpoint_event", event)
            project_checkpoint_events(self.events(checkpoint_id) + [event])
            self.db.execute(
                "INSERT INTO advice_presentation_leases(checkpoint_id,display_id,record) "
                "VALUES(?,?,?)", (checkpoint_id, display_id, _json(lease)))
            self.db.execute(
                "INSERT INTO advice_checkpoint_events(event_id,checkpoint_id,record) "
                "VALUES(?,?,?)", (event["event_id"], checkpoint_id, _json(event)))
            self.db.commit()
            return lease
        except Exception:
            self.db.rollback()
            raise

    def lease(self, checkpoint_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT record FROM advice_presentation_leases WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def checkpoints(self, driver_id: str | None = None) -> list[dict]:
        rows = self.db.execute(
            "SELECT record FROM advice_checkpoints ORDER BY rowid").fetchall()
        records = [json.loads(row[0]) for row in rows]
        if driver_id is not None:
            records = [record for record in records
                       if record["driver_id"] == driver_id]
        return records

    def checkpoint_states(self, driver_id: str | None = None) -> list[dict]:
        return [{**checkpoint, **self.state(checkpoint["checkpoint_id"])}
                for checkpoint in self.checkpoints(driver_id)]

    def artifact(self, artifact_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT record FROM advice_artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def artifacts(self, artifact_type: str | None = None) -> list[dict]:
        rows = self.db.execute(
            "SELECT record FROM advice_artifacts ORDER BY rowid").fetchall()
        records = [json.loads(row[0]) for row in rows]
        if artifact_type is not None:
            records = [record for record in records
                       if record["artifact_type"] == artifact_type]
        return records

    def put_artifact_record(self, record: dict) -> bool:
        self._validate("advice_artifact", record)
        existing = self.db.execute(
            "SELECT record FROM advice_artifacts WHERE digest = ?", (record["digest"],)
        ).fetchone()
        if existing:
            if json.loads(existing[0]) != record:
                raise ValueError(f"artifact digest {record['digest']!r} có payload khác")
            return False
        with self.db:
            self.db.execute(
                "INSERT INTO advice_artifacts(digest,artifact_id,artifact_type,record) "
                "VALUES(?,?,?,?)", (record["digest"], record["artifact_id"],
                                     record["artifact_type"], _json(record)))
        return True

    def generation_cache_get(self, cache_key: str, now: str) -> dict | None:
        row = self.db.execute(
            "SELECT expires_at,record FROM advice_generation_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row[0]) <= datetime.fromisoformat(now):
            return None
        return json.loads(row[1])

    def claim_generation(self, cache_key: str, owner_id: str, claimed_at: str,
                         expires_at: str) -> bool:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute(
                "SELECT cache_key,expires_at FROM advice_generation_claims").fetchall()
            now_dt = datetime.fromisoformat(claimed_at)
            expired = [key for key, expiry in rows
                       if datetime.fromisoformat(expiry) <= now_dt]
            for key in expired:
                self.db.execute(
                    "DELETE FROM advice_generation_claims WHERE cache_key = ?", (key,))
            current = self.db.execute(
                "SELECT owner_id FROM advice_generation_claims WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if current is not None:
                self.db.commit()
                return current[0] == owner_id
            self.db.execute(
                "INSERT INTO advice_generation_claims(cache_key,owner_id,claimed_at,expires_at) "
                "VALUES(?,?,?,?)", (cache_key, owner_id, claimed_at, expires_at))
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def put_generation_cache(self, cache_key: str, owner_id: str, record: dict,
                             expires_at: str) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            claim = self.db.execute(
                "SELECT owner_id FROM advice_generation_claims WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if claim is None or claim[0] != owner_id:
                raise ValueError("generation claim owner không khớp")
            # The replacing insert is syntax only the embedded database has, and it cannot be
            # translated mechanically: the other dialect's upsert must name the conflicting
            # columns, and that information lives in the schema rather than in the statement. Hence
            # two explicit branches, with the conflict key being the namespace and the cache key,
            # which is the primary key in the migration.
            if self._la_pg:
                self.db.execute(
                    "INSERT INTO advice_generation_cache(cache_key,expires_at,record) "
                    "VALUES(?,?,?) ON CONFLICT (ns, cache_key) DO UPDATE SET "
                    "expires_at = EXCLUDED.expires_at, record = EXCLUDED.record",
                    (cache_key, expires_at, _json(record)))
            else:
                self.db.execute(
                    "INSERT OR REPLACE INTO advice_generation_cache"
                    "(cache_key,expires_at,record) VALUES(?,?,?)",
                    (cache_key, expires_at, _json(record)))
            self.db.execute(
                "DELETE FROM advice_generation_claims WHERE cache_key = ?", (cache_key,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def release_generation_claim(self, cache_key: str, owner_id: str) -> None:
        with self.db:
            self.db.execute(
                "DELETE FROM advice_generation_claims WHERE cache_key = ? AND owner_id = ?",
                (cache_key, owner_id))

    def explanation_request(self, client_request_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT context_digest,record FROM advice_explanation_requests "
            "WHERE client_request_id = ?", (client_request_id,)).fetchone()
        if row is None:
            return None
        return {"context_digest": row[0], "record": json.loads(row[1])}

    def put_explanation_request(self, client_request_id: str, context_digest: str,
                                record: dict) -> bool:
        existing = self.explanation_request(client_request_id)
        if existing is not None:
            if existing["context_digest"] != context_digest or existing["record"] != record:
                raise ValueError("client_request_id đã dùng cho context explanation khác")
            return False
        with self.db:
            self.db.execute(
                "INSERT INTO advice_explanation_requests(client_request_id,context_digest,record) "
                "VALUES(?,?,?)", (client_request_id, context_digest, _json(record)))
        return True

    def append_presentation_metric(self, record: dict) -> None:
        required = {
            "occurred_at", "checkpoint_id", "cache_hit", "avoided_calls",
            "stale_discard", "fallback", "latency_ms", "input_tokens",
            "output_tokens", "cost_usd",
        }
        if set(record) != required:
            raise ValueError(
                f"presentation metric fields không hợp lệ: {set(record) ^ required}")
        datetime.fromisoformat(record["occurred_at"])
        if self.checkpoint(record["checkpoint_id"]) is None:
            raise ValueError(f"checkpoint không tồn tại: {record['checkpoint_id']}")
        with self.db:
            self.db.execute(
                "INSERT INTO advice_presentation_metrics(occurred_at,checkpoint_id,record) "
                "VALUES(?,?,?)", (record["occurred_at"], record["checkpoint_id"],
                                   _json(record)))

    def presentation_metrics(self, checkpoint_id: str | None = None) -> list[dict]:
        rows = self.db.execute(
            "SELECT record FROM advice_presentation_metrics ORDER BY metric_id").fetchall()
        records = [json.loads(row[0]) for row in rows]
        if checkpoint_id is not None:
            records = [record for record in records
                       if record["checkpoint_id"] == checkpoint_id]
        return records

    def _validate(self, entity: str, record: dict) -> None:
        errors = self._registry.validate(entity, record)
        if errors:
            raise ValueError(f"{entity} không hợp lệ: {errors}")


class InMemoryCheckpointJournal:
    """RAM journal with the same validation/projection contract as SQLite."""

    def __init__(self):
        self._registry = _registry()
        self._artifacts: dict[str, dict] = {}
        self._checkpoints: dict[str, dict] = {}
        self._events: dict[str, dict] = {}

    def _validate(self, entity: str, record: dict) -> None:
        errors = self._registry.validate(entity, record)
        if errors:
            raise ValueError(f"{entity} không hợp lệ: {errors}")

    def create_checkpoint(self, checkpoint: dict) -> bool:
        return self.create_checkpoint_bundle([], checkpoint)

    def create_checkpoint_bundle(self, artifacts: list[dict], checkpoint: dict) -> bool:
        self._validate("advice_checkpoint", checkpoint)
        for artifact in artifacts:
            self._validate("advice_artifact", artifact)
        checkpoint_id = checkpoint["checkpoint_id"]
        old = self._checkpoints.get(checkpoint_id)
        if old is not None:
            if old != checkpoint:
                raise ValueError(
                    f"checkpoint_id {checkpoint_id!r} xuất hiện với payload khác nhau")
            return False
        created = _created_event(checkpoint)
        self._validate("advice_checkpoint_event", created)
        # Stage copies before publishing so validation failure cannot leave a partial bundle.
        staged_artifacts = dict(self._artifacts)
        for artifact in artifacts:
            old_artifact = staged_artifacts.get(artifact["digest"])
            if old_artifact is not None and old_artifact != artifact:
                raise ValueError(
                    f"artifact digest {artifact['digest']!r} có payload khác nhau")
            staged_artifacts[artifact["digest"]] = artifact
        self._artifacts = staged_artifacts
        self._checkpoints[checkpoint_id] = checkpoint
        self._events[created["event_id"]] = created
        return True

    def append_event(self, event: dict) -> bool:
        self._validate("advice_checkpoint_event", event)
        checkpoint_id = event["checkpoint_id"]
        if checkpoint_id not in self._checkpoints:
            raise ValueError(f"checkpoint không tồn tại: {checkpoint_id}")
        old = self._events.get(event["event_id"])
        if old is not None:
            if old != event:
                raise ValueError(
                    f"event_id {event['event_id']!r} xuất hiện với payload khác nhau")
            return False
        project_checkpoint_events(self.events(checkpoint_id) + [event])
        self._events[event["event_id"]] = event
        return True

    def events(self, checkpoint_id: str) -> list[dict]:
        return [event for event in self._events.values()
                if event["checkpoint_id"] == checkpoint_id]

    def checkpoint(self, checkpoint_id: str) -> dict | None:
        return self._checkpoints.get(checkpoint_id)

    def state(self, checkpoint_id: str) -> dict:
        if checkpoint_id not in self._checkpoints:
            raise ValueError(f"checkpoint không tồn tại: {checkpoint_id}")
        return project_checkpoint_events(self.events(checkpoint_id))

    def artifacts(self) -> list[dict]:
        return [dict(record) for record in self._artifacts.values()]

    def checkpoints(self) -> list[dict]:
        return [dict(record) for record in self._checkpoints.values()]

    def all_events(self) -> list[dict]:
        return [dict(record) for record in self._events.values()]
