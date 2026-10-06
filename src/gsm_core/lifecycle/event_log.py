"""The advice event log: an append-only store for the advice lifecycle.

The canonical source of truth. Every state is read through projections, which can be rebuilt from
the events. The append-only promise belongs to the events table itself: no code path updates or
deletes rows in it, and correcting a mistake means appending a superseding event rather than
touching history. The same database file also holds a cache table that is deliberately mutable and
is not covered by this promise.

The approved decisions:

- idempotency by event id, with the first write winning rather than being overwritten;
- schema validation before writing, through the multi-version registry, so replay across a
  migration works from the first day;
- one writer at a time, with a busy timeout and bounded retries, counting how often the database
  was busy;
- write-ahead logging is deliberately not enabled, because of a reset defect in the versions in
  use;
- an explicit close and a context manager, because an open connection holds the file and made
  temporary-directory cleanup fail on Windows;
- the simulation does not write here inside its tick loop; its events stay in memory with a run id.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

_COLUMNS = ("event_id", "decision_id", "display_id", "driver_id", "run_id",
            "event_type", "reason_code", "occurred_at", "observed_at",
            "actor", "origin", "source", "context_revision", "schema_version",
            "payload")

_BUSY_RETRIES = 5
_BUSY_TIMEOUT_MS = 2000


def _registry():
    from gsm_core.schema_registry import SchemaRegistry
    return SchemaRegistry(Path(__file__).resolve().parents[3] / "schemas")


def _normalize(v):
    """Normalise a payload value to a plain Python type while preserving the value.

    Simulation details carry numeric scalars from the numerical library, and serialising them
    directly raises on the export path from the simulation into the store.

    It deliberately does not fall back to string conversion: stringifying corrupts numbers
    silently, and a consumer would read text where it expected a number, with comparisons and sums
    going wrong unnoticed. A genuinely unknown type is left to raise explicitly, which is the
    normalise-or-fail-loudly rule for a data boundary.

    Only zero-dimensional scalars are unwrapped. A one-element array used to be silently
    scalarised, losing its shape, while a larger one raised an unrelated error. Arrays now fall
    through to serialisation and raise an explicit type error naming the type.
    """
    if (getattr(v, "item", None) is not None
            and type(v).__module__ != "builtins"
            and getattr(v, "ndim", None) == 0):
        return v.item()                      # numpy scalar 0-d → int/float/bool Python
    if isinstance(v, dict):
        return {k: _normalize(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_normalize(x) for x in v]
    return v


class AdviceEventLog:
    """Append-only. The API deliberately has no update or delete, and a test checks that by attribute."""

    def __init__(self, db_path: str | Path):
        """Two dialects behind one API, on the same pattern the checkpoint
        store uses.

        Why this store was converted last: it opened its own database connection directly and so
        never passed through the shared backend seam, which meant the connection setting had no
        effect on it while the other three stores had already moved. It was also the last such
        place on the product path.

        Rows are read positionally, as in the checkpoint store, so forcing dictionary rows would
        mean changing every read site.
        """
        from gsm_core.agent.sql_backend import backend_for, is_dsn

        # The same rule as the checkpoint store: an explicit connection string, or the product environment.
        self._la_pg = is_dsn(str(db_path)) or bool(os.getenv("AGENT_STORE_DSN", "").strip())
        if self._la_pg:
            self.db = backend_for(db_path, dict_rows=False)
            # No schema creation here: the application role deliberately has no such rights.
            #
            # The events table has existed since an earlier migration, with its namespace column
            # and row-level security, so the missing piece was never the schema; it was this file
            # not being wired to the seam. A first attempt at this work added a migration to
            # recreate the table, and a migration test caught it immediately: the create statement
            # was skipped silently and the index creation then failed on a column that does not
            # exist in that schema. That migration was removed.
            #
            # The primary key here is the namespace together with the event id rather than the
            # event id alone, deliberately: two different replay sessions may carry the same event
            # id. The conflict-ignoring insert is still idempotent exactly as on the other
            # dialect.
            self.db.execute("SET search_path TO lifecycle, public")
            self.db.execute("SELECT set_config('app.ns', ?, false)",
                            (os.getenv("LIFECYCLE_NS", ""),))
            self.db.execute("SELECT set_config('app.demo_run', ?, false)",
                            (os.getenv("DEMO_RUN", ""),))
            self._registry = _registry()
            self.sqlite_busy_count = 0
            return
        self.db = sqlite3.connect(str(db_path))
        self.db.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS advice_events (
            event_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL, display_id TEXT, driver_id TEXT NOT NULL,
            run_id TEXT, event_type TEXT NOT NULL, reason_code TEXT,
            occurred_at TEXT NOT NULL, observed_at TEXT NOT NULL,
            actor TEXT NOT NULL, origin TEXT NOT NULL, source TEXT NOT NULL,
            context_revision TEXT, schema_version TEXT NOT NULL,
            payload TEXT NOT NULL
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_ae_decision"
                        " ON advice_events(decision_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_ae_run ON advice_events(run_id)")
        self.db.commit()
        # Creating a table only if absent does not check an existing one. An external database
        # with all the right columns but no primary key makes the ignoring insert useless: two
        # appends of one event id both report success and idempotency dies silently, which was
        # reproduced. The key is therefore checked at open time, failing loudly with the path.
        pk_cols = [r[1] for r in self.db.execute(
            "PRAGMA table_info(advice_events)") if r[5]]
        if pk_cols != ["event_id"]:
            self.db.close()
            raise ValueError(
                f"{db_path}: bảng advice_events không có PRIMARY KEY(event_id) "
                f"(pk hiện tại: {pk_cols}) — idempotency sẽ chết im lặng; DB này "
                f"không phải store hợp lệ của AdviceEventLog")
        self._registry = _registry()
        self.sqlite_busy_count = 0

    # -- the store's own lifecycle --

    def close(self) -> None:
        # Both backends expose a close, as does the plain connection, so no branching is
        # needed.
        self.db.close()

    def __enter__(self) -> "AdviceEventLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- ghi --

    def append(self, event: dict) -> bool:
        """Write an event, returning false when the event id already exists, the
        first write winning.

        An event failing schema validation raises before the database is touched, so the log can
        never contain a record that cannot be validated, which is what makes replay through an
        upcaster trustworthy.
        """
        errs = self._registry.validate("advice_lifecycle_event", event)
        if errs:
            raise ValueError(f"advice_lifecycle_event không hợp lệ: {errs}")
        # A regular expression cannot validate a calendar: an impossible date, a thirteenth
        # month, a forty-fifth day or an out-of-range offset all match the pattern and would then
        # be persisted permanently, the store being append-only, poisoning every later decision
        # state. The boundary must parse for real, with the same parser the projections sort by.
        for f in ("occurred_at", "observed_at"):
            try:
                datetime.fromisoformat(event[f])
            except ValueError as exc:
                raise ValueError(
                    f"{f} '{event[f]}' không phải thời điểm có thật trên lịch "
                    f"({exc}) — record độc trong store append-only là vĩnh viễn, "
                    f"chặn tại append") from exc
        payload = json.dumps(_normalize(event["payload"]), ensure_ascii=False)
        row = tuple(payload if c == "payload" else event.get(c) for c in _COLUMNS)
        if self._la_pg:
            # The seam translates the ignoring insert and the placeholder style for the other
            # dialect, so the statement text is unchanged. No commit, because that connection is
            # autocommitting, and no busy retry loop, because that style of lock does not exist
            # there.
            cur = self.db.execute(
                f"INSERT OR IGNORE INTO advice_events ({','.join(_COLUMNS)})"
                f" VALUES ({','.join('?' * len(_COLUMNS))})", row)
            return cur.rowcount == 1
        for attempt in range(_BUSY_RETRIES):
            try:
                cur = self.db.execute(
                    f"INSERT OR IGNORE INTO advice_events ({','.join(_COLUMNS)})"
                    f" VALUES ({','.join('?' * len(_COLUMNS))})", row)
                self.db.commit()
                return cur.rowcount == 1
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc):
                    raise
                self.sqlite_busy_count += 1
                if attempt == _BUSY_RETRIES - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise AssertionError("unreachable")

    # -- reading: raw material for the projections, interpreted nowhere here --

    def events(self, decision_id: str | None = None,
               run_id: str | None = None) -> list[dict]:
        """Events as schema-shaped dictionaries, optionally filtered. The order
        is not guaranteed: the projections sort them themselves."""
        # Columns are named explicitly rather than selected with a wildcard. The table on the
        # other dialect carries an extra namespace column for row-level security, and a wildcard
        # would make the two dialects return dictionaries of different shapes, so any consumer
        # iterating the items or comparing key sets would break on one dialect only — the kind of
        # divergence that surfaces solely in production.
        q, args = f"SELECT {','.join(_COLUMNS)} FROM advice_events", []
        conds = []
        if decision_id is not None:
            conds.append("decision_id = ?")
            args.append(decision_id)
        if run_id is not None:
            conds.append("run_id = ?")
            args.append(run_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        cur = self.db.execute(q, args)
        cols = [d[0] for d in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out
