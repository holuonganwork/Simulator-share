"""EnvironmentContext — biến môi trường (mưa, nhiệt độ, ngày trong tuần, sự kiện).

Nguồn: specs/environment-variables.md, research/simulation/environment-variables.md.
Nguyên tắc: mọi factor TẮT ĐƯỢC về 1 (scenario dry_weekday → baseline); tham số ở config;
CRN-safe (weather/event series sinh trước, deterministic theo seed).

Ba không gian kết hợp:
  - demand: nhân (Π factor level) + event CỘNG riêng
  - tốc độ / xác suất offline: survival product 1-Π(1-r)
  - pin: tuyến tính có sàn
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .geo import Grid, grid_disk


@dataclass(frozen=True)
class EventSpec:
    venue_cell: str
    t_start_min: float
    t_end_min: float
    attendance: int
    capture_rate: float
    sigma_cells: float
    ramp_in_min: float       # bắt đầu ramp trước t_start
    ramp_lead_min: float     # ramp kết thúc trước t_start
    egress_min: float        # egress kéo dài sau t_end
    egress_boost: float
    # route_effect: cấm/giảm tốc cục bộ quanh venue (dùng bởi CongestionField, KHÔNG nhân demand)
    route_speed_mult: float = 1.0   # <1 = chậm hơn (0.6 = còn 60% tốc độ tại venue); 1 = không ảnh hưởng
    route_sigma_cells: float = 1.5


class EnvironmentContext:
    """Ngữ cảnh môi trường cho 1 run. Sinh rain/temp series + event list từ config+seed."""

    def __init__(self, grid: Grid, cfg: Config, seed: int):
        self.grid = grid
        self.cfg = cfg
        env = cfg.get("environment", {})

        # --- rain series: list[[t_min, mm/h]] nội suy tuyến tính; [] = không mưa ---
        self._rain_series = self._sample_rain_series(env, seed)
        rd = env.get("rain", {}).get("demand", {})
        rs = env.get("rain", {}).get("speed", {})
        rsup = env.get("rain", {}).get("supply", {})
        self.rain_delta_peak = float(rd.get("delta_peak", 0.22))
        self.rain_r_peak = float(rd.get("r_peak_mmph", 8.0))
        self.rain_r_max = float(rs.get("r_max", 0.28))
        self.rain_r0 = float(rs.get("r0_mmph", 9.0))
        self.v_floor = float(rs.get("v_floor_kmh", 7.0))
        self.rain_r_s = float(rsup.get("r_s_mmph", 10.0))
        self.rain_alpha = {k: float(v) for k, v in rsup.get("alpha", {}).items()}
        self.rain_p_cap = float(rsup.get("p_cap", 0.5))

        # --- temp series ---
        tmp = env.get("temp", {})
        self._temp_const = float(tmp.get("const_c", 28.0))
        self._temp_series = tmp.get("series", []) or []
        self.temp_demand_factor = float(tmp.get("demand_factor", 1.0))
        rng_t = tmp.get("range", {})
        self.temp_opt_hi = float(rng_t.get("t_opt_hi_c", 30.0))
        self.temp_beta_hot = float(rng_t.get("beta_hot_per_c", 0.005))
        self.temp_range_floor = float(rng_t.get("floor", 0.85))

        # --- dow ---
        dow = env.get("dow", {})
        self.dow_type = str(dow.get("type", "weekday"))
        self.dow_level = float(dow.get("level", {}).get(self.dow_type, 1.0))

        # --- combine ---
        comb = env.get("combine", {})
        clamp = comb.get("demand_total_clamp", [0.3, 3.0])
        self.m_min, self.m_max = float(clamp[0]), float(clamp[1])
        self.log_clamp = bool(comb.get("log_raw_and_capped", True))
        self._clamp_hits = 0  # đếm số lần M bị bó (quan sát)

        # --- events ---
        self.events = self._build_events(env, grid)

        # --- event dynamics & weather profiles (gsm_sim/event) ---
        self.demand_profile = None
        self.weather_profile = None

    # ---------- rain ----------

    def _sample_rain_series(self, env: dict, seed: int) -> list[tuple[float, float]]:
        """Trả series [(t_min, mm/h)]. Nếu config cho series thì dùng; nếu cho
        'auto' (tần suất thấp ~30ph/ngày) thì sinh 1 đợt mưa ngắn deterministic."""
        rain = env.get("rain", {})
        series = rain.get("series", None)
        if series:
            return [(float(t), float(r)) for t, r in series]
        auto = rain.get("auto", None)
        if not auto:
            return []  # không mưa
        # auto: sinh 1-N đợt mưa ngắn tổng ~duration_min/ngày, cường độ peak
        rng = np.random.default_rng(seed ^ 0x4A17)
        dur = float(auto.get("duration_min_per_day", 30.0))
        peak = float(auto.get("peak_mmph", 12.0))
        start_lo = float(auto.get("window_min", [900, 1200])[0])
        start_hi = float(auto.get("window_min", [900, 1200])[1])
        t0 = rng.uniform(start_lo, start_hi)
        # tam giác: 0 → peak → 0 trong [t0, t0+dur]
        half = dur / 2
        return [(t0, 0.0), (t0 + half, peak), (t0 + dur, 0.0)]

    def rain_mm(self, t_min: float) -> float:
        """Cường độ mưa (mm/h) tại t, nội suy tuyến tính series."""
        s = self._rain_series
        if not s:
            return 0.0
        if t_min <= s[0][0] or t_min >= s[-1][0]:
            return 0.0
        for i in range(1, len(s)):
            if t_min <= s[i][0]:
                t0, r0 = s[i - 1]
                t1, r1 = s[i]
                if t1 == t0:
                    return r1
                frac = (t_min - t0) / (t1 - t0)
                return r0 + frac * (r1 - r0)
        return 0.0

    def rain_demand_factor(self, R: float) -> float:
        """Unimodal (chữ U ngược): tăng rồi giảm khi mưa nặng. R=0 → 1.0."""
        if R <= 0:
            return 1.0
        return 1.0 + self.rain_delta_peak * (R / self.rain_r_peak) * math.exp(1 - R / self.rain_r_peak)

    def rain_speed_slowdown(self, R: float) -> float:
        """r_rain ∈ [0, r_max], bão hòa."""
        if R <= 0:
            return 0.0
        return self.rain_r_max * (1 - math.exp(-R / self.rain_r0))

    def rain_offline_prob(self, R: float, archetype: str) -> float:
        if R <= 0:
            return 0.0
        alpha = self.rain_alpha.get(archetype, 0.2)
        p = alpha * (1 - math.exp(-R / self.rain_r_s))
        return min(self.rain_p_cap, max(0.0, p))

    # ---------- temp ----------

    def temp_c(self, t_min: float) -> float:
        s = self._temp_series
        if not s:
            return self._temp_const
        # nội suy đơn giản
        if t_min <= s[0][0]:
            return float(s[0][1])
        if t_min >= s[-1][0]:
            return float(s[-1][1])
        for i in range(1, len(s)):
            if t_min <= s[i][0]:
                t0, v0 = s[i - 1]
                t1, v1 = s[i]
                frac = (t_min - t0) / (t1 - t0) if t1 != t0 else 0
                return float(v0 + frac * (v1 - v0))
        return self._temp_const

    def range_factor(self, t_min: float) -> float:
        """Nhiệt độ → tầm pin (tuyến tính có sàn). >opt_hi → giảm."""
        T = self.temp_c(t_min)
        excess = max(0.0, T - self.temp_opt_hi)
        return min(1.0, max(self.temp_range_floor, 1.0 - self.temp_beta_hot * excess))

    # ---------- demand combine ----------

    def attach_event_profiles(self, demand_profile=None, weather_profile=None) -> None:
        """Gắn hồ sơ biến thiên nhu cầu và thời tiết vào ngữ cảnh môi trường."""
        self.demand_profile = demand_profile
        self.weather_profile = weather_profile

    def demand_factor(self, t_min: float, cell: str | None = None) -> float:
        """Tích các factor LEVEL (dow × rain × temp × weather_event), clamp tổng. Log khi bị bó."""
        R = self.rain_mm(t_min)
        m = self.dow_level * self.rain_demand_factor(R) * self.temp_demand_factor
        if self.weather_profile is not None:
            from .event import WeatherEventEngine
            m *= WeatherEventEngine.get_effective_demand_multiplier(self.weather_profile, t_min, cell)
        capped = min(self.m_max, max(self.m_min, m))
        if self.log_clamp and capped != m:
            self._clamp_hits += 1
        return capped

    # ---------- speed combine ----------

    def speed_factor(self, t_min: float, congestion_r: float = 0.0, cell: str | None = None) -> float:
        """Survival product: (1-r_rain)·(1-r_congestion)·weather_speed_mult. Luôn ∈ [0, 1]."""
        R = self.rain_mm(t_min)
        base_f = (1.0 - self.rain_speed_slowdown(R)) * (1.0 - max(0.0, min(0.95, congestion_r)))
        if self.weather_profile is not None:
            from .event import WeatherEventEngine
            w_speed = WeatherEventEngine.get_effective_speed_multiplier(self.weather_profile, t_min, cell)
            base_f *= w_speed
        return max(0.0, min(1.0, base_f))

    def is_route_passable(self, t_min: float, pickup_cell: str, drop_cell: str) -> bool:
        """Kiểm tra tuyến đường có bị chia cắt / phong tỏa (ngập lụt cấm đường) không."""
        if self.weather_profile is not None:
            from .event import WeatherEventEngine
            return WeatherEventEngine.is_route_passable(self.weather_profile, t_min, pickup_cell, drop_cell)
        return True

    def get_route_detour_factor(self, t_min: float, pickup_cell: str, drop_cell: str, base_detour: float = 1.3) -> float:
        """Hệ số detour: tăng cao khi xe phải đi đường vòng tránh vùng ngập lụt."""
        if self.weather_profile is not None:
            from .event import WeatherEventEngine
            return WeatherEventEngine.get_route_detour_factor(self.weather_profile, t_min, pickup_cell, drop_cell, base_detour)
        return base_detour

    # ---------- events ----------

    def _build_events(self, env: dict, grid: Grid) -> list[EventSpec]:
        out = []
        for e in env.get("events", []) or []:
            route = e.get("route_effect", {}) or {}
            out.append(EventSpec(
                venue_cell=str(e["venue_cell"]),
                t_start_min=float(e["t_start_min"]),
                t_end_min=float(e["t_end_min"]),
                attendance=int(e.get("attendance", 5000)),
                capture_rate=float(e.get("capture_rate", 0.1)),
                sigma_cells=float(e.get("sigma_cells", 2.0)),
                ramp_in_min=float(e.get("ramp_in_min", 120.0)),
                ramp_lead_min=float(e.get("ramp_lead_min", 15.0)),
                egress_min=float(e.get("egress_min", 60.0)),
                egress_boost=float(e.get("egress_boost", 2.0)),
                route_speed_mult=float(route.get("speed_multiplier", 1.0)),
                route_sigma_cells=float(route.get("sigma_cells", 1.5)),
            ))
        return out

    def event_addend(self, cell: str, t_min: float) -> float:
        """λ_event cộng thêm cho (cell, t) — Gauss không gian × profile thời gian."""
        if not self.events:
            return 0.0
        from .geo import grid_distance
        total = 0.0
        for ev in self.events:
            g_time = self._event_time_profile(ev, t_min)
            if g_time <= 0:
                continue
            gd = grid_distance(cell, ev.venue_cell)
            if gd < 0 or gd > 4 * ev.sigma_cells:
                continue
            g_space = math.exp(-(gd ** 2) / (2 * ev.sigma_cells ** 2))
            n = ev.attendance * ev.capture_rate
            total += n * g_space * g_time
        return total

    def _event_time_profile(self, ev: EventSpec, t: float) -> float:
        """ramp_in trước giờ diễn + egress spike sau khi tan. Chuẩn hóa xấp xỉ /giờ."""
        # ramp in: [t_start - ramp_in, t_start - ramp_lead]
        ramp_start = ev.t_start_min - ev.ramp_in_min
        ramp_end = ev.t_start_min - ev.ramp_lead_min
        if ramp_start <= t <= ramp_end:
            frac = (t - ramp_start) / max(1.0, ramp_end - ramp_start)
            return frac / 60.0  # per-minute rate xấp xỉ
        # egress: [t_end, t_end + egress]
        if ev.t_end_min <= t <= ev.t_end_min + ev.egress_min:
            frac = 1.0 - (t - ev.t_end_min) / max(1.0, ev.egress_min)
            return ev.egress_boost * frac / 60.0
        return 0.0

    # ---------- observability ----------

    def clamp_hits(self) -> int:
        return self._clamp_hits
