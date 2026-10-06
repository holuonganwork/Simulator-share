"""Mô-đun Quản lý Sự kiện & Biến thiên Môi trường (Events & Exogenous Dynamics).

Cung cấp các công cụ làm giàu dữ liệu (Data Enrichment) cho mô phỏng GSM:
- Biến thiên nhu cầu theo ngày (Day-of-Week, Ngày lễ, Nhiễu stochastic hàng ngày)
- Biến thiên hình thái đồ thị 24 giờ theo ngày thường / cuối tuần / ngày lễ
- Mô hình hóa thời tiết & thiên tai đô thị (Mát trời, Mưa, Sương mù, Lũ lụt/Ngập úng cấm đường)
"""

from gsm_sim.event.demand_variation import (
    DayType,
    DayDemandProfile,
    DailyDemandVariation,
    VN_FIXED_HOLIDAYS,
)
from gsm_sim.event.weather_event import (
    WeatherType,
    CellWeatherState,
    WeatherDayProfile,
    WeatherEventEngine,
)

__all__ = [
    # Demand Variation
    "DayType",
    "DayDemandProfile",
    "DailyDemandVariation",
    "VN_FIXED_HOLIDAYS",
    # Weather & Extreme Environmental Events
    "WeatherType",
    "CellWeatherState",
    "WeatherDayProfile",
    "WeatherEventEngine",
]
