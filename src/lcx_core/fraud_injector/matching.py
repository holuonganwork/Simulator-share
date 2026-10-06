"""Doi chieu RuleFlag (dau ra detector) voi InjectedLabel (nhan doc lap).

Tach rieng khoi injector.py de scripts/run_injector_eval.py va test deu dung
CHUNG mot logic doi chieu - tranh hai noi dinh nghia lech nhau.
"""

from __future__ import annotations

from lcx_core.tier_a.base import RuleFlag

# Voi moi match_field, danh sach cac ten key trong RuleFlag.evidence co the
# chua gia tri trung khop - mot so mau hinh co nhieu key evidence hop le (vd
# split_long_trip khop qua trip_id_next, location_dropout khop qua dropout_end).
_EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {
    "trip_id": ("trip_id", "trip_id_next"),
    "entered_current_hex_at": ("entered_current_hex_at", "dropout_end"),
    "local_date": ("local_date",),
    "week_key": ("week_key",),
    "toggle_off_event_id": ("toggle_off_event_id",),
    "session_id": ("session_id",),
}


def flag_matches_label(flag: RuleFlag, driver_id: str, match_field: str, match_value: str) -> bool:
    """True neu RuleFlag `flag` chinh la (hoac bat duoc) don vi da tiem duoc mo ta
    boi (driver_id, match_field, match_value)."""
    if flag.driver_id != driver_id:
        return False
    for key in _EVIDENCE_KEYS.get(match_field, (match_field,)):
        if str(flag.evidence.get(key)) == match_value:
            return True
    return False
