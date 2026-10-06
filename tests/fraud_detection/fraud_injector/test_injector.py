"""Test fraud injector (Pha A) - dung `store` that tu conftest.py.

Muc tieu: (1) inject_all() khong sua doi du lieu that tren dia (GsmDataStore
goc khong bi dung cham), (2) so nhan doc lap dung bang so mong doi, (3) voi
muc do "heavy", tung detector Tier A phai bat duoc PHAN LON don vi da tiem -
day la phep kiem tra hoi quy quan trong nhat: neu injector ngung tao ra tin
hieu phat hien duoc (vd do sua detector hoac sua tham so tiem sai), test nay
se do vo truoc khi anh huong toi bao cao that.
"""

from __future__ import annotations

import polars as pl
import pytest

from lcx_core.fraud_injector import PATTERNS, SEVERITIES, flag_matches_label, inject_all
from lcx_core.tier_a import REGISTRY

N_PER_SEVERITY = 8


@pytest.fixture(scope="module")
def injected(store):
    return inject_all(store, seed=7, n_per_severity=N_PER_SEVERITY)


# fake_ride_for_kpi tu hieu chuan (mean/stdev) tu LICH SU THAT cua tung tai xe,
# can >=10 tuan ISO/tai xe moi coi la "du du lieu" (xem
# tier_a/fake_ride_for_kpi.py va injector.py::_inject_fake_ride_for_kpi). Ca
# realdata-v1 lan v2 hien chi sinh 30 ngay lien tuc (~5 tuan ISO) => KHONG co
# tai xe nao du dieu kien, injector tra ve 0 nhan cho mau hinh nay voi BAT KY
# bo du lieu 30-ngay nao - khong phai loi rieng cua v2. Se tu het khi sinh lai
# du lieu voi >=10 tuan lien tuc (vd --days 90).
_PATTERNS_NEEDING_LONG_HISTORY = {"fake_ride_for_kpi"}


def test_ground_truth_has_expected_shape(injected):
    _injected_store, ground_truth = injected
    counts = dict(
        zip(*ground_truth.group_by("pattern").len().select(["pattern", "len"]).to_dict(as_series=False).values())
    )
    for pattern in PATTERNS:
        if pattern in _PATTERNS_NEEDING_LONG_HISTORY and pattern not in counts:
            continue
        expected = len(SEVERITIES) * N_PER_SEVERITY
        assert counts.get(pattern) == expected, (
            f"{pattern}: ky vong {expected} nhan, co {counts.get(pattern, 0)}"
        )
    assert set(ground_truth["severity"].unique().to_list()) == set(SEVERITIES)


def test_original_store_untouched(store, injected):
    injected_store, _ground_truth = injected
    assert injected_store is not store
    for name in ("trips", "public_driver_hex_tracking"):
        assert name not in store._cache or "INJECTED_FRAUD" not in (
            store.table(name)["source"].unique().to_list()
        )


@pytest.mark.parametrize("pattern", PATTERNS)
def test_heavy_severity_is_mostly_detected(injected, pattern):
    """Voi tham so 'heavy' (nghiem trong nhat), detector phai bat duoc phan
    lon don vi da tiem - nguong 60% de chiu duoc bien thien ngau nhien khi
    N_PER_SEVERITY nho trong test, thap hon nhieu so voi ket qua thuc te
    (xem var/injector_eval/ - hau het >=90% o muc heavy khi n=40)."""
    injected_store, ground_truth = injected
    result = REGISTRY[pattern].detect(injected_store)

    gt_rows = ground_truth.filter(
        (pl.col("pattern") == pattern) & (pl.col("severity") == "heavy")
    ).to_dicts()
    if not gt_rows and pattern in _PATTERNS_NEEDING_LONG_HISTORY:
        pytest.skip(
            f"{pattern}: injector khong tao duoc nhan tren bo du lieu hien tai "
            "(can >=10 tuan lich su/tai xe de tu hieu chuan; realdata hien chi "
            "co ~5 tuan) - xem test_ground_truth_has_expected_shape."
        )
    assert gt_rows, f"khong co nhan gt cho {pattern}/heavy"

    tp = sum(
        1
        for r in gt_rows
        if any(
            flag_matches_label(f, r["driver_id"], r["match_field"], r["match_value"])
            for f in result.flags
        )
    )
    recall = tp / len(gt_rows)
    assert recall >= 0.6, f"{pattern}: recall={recall:.0%} qua thap o muc heavy ({tp}/{len(gt_rows)})"
