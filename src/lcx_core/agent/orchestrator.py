"""Pipeline tat dinh cua agent (Tier D) - goi tool theo THU TU CO DINH, LLM (neu
co) chi dien dat ket qua thanh cau van, KHONG tu quyet dinh goi tool nao.

Dung dung triet ly "tool-free" cua src/gsm_core/advisor/advice_agent.py trong
GSM_SIMULATOR: moi con so trong cau tra loi phai la fact DA TINH SAN (tu cac
tool), khong phai do LLM "doan". O day chua noi bat cu LLM API nao (khong co
key duoc cau hinh) - draft_* la HAM TEMPLATE thuan Python dien fact vao cau,
dong dung vai tro "presentation layer" ma o he thong that se duoc thay bang mot
LLM call CHI DE VIET LAI VAN PHONG, khong duoc doi so lieu (xem
agent/verification.py - moi ban nhap deu phai qua verify_message truoc khi gui).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lcx_core.agent.tools import TOOLS
from lcx_core.agent.tools._common import ToolResult
from lcx_core.data.gsm_loader import GsmDataStore

# Ba bac che tai that (khong bia) - xem agent/tools/apply_action.py va
# GSM_SIMULATOR src/gsm_core/advisor/policy_kb.py:252.
SANCTION_TEXT_VI = (
    "tru 100% thuong tuan, tam khoa tai khoan 7 ngay hoac cham dut hop tac vinh "
    "vien tuy muc do vi pham"
)
EXPLAIN_DEADLINE_HOURS = 48


@dataclass(frozen=True)
class InvestigationBundle:
    driver_id: str
    steps: dict[str, ToolResult] = field(default_factory=dict)

    def step(self, name: str) -> ToolResult | None:
        return self.steps.get(name)


PIPELINE_STEPS: list[tuple[str, str]] = [
    # (buoc pipeline, ten tool trong TOOLS) - THU TU CO DINH, khong doi theo hoi thoai
    ("risk_profile", "get_driver_risk_profile"),
    ("location_integrity", "check_location_integrity"),
    ("incentive_activity", "get_incentive_activity"),
    ("device_account_graph", "query_device_account_graph"),
]


def investigate(store: GsmDataStore, driver_id: str) -> InvestigationBundle:
    """Chay dung 4 buoc theo PIPELINE_STEPS, theo THU TU CO DINH cho MOI driver -
    day la diem khac biet cot loi voi mot agent "tu do goi tool": khong co nhanh
    re dua tren noi dung hoi thoai, chi co dua tren KET QUA (vd device graph luon
    duoc thu, du biet truoc se bi chan boi missing_data_tier_c - de log lai dung
    trang thai "da kiem tra, chua co du lieu" thay vi "chua kiem tra").
    """
    steps: dict[str, ToolResult] = {}
    for step_name, tool_name in PIPELINE_STEPS:
        tool_fn = TOOLS[tool_name]
        steps[step_name] = tool_fn(store, driver_id)
    return InvestigationBundle(driver_id=driver_id, steps=steps)


def source_texts(bundle: InvestigationBundle) -> list[str]:
    """Tat ca text co the chua so lieu hop le - dung cho verify_message."""
    texts = []
    for result in bundle.steps.values():
        texts.append(str(result.data))
    return texts


def draft_driver_explanation_request(bundle: InvestigationBundle, pattern: str) -> str:
    """Ban nhap tin nhan cho CHAT TAI XE - khai quat, khong buoc toi, khong lo
    nguong/thuat toan (guardrail cua schemas/l3/anomaly_alert_input.schema.json).

    `bundle` chua duoc dung de ca nhan hoa noi dung (giu template hoan toan
    khai quat, dung tinh than "khong lo chi tiet phat hien") - tham so duoc giu
    lai trong chu ky ham de ban nhap tuong lai co the tham chieu lich su ma
    khong lo nguong/thuat toan cu the.
    """
    return (
        f"Chao anh/chi, he thong ghi nhan mot vai chi tiet can lam ro o chuyen/ngay "
        f"gan day lien quan toi '{pattern}'. Anh/chi vui long cho biet ly do "
        f"(vd ket xe, khach doi lo trinh...) trong vong {EXPLAIN_DEADLINE_HOURS} gio "
        f"de ho so duoc xu ly nhanh hon. Neu co anh chup/bang chung lien quan, "
        f"anh/chi co the dinh kem de ho tro xac minh."
    )


def draft_manager_summary(bundle: InvestigationBundle) -> str:
    """Ban nhap tom tat cho CHAT QUAN LY - day du chi tiet, dua tren fact THAT
    lay tu cac tool (khong tu suy dien) - so voi phien ban tai xe.
    """
    risk = bundle.step("risk_profile")
    loc = bundle.step("location_integrity")
    incentive = bundle.step("incentive_activity")
    device = bundle.step("device_account_graph")

    lines = [f"Tom tat dieu tra driver_id={bundle.driver_id}:"]

    if risk and risk.ok:
        d = risk.data
        lines.append(
            f"- Lich su: {d['n_prior_cases']} case truoc do "
            f"({d['n_confirmed_cases']} da confirmed), ty le huy trung binh "
            f"{d['avg_cancellation_rate']:.1%} qua {d['days_observed_in_window']} ngay quan sat."
        )
    if loc and loc.ok:
        d = loc.data
        lines.append(
            f"- Toan ven vi tri: {d['n_implausible_speed_events']} lan van toc phi vat ly, "
            f"{d['n_location_dropout_events']} lan mat dinh vi bat thuong."
        )
    if incentive and incentive.ok:
        d = incentive.data
        lines.append(
            f"- Thuong/KPI: {d['n_weekly_earn_anomalies']} tuan co tien thuong bat thuong "
            f"so voi chinh driver do (tong {d['n_mission_earn_records']} ban ghi mission)."
        )
    if device:
        if device.ok:
            n = len(device.data.get("clusters", []))
            lines.append(f"- Do thi thiet bi/tai khoan: {n} cum lien quan.")
        else:
            lines.append(
                "- Do thi thiet bi/tai khoan: CHUA CO DU LIEU "
                f"(blocked_by={device.blocked_by})."
            )

    lines.append(
        f"\nNhac lai chinh sach: che tai gom {SANCTION_TEXT_VI} "
        "(GSM_SIMULATOR src/gsm_core/advisor/policy_kb.py:252)."
    )
    return "\n".join(lines)


def bundle_to_dict(bundle: InvestigationBundle) -> dict[str, Any]:
    return {"driver_id": bundle.driver_id, "steps": {k: v.to_dict() for k, v in bundle.steps.items()}}
