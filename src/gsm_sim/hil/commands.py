"""HIL Lát 1 — lệnh của tài xế người và bộ kiểm theo tầng.

## Vì sao từ chối là KẾT QUẢ, không phải exception

Khuôn lấy nguyên từ `src/gsm_sim/agent/broker.py:28-31`: mỗi lệnh bị chặn phải trả về một
`ValidationResult` mang **tên tầng đã chặn**, không ném exception. Lý do đã trả giá ở đường
agent: từ chối im lặng thì không kiểm chứng được, và một tầng không nói tên mình thì không ai
chứng minh được nó là tầng ĐANG chặn — test sẽ xanh nhờ một tầng khác chặn hộ.

⇒ Luật của file này: **mỗi tầng phải ĐỎ ĐƯỢC MỘT MÌNH.** Test ma trận ở
`tests/test_hil_commands.py` gỡ từng tầng một và đòi đúng tầng đó phải là tầng từ chối.

## Thứ tự tầng (KHÔNG được đổi)

Thứ tự đi từ "rẻ nhất, không chạm state" tới "đắt nhất":

    0 UNKNOWN_COMMAND         tên lệnh không thuộc bộ đóng
    1 WRONG_SESSION           session_id không khớp
    2 BAD_SESSION_STATE       phiên chưa ACTIVE / đã COMPLETED / đã ABANDONED
    3 DUPLICATE_COMMAND       client_command_id đã phục vụ (idempotency — trả lại kết quả cũ)
    4 STALE_VERSION           expected_version lệch (optimistic concurrency)
    5 NOT_AWAITING_COMMAND    sim không đang chờ quyết định nào
    6 WRONG_COMMAND_FOR_PAUSE lệnh không hợp với loại điểm dừng đang chờ
    7 BAD_PAYLOAD             tham chiếu sai (offer_ref không khớp cuốc đang treo)

Tầng 3 đứng TRƯỚC tầng 4 có chủ đích: một lần bấm lặp (mạng chập chờn) phải trả lại kết quả cũ,
chứ không được báo lỗi version — người dùng bấm một lần, không phải hai.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CommandKind(str, Enum):
    """Bộ lệnh ĐÓNG của Lát 1. Thêm lệnh mới = thêm ở đây + một hàng trong ma trận test.

    ## 🔴 Vì sao có `FOLLOW_SUGGESTION` — một lỗi thiết kế ĐÃ ĐO ĐƯỢC (2026-08-13)

    Bản đầu chỉ có `CONTINUE_WAITING` / `END_SHIFT` cho điểm dừng lúc rảnh. Hệ quả **đo được**:
    khi bản năng gợi ý `RELOCATE` / `GO_SWAP` / `REST`, người dùng **không có lệnh nào diễn đạt
    được gợi ý đó** — lựa chọn duy nhất là đứng yên. Actor kẹt trong ô xấu, không bao giờ đổi
    chỗ, không bao giờ đi đổi pin, và bị hỏi lại liên tục: **462 lượt hỏi/ngày** thay vì 27
    (actor 63, seed 1000). Bộ lệnh đóng mà không phủ được không gian hành động của engine thì
    chính nó làm hỏng thế giới.

    `FOLLOW_SUGGESTION` = "làm điều hệ thống đang gợi ý", bất kể gợi ý đó là đổi chỗ, đổi pin,
    nghỉ hay kết ca. Một lệnh, phủ toàn bộ khoảng trống, và **đúng ngữ nghĩa sản phẩm**: agent
    gợi ý — tài xế quyết định (CLAUDE.md §5: hệ thống không tự thực thi thay tài xế).

    ## Lát 2 (2026-08-19) — bốn lệnh CÓ THAM SỐ

    `FOLLOW_SUGGESTION` chỉ cho phép *đồng ý*; nó không bao giờ cho phép *chọn khác*. Mà từ khi
    advisor lên tiếng (UPDATE-259), "gợi ý" trên màn hình là lời khuyên **của advisor** — nếu
    lệnh duy nhất là đồng ý thì tài xế chỉ có một nút, và cả demo trở thành minh hoạ cho việc
    làm theo, không phải cho việc **quyết định**. Cường 2026-08-19: *"tài xế giả lập có thể thực
    hiện đầy đủ tác vụ qua các nút trên màn hình"*.

    ⇒ `RELOCATE(cell)` · `GO_SWAP(station_id?)` · `GO_CHARGE` · `START_REST(rest_min?)` phủ nốt
    không gian hành động của `behavior.IdleAction`. `FOLLOW_SUGGESTION` GIỮ LẠI: nhật ký lệnh cũ
    và `run_to_end`/`replay` đang dùng nó, bỏ đi là làm hỏng mọi phiên đã ghi.

    Tham số **tuỳ chọn** ở `GO_SWAP`/`START_REST` có chủ đích: không truyền ⇒ engine dùng đúng
    đường cũ (`choose_station` / `rng.uniform`). Nghĩa là UI có thể mở nút trước, mở bộ chọn sau,
    mà không có nhánh nào chạy bằng giá trị bịa.
    """

    START_SHIFT = "START_SHIFT"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    DECLINE_OFFER = "DECLINE_OFFER"
    FOLLOW_SUGGESTION = "FOLLOW_SUGGESTION"
    CONTINUE_WAITING = "CONTINUE_WAITING"
    END_SHIFT = "END_SHIFT"
    # --- Lát 2 (Cường 2026-08-19: *"tài xế giả lập có thể thực hiện đầy đủ tác vụ qua các nút"*)
    RELOCATE = "RELOCATE"          # payload.cell — ô H3 lõi, KHÁC ô đang đứng
    GO_SWAP = "GO_SWAP"            # payload.station_id (tuỳ chọn) — chỉ xe loại SWAP
    GO_CHARGE = "GO_CHARGE"        # về nhà cắm sạc — không tham số
    START_REST = "START_REST"      # payload.rest_min (tuỳ chọn) — không truyền ⇒ engine tự rút


class PauseKind(str, Enum):
    """Vì sao sim đang đứng chờ người."""

    BEFORE_SHIFT = "BEFORE_SHIFT"    # chờ START_SHIFT
    OFFER = "OFFER"                  # chờ ACCEPT_OFFER / DECLINE_OFFER
    IDLE = "IDLE"                    # chờ CONTINUE_WAITING / END_SHIFT (Lát 2 thêm swap/rest/relocate)
    OUT_OF_CORE = "OUT_OF_CORE"      # trả khách NGOÀI vùng pilot — chờ RELOCATE về một ô lõi


# Lệnh nào hợp với điểm dừng nào — nguồn sự thật DUY NHẤT cho tầng 6.
ALLOWED_COMMANDS: dict[PauseKind, frozenset[CommandKind]] = {
    PauseKind.BEFORE_SHIFT: frozenset({CommandKind.START_SHIFT}),
    PauseKind.OFFER: frozenset({CommandKind.ACCEPT_OFFER, CommandKind.DECLINE_OFFER}),
    PauseKind.IDLE: frozenset({CommandKind.FOLLOW_SUGGESTION, CommandKind.CONTINUE_WAITING,
                               CommandKind.END_SHIFT, CommandKind.RELOCATE,
                               CommandKind.GO_SWAP, CommandKind.GO_CHARGE,
                               CommandKind.START_REST}),
    # 🔴 Ngoài lõi thì **chỉ có một việc**: chọn một ô lõi để về. Không phải vì ta muốn hạn chế
    # tài xế, mà vì ngoài vùng pilot không có cầu, không có trạm, không có gì để đo — đứng lại
    # đó là đứng ngoài thế giới. Cái tài xế được chọn là **về ô NÀO**, và đó là lựa chọn thật:
    # advisor gợi ý một ô, các ô lõi khác vẫn bấm được.
    PauseKind.OUT_OF_CORE: frozenset({CommandKind.RELOCATE}),
}


class RejectLayer(str, Enum):
    """Tên tầng đã chặn. Mỗi tầng phải ĐỎ ĐƯỢC MỘT MÌNH (xem docstring module)."""

    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    WRONG_SESSION = "WRONG_SESSION"
    BAD_SESSION_STATE = "BAD_SESSION_STATE"
    DUPLICATE_COMMAND = "DUPLICATE_COMMAND"
    STALE_VERSION = "STALE_VERSION"
    NOT_AWAITING_COMMAND = "NOT_AWAITING_COMMAND"
    WRONG_COMMAND_FOR_PAUSE = "WRONG_COMMAND_FOR_PAUSE"
    BAD_PAYLOAD = "BAD_PAYLOAD"


@dataclass(frozen=True)
class Command:
    """Một lệnh từ UI.

    `client_command_id` do client sinh và PHẢI ổn định qua retry — đó là khoá idempotency.
    `expected_version` là số lệnh đã áp mà client tin là hiện hành (optimistic concurrency,
    khuôn `demo_session.py:370-373`)."""

    kind: CommandKind
    session_id: str
    client_command_id: str
    expected_version: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PausePoint:
    """Trạng thái "sim đang đứng chờ người" — do session dựng, validator chỉ đọc."""

    kind: PauseKind
    t_min: float
    offer_ref: str | None = None                       # chỉ có ở OFFER
    hint: dict[str, Any] = field(default_factory=dict)  # bản năng định làm gì (để UI hiển thị)


@dataclass(frozen=True)
class ValidationResult:
    """Kết quả kiểm. `ok=False` LUÔN kèm `layer` — không có từ chối vô danh."""

    ok: bool
    layer: RejectLayer | None = None
    reason: str = ""
    is_duplicate: bool = False        # True khi tầng DUPLICATE_COMMAND bắt được retry hợp lệ


def validate_command(cmd: Command, *, session_id: str, state: str, version: int,
                     pause: PausePoint | None, served: dict[str, Any]) -> ValidationResult:
    """Chạy 8 tầng theo đúng thứ tự. Trả `ValidationResult` — KHÔNG ném exception.

    Tham số cố ý là giá trị thuần (không nhận cả object session) để hàm này **thuần** và test
    được không cần dựng World.
    """
    # Tầng 0 — lệnh lạ. `CommandKind` là Enum nên tên lạ đã chết ở biên parse, nhưng giữ tầng
    # này để nếu ai đó nới kiểu sang `str` cho tiện JSON thì vẫn có chốt chặn CÓ TÊN (không thì
    # chuỗi lạ rơi xuống tầng 6 và nổ KeyError → HTTP 500 thay vì một mã từ chối).
    if not isinstance(cmd.kind, CommandKind):
        return ValidationResult(False, RejectLayer.UNKNOWN_COMMAND,
                                f"lệnh không thuộc bộ đóng: {cmd.kind!r}")

    # Tầng 1 — sai phiên.
    if cmd.session_id != session_id:
        return ValidationResult(False, RejectLayer.WRONG_SESSION,
                                f"lệnh gửi cho phiên {cmd.session_id!r}, phiên này là {session_id!r}")

    # Tầng 2 — phiên sai trạng thái. `END_REQUESTED` VẪN nhận lệnh vì tài xế còn phải chạy nốt
    # cuốc đang chạy — `END_SHIFT` không cắt ngang (quyết định owner 2026-08-13).
    if state not in ("ACTIVE", "END_REQUESTED"):
        return ValidationResult(False, RejectLayer.BAD_SESSION_STATE,
                                f"phiên đang {state}, không nhận lệnh")

    # Tầng 3 — idempotency. ĐỨNG TRƯỚC tầng version có chủ đích: bấm lặp do mạng phải trả lại
    # kết quả cũ, không được báo lỗi version (`demo_session.py:360-367`).
    if cmd.client_command_id in served:
        return ValidationResult(True, RejectLayer.DUPLICATE_COMMAND,
                                f"client_command_id {cmd.client_command_id!r} đã phục vụ",
                                is_duplicate=True)

    # Tầng 4 — optimistic concurrency.
    if cmd.expected_version != version:
        return ValidationResult(False, RejectLayer.STALE_VERSION,
                                f"expected_version={cmd.expected_version} ≠ hiện hành {version}")

    # Tầng 5 — sim không đang chờ ai.
    if pause is None:
        return ValidationResult(False, RejectLayer.NOT_AWAITING_COMMAND,
                                "sim không đang chờ quyết định nào — gọi step() trước")

    # Tầng 6 — lệnh không hợp loại điểm dừng.
    allowed = ALLOWED_COMMANDS[pause.kind]
    if cmd.kind not in allowed:
        return ValidationResult(False, RejectLayer.WRONG_COMMAND_FOR_PAUSE,
                                f"đang ở {pause.kind.value}, chỉ nhận "
                                f"{sorted(x.value for x in allowed)}, nhận được {cmd.kind.value}")

    # Tầng 7 — payload. Chỉ nhánh cuốc mới có tham chiếu để kiểm.
    if pause.kind is PauseKind.OFFER:
        ref = cmd.payload.get("offer_ref")
        if ref != pause.offer_ref:
            return ValidationResult(False, RejectLayer.BAD_PAYLOAD,
                                    f"offer_ref={ref!r} ≠ cuốc đang treo {pause.offer_ref!r}")

    return ValidationResult(True)
