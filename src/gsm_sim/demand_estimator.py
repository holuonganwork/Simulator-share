"""E10a — λ̂ từ cuốc ĐÃ ĐÓN (event kind='pickup'), cửa sổ cuốn k bucket planner.

Spec: `specs/simulation/e10-advisor-noisy.md` §3. Narrow reader: CHỈ nhận tham chiếu
list event (append-only, t không giảm theo SimPy) + scalars. Cấu trúc chứa trace
tương-lai/λ-generator nằm NGOÀI tầm với theo thiết kế — test T5/T7 canh bằng whitelist
+ grep token cấm trên chính file này (vì thế docstring cũng không nhắc tên chúng).

0 lần chạm bộ sinh số ngẫu nhiên nào — deterministic từ realized data.
COLD ⇒ trả {} (advisor IM LẶNG — producer log `demand_est_cold`); CẤM mọi fallback.
"""
from __future__ import annotations


class RealizedDemandEstimator:
    """λ̂(c) = Σ_{i∈[lo,idx)} N(c,i) / n_buckets — pickups / bucket planner (spec §3.2).

    `estimate(idx)` ingest event tới `t_min < idx·b` rồi dừng (con trỏ nằm lại, lần sau
    tiếp) ⇒ kết quả không phụ thuộc thời điểm gọi trong bucket: cửa sổ `[lo, idx)` chỉ
    chứa bucket đã trọn. Đếm giữ theo bucket nên gọi LÙI idx nhỏ hơn vẫn exact.
    """

    def __init__(self, events: list, *, start_min: int, bucket_min: int,
                 window_buckets: int, min_pickups: int):
        self._events = events                      # tham chiếu sống — không copy
        self._b = int(bucket_min)
        self._first_op_bucket = int(start_min) // self._b
        self._k = int(window_buckets)
        self._min_n = int(min_pickups)
        self._ptr = 0                              # con trỏ ingest, O(1) amortized
        self._counts: dict[int, dict[str, int]] = {}
        # chẩn đoán của lần estimate gần nhất — producer log vào event demand_est[_cold]
        self.last_total = 0
        self.last_n_buckets = 0

    def _ingest_until(self, t_lim: float) -> None:
        ev = self._events
        while self._ptr < len(ev):
            e = ev[self._ptr]
            if e.t_min >= t_lim:
                break                              # t == biên thuộc bucket idx ⇒ ngoài cửa sổ
            if e.kind == "pickup":
                row = self._counts.setdefault(int(e.t_min // self._b), {})
                row[e.cell] = row.get(e.cell, 0) + 1
            self._ptr += 1

    def estimate(self, idx: int) -> dict[str, float]:
        idx = int(idx)
        self._ingest_until(idx * self._b)
        lo = max(idx - self._k, self._first_op_bucket)
        n_buckets = max(0, idx - lo)               # planner tick từ t=0 ⇒ idx<first_op gọi tới đây
        acc: dict[str, int] = {}
        for i in range(lo, idx):
            for cell, n in self._counts.get(i, {}).items():
                acc[cell] = acc.get(cell, 0) + n
        total = sum(acc.values())
        self.last_total, self.last_n_buckets = total, n_buckets
        if idx <= self._first_op_bucket or n_buckets == 0 or total < self._min_n:
            return {}                              # COLD — im lặng, KHÔNG fallback
        return {c: n / n_buckets for c, n in acc.items()}   # chỉ ô có Σ N > 0
