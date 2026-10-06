"""Observability per-layer (T-026 phase 2 — instrument tại C6).

Đọc metric TỪ SCHEMA đã có (SolverReport / ComposedAdvice / route) — không metric
"lơ lửng" (spec observability-metrics §0). 2 HARD invariant (spec §5):
  solver.number_traceability = 1.0   (mọi số solver có source)
  composer.faithfulness      = 1.0   (mọi số advice khớp SolverReport — không bịa)

Dual-channel: luôn ghi span parquet (polars, deterministic, headless-safe); Langfuse
BẬT khi có env key (lazy import) — parquet đủ nếu chưa có account.
"""

from __future__ import annotations

import time
from pathlib import Path


def _num_key(n: dict) -> tuple:
    return (n.get("value"), n.get("unit"), n.get("source"))


def compute_number_traceability(solver_reports: list[dict]) -> float:
    """% số trong SolverReport.numbers có `source` không rỗng. HARD =1.0."""
    total = traced = 0
    for rep in solver_reports:
        for n in rep.get("numbers", []):
            total += 1
            if n.get("source"):
                traced += 1
    return 1.0 if total == 0 else traced / total


def compute_faithfulness(advice_numbers: list[dict],
                         solver_reports: list[dict]) -> float:
    """% số trong ComposedAdvice.numbers KHỚP (value,unit,source) một SolverReport.
    HARD =1.0 — <1.0 nghĩa composer bịa số → phải veto."""
    solver_set = {_num_key(n) for rep in solver_reports for n in rep.get("numbers", [])}
    if not advice_numbers:
        return 1.0
    matched = sum(1 for n in advice_numbers if _num_key(n) in solver_set)
    return matched / len(advice_numbers)


def build_span_row(request_id: str, driver_id: str, feature: str, route: dict,
                   solver_reports: list[dict], advice: dict,
                   verify_result: dict, latency_ms: float) -> dict:
    """1 hàng span/request — gộp metric mọi layer từ schema (Langfuse-mappable)."""
    return {
        "request_id": request_id,
        "driver_id": driver_id,
        "feature": feature,
        "ts": time.time(),
        # router
        "router_intent": route.get("intent"),
        "router_out_of_taxonomy": route.get("intent") == "out_of_taxonomy",
        "router_stage1_fanout": len([s for s in route.get("solvers", [])
                                      if s != "policy_kb"]),
        # solver (HARD)
        "solver_number_traceability": compute_number_traceability(solver_reports),
        "solver_count": len(solver_reports),
        # composer (HARD faithfulness)
        "composer_faithfulness": compute_faithfulness(advice.get("numbers", []),
                                                      solver_reports),
        "composer_fallback_used": bool(advice.get("fallback_used")),
        "composer_confidence": advice.get("confidence"),
        # verifier
        # W-6: giữ BA trạng thái — None = "không chạy verify" (R5) ≠ False = "verify
        # fail". `bool(...)` cũ ép None→False (vu oan) và default True (bịa PASS).
        "verifier_passed": (None if verify_result.get("passed") is None
                            else bool(verify_result["passed"])),
        "verifier_error_count": len(verify_result.get("errors", [])),
        "verifier_errors": "; ".join(verify_result.get("errors", [])),
        # outcome
        "residual_path": advice.get("residual_path"),
        "citation_count": len(advice.get("citations", [])),
        "latency_ms": round(latency_ms, 2),
    }


class ObservabilityRecorder:
    """Thu span in-memory → flush parquet. Langfuse optional (env LANGFUSE_*)."""

    def __init__(self, parquet_path: str | Path | None = None,
                 enable_langfuse: bool | None = None):
        # `None` = đọc env `LANGFUSE_ENABLED` (quyết định owner 2026-08-14: SDK đã cài vào
        # pyproject, mặc định TẮT, chỉ bật bằng env khi đang gỡ lỗi — vì trace mang câu hỏi
        # của tài xế + số thu nhập RA NGOÀI máy, lên cloud Langfuse). Truyền bool tường minh
        # vẫn thắng env — test cần điều khiển trực tiếp.
        if enable_langfuse is None:
            import os
            enable_langfuse = os.environ.get("LANGFUSE_ENABLED", "0") == "1"
        self.parquet_path = Path(parquet_path) if parquet_path else None
        self.rows: list[dict] = []
        self._langfuse = None
        if enable_langfuse:
            self._init_langfuse()

    def _init_langfuse(self) -> None:
        import os
        if not os.environ.get("LANGFUSE_SECRET_KEY"):
            return  # chưa có key → parquet-only, không lỗi
        try:
            from langfuse import Langfuse  # lazy
            self._langfuse = Langfuse()
        except Exception:  # noqa: BLE001 — Langfuse hỏng không được chặn core
            self._langfuse = None

    def record(self, **kwargs) -> dict:
        row = build_span_row(**kwargs)
        self.rows.append(row)  # F-S9: flush() dùng infer_schema_length=None (họ W-2)
        # cảnh báo HARD invariant ngay (không chặn — chỉ đánh dấu để điều tra)
        row["hard_invariant_ok"] = (row["solver_number_traceability"] == 1.0
                                    and row["composer_faithfulness"] == 1.0)
        return row

    def flush(self) -> Path | None:
        if not self.parquet_path or not self.rows:
            return None
        import polars as pl
        self.parquet_path.parent.mkdir(parents=True, exist_ok=True)
        # F-S9 (cùng họ W-2): infer mặc định 100 hàng — verifier_passed nay tri-state
        # (None/False/True, W-6): batch mà 100 hàng đầu toàn None rồi gặp bool sẽ lệch
        # dtype. Quét toàn bộ hàng như logging_ev.
        pl.DataFrame(self.rows, infer_schema_length=None).write_parquet(self.parquet_path)
        return self.parquet_path
