"""E5 (UPDATE-158) — cache run sim SESSION-SCOPED theo phán quyết trọng tài lọc-test (r13).

113 call-site `run_once/run_multiday` trùng lặp xuyên file là nguồn thời gian suite lớn nhất.
Cache theo (fingerprint config, seed) với BA ĐIỀU KIỆN CỨNG của phán quyết:

  1. **Trả DEEPCOPY** — test mutate kết quả không được đầu độc test khác (mutation thật đã đo
     ở test_lifecycle_review_fixes dòng 275/294).
  2. **Cặp determinism/exact-repeat giữ ≥ 1 run TƯƠI** — hai vế cùng lấy từ cache là so cache
     với chính nó, cổng tất định thành vô dụng. Fixture này KHÔNG được dùng cho cả hai vế
     (test_run_smoke giữ nguyên cặp gốc, ngoài cache).
  3. **`test_parallel_worlds` đứng NGOÀI cache hoàn toàn** — máy đo A/B (CRN, draw-count,
     arm A untouched) phải chạy run độc lập.

Dùng: test NHẬN fixture `cached_run_once` rồi gọi `cached_run_once(cfg, seed)` — file nào
chưa chuyển thì hành vi giữ nguyên (conftest chỉ THÊM fixture, không đổi test cũ).
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

_CACHE: dict = {}


def _cfg_fingerprint(cfg) -> str:
    try:
        blob = json.dumps(cfg.data, sort_keys=True, default=str)
    except Exception:                      # config chứa thứ không serialize được ⇒ không cache
        return f"nocache-{id(cfg)}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@pytest.fixture(scope="session")
def cached_run_once():
    """`get(cfg, seed) -> RunResult` (DEEPCOPY từ cache session)."""
    from gsm_sim.runner import run_once

    def get(cfg, seed: int):
        key = ("once", _cfg_fingerprint(cfg), int(seed))
        if key not in _CACHE:
            _CACHE[key] = run_once(cfg, seed)
        return copy.deepcopy(_CACHE[key])

    return get


@pytest.fixture(scope="session")
def cached_run_multiday():
    """`get(cfg, seed, days) -> MultiDayResult` (DEEPCOPY từ cache session)."""
    from gsm_sim.multiday import run_multiday

    def get(cfg, seed: int, days: int):
        key = ("md", _cfg_fingerprint(cfg), int(seed), int(days))
        if key not in _CACHE:
            _CACHE[key] = run_multiday(cfg, seed, days=days)
        return copy.deepcopy(_CACHE[key])

    return get


@pytest.fixture(autouse=True)
def _agent_llm_off_by_default(monkeypatch):
    """Suite must never reach a real provider.

    ``AGENT_LLM_MODE`` and ``EPISODE_NARRATION_MODE`` are both read from the environment when a
    caller does not pass them explicitly, and a dev machine's ``.env`` sets them live.  One
    forgotten argument then makes a unit test spend real tokens over the network and depend on
    it — slow, non-deterministic and expensive.  ``ui/backend/tests/conftest.py`` learned the
    same lesson for the narration path; this is the agent half of it.

    A test that wants a model injects a double (``tests.agent_fixtures.CountingClassifier``) or
    sets the variable itself.  This fixture changes the default, it does not block anything.
    """
    monkeypatch.setenv("AGENT_LLM_MODE", "off")
    monkeypatch.setenv("EPISODE_NARRATION_MODE", "off")
    # 🔴 ĐẶT RỖNG, KHÔNG `delenv` — sửa 2026-08-22, và đây là một lỗ THẬT chứ không phải
    # chuyện gọn gàng.
    #
    # `load_env()` dùng `os.environ.setdefault`. Một biến bị XOÁ sẽ được `.env` nạp LẠI ngay
    # khi bất kỳ module nào gọi `load_env()` giữa lượt chạy — và `PolicyKB.__init__`
    # (`policy_kb.py:190`) gọi đúng hàm ấy. Nên chuỗi này xảy ra trong một test bình thường:
    #
    #     fixture xoá AGENT_STORE_DSN
    #       → AdvisorPipeline(...) dựng PolicyKB
    #         → load_env() → setdefault THÀNH CÔNG (vì biến đã bị xoá)
    #           → AGENT_STORE_DSN = DSN của database DÙNG CHUNG
    #             → mọi store mở sau đó nối vào database dùng chung
    #
    # Chốt ở dòng 154 của chính file này đã ghi đúng luật ấy — *"Đặt RỖNG, KHÔNG `pop`"* —
    # sau sự cố 2026-08-19 (*"session mới xuất hiện trên database DÙNG CHUNG lúc 21:42, giữa
    # lúc suite đang chạy"*). Fixture ở đây lại mở lại đúng cái lỗ đó.
    #
    # ⚠ Vì sao mãi mới lộ: `AdviceEventLog` trước đây cắm thẳng `sqlite3.connect` nên **không
    # đọc env**, và nó là store duy nhất trên đường ấy. Chuyển nó qua `backend_for` (2026-08-22)
    # làm lỗ hiện hình. `CheckpointStore`/`AgentStore`/`roleplay_db` vốn đã đọc env — tức lỗ này
    # đã mở sẵn cho chúng từ trước.
    #
    # Chuỗi rỗng thì `setdefault` vô hiệu, và mọi chỗ đọc đều `.strip()` nên coi như chưa đặt.
    monkeypatch.setenv("AGENT_STORE_DSN", "")
    # 🔴 MERGE 2026-08-20 (nhánh RAG của Khánh) — hai đường mạng MỚI, cùng đúng bài học trên.
    #
    # `policy_kb._init_qdrant_client()` và `tracing._get_client()` đều gọi `load_env()`, tức
    # **đọc thẳng `.env` của máy dev**, rồi coi "có credential" là công tắc duy nhất. Đo được
    # (socket patch, 2026-08-20): chỉ *khởi tạo* client đã mở TCP tới Qdrant Cloud
    # `34.172.177.94:6333` — constructor tự kiểm phiên bản server.
    #
    # Hệ quả nguy hiểm không phải chậm, mà là **cùng một suite chạy khác nhau** giữa máy có
    # `.env` và CI không có — đúng kiểu phân kỳ im lặng dự án này đã sập nhiều lần.
    #
    # `test_agent_rag_driver_benchmark.py` có đặt `GSM_LOCAL_KB_ONLY` nhưng đặt trong THÂN
    # test: tới muộn, rò sang test sau (không monkeypatch), và phụ thuộc thứ tự chạy.
    # `test_agent_langfuse_tracing.py` thì không có gì. Nên chốt ở đây, một chỗ, cho cả suite.
    monkeypatch.setenv("GSM_LOCAL_KB_ONLY", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "0")


# ─────────────────────────── Postgres cho test (gói A, 2026-08-19) ──────────────────────────

def _nap_env_va_chan_dsn_nham():
    """Nạp `.env` cho test, và CHẶN việc test trỏ vào database dùng chung.

    🔴 Vì sao cần chốt này: `scripts/load_pg.nap()` có `TRUNCATE`. Nếu `PG_TEST_DSN` trỏ nhầm
    vào `gsm_agent` (DB chung của Cường và Khánh), một lần chạy suite là xoá dữ liệu giữa lúc
    người kia đang làm — và không có gì báo, vì loader nạp lại đầy đủ ngay sau đó nên nhìn vẫn
    "đúng". Repo đã trả giá một lần cho đúng họ lỗi này: UPDATE-244 ghi lại chuyện một test
    `monkeypatch.undo()` rồi ghi thẳng vào DB THẬT của máy, và assert đỏ với thông báo vô nghĩa.

    Chốt so theo TÊN DATABASE chứ không so nguyên chuỗi DSN: hai chuỗi khác nhau vẫn có thể
    trỏ cùng một chỗ (khác `?connect_timeout`, khác host viết hoa/thường, khác `sslmode`).
    """
    import os

    try:
        from gsm_core.advisor.llm_client import load_env

        load_env()
    except Exception:  # noqa: BLE001 - không có .env là trạng thái bình thường
        pass

    def _ten_db(dsn: str) -> str:
        duoi = dsn.rsplit("/", 1)[-1] if "/" in dsn else ""
        return duoi.split("?", 1)[0].strip().lower()

    # 🔴 Gỡ DSN ỨNG DỤNG khỏi môi trường test.
    #
    # `.env` đặt `AGENT_STORE_DSN`/`ROLEPLAY_DSN` để SẢN PHẨM chạy trên Postgres dùng chung.
    # Nhưng `backend_for` coi biến đó là công tắc toàn cục, nên mọi test truyền một đường dẫn
    # file tạm (`AgentStore(tmp_path / "a.db")`) cũng bị đẩy sang Postgres — nơi role `gsm_app`
    # cố tình không có quyền tạo bảng. Đo 2026-08-19: 21 failed + 21 errors chỉ vì chuyện này.
    #
    # Test muốn Postgres thì nói rõ bằng `PG_TEST_DSN`. `L1R_DSN` được GIỮ: schema `l1r` là
    # chỉ-đọc ở tầng quyền, không test nào ghi được vào đó dù có muốn.
    # 🔴 Đặt RỖNG, KHÔNG `pop`. `load_env()` dùng `setdefault`, nên một biến bị xoá sẽ được
    # `.env` nạp LẠI ngay khi bất kỳ module nào gọi `load_env()` giữa lượt chạy (`agent/llm.py`
    # và `l1r_store.dsn()` đều gọi). Đo 2026-08-19: chốt bản `pop` vẫn để lọt — session mới
    # xuất hiện trên database DÙNG CHUNG lúc 21:42, giữa lúc suite đang chạy.
    # Chuỗi rỗng thì `setdefault` thành vô hiệu, và mọi chỗ đọc đều `.strip()` nên coi là chưa
    # đặt. Đây là khác biệt giữa "xoá một biến" và "khẳng định nó rỗng".
    for bien in ("AGENT_STORE_DSN", "ROLEPLAY_DSN"):
        os.environ[bien] = ""

    test_dsn = os.getenv("PG_TEST_DSN", "").strip()
    chung = [os.getenv(k, "").strip() for k in ("AGENT_STORE_DSN", "L1R_DSN", "ROLEPLAY_DSN")]
    if test_dsn:
        ten_test = _ten_db(test_dsn)
        trung = sorted({_ten_db(d) for d in chung if d and _ten_db(d) == ten_test})
        if trung:
            raise RuntimeError(
                f"PG_TEST_DSN trỏ vào database {ten_test!r} — cũng là database dùng chung. "
                "Test có TRUNCATE/DROP nên sẽ xoá dữ liệu của cả hai người. "
                "Tạo một database riêng (vd `gsm_test`) rồi trỏ PG_TEST_DSN vào đó.")


_nap_env_va_chan_dsn_nham()
