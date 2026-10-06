"""SIM-5 — gate cho bộ metric đầy đủ (spec `00-sim-overhaul-master.md` §6).

Rủi ro lớn nhất của một module "gộp metric" không phải sai số học, mà là **tạo ra nguồn sự thật
thứ hai**: cùng một đại lượng có hai con số khác nhau tuỳ chỗ đọc. Đó đúng là lỗi SIM-1 phải đi
sửa (data nói acceptance 0.88 trong khi sim hành xử 0.96). Vì vậy phần lớn test dưới đây kiểm
**tính nhất quán giữa các đường tính**, không phải kiểm giá trị tuyệt đối.
"""

from __future__ import annotations

import pytest

from gsm_sim.config import Config
from gsm_sim.journey import build_journey
from gsm_sim.metrics import summarize
from gsm_sim.runner import run_once
from gsm_sim.sim_metrics import (
    customer_wait, driver_metrics, full_report, supply_demand_density, system_metrics,
)

SEED = 4000


@pytest.fixture(scope="module")
def result():
    return run_once(Config.load("configs/pilot_hanoi.yaml"), seed=SEED)


# ---------- Không được có hai nguồn sự thật ----------


def test_system_metrics_extends_summarize(result):
    """`system_metrics` phải GIỮ NGUYÊN mọi con số của `summarize()`, chỉ bổ sung."""
    base, full = summarize(result), system_metrics(result)
    for k, v in base.items():
        assert full[k] == v, f"{k} lệch giữa summarize và system_metrics: {v} vs {full[k]}"


def test_driver_metrics_reads_journey_not_recompute(result):
    """Phân phối per-driver phải khớp CHÍNH XÁC journey — nếu lệch nghĩa là ai đó đã tính lại."""
    dm = driver_metrics(result)
    trips = sorted(build_journey(result, a.actor_id).metrics["trips_completed"]
                   for a in result.actors
                   if build_journey(result, a.actor_id).metrics["offers"] > 0)
    assert dm["trips_completed"]["n"] == len(trips)
    assert dm["trips_completed"]["median"] == pytest.approx(
        sorted(trips)[len(trips) // 2] if len(trips) % 2 else
        (sorted(trips)[len(trips) // 2 - 1] + sorted(trips)[len(trips) // 2]) / 2, abs=0.01)


def test_driver_trips_sum_matches_system(result):
    """Cộng cuốc mọi tài xế = cuốc hệ thống. Đây là phép thử bảo toàn xuyên tầng."""
    total = sum(build_journey(result, a.actor_id).metrics["trips_completed"]
                for a in result.actors)
    assert total == summarize(result)["orders_completed"]


# ---------- Chỉ số MỚI theo spec §6 ----------


def test_customer_wait_only_counts_matched(result):
    """Đơn HẾT HẠN không có thời gian chờ hữu hạn — gộp vào sẽ bóp méo trung vị và che
    chính vấn đề 'không ai nhận' (đã có `expired_rate` riêng đo việc đó)."""
    w = customer_wait(result)
    matched = sum(1 for e in result.events if e.kind == "order_matched")
    assert w["matched_n"] == matched
    assert 0 <= w["wait_median_min"] <= w["wait_p90_min"] <= w["wait_max_min"]
    # patience median cấu hình là 5 phút ⇒ chờ trung vị phải nhỏ hơn nhiều
    assert w["wait_median_min"] < 5.0


def test_density_detects_supply_and_demand(result):
    """Đây chính là phép đo đã tìm ra khuyết tật SIM-1 (05-06h có 0 tài xế cho 93 đơn).
    Nay là API có test, không phải script dùng một lần."""
    d = supply_demand_density(result)
    assert d["per_hour"], "không có giờ nào có cầu"
    for h, v in d["per_hour"].items():
        assert 0 <= h <= 23
        assert v["demand"] > 0
        assert 0.0 <= v["expired_rate"] <= 1.0
    assert d["top_cells"], "phải xếp hạng được cell đông cầu"


def test_no_starved_hours_after_sim1(result):
    """Không giờ nào được 'chết' — nhưng đây là test MỘT seed, còn tiêu chí THỐNG KÊ
    (aggregate 30 seed, ngưỡng 40%) sống ở `test_sim_realism.test_no_dead_hour`.

    SIM-XANH (Hà Nội toàn thành phố, 925 ô H3): với quy mô 1.000 xe car và 10.000 đơn,
    ngưỡng per-seed được siết từ 0.95 về 0.80 (thực tế khung tệ nhất 06h là ~75.4%).
    """
    d = supply_demand_density(result)
    worst = max((v["expired_rate"] for v in d["per_hour"].values()
                 if v["demand"] >= 20), default=0.0)
    assert worst <= 0.80, f"giờ tệ nhất hết hạn {worst:.0%} trên MỘT seed — sụp cung/cầu thật"


def test_full_report_shape(result):
    r = full_report(result)
    assert r["source"] == "MOCK" and r["seed"] == SEED
    assert {"system", "drivers"} <= set(r)
    assert r["drivers"]["acceptance_by_archetype"], "phải tách được accept theo archetype"


def test_acceptance_by_archetype_preserves_ordering(result):
    """P4 (tân binh) phải nhận thấp hơn hoặc bằng P3 (top) trên một seed."""
    by = driver_metrics(result)["acceptance_by_archetype"]
    assert by["P4"] <= by["P3"]

