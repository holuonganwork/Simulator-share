"""Runner — chạy 1 sim run: seed → config → world → event log.

Slice v0: 1 arm (B). Trả về (events, actors) để metrics/logging xử lý.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .archetypes import sample_actors
from .config import Config
from .congestion import CongestionField
from .demand import Order, generate_orders
from .environment import EnvironmentContext
from .geo import Grid, build_grid
from .policy import PolicyBundle
from .world import Event, World


@dataclass
class RunResult:
    seed: int
    events: list[Event]
    actors: list
    orders: list[Order]
    config: Config
    policy: PolicyBundle
    grid: Grid
    # Canonical identity emitted by World/derive_run_id.  Keeping it on the result is
    # required for trace/session/checkpoint joins; callers that construct synthetic
    # fixtures may leave the compatibility default empty.
    run_id: str = ""
    env: EnvironmentContext | None = None
    congestion: CongestionField | None = None
    traj: list = field(default_factory=list)
    trace_snapshots: list[dict] = field(default_factory=list)
    segments: list = field(default_factory=list)
    stations: list = field(default_factory=list)
    order_states: dict = field(default_factory=dict)
    advice_artifacts: list = field(default_factory=list)
    advice_checkpoints: list = field(default_factory=list)
    advice_checkpoint_events: list = field(default_factory=list)
    execution_links: list = field(default_factory=list)
    pending_execution_observations: list = field(default_factory=list)


def _data(cfg: Config, fname_key: str) -> Path:
    data_dir = cfg.resolve_path("world.data_dir")
    return data_dir / cfg.get(f"world.{fname_key}")


# Tên rút gọn cho kênh advice trong run_id — thứ tự cố định (không phụ thuộc dict order)
_CHANNEL_ABBREV = (("shift_plan", "sp"), ("accept_lift", "al"),
                   ("shift_extend", "se"), ("rest_window", "rw"))


def derive_run_id(cfg: Config, seed: int) -> str:
    """Run identity DETERMINISTIC từ (cfg advice, seed) — ĐA-05 Cycle W.

    Khác `logging_ev.write_run` (wall-clock, chỉ đặt tên folder): run_id này derive thuần
    nên exact-repeat cùng cfg+seed cho cùng ID — điều kiện để lifecycle event của sim
    replay/join được. Cặp A/B của `run_pair` CÙNG seed nhưng khác arm ⇒ khác run_id;
    A được cache xuyên ladder giữ MỘT run_id (nó là một run vật lý — chốt plan Cycle W).
    Multi-day: mỗi ngày một seed (`multiday.day_seed`) nên ngày đã nằm trong seed.

    ## Vì sao có digest — phần đọc-được KHÔNG đủ làm identity

    Bản đầu chỉ đọc block `advice`, nên hai run vật lý KHÁC HẲN nhau (đổi
    `environment.scenario`/`dow`, `demand.orders_per_day`, `advice.bucket_min`,
    `advice.share`…) nhận CÙNG run_id. Hai review đối kháng độc lập cùng chứng minh hậu
    quả tại store canonical: `INSERT OR IGNORE` coi event của run thứ hai là trùng và
    **nuốt im lặng** (đo được 128 event_id + 889 decision_id đụng nhau giữa hai run chỉ
    khác `dow`). Vì thế ID mang thêm digest của TOÀN BỘ config, trừ `meta` (thuần mô tả:
    nhãn mock/version, không đổi hành vi).

    Dạng: `{seed}-{A|B}-{kênh|none}-{coverage[.actor]}[-pos.{mode}]-c{digest8}`
    """
    adv = cfg.get("advice", {}) or {}
    arm = "B" if adv.get("enabled") else "A"
    ch = adv.get("channels", {}) or {}
    on = "+".join(abbr for name, abbr in _CHANNEL_ABBREV if ch.get(name)) or "none"
    cov = str(adv.get("coverage", "single"))
    if cov == "single" and adv.get("single_actor_id") is not None:
        cov += f".{adv['single_actor_id']}"
    rid = f"{seed}-{arm}-{on}-{cov}"
    pos = adv.get("positioning_overrides", "off")
    if pos and pos != "off":
        rid += f"-pos.{pos}"
    body = {k: v for k, v in (cfg.data or {}).items() if k != "meta"}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=_digest_default).encode()
    ).hexdigest()[:8]
    return f"{rid}-c{digest}"


def _digest_default(v):
    """Encoder cho digest run_id — X-2 (review batch 2): `default=str` phá identity
    CẢ HAI chiều: np.int64(30) ≠ 30 (cùng semantic, khác ID — str thêm ngoặc kép);
    `set` ⇒ ID đổi theo PYTHONHASHSEED (phá exact-repeat, đo được 6 process 6 ID);
    datetime(2026,1,1) == chuỗi "2026-01-01 00:00:00" (khác semantic, CÙNG ID — chiều
    nuốt-run của W-1). Normalize semantic-preserving; kiểu lạ nổ TypeError tường minh."""
    import datetime as _dt
    if (getattr(v, "item", None) is not None
            and type(v).__module__ != "builtins"
            and getattr(v, "ndim", None) == 0):
        return v.item()                                  # numpy scalar → Python thuần
    if isinstance(v, (_dt.datetime, _dt.date)):
        return f"__dt__{v.isoformat()}"                  # tag để không trùng chuỗi thường
    raise TypeError(
        f"config chứa kiểu {type(v).__name__} không digest được deterministic — "
        f"run_id là identity của store append-only, không được phụ thuộc str() ngẫu nhiên")


def build_environment(grid: Grid, cfg: Config, seed: int, day_index: int = 0,
                      date_str: str | None = None) -> EnvironmentContext | None:
    """Tạo EnvironmentContext nếu config có block `environment` hoặc `event_dynamics.enabled`.
    Scenario mặc định dry_weekday (mọi factor=1) ⇒ tương đương env=None (baseline).
    Không tiêu RNG khi không mưa/sự kiện ⇒ giữ CRN với các run không-env.
    Đồng thời tự động gắn event profiles (demand_variation & weather_event) nếu event_dynamics được bật."""
    has_legacy_env = cfg.get("environment", None) is not None
    ed_cfg = cfg.get("event_dynamics", {})
    has_event_dynamics = bool(ed_cfg.get("enabled", False))

    if not has_legacy_env and not has_event_dynamics:
        return None
    env = EnvironmentContext(grid, cfg, seed)

    if has_event_dynamics:
        from .event import DailyDemandVariation, WeatherEventEngine
        cur_date = date_str or str(ed_cfg.get("start_date", "2026-07-01"))

        # 1. Biến thiên nhu cầu theo ngày
        dv_cfg = ed_cfg.get("demand_variation", {})
        if dv_cfg.get("enabled", True):
            base_orders = float(cfg.get("demand.orders_per_day", 50000.0))
            dv_engine = DailyDemandVariation(
                base_orders_per_day=base_orders,
                stochastic_sigma=float(dv_cfg.get("stochastic_sigma", 0.045)),
                stochastic_clamp=tuple(dv_cfg.get("stochastic_clamp", [0.88, 1.15])),
                enabled=True,
            )
            d_profile = dv_engine.evaluate_day(cur_date, day_index=day_index, seed=seed)
        else:
            d_profile = None

        # 2. Sự kiện thời tiết & ngập lụt
        wt_cfg = ed_cfg.get("weather", {})
        if wt_cfg.get("enabled", True):
            wt_engine = WeatherEventEngine(
                probabilities=wt_cfg.get("probabilities", None),
                pleasant_speed_mult=float(wt_cfg.get("pleasant_speed_mult", 1.0)),
                pleasant_demand_mult=float(wt_cfg.get("pleasant_demand_mult", 1.0)),
                rain_speed_mult=float(wt_cfg.get("rain_speed_mult", 0.85)),
                rain_demand_mult=float(wt_cfg.get("rain_demand_mult", 0.92)),
                fog_speed_mult=float(wt_cfg.get("fog_speed_mult", 0.58)),
                fog_demand_mult=float(wt_cfg.get("fog_demand_mult", 0.94)),
                flood_city_demand_mult=float(wt_cfg.get("flood_city_demand_mult", 0.72)),
                flood_severe_speed_mult=float(wt_cfg.get("flood_severe_speed_mult", 0.25)),
                core_cells=grid.core_cells,
                enabled=True,
            )
            w_profile = wt_engine.evaluate_day(cur_date, day_index=day_index, seed=seed)
        else:
            w_profile = None

        env.attach_event_profiles(demand_profile=d_profile, weather_profile=w_profile)

    return env


def build_world(cfg: Config, seed: int, human_actor_id: int | None = None,
                hil_gate=None, day_index: int = 0,
                date_str: str | None = None) -> tuple[World, dict]:
    """Dựng World + mọi phụ thuộc, KHÔNG chạy. Trả `(world, deps)`.

    Tách ra khỏi `run_once` cho HIL Lát 1: phiên tương tác cần một World **sống** để tự lái
    `env.run(until=...)` theo lát. Tách thay vì nhân bản là có lý do cụ thể — hai đường dựng
    World song song sẽ trôi khỏi nhau và phiên HIL sẽ chạy trên một thế giới khác thế giới đo
    A/B, im lặng."""
    grid = build_grid(
        geom_path=_data(cfg, "geom_file"),
        stations_path=_data(cfg, "stations_file"),
        poi_path=_data(cfg, "poi_file"),
        res=int(cfg.get("world.h3_res")),
        res_report=int(cfg.get("world.h3_res_report")),
    )
    policy = PolicyBundle.from_config(cfg)
    env = build_environment(grid, cfg, seed, day_index=day_index, date_str=date_str)
    from .geo import load_road_matrix
    road = load_road_matrix(cfg, grid)      # SIM-XANH: fare theo đường thật (None = tắt)
    orders = generate_orders(grid, cfg, policy, seed, env=env, road=road)
    congestion = CongestionField(orders, cfg, env=env)
    actors = sample_actors(grid, cfg, seed)
    world = World(grid, cfg, policy, orders, actors, seed, environment=env, congestion=congestion,
                  run_id=derive_run_id(cfg, seed), human_actor_id=human_actor_id,
                  hil_gate=hil_gate)
    # Gate hỏi `world.env` nên chỉ gắn được SAU khi World tồn tại. Gắn ở đây để không nơi nào
    # khác phải nhớ bước này — quên gắn thì nổ `AttributeError: 'NoneType' has no attribute env`
    # giữa lúc chạy, rất khó truy về nguyên nhân thật.
    if hil_gate is not None and hasattr(hil_gate, "attach_world"):
        hil_gate.attach_world(world)
    deps = {"seed": seed, "actors": actors, "orders": orders, "cfg": cfg, "policy": policy,
            "grid": grid, "env": env, "congestion": congestion}
    return world, deps


def result_from_world(world: World, deps: dict) -> RunResult:
    """Gói World đã chạy xong thành `RunResult`. Dùng chung bởi `run_once` và phiên HIL."""
    segments, trace = finalize_checkpoint_trace(world, world.events)
    return RunResult(seed=deps["seed"], events=world.events, actors=deps["actors"],
                     orders=deps["orders"], config=deps["cfg"], policy=deps["policy"],
                     grid=deps["grid"], env=deps["env"], run_id=world.run_id,
                     congestion=deps["congestion"], traj=world.traj, segments=segments,
                     trace_snapshots=world.trace_snapshots,
                     stations=world.stations, order_states=world.order_states,
                     **trace)


def run_once(cfg: Config, seed: int, human_actor_id: int | None = None,
             hil_gate=None, day_index: int = 0,
             date_str: str | None = None) -> RunResult:
    """Chạy một ngày mô phỏng.

    `human_actor_id` (HIL Lát 1): khi khác `None`, actor đó **không tự quyết** — `decide_accept`
    và `choose_idle_action` của riêng nó được thay bằng lời gọi tới `world.hil_gate`. Mặc định
    `None` ⇒ **hành vi cũ, không đổi một bit** (cổng vân tay 5 seed:
    `tests/test_hil_world_unchanged.py`).

    Không truyền `hil_gate` thì mặc định là `InstinctGate` — gate trả nguyên giá trị bản năng và
    **không yield lần nào**, nên `run_once(cfg, seed, human_actor_id=X)` phải cho vân tay TRÙNG
    KHÍT `run_once(cfg, seed)`. Đó là phép đo chứng minh hai seam trong `world.py` là trơ.

    ⚠ `run_once` chạy tới hết ngày ngay lập tức, không dừng chờ ai. Phiên **tương tác** dùng
    `gsm_sim.hil.session.InteractiveSimSession` (nó tự lái `env.run(until=...)` theo lát)."""
    if human_actor_id is not None and hil_gate is None:
        from .hil.session import InstinctGate
        hil_gate = InstinctGate(human_actor_id)
    world, deps = build_world(cfg, seed, human_actor_id=human_actor_id, hil_gate=hil_gate,
                              day_index=day_index, date_str=date_str)
    world.run()
    return result_from_world(world, deps)


def finalize_checkpoint_trace(world: World, events: list[Event]) -> tuple[list, dict]:
    """Attach diagnostic identities and resolve observations after the run."""
    sink = world.checkpoint_trace
    if not sink.enabled:
        return world.segments, {
            "advice_artifacts": [], "advice_checkpoints": [],
            "advice_checkpoint_events": [], "execution_links": [],
            "pending_execution_observations": [],
        }
    from .checkpoint_trace import annotate_segment_ids
    segments = annotate_segment_ids(world.run_id, world.segments)
    sink.finalize_execution_links(segments, events)
    return segments, {
        "advice_artifacts": sink.artifacts,
        "advice_checkpoints": sink.checkpoints,
        "advice_checkpoint_events": sink.events,
        "execution_links": sink.execution_links,
        "pending_execution_observations": sink.pending_execution_observations,
    }
