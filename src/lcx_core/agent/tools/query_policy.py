"""Tool query_policy - BUOC BAT BUOC truoc khi tra loi "vi sao"/"theo quy che nao".

Theo docs/strategy.md muc 06: "Moi cau tra loi cua agent phai bam chinh sach cong
ty, khong duoc tu suy dien quy dinh". Tool nay uu tien doc kho chinh sach THAT da
crawl trong GSM_SIMULATOR (FALLBACK_POLICY_DOCS, 18-20 doan tu
https://www.greensm.com/vn-vi/helps), tim kiem lexical don gian (dem tu khoa
trung, KHONG can Qdrant/embedding - dung muc "fallback lexical" ma chinh
policy_kb.py da mo ta). Neu package `gsm-sim` chua duoc cai (xem pyproject.toml),
dung QUOTED_FALLBACK - mot vai trich dan da XAC MINH THAT tu chinh policy_kb.py,
sao chep nguyen van kem so dong nguon, KHONG bia them.
"""

from __future__ import annotations

from dataclasses import dataclass

from lcx_core._text import normalize_vi

# Trich dan THAT, sao chep nguyen van tu GSM_SIMULATOR/src/gsm_core/advisor/policy_kb.py
# (dong 252, 192, 294) va schemas/l3/anomaly_alert_input.schema.json (dong 87), dung khi
# package `gsm-sim` khong duoc cai o may nay. KHONG duoc tu y sua chu.
QUOTED_FALLBACK: list[dict[str, str]] = [
    {
        "source_id": "helps_penalty_fraud_10",
        "title": "Quy che Xu phat vi pham va Che tai hanh vi gian lan cuoc",
        "text": (
            "Che tai xu phat: Hanh vi tu tao cuoc ao, huy cuoc khong ly do chinh dang "
            "hoac gian lan diem thuong se bi phat tru 100% thuong tuan, tam khoa tai "
            "khoan 7 ngay hoac cham dut hop tac vinh vien tuy muc do vi pham."
        ),
        "cited_from": "GSM_SIMULATOR src/gsm_core/advisor/policy_kb.py:252",
    },
    {
        "source_id": "helps_account_sharing_03",
        "title": "Toi co the chia se tai khoan chay chung voi nguoi khac duoc khong?",
        "text": (
            "Tuyet doi khong chia se tai khoan cho nguoi khac chay chung. Neu tai xe "
            "co hanh vi chia se tai khoan cho nguoi khac, tai khoan se bi tam khoa "
            "hoac cham dut hop tac vinh vien theo Quy tac ung xu."
        ),
        "cited_from": "GSM_SIMULATOR src/gsm_core/advisor/policy_kb.py:192",
    },
    {
        "source_id": "helps_cash_dispute_16",
        "title": "Quy trinh Bao cao va Boi hoan doi voi Cuoc xe tien mat khach khong tra tien",
        "text": (
            "Xu ly su co tien mat: Truong hop khach khong thanh toan cuoc tien mat, tai "
            "xe tuyet doi khong xo xat, ghi nhan thong tin chuyen di va gui bao cao su co "
            "qua app kem hinh anh/lich su cuoc trong 12 gio. Bo phan CSKH se xac minh va "
            "hoan tien vao vi tai xe trong 24-48 gio."
        ),
        "cited_from": "GSM_SIMULATOR src/gsm_core/advisor/policy_kb.py:294",
    },
    {
        "source_id": "schema_explain_deadline",
        "title": "Han giai trinh truc tuyen khi bi gan co bat thuong",
        "text": (
            "Han giai trinh truc tuyen (gio) - official 48h tu 15/12/2025. "
            "null = khong ap dung."
        ),
        "cited_from": "GSM_SIMULATOR schemas/l3/anomaly_alert_input.schema.json:87",
    },
]

EXPLAIN_DEADLINE_HOURS = 48
EXPLAIN_DEADLINE_EFFECTIVE_FROM = "2025-12-15"


@dataclass(frozen=True)
class PolicyExcerpt:
    source_id: str
    title: str
    text: str
    cited_from: str
    match_score: int


def _load_corpus() -> list[dict[str, str]]:
    try:
        from gsm_core.advisor.policy_kb import FALLBACK_POLICY_DOCS  # type: ignore

        return [
            {
                "source_id": d["source_id"],
                "title": d["title"],
                "text": d["text"],
                "cited_from": d.get("source_url") or "GSM_SIMULATOR FALLBACK_POLICY_DOCS",
            }
            for d in FALLBACK_POLICY_DOCS
        ]
    except ImportError:
        return QUOTED_FALLBACK


def query_policy(question: str, top_k: int = 2) -> list[PolicyExcerpt]:
    """Tim kiem lexical don gian: dem so tu khoa (da chuan hoa) trung giua cau hoi
    va noi dung tung doan chinh sach. Khong can embedding/Qdrant - dung dung tinh
    than "fallback lexical" da co san trong policy_kb.py cua GSM_SIMULATOR.
    """
    corpus = _load_corpus()
    q_tokens = set(normalize_vi(question).split())

    scored = []
    for doc in corpus:
        doc_tokens = set(normalize_vi(doc["title"] + " " + doc["text"]).split())
        score = len(q_tokens & doc_tokens)
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda sd: sd[0], reverse=True)
    return [
        PolicyExcerpt(
            source_id=doc["source_id"],
            title=doc["title"],
            text=doc["text"],
            cited_from=doc["cited_from"],
            match_score=score,
        )
        for score, doc in scored[:top_k]
    ]
