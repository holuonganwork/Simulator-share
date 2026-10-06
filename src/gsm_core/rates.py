"""ĐA-01 — MỘT cách ước lượng tỷ lệ cho CẢ sim và UI.

Cường (2026-07-27): *"UI và sim phải chung 1 logic, sử dụng cùng 1 luật, 1 database"*.

## Vì sao cần file này

Trước đây **một khái niệm "tỷ lệ nhận" có ba cách tính khác nhau**:

| Nơi | Cách tính | Sai ở đâu |
|---|---|---|
| `gsm_sim/entities.py` property | `accepted/offered`, **0/0 → 1.0** | "chưa có dữ liệu" bị coi là "hoàn hảo" (BUG-DSIM13-02: chặn nhầm lời khuyên đầu ca) |
| `gsm_sim/journey.py` | `accepted/offered`, **0/0 → None** | đúng hơn, nhưng là quy ước THỨ HAI |
| `ui/backend .../advisor.build_gi` | aggregate **CẢ NGÀY** từ `driver_statistic_daily`, thiếu → **1.0** | **rò tương lai** (9h sáng đã biết tỷ lệ cuối ngày) + lại fallback 1.0 |

Ba quy ước cho một sự thật — đúng thứ đã đẻ ra các lỗi của UPDATE-075/076.

## Estimator

    p̂ = (k + m·p0) / (n + m)

Đây là hậu nghiệm trung bình của Beta-Binomial với prior `Beta(m·p0, m·(1−p0))`; `m` là **số quan
sát giả** (đơn vị: "số lần được chào"), nói *"tin prior bằng khoảng m lần chào"*.

Ba tính chất khiến nó thay được cả ba cách trên:

1. `n = 0` ⇒ `p̂ = p0` — **không bao giờ trả 1.0**. Không có bằng chứng thì trả niềm tin trước,
   không trả "hoàn hảo". Đây là điều ĐA-01 yêu cầu tường minh.
2. `n` nhỏ ⇒ co mạnh về prior — một cuốc bị từ chối không lật được kết luận.
3. `n` lớn ⇒ prior mờ dần, bám dữ liệu thật.

**KHÔNG có mặc định cho `p0` và `m`**: caller phải truyền, vì `p0` là số ĐO ĐƯỢC (pooled từ dữ
liệu, tính trên ngày TRƯỚC để không rò tương lai) chứ không phải hằng số bịa ra.

---

## `D-ADV-04` — quy ước MẪU SỐ của `historical_points_per_hour` (thêm 2026-08-06)

Cùng một lớp lỗi, lần thứ hai, ở một khái niệm khác: **"điểm/giờ theo khung"** từng có **hai** quy ước.

| Nơi | Quy ước | Đúng/sai |
|---|---|---|
| 3 producer (`features/bonus_gap.py`, `features/from_l1r.py`, `ui/.../advisor.py`) | điểm-của-bucket ÷ **giờ online TOÀN NGÀY** | **SAI** |
| Solver `solvers/bonus_feasibility.py` (`_hour_rate` → `_walk` nhân `rate × span` cho **từng giờ** của bucket) | điểm ÷ **giờ online TRONG bucket** | **ĐÚNG** (có test ghim `tests/test_bonus_feasibility.py:112-119`) |

Vì `giờ_ngày ≥ giờ_trong_bucket`, rate **luôn** bị ước NON (peak thường 2–5×) ⇒ S1 phán *"không với
tới mốc"* về mốc **với tới được**. Reproduce: `research/audit/2026-08-06-math-model-audit/repro-s1-denominator.py`
— producer trả `{peak: 6.0, offpeak: 6.0}` (đúng: `{30, 7.5}`) ⇒ INFEASIBLE thay vì FEASIBLE tại 2,42h.
Vì `S1` là solver **duy nhất** đường sản phẩm chạy (`B6-PARITY`), lỗi này đập thẳng vào card tài xế.

**Quy ước CHỐT (một, duy nhất):** `historical_points_per_hour[b]` = median theo ngày của
`điểm kiếm trong bucket b` ÷ `giờ ONLINE TRONG bucket b`. Giờ **ngoài khung tính điểm** bị loại khỏi
**cả** tử số lẫn mẫu số — solver không bao giờ áp rate bucket cho giờ `trip_points == 0`, nên phút
online lúc 23h **không được** làm loãng mẫu số offpeak.

⚠ Đừng đặt tên hàm mới ở đây bằng token tiền (`vnd|payout|gross|fare|revenue|income|topup`) — cổng
`tests/_health_boundary_scan.py` quét theo token đó và sẽ đòi khai vào money-manifest.
"""

from __future__ import annotations

from gsm_core.policy import PolicyBundle

# ASSUMPTION có lập luận (CHƯA hiệu chỉnh bằng dữ liệu thật): ngưỡng bằng-chứng-hiện-diện.
# Dưới 30′ trong một bucket thì "điểm/giờ" của bucket đó là ngoại suy từ mẩu quá nhỏ — một cuốc peak
# lúc 16:55 với 5′ hiện diện cho 120đ/h, một con số vô nghĩa. Đây là hằng số cần được falsify: nếu
# verdict đảo chiều khi đổi sang 0,25 hoặc 1,0 thì kết luận là hiện vật của hằng số, không phải của
# dữ liệu (xem mục falsifier trong plan Cycle B0).
MIN_BUCKET_HOURS = 0.5

# Giữ nguyên quy ước cũ của cả ba producer: dưới 3 ngày thì không tin lịch sử cá nhân.
MIN_DAYS_FOR_SELF_HISTORY = 3

# Đường XẤP XỈ: `online_time` phải phủ ít nhất bấy nhiêu phần của span hoạt động mới suy được HÌNH
# DẠNG. Nếu tài xế online 3h trong một span 10h thì span phần lớn là khoảng OFFLINE ⇒ hình dạng của
# span không nói gì về nơi 3h đó nằm ⇒ thà không có mẫu còn hơn có mẫu bịa.
MIN_SHAPE_COVERAGE = 0.5


def bucket_of_hour(policy: PolicyBundle, hour: int) -> str | None:
    """`'peak' | 'offpeak' | None`. `None` = giờ NGOÀI khung tính điểm.

    `None` phải được loại khỏi **cả** tử số lẫn mẫu số (xem docstring module).
    """
    if policy.trip_points(hour) <= 0:
        return None
    return "peak" if policy.is_peak(hour) else "offpeak"


def split_minutes_by_bucket(policy: PolicyBundle,
                            intervals: list[tuple[float, float]]) -> dict[str, float]:
    """Chồng lấn từng-giờ của các khoảng `[phút-trong-ngày, phút-trong-ngày]` với giờ của từng bucket.

    Thuần hình học, không đọc dữ liệu nào ⇒ dùng lại được cho sim (world tích luỹ span `[last, now]`).
    Chỉ trả bucket có thời lượng > 0 — bucket vắng mặt nghĩa là **không có bằng chứng hiện diện**,
    khác hẳn "có mặt mà 0 điểm".
    """
    out: dict[str, float] = {}
    for start, end in intervals:
        t = float(start)
        while t < end:
            h = int(t // 60)
            nxt = min((h + 1) * 60.0, float(end))
            b = bucket_of_hour(policy, h % 24)
            if b is not None:
                out[b] = out.get(b, 0.0) + (nxt - t)
            t = nxt
    return out


def bucket_online_hours_measured(policy: PolicyBundle,
                                 intervals: list[tuple[float, float]]
                                 ) -> tuple[dict[str, float], str]:
    """Đường CÓ mốc thời gian (L1 `app_event`; sim). Trả `(giờ/bucket, method)`."""
    by = split_minutes_by_bucket(policy, intervals)
    if not by:
        return {}, "none"
    return {b: m / 60.0 for b, m in by.items()}, "measured_intervals"


def bucket_online_hours_estimated(policy: PolicyBundle, online_hours_total: float,
                                  activity_span: tuple[float, float]
                                  ) -> tuple[dict[str, float], str]:
    """Đường KHÔNG có mốc thời gian: chỉ có TỔNG `online_time` + span hoạt động quan sát được.

    Đây là ràng buộc của dữ liệu thật (`specs/real-data/data-contract-counterfactual.md`: bảng không
    có `go_online`/`go_offline`, chỉ có `online_time`). Phân bổ tổng đó theo **hình dạng** của
    span ∩ bucket:

        oh_bucket = overlap(span, giờ-của-bucket ∩ khung-điểm) / độ-dài-span × online_hours_total

    **BẤT BIẾN (có test):** `Σ oh_bucket ≤ online_hours_total` — hàm này chỉ PHÂN BỔ con số đo được,
    **không bao giờ bịa thêm giờ online**. (Đúng theo cấu trúc: Σ overlap ≤ độ dài span vì giờ ngoài
    khung điểm bị loại.)

    Ca suy biến `span = 0` (mọi cuốc trong ngày ở cùng một giờ): dồn toàn bộ `online_time` vào bucket
    của giờ đó ⇒ **quay về đúng quy ước cũ** cho bucket quan sát được (bi quan), và **không** bịa mẫu
    cho bucket kia. Đó là suy biến an toàn, không phải im lặng.
    """
    if online_hours_total <= 0:
        return {}, "none"
    start, end = float(activity_span[0]), float(activity_span[1])
    span_min = end - start
    if span_min <= 0:
        b = bucket_of_hour(policy, int(start // 60) % 24)
        return ({b: float(online_hours_total)}, "estimated_span_scaled") if b else ({}, "none")
    if online_hours_total * 60.0 < MIN_SHAPE_COVERAGE * span_min:
        return {}, "none"          # span phần lớn là OFFLINE ⇒ không suy được hình dạng
    by = split_minutes_by_bucket(policy, [(start, end)])
    if not by:
        return {}, "none"          # span không giao khung tính điểm
    return ({b: (m / span_min) * float(online_hours_total) for b, m in by.items()},
            "estimated_span_scaled")


def bucket_rate_samples(points_by_bucket: dict[str, float], bucket_hours: dict[str, float],
                        min_hours: float = MIN_BUCKET_HOURS) -> dict[str, float]:
    """MỘT ngày → mẫu `điểm / giờ-TRONG-bucket`.

    Ba trạng thái phải phân biệt (bài học hidden-fallback repo đã trả giá nhiều lần):

    | trạng thái | biểu diễn | solver thấy gì |
    |---|---|---|
    | online trong bucket, CÓ điểm | `samples[b] = r` | rate cá nhân |
    | online trong bucket, **0 điểm** | `samples[b] = 0.0` | `hist[b] = 0.0` ⇒ rate 0 (ADV-05: 0.0 là DỮ LIỆU) |
    | **không có bằng chứng** online | **không có khoá** | `hist.get(b) is None` ⇒ fallback lý thuyết |
    """
    return {b: float(points_by_bucket.get(b, 0.0)) / oh
            for b, oh in bucket_hours.items() if oh >= min_hours}


def median_bucket_rates(samples: dict[str, list[float]],
                        min_days: int = MIN_DAYS_FOR_SELF_HISTORY) -> dict[str, float]:
    """Nhiều ngày → median (làm tròn 3) — đúng hình dạng `historical_points_per_hour` mà
    `bonus_feasibility._hour_rate` tiêu thụ. Bucket dưới `min_days` ngày ⇒ bỏ (không tin cá nhân)."""
    out: dict[str, float] = {}
    for b, vals in samples.items():
        if len(vals) >= min_days:
            s = sorted(vals)
            out[b] = round(s[len(s) // 2], 3)
    return out


def shrunk_rate(k: int | float, n: int | float, p0: float, m: float) -> float:
    """Tỷ lệ co về prior: `(k + m·p0) / (n + m)`.

    Args:
        k: số lần thành công (đã nhận / đã hoàn thành).
        n: số lần thử (được chào / đã nhận). `n = 0` hợp lệ ⇒ trả đúng `p0`.
        p0: prior — phải là xác suất **đo được**, không bịa (xem docstring module).
        m: số quan sát giả, `> 0`. Lớn hơn = tin prior hơn.

    Raises:
        ValueError: dữ liệu bất khả (`k > n`, số âm, `p0` ngoài [0,1], `m ≤ 0`).
            **Nổ chứ không lặng lẽ trả số đẹp** — đầu vào hỏng mà trả ra tỷ lệ trông hợp lý là
            cách nhanh nhất để một con số sai đi thẳng tới tài xế.
    """
    if not (0.0 <= p0 <= 1.0):
        raise ValueError(f"p0 phải là xác suất trong [0,1], nhận {p0!r}")
    if m <= 0:
        raise ValueError(f"m (số quan sát giả) phải > 0, nhận {m!r}")
    if k < 0 or n < 0:
        raise ValueError(f"đếm âm là dữ liệu hỏng: k={k!r}, n={n!r}")
    if k > n:
        raise ValueError(f"k > n là dữ liệu hỏng: k={k!r}, n={n!r}")
    return (float(k) + m * p0) / (float(n) + m)
