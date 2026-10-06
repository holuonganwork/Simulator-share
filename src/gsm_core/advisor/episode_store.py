"""Episode store — LEGACY ADAPTER trên AdviceEventLog (ĐA-05, Cycle W) + exact-key cache.

Trước Cycle W đây là bảng `episodes` một-dòng-một-episode với cột mutable
(`shown/accepted/reward`) vĩnh viễn NULL — write-only (MEMSTATE-2), không schema_version,
không join được với UI JSONL/sim events (D-A3-04). Nay:

- `append_episode` PHÁT event `decided` vào event log append-only (một đường ghi duy nhất
  cho pipeline C6 — pipeline vẫn gọi API cũ, không double-write);
- `count_episodes` đếm distinct decision qua projection (rebuild từ events);
- `cache_get/put` giữ nguyên bảng `advice_cache` CÙNG FILE (exact-key, TTL 6h — research
  đợt 7: TUYỆT ĐỐI KHÔNG semantic cache). ⚠ Bảng cache là bảng MUTABLE (INSERT OR
  REPLACE) — lời hứa append-only chỉ thuộc bảng `advice_events` (F-S2). Cache theo
  `problem_digest` là ý tưởng A2 của Cường — cycle riêng, KHÔNG nuôi thêm ở đây.

12 call site cũ (pipeline + tests) giữ nguyên chữ ký. `close()` mới — LAYEROUT-16
(connection giữ file ⇒ PermissionError khi cleanup TemporaryDirectory trên Windows).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from gsm_core.lifecycle.event_log import AdviceEventLog
from gsm_core.lifecycle.projections import decision_state

CACHE_TTL_S = 6 * 3600


def _key(driver_id, state_digest, policy_version, solver_version,
         prompt_version, model_id) -> str:
    raw = "|".join([driver_id, state_digest, policy_version, solver_version,
                     prompt_version, model_id])
    return hashlib.sha256(raw.encode()).hexdigest()


class EpisodeStore:
    def __init__(self, db_path: str | Path):
        self.log = AdviceEventLog(db_path)
        self.db = self.log.db  # cùng connection/file — "một database" (verdict ĐA-05)
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS advice_cache (
            key TEXT PRIMARY KEY, advice_json TEXT, created_at REAL
        )""")
        self.db.commit()

    def close(self) -> None:
        self.log.close()

    def __enter__(self) -> "EpisodeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def append_episode(self, ep: dict) -> None:
        """Map episode dict cũ → event `decided`. Idempotent theo episode_id (append
        trùng bị event log bỏ qua — khác bản cũ: INSERT trùng PK nổ IntegrityError)."""
        ts = ep.get("ts", time.time())
        iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        payload = {k: ep.get(k) for k in
                   ("feature", "state_digest", "solver_report_refs", "advice_spec",
                    "message", "confidence", "route", "fallback_used", "residual_path",
                    "verify") if k in ep}
        # `N1` (UPDATE-136): nâng `advice_spec.action_type` lên `payload["topic"]`.
        #
        # Đây là đường ghi THỨ BA vào event log (UI · sim · pipeline). UPDATE-135 chỉ bịt đường UI
        # (validator 422 ở `routers/advice.py`), nên **mọi quyết định pipeline có `topic=None`** ⇒
        # `classify(None)` = `"measured"` ⇒ chúng mặc định vào bảng ĐO. Nếu pipeline sinh một lời
        # khuyên KHUYÊN MỀM thì nó bị đo im lặng — đúng ranh giới §1.2c cấm.
        #
        # Không bịa tên mới: `advice_spec.action_type` ĐÃ tồn tại và `templates.py:240` gọi nó
        # nguyên văn là *"adherence taxonomy"*. Từ vựng thật đo bằng grep: `online` (mặc định của
        # `_advice_spec`) · `rest_window` · `shift_plan`.
        #
        # ⚠ Đây KHÔNG phải hợp nhất taxonomy. `action_type` (pipeline) và `MEASURED_TOPICS`
        # (registry) vẫn là hai từ vựng; việc thống nhất chúng là điều kiện để hai đường đo join
        # được (`specs/adherence-measurement.md` §(c)#2) và là một cycle riêng. Ở đây ta chỉ NỐI
        # chúng, và để tầng đọc fail-closed + TREO bắt mọi giá trị chưa khai — tức một `action_type`
        # lạ sẽ **kêu to**, không lặng lẽ vào mẫu số.
        act = (ep.get("advice_spec") or {}).get("action_type")
        if act:
            payload["topic"] = act
        self.log.append({
            "event_id": f"ep-{ep['episode_id']}",
            "decision_id": ep["episode_id"],
            "display_id": None,
            "driver_id": ep["driver_id"],
            "run_id": None,
            "event_type": "decided",
            "reason_code": None,
            "occurred_at": iso,
            "observed_at": iso,
            "actor": "advisor",
            "origin": "pipeline",
            "source": "MOCK",
            "context_revision": None,
            "payload": payload,
            "schema_version": "1.0.0",
        })

    def count_episodes(self) -> int:
        """Số DECISION của PIPELINE — lọc `origin` bắt buộc (F-6, review đối kháng):
        "một database" nghĩa là event UI/sim ghi CHUNG file; không lọc thì 12 episode
        + 1 event UI + 3 event sim đếm thành 16 (đo được trước khi sửa)."""
        return len(decision_state(
            e for e in self.log.events() if e["origin"] == "pipeline"))

    def cache_get(self, **key_parts):
        k = _key(**key_parts)
        row = self.db.execute(
            "SELECT advice_json, created_at FROM advice_cache WHERE key=?", (k,)
        ).fetchone()
        if row is None or (time.time() - row[1]) > CACHE_TTL_S:
            return None
        return json.loads(row[0])

    def cache_put(self, advice: dict, **key_parts) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO advice_cache (key, advice_json, created_at)"
            " VALUES (?,?,?)",
            (_key(**key_parts), json.dumps(advice, ensure_ascii=False), time.time()))
        self.db.commit()
