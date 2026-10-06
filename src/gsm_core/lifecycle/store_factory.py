"""Choosing the checkpoint store, from the same environment variable
as the agent store.

The agent store's factory explains why the older backend-selection variable was removed.

The Postgres checkpoint store that once existed was not broken in the way the agent one was: this
store uses only the connection methods the shared wrapper provides. It was dropped for a different
reason — it had no notion of a namespace, so pooling a thousand replay sessions into one set of
tables mixed data between them, because checkpoint ids derive from content and two different
sessions for one driver produce the same id. Isolation is now handled by the namespace column and
row-level security.
"""
from __future__ import annotations

from pathlib import Path


def checkpoint_store_from_env(path: str | Path, *, ns: str = ""):
    from gsm_core.lifecycle.checkpoint_store import CheckpointStore

    return CheckpointStore(path, ns=ns)
