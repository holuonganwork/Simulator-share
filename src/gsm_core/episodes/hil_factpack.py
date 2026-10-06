"""HIL snapshot → FactPack — cầu nối duy nhất giữa phiên đóng vai và đường narration.

## Vì sao builder riêng thay vì đi qua CompositeEpisode

`FactPack` vốn được dựng từ `CompositeEpisode` đã validate. Phiên HIL không có episode —
nguồn sự thật của nó là `gsm_sim.hil.projection.snapshot()` (ba-nguồn-hợp-lệ, cấm rò tương
lai). Builder này dựng FactPack **trực tiếp từ snapshot**, nhờ đó tái dùng nguyên trạng
`narrate()` + verifier + cache mà không đụng một dòng nào của đường episode.

## Ba ràng buộc mà từng dòng ở đây phục vụ

1. **Mọi số model được viết phải nằm trong `validated_numbers`** (luật N1) — nên mỗi con số
   lấy từ snapshot đều thành một `FactNumber` với `source` trỏ về đúng đường dẫn trong
   snapshot. Không có số nào sinh tại chỗ.
2. **Chữ hành động bị verifier canh** (`check_actions`): "đổi pin"/"kết thúc ca"/"nghỉ" chỉ
   hợp lệ khi code tương ứng có trong `current_action`/`future_plan`. Vì thế gợi ý bản năng
   được map sang vocabulary chuẩn của factpack (`INSTINCT_TO_ACTION`) và đặt vào
   `current_action` — không phải để hiển thị, mà để verifier biết hành động đó CÓ THẬT.
3. **Không nêu địa điểm đích** (`check_location` + ranh giới "không gợi ý điểm nóng"):
   target của relocate/go_swap bị cố ý BỎ khỏi fact/template — người chơi thấy hành động,
   không thấy toạ độ. Lát 2 (lệnh có tham số) mới mở chuyện chọn đích, với thiết kế riêng.

Thuần: không clock, không network, không RNG — hai lần gọi cùng snapshot cho pack
byte-identical (`fingerprint` bằng nhau), điều kiện để `NarrationCache` an toàn.
"""
from __future__ import annotations

from typing import Any

from gsm_core.episodes.factpack import (
    FACTPACK_SCHEMA_VERSION,
    MAX_WHY_CHARS,
    TASK_HIL_PAUSE,
    Fact,
    FactNumber,
    FactPack,
    _clock,
)

# 🔴 GỠ KHỎI `caveats` 2026-08-23 (Cường: *"không lời khuyên nào được chứa cảnh báo đây là
# mô phỏng hay dữ liệu MOCK"*).
#
# `caveats` đi **HAI đường**: ra màn hình cho tài xế, VÀ vào prompt bộ tổng hợp. Nên câu này
# vừa hiện trên UI vừa dạy model nói về mô phỏng ở mọi lượt.
#
# ⚠ KHÔNG phải nới `CLAUDE.md` §5. Nhãn mock vẫn bắt buộc — nó chỉ đổi **CHO AI ĐỌC**, đúng
# theo quyết định 2026-08-21 (Cường chốt phương án (b)): nhãn sống ở
# `ResponseDraft.data_mode_note`, tức dòng CHẨN ĐOÁN cho kỹ sư. Hằng số dưới đây giữ lại
# **đúng cho mục đích ấy** và cho test đối chứng — không hàm nào được đưa nó vào `caveats`.
# Cổng: `tests/test_hil_la_tai_xe_that.py`.
HIL_CAVEAT = ("Toàn bộ là mô phỏng (MOCK) — người chơi đang đóng vai, "
              "không phải tài xế thật.")

# Nhãn tiếng Việt cho trạng thái phiên/điểm dừng (state_label của pack).
PAUSE_LABELS: dict[str, str] = {
    "BEFORE_SHIFT": "Chờ vào ca",
    "OFFER": "Có cuốc mới",
    "IDLE": "Đang rảnh, chờ quyết định",
    "COMPLETED": "Ca đã kết thúc",
}

# IdleAction.value → action code trong vocabulary factpack (xem ràng buộc 2 ở docstring).
# `wait`/`go_charge` không có code tương ứng: chữ mô tả chúng ("tiếp tục chờ", "đi sạc pin")
# không nằm trong ACTION_PHRASES nên verifier không cần được báo trước.
INSTINCT_TO_ACTION: dict[str, str] = {
    "go_swap": "SWAP",
    "end_shift": "END",
    "relocate": "REPOSITION_SIM_ONLY",
    "rest": "REST",
}

# 🔴 `INSTINCT_LABELS` KHÔNG còn được dùng để nói với tài xế (Cường 2026-08-19: *"nếu nó không
# phải gợi ý từ advisor thì không in ra"*). Nó từng sinh ra đúng câu anh chỉ đích danh —
# *"Thói quen của bạn lúc này là kết thúc ca"* — và câu đó đến từ `behavior.choose_idle_action`,
# tức bản năng của actor SIM, không phải phán quyết của solver nào.
#
# Giữ bảng lại vì `INSTINCT_TO_ACTION` ở trên vẫn cần vocabulary để **verifier** biết hành động
# nào có thật; và vì nhật ký/replay vẫn đọc `hint.instinct`. Nhưng không hàm nào ở dưới được
# đưa nó vào `fact`/`template` nữa — cổng `test_narration_khong_noi_giong_ban_nang` canh.
INSTINCT_LABELS: dict[str, str] = {
    "wait": "tiếp tục chờ tại chỗ",
    "relocate": "di chuyển khu vực",
    "go_swap": "đi đổi pin",
    "go_charge": "đi sạc pin",
    "rest": "tạm nghỉ một lúc",
    "end_shift": "kết thúc ca",
}

_SIGNAL_LABELS: dict[str, str] = {
    "SOC_LOW": "pin thấp",
    "SOC_CRITICAL": "pin rất thấp",
    "SHIFT_ENDING": "sắp hết ca",
    "BONUS_NEAR": "sát mốc thưởng",
}


def _vnd(value: Any) -> str:
    """62000 → `62.000đ` (dấu chấm ngăn nghìn — chuẩn hiển thị VN của repo)."""
    return f"{int(round(float(value))):,}".replace(",", ".") + "đ"


def _km(value: Any) -> str:
    """1.23 → `1,2 km` (dấu phẩy thập phân)."""
    return f"{float(value):.1f}".replace(".", ",") + " km"


def _pct_int(value01: Any) -> str:
    """0.8412 → `84%` — model chỉ được viết bản display, không viết xác suất thô."""
    return f"{int(round(float(value01) * 100))}%"


class _PackDraft:
    """Gom fact/number với id tuần tự — chỉ là tiện ích cục bộ, không phải API."""

    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.numbers: list[FactNumber] = []

    def fact(self, text: str, at_min: float | None = None) -> None:
        self.facts.append(Fact(fact_id=f"f{len(self.facts) + 1}", kind="observation",
                               text=text, evidence_mode="OBSERVED", at_min=at_min))

    def number(self, key: str, label: str, value: Any, unit: str, display: str,
               source_path: str) -> None:
        self.numbers.append(FactNumber(
            number_id=f"n{len(self.numbers) + 1}", key=key, label=label, value=value,
            unit=unit, display=display, source=f"hil.snapshot.{source_path}"))


def _offer_lines(draft: _PackDraft, pause: dict, t_min: float) -> str:
    hint = dict(pause.get("hint") or {})
    ctx = dict(pause.get("context") or {})
    gross = ctx.get("gross_vnd")
    pickup = ctx.get("pickup_dist_km")
    parts = ["Có cuốc mới"]
    if gross is not None:
        draft.number("gross_vnd", "giá cuốc (gross)", int(gross), "đ", _vnd(gross),
                     "pause.context.gross_vnd")
        parts.append(f"giá {_vnd(gross)}")
    if pickup is not None:
        draft.number("pickup_dist_km", "quãng đường đi đón", float(pickup), "km",
                     _km(pickup), "pause.context.pickup_dist_km")
        parts.append(f"đón cách {_km(pickup)}")
    draft.fact(", ".join(parts) + ".", at_min=t_min)

    # 🔴 KHÔNG nêu `instinct_accepts`/`p_accept`. Hai lý do, và lý do thứ hai là ranh giới cứng:
    #   1. Đó là xác suất bản mô phỏng NHẬN cuốc — một tham số mô hình, không phải lời khuyên.
    #   2. CLAUDE.md §5 cấm khuyên nhận/từ chối **một cuốc cụ thể**. Một câu nghiêng về "nhận"
    #      đặt ngay cạnh nút «Nhận cuốc» thì khoảng cách giữa *quan sát thói quen* và *lời
    #      khuyên* mỏng tới mức không ai phân biệt được lúc đang vội. `hil.js` đã bỏ câu này từ
    #      2026-08-17; đường narration vẫn còn nó tới hôm nay — cùng một khuyết tật, cửa khác.
    # Dữ kiện (giá, cự ly đón) giữ nguyên: đó mới là thứ tài xế cần để tự quyết.
    return (f"Có cuốc mới{f' giá {_vnd(gross)}' if gross is not None else ''}"
            f"{f', đón cách {_km(pickup)}' if pickup is not None else ''}. "
            f"Quyết định vẫn là của bác.")


def _idle_lines(draft: _PackDraft, pause: dict, t_min: float) -> tuple[str, dict | None]:
    """Lúc rảnh: chỉ SỰ KIỆN — trừ khi ADVISOR có lời khuyên, và khi đó nói rõ là của advisor.

    🔴 Bản trước sinh câu *"Thói quen của bạn lúc này là kết thúc ca"* từ `hint.instinct`. Đó
    đúng là câu Cường chỉ đích danh 2026-08-19, và nó **không** đến từ solver nào —
    `behavior.choose_idle_action` là bản năng của actor SIM. Đặt cạnh một nút làm theo, nó khoác
    uy tín của trợ lý lên một tham số mô phỏng.

    Từ UPDATE-259, snapshot mang `hint.source`. Chỉ `"advisor"` mới được nói.
    """
    hint = dict(pause.get("hint") or {})
    ctx = dict(pause.get("context") or {})

    soc = ctx.get("soc_pct")
    if soc is not None:
        draft.number("soc_pct", "mức pin", float(soc), "%",
                     f"{float(soc):.0f}%", "pause.context.soc_pct")
    draft.fact(f"Bác đang rảnh{f', pin {float(soc):.0f}%' if soc is not None else ''}.",
               at_min=t_min)

    la_advisor = hint.get("source") == "advisor"
    hanh_dong = str(hint.get("action") or "") if la_advisor else ""
    code = INSTINCT_TO_ACTION.get(hanh_dong) if la_advisor else None
    current_action = None
    if code is not None:
        # `current_action` KHÔNG phải để hiển thị — nó nói cho verifier biết hành động ấy CÓ
        # THẬT, nếu không `check_actions` sẽ bác chính câu khuyên hợp lệ. `authority` nay là
        # `advisor` chứ không phải `instinct`: đó là sự thật về ai đưa ra khuyến nghị.
        from gsm_core.episodes.factpack import ACTION_LABELS
        current_action = {"code": code, "label": ACTION_LABELS.get(code, code),
                          "window": None, "window_label": None, "authority": "advisor"}

    if la_advisor and hanh_dong:
        # Cố ý KHÔNG nêu mã ô đích (ràng buộc 3 ở docstring + `check_location`): chuỗi H3 không
        # nói gì với người, và bản đồ đã tô sẵn ô đó.
        nhan = INSTINCT_LABELS.get(hanh_dong, hanh_dong)
        draft.fact(f"Trợ lý gợi ý {nhan} sang khu vực đã tô trên bản đồ.", at_min=t_min)
        template = (f"Bác đang rảnh{f' (pin {float(soc):.0f}%)' if soc is not None else ''}. "
                    f"Trợ lý gợi ý {nhan} sang khu vực đã tô trên bản đồ — "
                    f"quyết định vẫn là của bác.")
        return template, current_action

    # Advisor không có gì để nói ⇒ IM. Chỉ thuật lại sự kiện, không một chữ nào về việc nên làm.
    # ⚠ Không dùng cụm "làm theo" trong văn narration — luật N8 (check_adherence) cấm.
    template = f"Bác đang rảnh{f' (pin {float(soc):.0f}%)' if soc is not None else ''}."
    return template, None


def _before_shift_lines(draft: _PackDraft, pause: dict, t_min: float) -> str:
    hint = dict(pause.get("hint") or {})
    start = hint.get("shift_start_min")
    if start is not None:
        draft.fact(f"Ca của bác bắt đầu lúc {_clock(float(start))}.", at_min=t_min)
        return (f"Bác chuẩn bị vào ca lúc {_clock(float(start))}. "
                f"Bấm «Vào ca» khi sẵn sàng.")
    draft.fact("Phiên đã sẵn sàng, chờ bác vào ca.", at_min=t_min)
    return "Phiên đã sẵn sàng. Bấm «Vào ca» khi sẵn sàng."


def _day_so_far_lines(draft: _PackDraft, day: dict, t_min: float) -> None:
    payout = day.get("payout_vnd")
    points = day.get("points")
    trips = day.get("trips_done")
    if payout is None and points is None and trips is None:
        return
    parts = []
    if payout is not None:
        draft.number("payout_vnd", "thu nhập tài xế từ đầu ca", int(payout), "đ",
                     _vnd(payout), "day_so_far.payout_vnd")
        parts.append(f"thu nhập {_vnd(payout)}")
    if points is not None:
        draft.number("points", "điểm thưởng", int(points), "điểm", str(int(points)),
                     "day_so_far.points")
        parts.append(f"{int(points)} điểm")
    if trips is not None:
        draft.number("trips_done", "số cuốc đã xong", int(trips), "cuốc",
                     str(int(trips)), "day_so_far.trips_done")
        parts.append(f"{int(trips)} cuốc đã xong")
    if parts:
        draft.fact("Từ đầu ca tới giờ: " + ", ".join(parts) + ".", at_min=t_min)


def _signal_lines(draft: _PackDraft, signals: list, t_min: float) -> None:
    for sig in signals or ():
        code = str(sig.get("code") or "")
        basis = dict(sig.get("basis") or {})
        if code in ("SOC_LOW", "SOC_CRITICAL") and basis.get("soc_pct") is not None:
            soc = float(basis["soc_pct"])
            draft.number(f"{code.lower()}_soc", "mức pin (tín hiệu)", soc, "%",
                         f"{soc:.0f}%", "signals.basis.soc_pct")
            draft.fact(f"Tín hiệu {_SIGNAL_LABELS.get(code, code)}: pin còn {soc:.0f}%.",
                       at_min=t_min)
        elif code == "SHIFT_ENDING" and basis.get("minutes_left") is not None:
            left = float(basis["minutes_left"])
            draft.number("minutes_left", "số phút còn lại của ca", left, "phút",
                         f"{left:.0f}", "signals.basis.minutes_left")
            draft.fact(f"Còn {left:.0f} phút nữa là hết ca.", at_min=t_min)
        elif code == "BONUS_NEAR" and basis.get("points_needed") is not None:
            need = int(basis["points_needed"])
            draft.number("points_needed", "số điểm còn thiếu tới mốc thưởng", need,
                         "điểm", str(need), "signals.basis.points_needed")
            tier_vnd = basis.get("tier_bonus_vnd")
            if tier_vnd is not None:
                draft.number("tier_bonus_vnd", "tiền thưởng của mốc kế", int(tier_vnd),
                             "đ", _vnd(tier_vnd), "signals.basis.tier_bonus_vnd")
                draft.fact(f"Còn {need} điểm nữa tới mốc thưởng {_vnd(tier_vnd)}.",
                           at_min=t_min)
            else:
                draft.fact(f"Còn {need} điểm nữa tới mốc thưởng kế tiếp.", at_min=t_min)


def build_hil_pack(snap: dict[str, Any]) -> FactPack:
    """Snapshot phiên HIL → FactPack cho task `HIL_PAUSE`. Thuần, deterministic.

    Chấp nhận snapshot ở mọi trạng thái: có `pause` (ba loại điểm dừng) hoặc không
    (phiên COMPLETED ⇒ câu tổng kết). Field tuỳ chọn vắng mặt thì bỏ ý tương ứng,
    không raise — snapshot là contract của projection, builder không tự đòi thêm."""
    session_id = str(snap.get("session_id") or "hil")
    version = int(snap.get("version") or 0)
    t_min = float(snap.get("t_min") or 0.0)
    pause = snap.get("pause")
    state = str(snap.get("state") or "")
    kind = str(pause["kind"]) if pause else state or "UNKNOWN"

    draft = _PackDraft()
    current_action: dict | None = None

    if pause and kind == "OFFER":
        template = _offer_lines(draft, pause, t_min)
    elif pause and kind == "IDLE":
        template, current_action = _idle_lines(draft, pause, t_min)
    elif pause and kind == "BEFORE_SHIFT":
        template = _before_shift_lines(draft, pause, t_min)
    elif kind == "COMPLETED":
        draft.fact("Ca đã kết thúc.", at_min=t_min)
        template = "Ca đã kết thúc — xem tổng kết bên dưới."
    else:
        draft.fact("Phiên đang chạy, chưa có điểm dừng nào chờ quyết định.", at_min=t_min)
        template = "Chưa có gì chờ quyết định."

    _signal_lines(draft, snap.get("signals") or [], t_min)
    day = snap.get("day_so_far")
    if isinstance(day, dict):
        _day_so_far_lines(draft, day, t_min)

    driver = snap.get("driver") or {}
    return FactPack(
        task_type=TASK_HIL_PAUSE,
        schema_version=FACTPACK_SCHEMA_VERSION,
        episode_id=f"hil:{session_id}:{version}",
        episode_type="HIL_PAUSE",
        run_id=f"hil-{session_id}",
        actor_id=int(snap.get("human_actor_id") or 0),
        driver_id=f"hil-{int(snap.get('human_actor_id') or 0)}",
        material_revision=version,
        at_min=t_min,
        data_mode="SIM",
        is_mock=True,
        evidence_mode="OBSERVED",
        lifecycle_state=kind,
        state_label=PAUSE_LABELS.get(kind, kind),
        current_action=current_action,
        future_plan=(),
        plan_deltas=(),
        observed_facts=tuple(draft.facts),
        inferred_facts=(),
        validated_numbers=tuple(draft.numbers),
        episode_refs=(),
        source_refs=(f"hil:{session_id}",),
        # `caveats` RỖNG: xem ghi chú ở `HIL_CAVEAT`. Nhãn nguồn dữ liệu không đi đường này.
        caveats=(),
        template_baseline=template,
        max_output_chars=MAX_WHY_CHARS,
    )
