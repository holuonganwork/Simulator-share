""""Xem truoc" don de chon loc cuoc (ride_preview_selection) - giai doan cho cuoc.

CANH BAO PROXY THO va MOT CHIEU (xem docs/data-matrix.md): mau hinh that su can
doi chieu ty le CHAP NHAN voi DAC DIEM cua don bi TU CHOI (quang duong, gia) -
nhung trips.parquet chi ghi cuoc DA HOAN THANH, khong co log cuoc bi tu choi kem
dac diem. Vi vay o day chi flag "ty le nhan cuoc thap bat thuong" tu
driver_statistic_daily.acceptance_rate (percentile THAP nhat, vd duoi p1) - day la
tin hieu MO HO nhat trong toan bo Tier A: ty le nhan thap co the do rat nhieu ly do
khac (dia ban xa, gio thap diem...), KHONG rieng gi hanh vi chon loc cuoc dai.

Khuyen nghi: dung ket qua nay CHI de agent dat cau hoi mo trong chat quan ly, KHONG
dua vao flag_case/apply_action mot minh no.
"""

from __future__ import annotations

import polars as pl

from lcx_core.data.gsm_loader import GsmDataStore
from lcx_core.tier_a.base import RuleFlag, RuleResult, percentile_threshold

PATTERN = "ride_preview_selection"
DEFAULT_PERCENTILE_LOW = 0.01
MIN_ACCEPTED_SAMPLE = 5  # bo qua driver moi/it du lieu, ty le se rat nhieu
CAVEAT = (
    "THO va MOT CHIEU: khong co log cuoc bi tu choi kem dac diem (quang duong/gia) "
    "de doi chieu that su voi hanh vi 'chon loc cuoc dai'. Chi la ty le nhan thap "
    "bat thuong, co the do nhieu nguyen nhan khac. Xem docs/data-matrix.md."
)


def detect(store: GsmDataStore, percentile_low: float = DEFAULT_PERCENTILE_LOW) -> RuleResult:
    df = store.table("driver_statistic_daily").filter(
        pl.col("accepted_count") >= MIN_ACCEPTED_SAMPLE
    )
    values = df["acceptance_rate"].drop_nulls().to_list()
    # nguong duoi: percentile_low cua phan phoi -> bat nhung driver co ty le THAP nhat
    threshold = percentile_threshold(values, percentile_low)

    flagged = df.filter(pl.col("acceptance_rate") < threshold)
    flags = [
        RuleFlag(
            pattern=PATTERN,
            driver_id=row["driver_id"],
            signal_value=row["acceptance_rate"],
            threshold=threshold,
            evidence={
                "local_date": row["local_date"],
                "accepted_count": row["accepted_count"],
            },
            caveat=CAVEAT,
        )
        for row in flagged.iter_rows(named=True)
    ]
    return RuleResult(
        pattern=PATTERN,
        method=f"percentile_low_p{int(percentile_low * 100)}",
        threshold=threshold,
        n_evaluated=df.height,
        flags=flags,
    )
