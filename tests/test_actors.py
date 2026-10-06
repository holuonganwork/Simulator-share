"""Test actor sampling + behavior model."""

from pathlib import Path

import numpy as np
import pytest

from gsm_sim import geo
from gsm_sim.archetypes import sample_actors
from gsm_sim.behavior import IdleAction, accept_order, choose_idle_action
from gsm_sim.config import Config
from gsm_sim.entities import ActorState, FleetType

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "research" / "simulation" / "data"


@pytest.fixture(scope="module")
def cfg():
    return Config.load(ROOT / "configs" / "pilot_hanoi.yaml")


@pytest.fixture(scope="module")
def grid():
    return geo.build_grid(DATA / "hanoi_geom.json", DATA / "batt_hanoi.json", DATA / "poi_hanoi.json", 7, 6)


def test_sample_count(grid, cfg):
    """SIM-1: đọc `actors.n` từ CONFIG, không hard-code. Trước đây khoá cứng 50 nên
    mọi lần hiệu chỉnh cung đều làm test đỏ mà KHÔNG chỉ ra khuyết tật nào."""
    actors = sample_actors(grid, cfg, seed=1)
    assert len(actors) == int(cfg.get("actors.n"))


def test_archetype_mix(grid, cfg):
    """Kiểm tra SAMPLER tôn trọng tỷ trọng trong config (không kiểm tra chính config)."""
    actors = sample_actors(grid, cfg, seed=1)
    from collections import Counter
    c = Counter(a.archetype for a in actors)
    n = int(cfg.get("actors.n"))
    mix = cfg.get("actors.archetype_mix")
    assert sum(c.values()) == n
    for name, share in mix.items():
        assert abs(c[name] - round(n * float(share))) <= 1, (
            f"{name}: sampler cho {c[name]}, tỷ trọng {share} trên n={n} kỳ vọng "
            f"{round(n * float(share))}")
    # thứ tự tương đối phải giữ: archetype có tỷ trọng lớn hơn thì đông hơn (hoặc bằng)
    ranked = sorted(mix, key=lambda k: -float(mix[k]))
    assert c[ranked[0]] >= c[ranked[-1]]


def test_actors_deterministic(grid, cfg):
    a1 = sample_actors(grid, cfg, seed=5)
    a2 = sample_actors(grid, cfg, seed=5)
    assert [(a.actor_id, a.archetype, a.home_cell, a.fleet) for a in a1] == \
           [(a.actor_id, a.archetype, a.home_cell, a.fleet) for a in a2]


def test_home_in_core(grid, cfg):
    actors = sample_actors(grid, cfg, seed=2)
    assert all(grid.is_core(a.home_cell) for a in actors)


def test_fleet_assignment(grid, cfg):
    actors = sample_actors(grid, cfg, seed=1)
    # P2/P4 luôn swap; P1 luôn charge
    for a in actors:
        if a.archetype == "P2" or a.archetype == "P4":
            assert a.fleet == FleetType.SWAP
        if a.archetype == "P1":
            assert a.fleet == FleetType.CHARGE


def test_accept_forced(grid, cfg):
    actors = sample_actors(grid, cfg, seed=1)
    rng = np.random.default_rng(0)
    assert accept_order(actors[0], 20000, 1.0, forced=True, rng=rng) is True


def test_accept_probabilistic(grid, cfg):
    actors = sample_actors(grid, cfg, seed=1)
    rng = np.random.default_rng(0)
    # đơn giá trị cao → nhận nhiều; đơn xấu (pickup xa, gross thấp) → nhận ít
    hi = sum(accept_order(actors[0], 40000, 0.3, False, rng) for _ in range(200))
    lo = sum(accept_order(actors[0], 12000, 4.0, False, rng) for _ in range(200))
    assert hi > lo


def test_idle_low_soc_goes_charge(grid, cfg):
    actors = sample_actors(grid, cfg, seed=1)
    a = actors[0]
    a.soc_pct = 10.0  # dưới ngưỡng 20
    a.state = ActorState.IDLE
    rng = np.random.default_rng(0)
    veh = cfg.get("vehicle")
    action, _ = choose_idle_action(a, now_min=600, grid=grid, cfg_vehicle=veh, hour=10, demand_hint=None, rng=rng)
    assert action in (IdleAction.GO_SWAP, IdleAction.GO_CHARGE)


def test_idle_past_shift_end(grid, cfg):
    actors = sample_actors(grid, cfg, seed=1)
    a = actors[0]
    a.soc_pct = 80.0
    a.shift_end_min = 500.0
    rng = np.random.default_rng(0)
    veh = cfg.get("vehicle")
    action, _ = choose_idle_action(a, now_min=600, grid=grid, cfg_vehicle=veh, hour=10, demand_hint=None, rng=rng)
    assert action == IdleAction.END_SHIFT
