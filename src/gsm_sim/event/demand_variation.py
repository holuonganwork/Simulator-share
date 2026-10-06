"""Mô-đun Biến thiên Nhu cầu Theo Ngày (Daily Demand Variation Engine).

Phục vụ làm giàu dữ liệu (Data Enrichment) cho GSM_SIMULATOR-rl.
Giải quyết triệt để vấn đề "quá cố định / hardcode" của mô hình nhu cầu cũ:
1. Mô hình hóa biến thiên theo Thứ trong tuần (Day of Week - DOW):
   - Thứ Hai: Cao điểm sáng sớm (commute rush) tăng mạnh.
   - Thứ Ba - Thứ Năm: Ngày làm việc tiêu chuẩn (Mid-week baseline).
   - Thứ Sáu: Cao điểm chiều tối tăng vọt (weekend-eve surge), kéo dài đến đêm.
   - Thứ Bảy: Chuyển dịch đỉnh sang buổi trưa (10-12h) và buổi tối (19-22h) đi chơi/TTTM.
   - Chủ Nhật: Buổi sáng thong thả, chiều tối hồi gia sớm, đêm giảm sâu.
2. Mô hình hóa Ngày Lễ / Ngày Đặc biệt (Holidays & Festive Days):
   - Tết Nguyên Đán, 30/4 - 1/5, Quốc khánh 2/9, Tết Dương Lịch...
   - Nhu cầu biến thiên đột biến theo lịch âm/dương thực tế tại Việt Nam.
3. Nhiễu ngẫu nhiên thường nhật (Daily Exogenous Shock / Stochastic Noise):
   - Mỗi ngày có độ lệch ngẫu nhiên tự nhiên (stochastic fluctuation ~3-8%) tuân theo
     phân phối chuẩn/lognormal có chặn, đảm bảo không có hai ngày nào giống hệt nhau.
4. Điều chế động hình dáng đồ thị 24 giờ (Dynamic Hourly Shape Modulation):
   - Thay đổi trọng số giờ theo từng loại ngày (ngày làm việc vs ngày nghỉ).
5. Bảo toàn hạt giống ngẫu nhiên (CRN-Safe & Deterministic):
   - Đảm bảo khả năng tái lập 100% khi chạy lại cùng seed và cùng ngày.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping

import numpy as np


class DayType(str, Enum):
    """Phân loại ngày trong tuần và ngày đặc biệt."""
    MONDAY = "monday"
    MIDWEEK = "midweek"       # Thứ Ba, Thứ Tư, Thứ Năm
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"
    HOLIDAY = "holiday"       # Ngày lễ quốc gia
    SPECIAL_EVENT = "special" # Ngày diễn ra sự kiện lớn


@dataclass(frozen=True)
class DayDemandProfile:
    """Hồ sơ nhu cầu chi tiết của một ngày cụ thể sau khi áp dụng biến thiên."""
    date: str                          # YYYY-MM-DD
    day_index: int                     # Thứ tự ngày trong chuỗi mô phỏng (0, 1, 2, ...)
    day_of_week: int                   # 0 = Thứ Hai, ..., 6 = Chủ Nhật
    day_type: DayType                  # Phân loại ngày
    is_weekend: bool                   # True nếu là Thứ Bảy hoặc Chủ Nhật
    is_holiday: bool                   # True nếu là ngày lễ
    
    # Các hệ số điều chế nhu cầu
    base_orders_per_day: float         # Cầu gốc (ví dụ 50.000 đơn)
    dow_multiplier: float              # Hệ số thứ trong tuần (ví dụ 1.15 cho Thứ 6)
    stochastic_multiplier: float       # Nhiễu ngẫu nhiên thường nhật (ví dụ 1.03)
    event_multiplier: float            # Hệ số sự kiện/lễ (ví dụ 1.25)
    total_multiplier: float            # Tích toàn bộ hệ số: dow * stoch * event
    
    # Nhu cầu kỳ vọng thực tế của ngày
    effective_orders_per_day: int      # round(base * total_multiplier)
    
    # Trọng số phân bổ theo 24 giờ của riêng ngày này
    hourly_weights: dict[int, float]   # Trọng số thô theo giờ (0h - 23h)
    hourly_shares: dict[int, float]    # Tỷ trọng đã chuẩn hóa (tổng = 1.0) trên khung chạy
    
    description: str                   # Diễn giải nghiệp vụ cho báo cáo/log


# Danh mục các ngày lễ cố định tại Việt Nam (tháng, ngày)
VN_FIXED_HOLIDAYS: set[tuple[int, int]] = {
    (1, 1),    # Tết Dương Lịch
    (4, 30),   # Ngày Giải phóng miền Nam
    (5, 1),    # Quốc tế Lao động
    (9, 2),    # Quốc khánh
    (9, 3),    # Nghỉ bù Quốc khánh
}


class DailyDemandVariation:
    """Bộ máy tính toán và điều chế biến thiên nhu cầu hàng ngày."""

    # Hệ số nhu cầu theo thứ trong tuần (DOW Multipliers)
    DEFAULT_DOW_MULTIPLIERS = {
        DayType.MONDAY: 1.04,     # Đầu tuần đi làm/họp hành, nhu cầu cao
        DayType.MIDWEEK: 1.00,    # Giữa tuần chuẩn mốc baseline
        DayType.FRIDAY: 1.14,     # Cuối tuần đi ăn uống/liên hoan/về quê
        DayType.SATURDAY: 1.18,   # Thứ Bảy vui chơi, TTTM, ăn uống bùng nổ
        DayType.SUNDAY: 0.96,     # Chủ Nhật nghỉ ngơi tại nhà, chiều về sớm
        DayType.HOLIDAY: 1.28,    # Ngày lễ nhu cầu di chuyển rất cao
        DayType.SPECIAL_EVENT: 1.22,
    }

    # Đồ thị giờ ngày thường chuẩn (2 đỉnh nhọn: 7-9h và 17-19h)
    WEEKDAY_HOURLY_WEIGHTS = {
        0: 0.20,  1: 0.12,  2: 0.08,  3: 0.08,  4: 0.15,
        5: 0.35,  6: 0.80,  7: 1.05,  8: 1.10,  9: 0.70,
        10: 0.60, 11: 0.65, 12: 0.65, 13: 0.55, 14: 0.55,
        15: 0.65, 16: 0.90, 17: 1.25, 18: 1.35, 19: 1.10,
        20: 0.85, 21: 0.75, 22: 0.55, 23: 0.35,
    }

    # Đồ thị giờ Thứ Sáu (Đỉnh chiều tối mở rộng, kéo dài đến khuya)
    FRIDAY_HOURLY_WEIGHTS = {
        0: 0.22,  1: 0.15,  2: 0.10,  3: 0.08,  4: 0.12,
        5: 0.30,  6: 0.75,  7: 1.00,  8: 1.05,  9: 0.70,
        10: 0.60, 11: 0.65, 12: 0.65, 13: 0.55, 14: 0.60,
        15: 0.70, 16: 1.00, 17: 1.35, 18: 1.45, 19: 1.30,
        20: 1.15, 21: 1.00, 22: 0.80, 23: 0.55,
    }

    # Đồ thị giờ Cuối tuần Thứ Bảy (Sáng thong thả 9-11h, trưa đông, tối bùng nổ 18-22h)
    WEEKEND_HOURLY_WEIGHTS = {
        0: 0.35,  1: 0.20,  2: 0.12,  3: 0.08,  4: 0.10,
        5: 0.20,  6: 0.45,  7: 0.65,  8: 0.90,  9: 1.15,
        10: 1.25, 11: 1.20, 12: 1.05, 13: 0.85, 14: 0.85,
        15: 0.95, 16: 1.05, 17: 1.20, 18: 1.35, 19: 1.40,
        20: 1.35, 21: 1.20, 22: 0.95, 23: 0.65,
    }

    # Đồ thị giờ Chủ Nhật (Sáng thong thả, chiều hồi gia 16-19h, đêm giảm sớm)
    SUNDAY_HOURLY_WEIGHTS = {
        0: 0.30,  1: 0.18,  2: 0.10,  3: 0.08,  4: 0.10,
        5: 0.20,  6: 0.40,  7: 0.60,  8: 0.85,  9: 1.10,
        10: 1.20, 11: 1.15, 12: 1.00, 13: 0.80, 14: 0.80,
        15: 0.90, 16: 1.10, 17: 1.25, 18: 1.30, 19: 1.20,
        20: 1.00, 21: 0.80, 22: 0.55, 23: 0.35,
    }

    def __init__(
        self,
        base_orders_per_day: float = 50000.0,
        start_min: int = 300,        # 05:00
        end_min: int = 1440,         # 24:00
        stochastic_sigma: float = 0.045,  # Độ lệch chuẩn ngẫu nhiên hàng ngày (~4.5%)
        stochastic_clamp: tuple[float, float] = (0.88, 1.15),  # Giới hạn nhiễu an toàn
        dow_multipliers: Mapping[DayType, float] | None = None,
        custom_holidays: set[str] | None = None,  # Tập các ngày YYYY-MM-DD coi là lễ
        enabled: bool = True,
    ):
        self.base_orders_per_day = float(base_orders_per_day)
        self.start_min = int(start_min)
        self.end_min = int(end_min)
        self.start_hour = self.start_min // 60
        self.end_hour = self.end_min // 60
        self.stochastic_sigma = float(stochastic_sigma)
        self.stochastic_clamp = (float(stochastic_clamp[0]), float(stochastic_clamp[1]))
        self.enabled = bool(enabled)
        
        self.dow_multipliers = dict(self.DEFAULT_DOW_MULTIPLIERS)
        if dow_multipliers:
            self.dow_multipliers.update(dow_multipliers)
            
        self.custom_holidays = set(custom_holidays or set())

    def classify_date(self, d: _date) -> tuple[DayType, bool, bool]:
        """Xác định phân loại ngày (DayType), cờ cuối tuần, cờ ngày lễ."""
        d_str = d.isoformat()
        is_fixed_vn = (d.month, d.day) in VN_FIXED_HOLIDAYS
        is_custom_holiday = d_str in self.custom_holidays
        is_holiday = is_fixed_vn or is_custom_holiday

        weekday = d.weekday()  # 0 = Monday, ..., 6 = Sunday
        is_weekend = weekday in (5, 6)

        if is_holiday:
            return DayType.HOLIDAY, is_weekend, True
        if weekday == 0:
            return DayType.MONDAY, False, False
        if weekday in (1, 2, 3):
            return DayType.MIDWEEK, False, False
        if weekday == 4:
            return DayType.FRIDAY, False, False
        if weekday == 5:
            return DayType.SATURDAY, True, False
        return DayType.SUNDAY, True, False

    def _sample_stochastic_factor(self, seed: int, day_index: int) -> float:
        """Sinh hệ số nhiễu ngẫu nhiên thường nhật có tính tái lập (CRN-Safe)."""
        if not self.enabled or self.stochastic_sigma <= 0:
            return 1.0
        # Hạt giống riêng biệt cho biến thiên nhu cầu ngày, không đụng stream khác
        rng = np.random.default_rng((seed, day_index, 0xDE7A4D))
        raw_noise = float(rng.normal(loc=0.0, scale=self.stochastic_sigma))
        mult = math.exp(raw_noise)
        return float(min(self.stochastic_clamp[1], max(self.stochastic_clamp[0], mult)))

    def get_hourly_curve(self, day_type: DayType) -> dict[int, float]:
        """Lấy biểu đồ trọng số 24 giờ phù hợp với loại ngày."""
        if not self.enabled:
            return dict(self.WEEKDAY_HOURLY_WEIGHTS)

        if day_type in (DayType.MONDAY, DayType.MIDWEEK):
            return dict(self.WEEKDAY_HOURLY_WEIGHTS)
        if day_type == DayType.FRIDAY:
            return dict(self.FRIDAY_HOURLY_WEIGHTS)
        if day_type in (DayType.SATURDAY, DayType.HOLIDAY, DayType.SPECIAL_EVENT):
            return dict(self.WEEKEND_HOURLY_WEIGHTS)
        if day_type == DayType.SUNDAY:
            return dict(self.SUNDAY_HOURLY_WEIGHTS)
        return dict(self.WEEKDAY_HOURLY_WEIGHTS)

    def compute_hourly_shares(self, hourly_weights: dict[int, float]) -> dict[int, float]:
        """Chuẩn hóa trọng số trong khung thời gian [start_hour, end_hour) về tổng = 1.0."""
        kept = {
            h: float(w) for h, w in hourly_weights.items()
            if self.start_hour <= h < self.end_hour
        }
        total_s = sum(kept.values()) or 1.0
        return {h: w / total_s for h, w in kept.items()}

    def evaluate_day(
        self,
        date_str: str,
        day_index: int = 0,
        seed: int = 7000,
        override_multiplier: float | None = None,
    ) -> DayDemandProfile:
        """Đánh giá toàn diện hồ sơ nhu cầu cho một ngày cụ thể."""
        d = _date.fromisoformat(date_str)
        day_type, is_weekend, is_holiday = self.classify_date(d)
        weekday = d.weekday()

        if not self.enabled:
            # Chế độ tắt: giữ nguyên 100% baseline cũ
            dow_m = 1.0
            stoch_m = 1.0
            event_m = 1.0
            total_m = 1.0
            eff_orders = int(round(self.base_orders_per_day))
            weights = dict(self.WEEKDAY_HOURLY_WEIGHTS)
            desc = "Baseline (Biến thiên ngày tắt, 100% đồng nhất)"
        else:
            dow_m = float(self.dow_multipliers.get(day_type, 1.0))
            stoch_m = self._sample_stochastic_factor(seed, day_index)
            event_m = float(override_multiplier) if override_multiplier is not None else 1.0
            total_m = round(dow_m * stoch_m * event_m, 4)
            eff_orders = int(round(self.base_orders_per_day * total_m))
            weights = self.get_hourly_curve(day_type)

            desc_parts = [f"Thứ {weekday + 2 if weekday < 6 else 'CN'} ({day_type.value})"]
            if is_holiday:
                desc_parts.append("Ngày Lễ")
            desc_parts.append(f"DOW: x{dow_m:.2f}")
            desc_parts.append(f"Nhiễu: x{stoch_m:.3f}")
            if event_m != 1.0:
                desc_parts.append(f"Sự kiện: x{event_m:.2f}")
            desc_parts.append(f"-> Kỳ vọng: {eff_orders:,} đơn (x{total_m:.3f})")
            desc = " · ".join(desc_parts)

        shares = self.compute_hourly_shares(weights)

        return DayDemandProfile(
            date=date_str,
            day_index=day_index,
            day_of_week=weekday,
            day_type=day_type,
            is_weekend=is_weekend,
            is_holiday=is_holiday,
            base_orders_per_day=self.base_orders_per_day,
            dow_multiplier=dow_m,
            stochastic_multiplier=stoch_m,
            event_multiplier=event_m,
            total_multiplier=total_m,
            effective_orders_per_day=eff_orders,
            hourly_weights=weights,
            hourly_shares=shares,
            description=desc,
        )

    def evaluate_calendar(
        self,
        start_date: str,
        days: int,
        seed: int = 7000,
        special_days: dict[str, float] | None = None,
    ) -> list[DayDemandProfile]:
        """Tạo chuỗi hồ sơ nhu cầu cho chuỗi ngày liên tục (ví dụ 7 ngày, 30 ngày, 90 ngày)."""
        d0 = _date.fromisoformat(start_date)
        profiles: list[DayDemandProfile] = []
        specials = special_days or {}

        for i in range(days):
            cur_date = (d0 + timedelta(days=i)).isoformat()
            ovr = specials.get(cur_date, None)
            prof = self.evaluate_day(cur_date, day_index=i, seed=seed, override_multiplier=ovr)
            profiles.append(prof)

        return profiles
