"""Mô-đun Quản lý Sự kiện Thời tiết & Môi trường Cực đoan (Weather & Environmental Events Engine).

Phục vụ làm giàu dữ liệu (Data Enrichment) cho GSM_SIMULATOR-rl.
Mô hình hóa chính xác các hiện tượng thời tiết đô thị thực tế:
1. Mát trời (PLEASANT):
   - Tần suất cao nhất (~78% số ngày, thời tiết lý tưởng).
   - Tần suất khách bình thường (demand_factor = 1.0).
   - Vận tốc di chuyển bình thường (speed_factor = 1.0).
   - Toàn bộ mạng lưới đường sá thông suốt 100%.

2. Trời mưa (RAIN):
   - Tần suất ít hơn (~18% số ngày).
   - Nhu cầu khách giảm đôi chút (demand_factor ~0.90 - 0.94 do ngại ra đường/hủy hẹn).
   - Vận tốc di chuyển giảm đôi chút (speed_factor ~0.82 - 0.86 do đường trơn, tầm nhìn giảm).

3. Sương mù (FOG):
   - Tần suất siêu hiếm (~2.5% số ngày, thường tập trung sáng sớm 05:00 - 09:00).
   - Vận tốc di chuyển giảm đi đáng kể (speed_factor ~0.55 - 0.65, tức giảm 35-45% do tầm nhìn hạn chế <50m).
   - Sương mù tan dần sau 09:00 khi có ánh nắng mặt trời.

4. Lũ lụt / Ngập úng đô thị (FLOOD):
   - Tần suất siêu hiếm (~1.5% số ngày, bão lớn/mưa cực đoan diện rộng).
   - Tác động không gian cục bộ (Spatio-Temporal Impact):
     + Một số tuyến đường/ô bị ngập sâu (>0.5m) KHÔNG THỂ ĐI ĐƯỢC (is_blocked = True).
       Xe điện (EV) tuyệt đối cấm lưu thông để chống ngập pin và thủy kích.
     + Một số tuyến đường/ô lân cận thì VẬN TỐC GIẢM TRẦM TRỌNG (speed_factor ~0.20 - 0.30, chỉ bò 3-5 km/h).
     + Nhu cầu khách tại các ô ngập sâu giảm mạnh, khách bị cô lập hoặc hủy đơn.

5. Bảo toàn hạt giống ngẫu nhiên (CRN-Safe & Deterministic):
   - Đảm bảo 100% tái lập khi chạy lại cùng seed và cùng ngày.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as _date, timedelta
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np


class WeatherType(str, Enum):
    """Phân loại hình thái thời tiết."""
    PLEASANT = "pleasant"  # Mát trời, bình thường
    RAIN = "rain"          # Trời mưa
    FOG = "fog"            # Sương mù dày đặc
    FLOOD = "flood"        # Lũ lụt, ngập úng đô thị


@dataclass(frozen=True)
class CellWeatherState:
    """Trạng thái môi trường chi tiết tại một ô H3 cụ thể."""
    cell: str
    is_blocked: bool = False                # True: đường bị ngập sâu, cấm lưu thông tuyệt đối
    speed_multiplier: float = 1.0           # Hệ số tốc độ tại ô này (0.0 nếu blocked)
    demand_multiplier: float = 1.0          # Hệ số nhu cầu phát sinh tại ô này
    flood_depth_cm: float = 0.0             # Mực nước ngập ước tính (cm)
    status_label: str = "Bình thường"       # Diễn giải trạng thái ô


@dataclass(frozen=True)
class WeatherDayProfile:
    """Hồ sơ môi trường thời tiết của một ngày cụ thể."""
    date: str                               # YYYY-MM-DD
    day_index: int                          # Thứ tự ngày (0, 1, 2, ...)
    weather_type: WeatherType               # Loại thời tiết trong ngày
    description: str                        # Diễn giải nghiệp vụ chi tiết
    
    # Khung thời gian tác động chính (phút tính từ 00:00)
    event_start_min: int                    # Thời điểm bắt đầu tác động
    event_end_min: int                      # Thời điểm kết thúc tác động
    
    # Các hệ số vĩ mô toàn thành phố (Global Multipliers)
    base_demand_multiplier: float           # Tác động nhu cầu toàn thành
    base_speed_multiplier: float            # Tác động vận tốc toàn thành
    
    # Tác động không gian cục bộ (Spatial impacts cho các ô cụ thể)
    # Đặc biệt quan trọng cho sự kiện Lũ lụt / Ngập úng
    blocked_cells: frozenset[str]           # Các ô ngập sâu bị phong tỏa hoàn toàn (vận tốc = 0)
    severe_slowdown_cells: frozenset[str]   # Các ô ngập vừa với vận tốc giảm trầm trọng
    cell_states: dict[str, CellWeatherState] # Trạng thái chi tiết từng ô bị ảnh hưởng
    
    # Thống kê nhanh
    num_blocked_cells: int
    num_severe_cells: int


class WeatherEventEngine:
    """Bộ máy mô phỏng thời tiết và các sự kiện thiên tai đô thị."""

    # Tần suất xác suất xuất hiện mặc định theo yêu cầu:
    # Mát trời cao nhất, mưa ít, lũ lụt và sương mù siêu hiếm
    DEFAULT_WEATHER_PROBABILITIES = {
        WeatherType.PLEASANT: 0.780,  # 78.0% - Mát trời (tần suất cao nhất)
        WeatherType.RAIN:     0.180,  # 18.0% - Mưa (ít hơn)
        WeatherType.FOG:      0.025,  #  2.5% - Sương mù (siêu hiếm)
        WeatherType.FLOOD:    0.015,  #  1.5% - Lũ lụt / Ngập úng (siêu hiếm)
    }

    # Danh mục điểm trũng ngập lụt kinh điển thực tế tại Hà Nội (H3 Res 7 mock representation)
    # Các tuyến: Thái Hà, Chùa Bộc, Nguyễn Khuyến, Phùng Hưng, hầm chui Thăng Long, Triều Khúc, Hoa Bằng...
    # Sẽ được ánh xạ từ core_cells nếu có, hoặc tạo tự động qua spatial clustering
    DEFAULT_FLOOD_PRONE_RATIO = 0.12  # Khi ngập lụt, ~12% số ô vùng trũng bị ngập

    def __init__(
        self,
        probabilities: Mapping[WeatherType, float] | None = None,
        pleasant_speed_mult: float = 1.00,
        pleasant_demand_mult: float = 1.00,
        rain_speed_mult: float = 0.84,        # Vận tốc giảm ~16% khi mưa
        rain_demand_mult: float = 0.92,       # Khách giảm ~8% khi mưa (đúng yêu cầu user)
        fog_speed_mult: float = 0.58,         # Vận tốc giảm ~42% khi sương mù (giảm đáng kể)
        fog_demand_mult: float = 0.94,        # Khách giảm nhẹ khi sương mù
        flood_city_demand_mult: float = 0.72, # Cầu toàn thành giảm ~28% do lũ lụt
        flood_severe_speed_mult: float = 0.25,# Ô ngập vừa: vận tốc giảm trầm trọng còn 25% (3-5 km/h)
        core_cells: Sequence[str] | None = None,
        enabled: bool = True,
    ):
        self.enabled = bool(enabled)
        self.probabilities = dict(self.DEFAULT_WEATHER_PROBABILITIES)
        if probabilities:
            self.probabilities.update(probabilities)
        
        # Chuẩn hóa tổng xác suất = 1.0
        total_p = sum(self.probabilities.values())
        self.probabilities = {k: v / total_p for k, v in self.probabilities.items()}

        self.pleasant_speed_mult = float(pleasant_speed_mult)
        self.pleasant_demand_mult = float(pleasant_demand_mult)
        self.rain_speed_mult = float(rain_speed_mult)
        self.rain_demand_mult = float(rain_demand_mult)
        self.fog_speed_mult = float(fog_speed_mult)
        self.fog_demand_mult = float(fog_demand_mult)
        self.flood_city_demand_mult = float(flood_city_demand_mult)
        self.flood_severe_speed_mult = float(flood_severe_speed_mult)

        self.core_cells = list(core_cells or [])

    def set_core_cells(self, cells: Sequence[str]) -> None:
        """Cập nhật danh sách các ô trong mạng lưới mô phỏng."""
        self.core_cells = list(cells)

    def _sample_weather_type(self, rng: np.random.Generator) -> WeatherType:
        """Lấy mẫu ngẫu nhiên loại thời tiết theo phân phối xác suất tích lũy."""
        if not self.enabled:
            return WeatherType.PLEASANT

        types = list(self.probabilities.keys())
        probs = [self.probabilities[t] for t in types]
        return types[rng.choice(len(types), p=probs)]

    def _generate_flood_spatial_impact(
        self,
        rng: np.random.Generator,
        cells: list[str],
    ) -> tuple[frozenset[str], frozenset[str], dict[str, CellWeatherState]]:
        """Mô hình hóa ngập úng không gian: một số đường cấm đi, một số đường giảm tốc trầm trọng."""
        if not cells:
            return frozenset(), frozenset(), {}

        n_total = len(cells)
        # Số tâm ngập úng (epicenter) từ 2 đến 4 cụm trũng
        n_clusters = int(rng.integers(2, 5))
        
        # Chọn các tâm ngập
        shuffled = list(cells)
        rng.shuffle(shuffled)
        epicenters = shuffled[:n_clusters]

        blocked_set: set[str] = set()
        severe_set: set[str] = set()
        cell_states: dict[str, CellWeatherState] = {}

        # 1. Các ô ngập sâu (KHÔNG THỂ ĐI ĐƯỢC - Impassable / Blocked):
        # Chiếm khoảng 3-5% tổng số ô (các rốn ngập sâu >50cm, ngập pin xe điện)
        n_blocked = max(1, int(round(n_total * 0.04)))
        blocked_candidates = shuffled[:n_blocked]
        for c in blocked_candidates:
            blocked_set.add(c)
            cell_states[c] = CellWeatherState(
                cell=c,
                is_blocked=True,
                speed_multiplier=0.0,            # Hoàn toàn cấm lưu thông
                demand_multiplier=0.10,          # Khách bị cô lập, hủy cuốc
                flood_depth_cm=float(rng.uniform(50.0, 90.0)),
                status_label="Ngập sâu cấm đường (xe điện không thể qua)",
            )

        # 2. Các ô ngập vừa (VẬN TỐC GIẢM TRẦM TRỌNG - Severe Slowdown):
        # Chiếm khoảng 8-12% tổng số ô (nước ngập 20-40cm, xe chỉ bò được 3-5 km/h)
        n_severe = max(2, int(round(n_total * 0.10)))
        severe_candidates = shuffled[n_blocked : n_blocked + n_severe]
        for c in severe_candidates:
            severe_set.add(c)
            depth = float(rng.uniform(20.0, 45.0))
            cell_states[c] = CellWeatherState(
                cell=c,
                is_blocked=False,
                speed_multiplier=self.flood_severe_speed_mult,  # Giảm trầm trọng (0.25)
                demand_multiplier=0.45,                         # Nhu cầu giảm một nửa
                flood_depth_cm=depth,
                status_label=f"Ngập cục bộ {depth:.0f}cm (vận tốc giảm trầm trọng)",
            )

        return frozenset(blocked_set), frozenset(severe_set), cell_states

    def evaluate_day(
        self,
        date_str: str,
        day_index: int = 0,
        seed: int = 7000,
        override_type: WeatherType | str | None = None,
        available_cells: Sequence[str] | None = None,
    ) -> WeatherDayProfile:
        """Đánh giá toàn diện hồ sơ thời tiết và tác động môi trường của một ngày cụ thể."""
        cells = list(available_cells or self.core_cells)
        
        # Sinh seed độc lập cho stream thời tiết (CRN-Safe)
        rng = np.random.default_rng((seed, day_index, 0x57EA78))  # "WEATH" stream

        if override_type is not None:
            w_type = WeatherType(override_type)
        else:
            w_type = self._sample_weather_type(rng)

        # 1. Kịch bản Mát trời (PLEASANT)
        if w_type == WeatherType.PLEASANT or not self.enabled:
            desc = "Thời tiết mát mẻ, đường sá khô ráo, vận hành tiêu chuẩn 100%"
            return WeatherDayProfile(
                date=date_str,
                day_index=day_index,
                weather_type=WeatherType.PLEASANT,
                description=desc,
                event_start_min=0,
                event_end_min=1440,
                base_demand_multiplier=self.pleasant_demand_mult,
                base_speed_multiplier=self.pleasant_speed_mult,
                blocked_cells=frozenset(),
                severe_slowdown_cells=frozenset(),
                cell_states={},
                num_blocked_cells=0,
                num_severe_cells=0,
            )

        # 2. Kịch bản Trời mưa (RAIN)
        if w_type == WeatherType.RAIN:
            # Nhiễu nhẹ ngẫu nhiên về lượng mưa
            rain_speed = round(float(self.rain_speed_mult * rng.uniform(0.97, 1.03)), 3)
            rain_demand = round(float(self.rain_demand_mult * rng.uniform(0.98, 1.02)), 3)
            # Mưa rào diễn ra trong ngày (thường bắt đầu từ trưa chiều hoặc rải rác)
            start_m = int(rng.integers(360, 720))  # 6h - 12h
            dur_m = int(rng.integers(240, 540))    # Kéo dài 4 - 9 tiếng
            end_m = min(1440, start_m + dur_m)
            
            desc = (f"Trời mưa rào ({start_m//60}h00 - {end_m//60}h00) · "
                    f"Khách giảm nhẹ (x{rain_demand:.2f}) · Vận tốc giảm nhẹ (x{rain_speed:.2f})")
            
            return WeatherDayProfile(
                date=date_str,
                day_index=day_index,
                weather_type=WeatherType.RAIN,
                description=desc,
                event_start_min=start_m,
                event_end_min=end_m,
                base_demand_multiplier=rain_demand,
                base_speed_multiplier=rain_speed,
                blocked_cells=frozenset(),
                severe_slowdown_cells=frozenset(),
                cell_states={},
                num_blocked_cells=0,
                num_severe_cells=0,
            )

        # 3. Kịch bản Sương mù dày đặc (FOG)
        if w_type == WeatherType.FOG:
            # Sương mù dày đặc sáng sớm: 04:30 - 09:00
            start_m = int(rng.integers(270, 330))  # 4h30 - 5h30 sáng
            end_m = int(rng.integers(510, 570))    # 8h30 - 9h30 sáng tan dần
            fog_speed = round(float(self.fog_speed_mult * rng.uniform(0.96, 1.04)), 3)
            fog_demand = round(float(self.fog_demand_mult * rng.uniform(0.98, 1.02)), 3)

            desc = (f"Sương mù dày đặc sáng sớm ({start_m//60}h{start_m%60:02d} - {end_m//60}h{end_m%60:02d}) · "
                    f"Tầm nhìn <50m · Vận tốc giảm đáng kể (x{fog_speed:.2f})")

            return WeatherDayProfile(
                date=date_str,
                day_index=day_index,
                weather_type=WeatherType.FOG,
                description=desc,
                event_start_min=start_m,
                event_end_min=end_m,
                base_demand_multiplier=fog_demand,
                base_speed_multiplier=fog_speed,
                blocked_cells=frozenset(),
                severe_slowdown_cells=frozenset(),
                cell_states={},
                num_blocked_cells=0,
                num_severe_cells=0,
            )

        # 4. Kịch bản Lũ lụt / Ngập úng đô thị (FLOOD)
        # Bão lớn hoặc mưa ngập diện rộng, bắt đầu từ sáng/trưa kéo dài đến đêm
        start_m = int(rng.integers(420, 600))  # 7h - 10h sáng
        end_m = 1440                           # Nước rút chậm, kéo dài hết ngày
        blocked, severe, cell_states = self._generate_flood_spatial_impact(rng, cells)

        desc = (f"Cảnh báo Thiên tai: Ngập lụt đô thị trên diện rộng ({start_m//60}h00 - 24h00) · "
                f"{len(blocked)} ô ngập sâu cấm đường tuyệt đối · "
                f"{len(severe)} ô ngập vừa vận tốc giảm trầm trọng (x{self.flood_severe_speed_mult:.2f}) · "
                f"Cầu toàn thành giảm x{self.flood_city_demand_mult:.2f}")

        return WeatherDayProfile(
            date=date_str,
            day_index=day_index,
            weather_type=WeatherType.FLOOD,
            description=desc,
            event_start_min=start_m,
            event_end_min=end_m,
            base_demand_multiplier=self.flood_city_demand_mult,
            base_speed_multiplier=0.65,  # Các ô không ngập cũng bị kẹt xe lan tỏa
            blocked_cells=blocked,
            severe_slowdown_cells=severe,
            cell_states=cell_states,
            num_blocked_cells=len(blocked),
            num_severe_cells=len(severe),
        )

    def evaluate_calendar(
        self,
        start_date: str,
        days: int,
        seed: int = 7000,
        special_weather_map: dict[str, WeatherType | str] | None = None,
        available_cells: Sequence[str] | None = None,
    ) -> list[WeatherDayProfile]:
        """Tạo chuỗi hồ sơ thời tiết cho nhiều ngày liên tiếp (ví dụ 30 ngày, 90 ngày)."""
        d0 = _date.fromisoformat(start_date)
        profiles: list[WeatherDayProfile] = []
        specials = special_weather_map or {}

        for i in range(days):
            cur_date = (d0 + timedelta(days=i)).isoformat()
            forced_type = specials.get(cur_date, None)
            prof = self.evaluate_day(
                date_str=cur_date,
                day_index=i,
                seed=seed,
                override_type=forced_type,
                available_cells=available_cells,
            )
            profiles.append(prof)

        return profiles

    # =========================================================================
    # CÁC HÀM TRUY VẤN TỐC ĐỘ, NHU CẦU & ĐƯỜNG ĐI CHO SIMULATOR
    # =========================================================================

    @staticmethod
    def get_effective_speed_multiplier(
        profile: WeatherDayProfile,
        t_min: float,
        cell: str | None = None,
    ) -> float:
        """Tính hệ số nhân vận tốc thực tế tại một ô và thời điểm cụ thể.
        
        Trả về:
        - 0.0 nếu ô bị ngập sâu cấm đường (impassable).
        - ~0.20 - 0.30 nếu ô bị ngập vừa (vận tốc giảm trầm trọng).
        - ~0.55 - 0.65 nếu có sương mù dày đặc (trong khung giờ sáng).
        - ~0.82 - 0.86 nếu trời mưa.
        - 1.0 nếu mát trời bình thường hoặc ngoài khung giờ sự kiện.
        """
        if profile.weather_type == WeatherType.PLEASANT:
            return 1.0

        # Nếu thời điểm nằm ngoài khung giờ sự kiện -> hồi phục tốc độ chuẩn
        if t_min < profile.event_start_min or t_min > profile.event_end_min:
            return 1.0

        # Kiểm tra trạng thái đặc thù của ô trong sự kiện ngập lụt
        if cell is not None and profile.weather_type == WeatherType.FLOOD:
            if cell in profile.blocked_cells:
                return 0.0  # Hoàn toàn không thể đi được
            if cell in profile.severe_slowdown_cells:
                st = profile.cell_states.get(cell)
                return st.speed_multiplier if st else profile.base_speed_multiplier * 0.4

        # Trả về hệ số tốc độ chung của sự kiện
        return profile.base_speed_multiplier

    @staticmethod
    def get_effective_demand_multiplier(
        profile: WeatherDayProfile,
        t_min: float,
        cell: str | None = None,
    ) -> float:
        """Tính hệ số nhân nhu cầu khách thực tế tại một ô và thời điểm cụ thể."""
        if profile.weather_type == WeatherType.PLEASANT:
            return 1.0

        if t_min < profile.event_start_min or t_min > profile.event_end_min:
            return 1.0

        if cell is not None and profile.weather_type == WeatherType.FLOOD:
            if cell in profile.cell_states:
                return profile.cell_states[cell].demand_multiplier

        return profile.base_demand_multiplier

    @staticmethod
    def is_route_passable(
        profile: WeatherDayProfile,
        t_min: float,
        pickup_cell: str,
        drop_cell: str,
    ) -> bool:
        """Kiểm tra lộ trình có đi được không (False nếu điểm đón hoặc trả nằm trong vùng ngập sâu)."""
        if profile.weather_type != WeatherType.FLOOD:
            return True

        if t_min < profile.event_start_min or t_min > profile.event_end_min:
            return True

        # Nếu điểm đón hoặc trả nằm trong vùng cấm đường -> không thể thực hiện cuốc xe
        if pickup_cell in profile.blocked_cells or drop_cell in profile.blocked_cells:
            return False

        return True

    @staticmethod
    def get_route_detour_factor(
        profile: WeatherDayProfile,
        t_min: float,
        pickup_cell: str,
        drop_cell: str,
        base_detour: float = 1.3,
    ) -> float:
        """Hệ số uốn lượn đường sá: khi ngập lụt, xe phải đi vòng tránh vùng ngập nên detour tăng cao."""
        if profile.weather_type != WeatherType.FLOOD:
            return base_detour

        if t_min < profile.event_start_min or t_min > profile.event_end_min:
            return base_detour

        # Nếu đi qua vùng ngập vừa, phải đi vòng tránh các vũng nước lớn -> detour tăng thêm 25-40%
        is_near_flood = (
            pickup_cell in profile.severe_slowdown_cells or
            drop_cell in profile.severe_slowdown_cells
        )
        if is_near_flood:
            return round(base_detour * 1.35, 3)

        return base_detour
