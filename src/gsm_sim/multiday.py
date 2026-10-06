"""D-SIM-10 — SIM NHIỀU NGÀY: lời khuyên ngày N tác động vào hành vi ngày N+1.

## Vì sao cần

UPDATE-050 chẩn đoán: **S2 `shift_dp` là solver DUY NHẤT nhìn về phía trước.** S3/S7/S8/S9 là
**hồi cứu** (phân tích cái đã xảy ra), S5 tính theo **TUẦN**. Với sim MỘT ngày, lời khuyên hồi
cứu **không có chỗ để tác động** — kênh `rest_window` (S7) inert hoàn toàn vì khung nó chỉ ra
luôn nằm phía sau actor đang chạy.

Nhiều ngày mở khoá đúng cơ chế các solver đó được thiết kế cho: **học từ hôm qua, áp dụng hôm nay.**

## Ranh giới reset / mang sang (chỗ dễ sai nhất)

- **Reset mỗi ngày**: điểm, mọi counter, payout, SOC, mệt, cờ bữa ăn, `accept_lift` —
  thưởng là theo NGÀY, và ngủ dậy là người mới. Danh sách tường minh ở
  `Actor.reset_for_new_day()`.
- **Mang sang**: **danh tính** tài xế (cùng một người!), **lịch sử cuộn** (`DriverMemory`),
  **kế hoạch từ advice hôm qua**, **tích luỹ tuần**.

Sai theo chiều nào cũng hỏng: quên reset ⇒ điểm cộng dồn vô hạn, thưởng ngày sai; reset nhầm ⇒
mất lịch sử, "học từ hôm qua" chỉ là vỏ.

## Không rò thông tin tương lai — rủi ro MỚI của chế độ nhiều ngày

Ngày N chỉ được đọc dữ liệu ngày **≤ N**. `DriverMemory` vì thế chỉ được cập nhật **SAU** khi
ngày N chạy xong, và chỉ từ kết quả ngày đó. Sim một ngày không có loại lỗi này.
"""

from __future__ import annotations

import copy
from datetime import date as _dt_date, timedelta as _dt_timedelta
import statistics as st
from dataclasses import dataclass, field

import numpy as np

from gsm_core.solvers import idle_reduction

from .archetypes import sample_actors
from .config import Config
from .congestion import CongestionField
from .demand import generate_orders
from .geo import build_grid, load_road_matrix
from .journey import build_journey
from .policy import PolicyBundle
from .runner import (RunResult, _data, build_environment, derive_run_id,
                     finalize_checkpoint_trace)
from .world import World


@dataclass
class DriverMemory:
    """Lịch sử cuộn + kế hoạch của MỘT tài xế, sống xuyên ngày.

    Đây là thứ thay cho việc ước lượng vội trong-ngày: `build_bonus_gap_input` hiện phải đoán
    `historical_points_per_hour` từ chính ca đang chạy (đầu ca thì chưa có gì). Có nhiều ngày
    rồi thì dùng **lịch sử thật** — đúng thứ solver S1 được thiết kế để nhận.
    """
    actor_id: int
    days: int = 0
    acceptance_hist: list[float] = field(default_factory=list)
    completion_hist: list[float] = field(default_factory=list)
    points_per_hour_hist: list[float] = field(default_factory=list)
    payout_hist: list[int] = field(default_factory=list)
    trips_hist: list[int] = field(default_factory=list)
    # kế hoạch rút ra từ advice HÔM QUA, áp dụng HÔM NAY
    planned_rest_hour: int | None = None
    # tích luỹ TUẦN (nền cho S5 khoán tuần — chưa nối, xem D-POL-01).
    # D-SIM-13(C): tuần ĐANG CHẠY + danh sách tuần ĐÃ ĐÓNG. Trước đây cộng dồn tuyến tính
    # không bao giờ reset ⇒ chỉ đúng khi days ≤ 7; chạy dài hơn là "tuần" phình vô hạn.
    week_index: int = 0
    week_gross_vnd: int = 0
    week_trips: int = 0
    weeks_hist: list[dict] = field(default_factory=list)   # [{week, gross_vnd, trips}]
    # SIM-XANH P2: mốc tân binh 50-cuốc/7-ngày đã trả chưa (trả đúng MỘT lần)
    newbie_week1_paid: bool = False

    def close_week(self) -> None:
        """Chốt tuần đang chạy vào lịch sử rồi mở tuần mới. Gọi tại RANH GIỚI tuần
        (day_index % 7 == 0, trừ ngày 0)."""
        self.weeks_hist.append({"week": self.week_index,
                                "gross_vnd": self.week_gross_vnd,
                                "trips": self.week_trips})
        self.week_index += 1
        self.week_gross_vnd = 0
        self.week_trips = 0

    @property
    def acceptance_avg(self) -> float | None:
        return round(st.mean(self.acceptance_hist), 4) if self.acceptance_hist else None

    @property
    def completion_avg(self) -> float | None:
        return round(st.mean(self.completion_hist), 4) if self.completion_hist else None

    @property
    def points_per_hour_avg(self) -> float | None:
        return (round(st.mean(self.points_per_hour_hist), 3)
                if self.points_per_hour_hist else None)


@dataclass
class MultiDayResult:
    seed: int
    days: list[RunResult]
    memory: dict[int, DriverMemory]

    def totals(self, actor_id: int) -> dict:
        """Cộng dồn theo tài xế qua mọi ngày."""
        tot = {"payout_vnd": 0, "trips_completed": 0, "day_bonus_vnd": 0, "idle_min": 0.0}
        for r in self.days:
            m = build_journey(r, actor_id).metrics
            for k in tot:
                tot[k] += m[k]
        return tot


def day_seed(seed: int, day_index: int) -> int:
    """Seed ngoại sinh của ngày d. Khác nhau giữa các ngày (cầu mỗi ngày một khác) nhưng
    **tái lập được** từ seed gốc ⇒ A/B vẫn pair theo seed gốc, CRN giữ nguyên."""
    return int(np.random.default_rng((seed, day_index)).integers(0, 2**31 - 1))


def _update_memory(mem: DriverMemory, result: RunResult, actor) -> None:
    """Cập nhật lịch sử SAU khi ngày đã chạy xong — không bao giờ trước.

    Đây là chốt chặn không-rò-tương-lai: nếu gọi hàm này trước/khi ngày đang chạy thì hành vi
    trong ngày sẽ phụ thuộc chính kết quả của nó.
    """
    mem.days += 1
    # REVIEW-C1 (D-SIM-13): CHỈ ghi acceptance của ngày KHÔNG bị advice can thiệp.
    # `acceptance_hist` được bridge dùng làm ước lượng HÀNH VI GỐC (base propensity) để
    # quyết định có cần khuyên không. Nếu ghi cả ngày đã-lift (accept_lift > 0), lịch sử
    # đo "hành vi đã được chữa" thay vì "bệnh nền" ⇒ advisor tưởng tài xế đã ổn, TỰ TẮT
    # lời khuyên phòng ngừa ngày hôm sau, thưởng mất, tỷ lệ tụt, rồi lại khuyên — dao động
    # khuyên/im tự tạo. (accept_lift chỉ reset sáng hôm sau nên tại đây còn giá trị cuối ngày.)
    if actor.orders_offered and actor.accept_lift == 0.0:
        mem.acceptance_hist.append(round(actor.acceptance_rate, 4))
    if actor.orders_accepted:
        mem.completion_hist.append(round(actor.completion_rate, 4))
    if actor.online_min > 0:
        mem.points_per_hour_hist.append(round(actor.points / (actor.online_min / 60.0), 3))
    mem.payout_hist.append(int(actor.payout_vnd))
    mem.trips_hist.append(int(actor.trips_done))
    mem.week_gross_vnd += int(actor.gross_vnd)
    mem.week_trips += int(actor.trips_done)

    # S7 HỒI CỨU: phân tích idle CẢ NGÀY vừa xong → khung nên dồn nghỉ NGÀY MAI.
    # Đây chính là cơ chế mà sim một ngày không có chỗ dùng (UPDATE-050).
    segs, total, longest = [], 0.0, 0.0
    for h, mins in sorted(actor.idle_by_hour.items()):
        segs.append({"hour": int(h), "duration_seconds": float(mins) * 60.0})
        total += float(mins)
        longest = max(longest, float(mins))
    if segs:
        ii = {
            "schema_version": "1.0.0", "driver_id": f"d-{actor.actor_id}",
            "t_now": "2026-07-01T23:59:00+07:00", "session_date": "2026-07-01",
            "idle_segments": segs, "total_idle_min": round(total, 2),
            "longest_idle_min": round(longest, 2),
            "online_hours": round(actor.online_min / 60.0, 3),
            "demand_by_hour": _demand_index(result),
            "active_reposition": None, "view_version": "sim-3", "source": "MOCK",
        }
        try:
            sol = idle_reduction.solve(ii).get("solution") or {}
        except (AttributeError, NotImplementedError):
            # Solver S7 khong co trong goi simulator cong khai (xem gsm_core/solvers/__init__.py):
            # bo qua ke hoach nghi; chi advisor moi dung `planned_rest_hour`.
            sol = {}
        w = sol.get("worst_window") if sol.get("notable") else None
        mem.planned_rest_hour = int(w["hour"]) if w else None
    else:
        # REVIEW-C8: ngày KHÔNG có idle ⇒ xoá kế hoạch cũ. Nếu giữ, kế hoạch từ một ngày
        # xa xưa dính mãi (bridge short-circuit theo planned_rest_hour) — "memory chỉ được
        # cập nhật từ ngày vừa xong" phải đúng theo CẢ chiều xoá, không chỉ chiều ghi.
        mem.planned_rest_hour = None


def _demand_index(result: RunResult) -> dict[str, float]:
    """Chỉ số cầu 0..1 theo giờ, từ đơn ĐÃ XẢY RA trong ngày vừa kết thúc.

    Hợp lệ vì đây là hồi cứu: ngày đó đã xong, không có gì thuộc tương lai.
    """
    by_hour: dict[int, int] = {}
    for o in result.orders:
        h = int(o.t_min // 60) % 24
        by_hour[h] = by_hour.get(h, 0) + 1
    peak = max(by_hour.values()) if by_hour else 1
    return {str(h): round(by_hour.get(h, 0) / peak, 4) for h in range(24)}


def iter_multiday(cfg: Config, seed: int, days: int = 7,
                  week_offset: int = 0):
    """Generator chạy `days` ngày liên tiếp, yield (day_index, run_result, memory) từng ngày.

    Giúp consumer (như realdata streaming) xử lý và ghi đĩa ngay sau mỗi ngày,
    giải phóng hàng trăm ngàn events / pings khỏi RAM, giúp chạy 30-90 ngày với
    hàng nghìn tài xế mà bộ nhớ RAM không bao giờ bị phình to (OOM).
    """
    grid = build_grid(
        geom_path=_data(cfg, "geom_file"), stations_path=_data(cfg, "stations_file"),
        poi_path=_data(cfg, "poi_file"), res=int(cfg.get("world.h3_res")),
        res_report=int(cfg.get("world.h3_res_report")),
    )
    policy = PolicyBundle.from_config(cfg)
    actors = sample_actors(grid, cfg, seed)            # MỘT LẦN — cùng một nhóm người
    base_shift = {a.actor_id: (a.shift_start_min, a.shift_end_min) for a in actors}
    memory = {a.actor_id: DriverMemory(actor_id=a.actor_id) for a in actors}

    # REVIEW-C5/C11/C14: `day_seed` là hash 31-bit ⇒ hai ngày CÓ THỂ trùng seed (xác suất
    # nhỏ nhưng khác 0). Trùng seed = trùng CẢ dòng ngoại sinh (hai ngày y hệt nhau) và
    # trùng tiền tố ID mọi bản ghi (order_id/event_id đụng nhau xuyên ngày). Tính duy nhất
    # phải CẤU TRÚC, không phải may mắn: rehash quyết định luận tới khi hết trùng.
    day_seeds: list[int] = []
    for d in range(days):
        s = day_seed(seed, d)
        bump = 0
        while s in day_seeds:
            bump += 1
            s = int(np.random.default_rng((seed, d, 0xC0111DE, bump)).integers(0, 2**31 - 1))
        day_seeds.append(s)

    for d in range(days):
        s_day = day_seeds[d]
        if d > 0:
            rng = np.random.default_rng((seed, d, 0xDA1))
            for a in actors:
                st_min, en_min = base_shift[a.actor_id]
                a.reset_for_new_day(soc_pct=float(rng.uniform(85, 100)),
                                    shift_start_min=st_min, shift_end_min=en_min)
                a.planned_rest_hour = memory[a.actor_id].planned_rest_hour
            # D-SIM-13(C): ranh giới tuần — chốt tuần cũ, mở tuần mới.
            # (d + week_offset) % 7 == 0 ⇔ ngày d là Thứ Hai theo lịch thật (REVIEW-C13).
            if (d + week_offset) % 7 == 0:
                for mem in memory.values():
                    mem.close_week()

        # Ngày thực tế theo lịch cho biến thiên thời tiết & nhu cầu
        start_d_str = str(cfg.get("event_dynamics.start_date", "2026-07-01"))
        cur_d_str = (_dt_date.fromisoformat(start_d_str) + _dt_timedelta(days=d)).isoformat()

        env = build_environment(grid, cfg, s_day, day_index=d, date_str=cur_d_str)
        orders = generate_orders(grid, cfg, policy, s_day, env=env,
                                 road=load_road_matrix(cfg, grid))
        congestion = CongestionField(orders, cfg, env=env)
        # ĐA-05: multiday tạo World TRỰC TIẾP (không qua `run_once`) nên phải tự stamp
        # run_id — quên là mọi event nhiều-ngày mất identity, không join/export được.
        # `s_day` đã mang ngày (xem `day_seed`) ⇒ mỗi ngày một run_id riêng.
        world = World(grid, cfg, policy, orders, actors, s_day,
                      environment=env, congestion=congestion,
                      run_id=derive_run_id(cfg, s_day))
        # D-SIM-13(B): trao lịch sử cuộn cho advisor TRƯỚC khi ngày chạy. An toàn vì memory
        # lúc này chỉ chứa các ngày ĐÃ XONG (cập nhật ở cuối vòng lặp) — không rò tương lai.
        world.advice.memory = memory
        events = world.run()
        segments, trace = finalize_checkpoint_trace(world, events)
        # CHỤP ẢNH actor của NGÀY NÀY. Nếu để chung một list, `days[0].actors` sẽ phản ánh
        # trạng thái ngày CUỐI (vì actor bị reset tại chỗ mỗi ngày) ⇒ mọi journey/metric theo
        # ngày đều sai mà không có gì báo.
        snapshot = copy.deepcopy(actors)
        r = RunResult(seed=s_day, events=events, actors=snapshot, orders=orders, config=cfg,
                      policy=policy, grid=grid, env=env, congestion=congestion,
                      run_id=world.run_id,
                      traj=world.traj, segments=segments, stations=world.stations,
                      order_states=world.order_states, **trace)
        for a in actors:                                # cập nhật SAU khi ngày đã xong
            _update_memory(memory[a.actor_id], r, a)

        yield d, r, memory

    # REVIEW-C3: chốt nốt tuần cuối nếu nó VỪA TRÒN khi run kết thúc. Không có dòng này,
    # days=7 cho weeks_hist RỖNG — consumer cộng weeks_hist (S5 khoán tuần) hụt đúng
    # tuần cuối mỗi khi days chia hết cho 7. Tuần DỞ (days % 7 != 0) thì để mở — đúng
    # nghĩa "tuần đang chạy".
    if days > 0 and (days + week_offset) % 7 == 0:
        for mem in memory.values():
            mem.close_week()


def run_multiday(cfg: Config, seed: int, days: int = 7,
                 week_offset: int = 0) -> MultiDayResult:
    """Chạy `days` ngày LIÊN TIẾP với **cùng một nhóm tài xế**.

    Khác biệt cốt lõi so với gọi `run_once` nhiều lần: actor được sample **MỘT LẦN**. Sample
    lại mỗi ngày nghĩa là mỗi ngày một nhóm người khác — khi đó "học từ hôm qua" là vô nghĩa.

    `week_offset` (REVIEW-C13): số ngày đã trôi của tuần ISO tại ngày 0 — tức `weekday()`
    của ngày bắt đầu (0=Thứ Hai). Ranh giới tuần của `DriverMemory` nhờ đó TRÙNG với tuần
    ISO mà bảng data tuần (`_week_key`, kpi_driver_platform...) dùng. Mặc định 0 = ngày 0
    là Thứ Hai. Nếu hai định nghĩa tuần lệch nhau thì khoán tuần của S5 không join được
    với data — hai nguồn sự thật về "tuần".
    """
    out: list[RunResult] = []
    final_mem: dict[int, DriverMemory] = {}
    for d, r, mem in iter_multiday(cfg, seed=seed, days=days, week_offset=week_offset):
        out.append(r)
        final_mem = mem
    return MultiDayResult(seed=seed, days=out, memory=final_mem)
