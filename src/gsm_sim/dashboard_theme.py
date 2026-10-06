"""SIM-XANH P4 — design tokens + template cho dashboard (một nguồn sự thật về MÀU).

Quy trình dataviz-skill đã theo ĐỦ:
  1. Form trước, màu SAU. 2. Màu theo JOB (categorical=identity, sequential=magnitude,
  status=state). 3. **Palette VALIDATED bằng script** (5/5 checks trên surface #12181c —
  lightness band, chroma floor, CVD ΔE=16.0 worst-pair, normal-vision 19.7, contrast ≥3:1),
  không ước bằng mắt. 4. Idle KHÔNG phải series — nó là "vắng hoạt động": tông nền mờ +
  nhãn trực tiếp (secondary encoding), không chiếm slot màu.

Taste-skill (phần chuyển giao được cho dashboard — landing-page rules out-of-scope §13):
  MỘT accent (aqua #199e70, trùng brand Xanh) khoá toàn trang · theme khoá dark ·
  không AI-purple/neon-glow · bo góc một hệ (8px) · số dùng mono · không em-dash.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# ---------- tokens ----------
SURFACE = "#12181c"          # nền trang
SURFACE_ELEV = "#1a2126"     # card / panel
SURFACE_DIM = "#242d33"      # idle block, grid mạnh
GRID = "#263036"
TEXT = "#f2f5f4"
TEXT_2 = "#9fb0ac"
ACCENT = "#199e70"           # brand Xanh — accent DUY NHẤT của trang

# Categorical — THỨ TỰ CỐ ĐỊNH, màu theo entity vĩnh viễn (không repaint khi filter)
ACTIVITY_COLORS = {
    "on_trip": "#199e70",    # làm việc có khách — series 1 = accent
    "enroute": "#3987e5",    # đi đón
    "charge": "#c98500",     # đổi pin / sạc
    "rest": "#9085e9",       # nghỉ
    "relocate": "#d55181",   # dịch chuyển tìm khách / deadhead
    "demand_seek": "#d55181",
    "idle": SURFACE_DIM,     # KHÔNG phải series — vắng hoạt động (nhãn trực tiếp)
}
SERIES = ["#199e70", "#3987e5", "#c98500", "#9085e9", "#d55181"]

# Status — reserved, không tái dụng làm series
STATUS = {"good": "#199e70", "warning": "#c98500", "critical": "#e66767"}

# Sequential (mật độ cầu) — MỘT hue, mờ→sáng trên nền tối
SEQ_AQUA = ["#16302a", "#1b4a3c", "#1f6851", "#199e70", "#2ecf95"]

VN_KIND = {"on_trip": "chở khách", "enroute": "đi đón", "charge": "đổi pin/sạc",
           "rest": "nghỉ", "relocate": "dịch chuyển", "demand_seek": "dịch chuyển",
           "idle": "chờ đơn"}

FONT = "'Segoe UI', system-ui, -apple-system, sans-serif"
MONO = "'Cascadia Code', 'Consolas', monospace"


def register_template() -> None:
    """Template plotly dùng CHUNG cho mọi chart — grid recessive, nền trong suốt."""
    pio.templates["xanh_dark"] = go.layout.Template(layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=TEXT_2, size=12),
        title=dict(font=dict(color=TEXT, size=14)),
        colorway=SERIES,
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID,
                   tickfont=dict(family=MONO, size=11)),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID,
                   tickfont=dict(family=MONO, size=11)),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        margin=dict(l=8, r=8, t=36, b=8),
        hoverlabel=dict(bgcolor=SURFACE_ELEV, font=dict(family=FONT, color=TEXT)),
    ))
    pio.templates.default = "xanh_dark"


CSS = f"""
<style>
/* SIM-XANH P4 — mot he thong: type scale, mot accent, card phang khong bong den */
html, body, [data-testid="stAppViewContainer"] {{
  font-family: {FONT};
}}
h1, h2, h3 {{ letter-spacing: -0.02em; color: {TEXT}; }}
[data-testid="stMetric"] {{
  background: {SURFACE_ELEV}; border: 1px solid {GRID};
  border-radius: 8px; padding: 12px 16px;
}}
[data-testid="stMetricValue"] {{ font-family: {MONO}; font-size: 1.45rem; color: {TEXT}; }}
[data-testid="stMetricLabel"] {{ color: {TEXT_2}; font-size: 0.78rem;
  text-transform: uppercase; letter-spacing: 0.06em; }}
[data-testid="stMetricDelta"] {{ font-family: {MONO}; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 1px solid {GRID}; }}
.stTabs [data-baseweb="tab"] {{
  border-radius: 8px 8px 0 0; padding: 8px 18px; color: {TEXT_2};
}}
.stTabs [aria-selected="true"] {{
  color: {TEXT}; border-bottom: 2px solid {ACCENT};
}}
[data-testid="stSidebar"] {{ background: {SURFACE_ELEV}; border-right: 1px solid {GRID}; }}
div[data-testid="stDataFrame"] {{ border: 1px solid {GRID}; border-radius: 8px; }}
.xanh-header {{
  display: flex; align-items: baseline; gap: 14px;
  border-bottom: 1px solid {GRID}; padding: 4px 0 14px 0; margin-bottom: 6px;
}}
.xanh-header .brand {{
  color: {ACCENT}; font-weight: 700; font-size: 1.35rem; letter-spacing: -0.02em;
}}
.xanh-header .sub {{ color: {TEXT_2}; font-size: 0.85rem; }}
.xanh-badge {{
  display: inline-block; background: {SURFACE_DIM}; color: {TEXT_2};
  border-radius: 8px; padding: 2px 10px; font-size: 0.72rem;
  font-family: {MONO}; letter-spacing: 0.04em;
}}
</style>
"""
