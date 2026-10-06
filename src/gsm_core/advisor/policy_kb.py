"""F0 Policy Knowledge Base & Retrieval-Augmented Generation (RAG) Tool.

Provides vector search over crawled Green SM driver policies and regulations via
Qdrant and Jina AI embeddings, with a deterministic lexical fallback.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from gsm_core.advisor._text import normalize_vi as _norm

_LOG = logging.getLogger(__name__)

VALID_TRACKS = frozenset({"core_owned", "platform", "rto"})
EXCERPT_CHARS = 450
DEFAULT_COLLECTION = "gsm_policy_kb"

# Built-in fallback policy excerpts when Qdrant is unavailable or unconfigured
# Tên MỤC quá chung để dùng làm nhãn nguồn cho tài xế. Một hằng số DÙNG CHUNG cho cả hai
# đường tra cứu (lexical `_init_index` và vector Qdrant) — trước đây hai đường xếp thứ tự ưu
# tiên NGƯỢC NHAU (`title or heading` vs `heading or title`), nên cùng một chunk cho ra hai
# nhãn khác nhau tuỳ đường nào trả lời. Chép luật ra hai chỗ là cách chúng lệch nhau.
_HEADING_CHUNG_CHUNG = frozenset({"mở đầu", "giới thiệu", "kết luận", "tổng quan"})


#: Lùi tối đa bao nhiêu ký tự để tìm đầu câu. Quá ngắn thì hay trượt; quá dài thì cửa sổ trôi
#: khỏi chỗ khớp. 200 ≈ một câu chính sách dài.
_LUI_TOI_DA = 200


def _doan_lien_quan(text: str, cau_hoi: str, n: int = EXCERPT_CHARS) -> str:
    """Cửa sổ `n` ký tự quanh chỗ khớp câu hỏi nhất — thay vì `text[:n]` (`F-302-05`).

    ## Vì sao đây không phải chuyện thẩm mỹ

    Corpus đường sản phẩm là **nguyên trang scrape** — đo được **98,9%** câu trùng nguyên văn
    trang sống, bản dài nhất **4.133 ký tự**. `text[:450]` vì thế trả về *chrome + mở bài*
    (*"Tin tức Tài xế Green SM Bike 15/04/2026 18:39 …"*), **bất kể tài xế hỏi gì**, và
    `workflows.py` lấy đúng chuỗi ấy làm nội dung câu trả lời.

    Hệ quả: bài *"Bộ quy tắc ứng xử"* có **79 câu**; mọi câu hỏi về một điều khoản cụ thể đều
    nhận về đoạn mở đầu. Tầng tìm kiếm chọn **đúng tài liệu** rồi tầng cắt vứt mất câu trả lời —
    hỏng ở khâu cuối, nên mọi phép đo retrieval vẫn đẹp.

    ## Cách làm, và chỗ dễ sai

    Trượt cửa sổ theo **vị trí token khớp**. ⚠ Chuẩn hoá **từng token một**, KHÔNG chuẩn hoá cả
    chuỗi rồi lấy `.start()`: `normalize_vi` đổi độ dài, nên offset tính trên bản đã chuẩn hoá
    sẽ trỏ lệch khi cắt bản gốc — cắt giữa chữ, và không test nào thấy vì kết quả vẫn là một
    chuỗi hợp lệ.

    Không khớp token nào thì về `text[:n]` như cũ: không có tín hiệu thì đầu trang là phỏng đoán
    hợp lý nhất, không phải một chỗ ngẫu nhiên.
    """
    if len(text) <= n:
        return text
    khoa = {t for t in re.split(r"\W+", _norm(cau_hoi)) if len(t) >= 2}
    if not khoa:
        return text[:n]

    vi_tri = [m.start() for m in re.finditer(r"[^\W\d_]+", text) if _norm(m.group()) in khoa]
    if not vi_tri:
        return text[:n]

    tam = max(vi_tri, key=lambda h: sum(1 for x in vi_tri if h <= x < h + n))
    dau = max(0, tam - n // 4)
    if dau:
        # 🔴 Lùi về đầu CÂU, không chỉ đầu từ. Chính sách đầy vế điều kiện đứng TRƯỚC
        # (*"Trường hợp tài xế đã đăng ký khung giờ, mức bù KHÔNG áp dụng khi…"*); cắt giữa câu
        # có thể bỏ mất vế phủ định và làm câu trả lời **đảo nghĩa** — hỏng đúng kiểu §5 phạt
        # nặng nhất, vì tài xế TIN nó. Đây là rủi ro do chính cửa sổ này tạo ra (`F-312-06`).
        cau = max(text.rfind(c, max(0, dau - _LUI_TOI_DA), dau) for c in (". ", "! ", "? ", "\n"))
        if cau != -1:
            dau = cau + 1
        else:  # không thấy ranh giới câu thì ít nhất đừng cắt giữa chữ
            khoang = text.rfind(" ", max(0, dau - 40), dau)
            dau = khoang + 1 if khoang != -1 else dau
    doan = text[dau:dau + n].strip()

    # 🔴 Neo cả ĐUÔI, không chỉ đầu (`F-312-06`). Đo trên 112 cửa sổ thật: **49,1%** kết thúc
    # GIỮA MỘT CHỮ — tài xế đọc *"Thời gian mở dịch vụ: + Hà Nội & T"* rồi mất hẳn vế sau — và
    # **25,9%** vứt mất một vế chứa từ phủ định. Sau khi neo: cắt giữa chữ **0%**, và **97,3%**
    # kết thúc ở ranh giới câu thật (vế phủ định còn nguyên); 2,7% còn lại vẫn cắt nhưng mang
    # dấu `…` để tài xế BIẾT là còn nữa. Giá: đoạn ngắn đi 13% (449 → 391 ký tự).
    #
    # ⚠ Khuyết tật cắt-giữa-chữ **có từ trước** `text[:n]`, không phải do cửa sổ sinh ra. Cửa sổ
    # chỉ làm nó lộ ra, vì tôi neo đầu mà quên đuôi.
    if dau + n < len(text) and not doan.endswith((".", "!", "?", ":")):
        het_cau = max(doan.rfind(c) for c in (". ", "! ", "? ", ": "))
        if het_cau >= len(doan) - _LUI_TOI_DA:
            doan = doan[:het_cau + 1]
        else:  # đoạn không có ranh giới câu nào gần cuối (danh sách bị làm phẳng lúc scrape)
            khoang = doan.rfind(" ")
            doan = (doan[:khoang] + " …") if khoang != -1 else doan

    return ("… " + doan) if dau else doan


def _bang_ten_theo_slug(records) -> dict[str, str]:
    """Ánh xạ slug URL → tên tài liệu thật, **suy từ corpus ĐANG NẠP**, không liệt kê tay.

    ⚠ Phải là `records` (corpus đang dùng), **không** phải `FALLBACK_POLICY_DOCS`. Bản đầu tôi
    viết đọc thẳng hằng số dự phòng — và đo ra nó là nguồn SAI: đường sản phẩm nạp
    `research/policy/t004-*.json` (7 bản ghi, URL thật, đủ `title`), còn `FALLBACK_POLICY_DOCS`
    chỉ là mặc định lúc thiếu file. Bảng dựng từ nguồn không được dùng thì tra không bao giờ
    trúng, mà mọi test vẫn xanh vì test cũng đọc đúng cái hằng số ấy.
    """
    bang: dict[str, str] = {}
    for doc in records:
        slug = _slug_tu_url(str(doc.get("source_url") or ""))
        tieu_de = str(doc.get("title") or "").strip()
        if slug and tieu_de:
            bang.setdefault(slug, tieu_de)
    return bang


#: Đoạn cuối URL không định danh tài liệu nào — trang mục lục, không phải một bài.
_SLUG_KHONG_PHAI_BAI = frozenset({"", "helps", "news", "vn-vi", "vi", "vn"})


#: Đuôi tên site mà CMS gắn vào mọi `<title>`. Đo được: **7/7** bản ghi corpus thật đều dính.
#: ⚠ Neo `$` là bắt buộc — một tiêu đề thật chứa "Green SM" ở GIỮA
#: (*"…Vững Vàng Thu Nhập Cùng Green SM | Green SM"*), cắt không neo sẽ xén mất nửa tên bài.
_DUOI_TEN_SITE = re.compile(r"\s*[|–-]\s*Green\s*SM\s*$", re.IGNORECASE)


def _bo_duoi_site(tieu_de: str) -> str:
    """Bỏ đuôi site khỏi tên tài liệu — nó là chrome của trang, không phải tên bài.

    Đuôi này **đã** lọt vào trích dẫn hôm nay qua đường corpus cục bộ (`r["title"]` dùng thẳng),
    nên cắt ở chỗ cả hai đường đi qua — lúc nạp `records` — chứ không vá riêng đường Qdrant.
    """
    moi = _DUOI_TEN_SITE.sub("", tieu_de).strip()
    return moi or tieu_de


def _slug_tu_url(source_url: str) -> str:
    """Đoạn cuối URL, đã bỏ query/fragment. Rỗng nếu đó là trang mục lục chứ không phải một bài."""
    slug = source_url.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return "" if slug.casefold() in _SLUG_KHONG_PHAI_BAI else slug


def _ten_tai_lieu(source_url: str, bang: dict[str, str] | None = None) -> str:
    """Tên tài liệu suy từ URL — **không cần index lại** (`F-303-02`).

    `UPDATE-303` đổi nhãn nguồn từ `[Mở đầu]` (sai: tên MỤC, không phải tên bài) sang
    `"Chính sách GSM"` (đúng nhưng **mơ hồ**), và ghi thẳng trong phần tự soi rằng *"đúng hơn là
    lấy tên tài liệu — nhưng payload Qdrant không có khoá `title`"*.

    Payload **có** `source_url`, và URL mang slug của bài (`chinh-sach-dam-bao-thu-nhap-cho-...`).
    Tên tài liệu đã nằm sẵn trong dữ liệu — chỉ chưa ai đọc nó.

    Slug lạ (bài mới chưa có trong corpus) thì trả **slug đã bỏ gạch nối**: không dấu, nhưng vẫn
    nói được tài xế đang đọc bài nào — cụ thể hơn hẳn `"Chính sách GSM"`. Đây là đánh đổi có ý
    thức; muốn có dấu thì phải sửa đường ingest, việc đó không làm được từ đây.
    """
    slug = _slug_tu_url(source_url)
    if not slug:
        return ""
    ten = (bang or {}).get(slug)
    if ten:
        return ten
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


FALLBACK_POLICY_DOCS = [
    {
        "source_id": "helps_multi_service_01",
        "title": "Dành cho tài xế Bike > Hồ sơ, tài khoản và phương tiện > Tôi có thể chạy đồng thời dịch vụ Green SM Taxi và Green SM Bike không?",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto"],
        "text": "Câu hỏi: Tôi có thể chạy đồng thời dịch vụ Green SM Taxi và Green SM Bike không? Giải đáp: Không được phép. Trong cùng một thời điểm, tài xế chỉ có thể hoạt động 01 loại dịch vụ. Nếu đang chạy Green SM Bike muốn chuyển sang Taxi thì cần làm thủ tục đổi loại hình hợp tác tại văn phòng.",
    },
    {
        "source_id": "helps_multi_app_02",
        "title": "Dành cho tài xế Bike > Vấn đề về chuyến đi > Tôi có được chạy Green SM Bike song song với các ứng dụng khác không?",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto"],
        "text": "Câu hỏi: Tôi có được chạy Green SM Bike song song với các ứng dụng khác không? Giải đáp: Tài xế không được sử dụng xe máy điện của Green SM Bike để chạy song song với các ứng dụng khác nhằm đảm bảo chất lượng dịch vụ và an toàn vận hành.",
    },
    {
        "source_id": "helps_account_sharing_03",
        "title": "Dành cho tài xế Bike > Hồ sơ, tài khoản và phương tiện > Tôi có thể chia sẻ tài khoản chạy chung với người khác được không?",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Câu hỏi: Tôi có thể chia sẻ tài khoản chạy chung với người khác được không? Giải đáp: Tuyệt đối không chia sẻ tài khoản cho người khác chạy chung. Nếu tài xế có hành vi chia sẻ tài khoản cho người khác, tài khoản sẽ bị tạm khóa hoặc chấm dứt hợp tác vĩnh viễn theo Quy tắc ứng xử.",
    },
    {
        "source_id": "pol_bonus_weekly_v1",
        "title": "Chính sách Thưởng tuần và Năng suất (Áp dụng đến 31/07/2026)",
        "source_url": "", "nguon_mock": True,
        "f0_tracks": ["platform", "rto", "core_owned"],
        "effective_from": "2026-01-01",
        "effective_until": "2026-07-31",
        "text": "Chính sách thưởng tuần giai đoạn áp dụng đến hết 31/07/2026: Điều kiện nhận thưởng tuần gồm tối thiểu 50 chuyến hoàn thành/tuần, tỷ lệ nhận chuyến ADR >= 80%, tỷ lệ hoàn thành CDR >= 80%. Mức thưởng 500.000đ.",
    },
    {
        "source_id": "helps_bonus_points_04",
        "title": "Chính sách Thưởng tuần và Điểm sao đánh giá chất lượng (Áp dụng từ 01/08/2026)",
        "source_url": "https://www.greensm.com/vn-vi/news/cap-nhat-chinh-sach-thu-nhap-van-doanh-ha-noi-ho-chi-minh-dong-nai",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "effective_from": "2026-08-01",
        "effective_until": None,
        "text": "Chính sách thưởng tuần mới áp dụng từ 01/08/2026: Để đủ điều kiện xét thưởng tuần, Bác Tài cần đạt điểm sao trung bình tuần tối thiểu 4.85, tối thiểu 60 chuyến hoàn thành/tuần, tỷ lệ nhận chuyến (ADR) >= 85%, tỷ lệ hoàn thành (CDR) >= 85% và không vi phạm quy chế.",
    },
    {
        "source_id": "helps_registration_docs_05",
        "title": "Dành cho tài xế Bike > Cách thức ứng tuyển > Hồ sơ thủ tục đăng ký chạy cho tài xế mới",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto"],
        "text": "Hồ sơ đăng ký tài xế Green SM Bike bao gồm: CCCD gắn chip (hoặc định danh mức 2 trên VNeID), Giấy phép lái xe hạng A1/A2 bản gốc, Lý lịch tư pháp số 2 hoặc Giấy xác nhận không tiền án tiền sự, và tài khoản ngân hàng chính chủ.",
    },
    {
        "source_id": "helps_rto_deposit_06",
        "title": "Chính sách Thuê mua phương tiện (RTO) > Ký quỹ và Đặt cọc xe máy điện",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["rto"],
        "text": "Chính sách Thuê mua (RTO): Tài xế đóng tiền cọc xe ban đầu theo quy định từng dòng xe Feliz S/Evo200 và thanh toán tiền thuê mua trừ dần vào doanh thu ngày. Sau thời hạn cam kết (12-24 tháng), tài xế được sang tên sở hữu xe hoàn toàn.",
    },
    {
        "source_id": "helps_battery_swap_07",
        "title": "Quy trình Đổi pin tại trạm dành cho xe máy điện Xanh SM",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto"],
        "text": "Quy trình đổi pin: Khi mức pin xe dưới 20%, tài xế mở app tìm trạm đổi pin gần nhất còn pin sẵn sàng (>90%). Tại trạm, đăng nhập tài khoản tài xế và quét mã QR trên tủ pin, trả pin cũ vào ngăn mở và nhận pin mới đã sạc đầy.",
    },
    {
        "source_id": "helps_uniform_08",
        "title": "Quy định về Trang phục và Đồng phục tiêu chuẩn của tài xế Green SM",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Quy định đồng phục: Bắt buộc mặc áo khoác/áo thun đồng phục Green SM, đội mũ bảo hiểm Green SM và trang bị đủ 01 mũ bảo hiểm đạt chuẩn cho hành khách. Không mang dép lê hoặc quần lửng khi đang trực tuyến đón khách.",
    },
    {
        "source_id": "helps_income_guarantee_09",
        "title": "Chính sách Bảo đảm Thu nhập và Lương cơ bản dành cho tài xế",
        "source_url": "", "nguon_mock": True,
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Chính sách bảo đảm thu nhập: Tài xế đăng ký khung giờ tiêu chuẩn và đạt tỷ lệ nhận chuyến ADR >= 90%, tỷ lệ hoàn thành CDR >= 90% sẽ được công ty cam kết bù chênh lệch nếu tổng doanh thu ngày thấp hơn mức thu nhập bảo đảm theo quy chế.",
    },
    {
        "source_id": "helps_penalty_fraud_10",
        "title": "Quy chế Xử phạt vi phạm và Chế tài hành vi gian lận cuốc",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Chế tài xử phạt: Hành vi tự tạo cuốc ảo, hủy cuốc không lý do chính đáng hoặc gian lận điểm thưởng sẽ bị phạt trừ 100% thưởng tuần, tạm khóa tài khoản 7 ngày hoặc chấm dứt hợp tác vĩnh viễn tùy mức độ vi phạm.",
    },
    {
        "source_id": "helps_payout_wallet_11",
        "title": "Quy trình Rút tiền thu nhập từ Ví tài xế về Tài khoản Ngân hàng",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Quy trình rút tiền: Tiền thu nhập khả dụng sau khi trừ phí nền tảng và thuế sẽ được quyết toán vào ví tài xế lúc 24:00 hàng ngày. Tài xế có thể tạo lệnh rút tiền về tài khoản ngân hàng chính chủ, tiền về trong 1-2 giờ làm việc.",
    },
    {
        "source_id": "helps_shift_limits_12",
        "title": "Quy định Khung giờ hoạt động tối đa và Thời gian nghỉ ngơi của tài xế",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Quy định thời gian lái xe: Nhằm đảm bảo an toàn, tài xế không được hoạt động trực tuyến quá 12 giờ trong một ngày và không lái xe liên tục quá 4 giờ mà không nghỉ tối thiểu 15 phút.",
    },
    {
        "source_id": "helps_lost_items_13",
        "title": "Quy trình Xử lý khi Hành khách để quên đồ trên xe",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Quy trình xử lý đồ thất lạc: Khi phát hiện khách để quên tài sản trên xe, tài xế có trách nhiệm bảo quản nguyên trạng và báo cáo ngay qua mục Hỗ trợ trên app trong vòng 24 giờ. Tài xế liên hệ khách để hoàn trả hoặc bàn giao tài sản về văn phòng Green SM gần nhất.",
    },
    {
        "source_id": "helps_tax_service_fee_14",
        "title": "Chính sách Khấu trừ Thuế thu nhập cá nhân và Phí dịch vụ trên mỗi cuốc xe",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Khấu trừ thuế và phí: Phí dịch vụ nền tảng được trích từ 20% đến 25% trên giá trị cuốc xe theo hợp đồng đối tác. Thuế TNCN (1.5%) và thuế GTGT (3%) được công ty khấu trừ tự động tại nguồn và nộp vào ngân sách nhà nước theo quy định pháp luật.",
    },
    {
        "source_id": "helps_cancellation_comp_15",
        "title": "Chính sách Bồi hoàn khi Khách hàng tự ý hủy chuyến sau khi tài xế đã đến điểm đón",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Bồi hoàn huỷ chuyến: Nếu khách hàng tự ý huỷ chuyến sau 5 phút kể từ khi tài xế đã đến đúng điểm đón quy định, tài xế được nhận phí hỗ trợ huỷ chuyến (10.000đ - 15.000đ) cộng trực tiếp vào ví và tỷ lệ nhận/huỷ chuyến không bị ảnh hưởng.",
    },
    {
        "source_id": "helps_cash_dispute_16",
        "title": "Quy trình Báo cáo và Bồi hoàn đối với Cuốc xe tiền mặt khách không trả tiền",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Xử lý sự cố tiền mặt: Trường hợp khách không thanh toán cước tiền mặt, tài xế tuyệt đối không xô xát, ghi nhận thông tin chuyến đi và gửi báo cáo sự cố qua app kèm hình ảnh/lịch sử cuốc trong 12 giờ. Bộ phận CSKH sẽ xác minh và hoàn tiền vào ví tài xế trong 24-48 giờ.",
    },
    {
        "source_id": "helps_battery_emergency_17",
        "title": "Quy trình Ứng phó sự cố Hết pin hoặc Sự cố kỹ thuật đột ngột khi đang chở khách",
        "source_url": "https://www.greensm.com/vn-vi/helps",
        "f0_tracks": ["platform", "rto", "core_owned"],
        "text": "Sự cố pin khẩn cấp: Đang chở khách mà xe gặp sự cố cạn pin hoặc lỗi kỹ thuật, tài xế tấp xe vào lề an toàn, xin lỗi khách và liên hệ tổng đài cứu hộ Xanh SM 19002088 để điều xe hỗ trợ chuyển tiếp khách miễn phí, đồng thời đội cứu hộ pin sẽ đến hỗ trợ trong 30 phút.",
    },
]


def _get_jina_embedding(text: str, api_key: str) -> list[float] | None:
    """Fetch text embedding from Jina AI Embeddings v3 (1024-dim)."""
    if not text or not api_key:
        return None
    try:
        import httpx
        url = "https://api.jina.ai/v1/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        data = {
            "model": "jina-embeddings-v3",
            "task": "retrieval.query",
            "dimensions": 1024,
            "late_chunking": False,
            "input": [text[:1000]],
        }
        resp = httpx.post(url, headers=headers, json=data, timeout=10.0)
        if resp.status_code == 200:
            res_json = resp.json()
            return res_json["data"][0]["embedding"]
    except Exception as exc:
        _LOG.warning("Jina embedding call failed: %s", exc)
    return None


def _init_qdrant_client() -> tuple[Any | None, str | None]:
    """Initialize Qdrant client and Jina API key from environment."""
    try:
        from gsm_core.advisor.llm_client import load_env
        load_env()
    except Exception:
        pass

    qdrant_url = os.environ.get("qdrant_Cluster_Endpoint") or os.environ.get("QDRANT_URL")
    qdrant_key = os.environ.get("qdrant_api") or os.environ.get("QDRANT_API_KEY")
    jina_key = os.environ.get("Jina_api_key") or os.environ.get("JINA_API_KEY")

    if not qdrant_url or not qdrant_key:
        return None, jina_key

    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=qdrant_url, api_key=qdrant_key, timeout=10.0)
        return client, jina_key
    except Exception as exc:
        _LOG.warning("Failed to initialize QdrantClient: %s", exc)
        return None, jina_key


def _normalize_f0_tracks(raw_tracks: Any) -> list[str]:
    if not raw_tracks:
        return ["platform", "rto", "core_owned"]
    if isinstance(raw_tracks, str):
        raw_tracks = [raw_tracks]
    mapped = set()
    for t in raw_tracks:
        t_low = str(t).strip().lower()
        if t_low in {"platform", "core_owned", "rto"}:
            mapped.add(t_low)
        elif t_low in {"bike", "platform_bike", "xe_may", "2w"}:
            mapped.update(["platform", "rto"])
        elif t_low in {"taxi", "car", "4w", "oto"}:
            mapped.update(["core_owned", "platform"])
        else:
            mapped.add("platform")
    return sorted(mapped) if mapped else ["platform", "rto", "core_owned"]


import math
from collections import Counter


class OkapiBM25:
    """Okapi BM25 indexer and ranker for policy documents.

    Implements Robertson-Spärck Jones IDF with document length normalization
    (k1=1.5, b=0.75) and title term frequency boosting.
    """

    def __init__(self, corpus: list[dict], k1: float = 1.5, b: float = 0.75, title_boost: float = 2.0):
        self.k1 = k1
        self.b = b
        self.title_boost = title_boost
        self.corpus = corpus
        self.num_docs = len(corpus)
        self.doc_len: list[int] = []
        self.doc_freqs: list[Counter] = []
        self.title_tokens: list[set[str]] = []
        self.nd: dict[str, int] = {}
        self._init_index()

    @staticmethod
    def tokenize(text: str) -> list[str]:
        return [w for w in re.split(r"\W+", _norm(text)) if len(w) >= 2]

    def _init_index(self) -> None:
        total_len = 0
        for doc in self.corpus:
            # Cùng luật với đường Qdrant bên dưới: heading chung chung không làm nhãn.
            _h = str(doc.get("heading") or "").strip()
            title = str(doc.get("title")
                        or (_h if _h.casefold() not in _HEADING_CHUNG_CHUNG else "")
                        or "")
            text = str(doc.get("text") or doc.get("main_text") or doc.get("content") or "")
            toks = self.tokenize(text)
            t_toks = set(self.tokenize(title))
            self.title_tokens.append(t_toks)
            d_len = len(toks)
            self.doc_len.append(d_len)
            total_len += d_len
            freq = Counter(toks)
            self.doc_freqs.append(freq)
            for term in freq.keys():
                self.nd[term] = self.nd.get(term, 0) + 1
        self.avgdl = (total_len / self.num_docs) if self.num_docs > 0 else 1.0

    def score(self, query_terms: list[str]) -> list[float]:
        scores = [0.0] * self.num_docs
        for term in query_terms:
            if term not in self.nd:
                continue
            n_t = self.nd[term]
            idf = math.log(1.0 + (self.num_docs - n_t + 0.5) / (n_t + 0.5))
            for idx in range(self.num_docs):
                freq = self.doc_freqs[idx].get(term, 0)
                if freq == 0:
                    continue
                t_boost = self.title_boost if term in self.title_tokens[idx] else 1.0
                effective_freq = freq * t_boost
                numerator = effective_freq * (self.k1 + 1.0)
                denominator = effective_freq + self.k1 * (1.0 - self.b + self.b * (self.doc_len[idx] / self.avgdl))
                scores[idx] += idf * (numerator / denominator)
        return scores


def reciprocal_rank_fusion(dense_ranks: dict[str, int], sparse_ranks: dict[str, int], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion (RRF) for merging dense and sparse rankings.

    Formula: RRF(d) = sum(1 / (k + rank_m(d))) for m in {dense, sparse}.
    Standard constant k=60 balances high and mid-ranked matches.
    """
    all_keys = set(dense_ranks.keys()) | set(sparse_ranks.keys())
    rrf_scores: dict[str, float] = {}
    for doc_id in all_keys:
        score = 0.0
        if doc_id in dense_ranks:
            score += 1.0 / (k + dense_ranks[doc_id])
        if doc_id in sparse_ranks:
            score += 1.0 / (k + sparse_ranks[doc_id])
        rrf_scores[doc_id] = score
    return rrf_scores


class PolicyKB:
    """Multi-source Policy Knowledge Base with Hybrid Vector + BM25 RRF retrieval."""

    def __init__(self, corpus_path: str | Path | None = None):
        self.records: list[dict] = list(FALLBACK_POLICY_DOCS)
        self.corpus_id = "t004_qdrant_v1"
        self.snapshot_date = "2026-08-18"

        if corpus_path and Path(corpus_path).exists():
            try:
                data = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
                extra_records = data.get("records", [])
                if extra_records:
                    self.records = list(extra_records)
                self.corpus_id = data.get("corpus_id", self.corpus_id)
                self.snapshot_date = data.get("snapshot_date", self.snapshot_date)
            except Exception as exc:
                _LOG.warning("Could not load local corpus %s: %s", corpus_path, exc)

        for _r in self.records:
            if _r.get("title"):
                _r["title"] = _bo_duoi_site(str(_r["title"]))
        self._ten_theo_slug = _bang_ten_theo_slug(self.records)
        self._qdrant_client, self._jina_key = _init_qdrant_client()

    @property
    def n_records(self) -> int:
        return len(self.records)

    def retrieve(self, track: str | None, query_text: str, top_k: int = 3, as_of_date: str | None = None) -> list[dict] | str:
        """Retrieve top matching policy chunks using Hybrid Vector + BM25 RRF.

        If track is specified but invalid or None, returns 'need_track'.
        Merges Qdrant dense vector search with Okapi BM25 sparse search via RRF (k=60).
        Falls back to pure BM25 if Qdrant is unavailable.
        Filters by effective date range if as_of_date is provided.
        """
        norm_track = str(track or "").strip().lower()
        if norm_track in {"bike", "platform_bike", "doi_tac"}:
            norm_track = "platform"
        elif norm_track in {"taxi", "car", "core", "tu_so_huu"}:
            norm_track = "core_owned"

        if not norm_track or norm_track not in VALID_TRACKS:
            return "need_track"

        q_clean = str(query_text or "").strip()
        if not q_clean:
            q_clean = "chinh sach thu nhap thuong"

        q_tokens = OkapiBM25.tokenize(q_clean)

        # 1. Try Hybrid Search (Qdrant Vector + BM25 + RRF)
        if self._qdrant_client and self._jina_key and not os.environ.get("GSM_LOCAL_KB_ONLY"):
            try:
                query_vec = _get_jina_embedding(q_clean, self._jina_key)
                if query_vec:
                    res = self._qdrant_client.query_points(
                        collection_name=DEFAULT_COLLECTION,
                        query=query_vec,
                        limit=top_k * 5,
                    )
                    points = getattr(res, "points", []) or []
                    
                    candidate_docs: list[dict] = []
                    dense_ranks: dict[str, int] = {}
                    doc_by_id: dict[str, dict] = {}

                    # Collect and filter dense points
                    for point in points:
                        payload = point.payload or {}
                        raw_tracks = payload.get("f0_tracks") or payload.get("tracks")
                        p_tracks = _normalize_f0_tracks(raw_tracks)
                        if norm_track and norm_track not in p_tracks:
                            continue

                        text = str(payload.get("text") or payload.get("main_text") or payload.get("content") or "").strip()
                        # 🔴 `heading` CHUNG CHUNG thì coi như VẮNG, không dùng làm nhãn nguồn.
                        #
                        # Đo 2026-08-31: 39/132 chunk có `heading = "Mở đầu"`, và payload Qdrant
                        # KHÔNG có khoá `title` (kiểm: `any("title" in p)` -> False). Nên chuỗi
                        # `or` này rơi về `heading`, và tài xế đọc được nguyên văn:
                        #
                        #     "Theo [Mở đầu](https://…): …"
                        #
                        # "Mở đầu" là tên MỤC trong bài, không phải tên tài liệu — nó không nói
                        # cho tài xế biết họ đang được trích từ đâu. Rơi tiếp về "Chính sách GSM"
                        # thì kém cụ thể hơn nhưng ĐỌC ĐƯỢC, và không giả vờ cụ thể.
                        #
                        # ⚠ Vẫn giữ nguyên luật bỏ chunk ngắn + heading chung chung ở dưới: nó
                        # lọc chunk RỖNG NGHĨA, khác việc sửa cái NHÃN. Tính `la_chung_chung`
                        # một lần để hai việc dùng chung một định nghĩa.
                        heading = str(payload.get("heading") or "").strip()
                        la_chung_chung = heading.casefold() in _HEADING_CHUNG_CHUNG
                        source_url = payload.get("source_url") or payload.get("url") or "https://www.greensm.com/vn-vi/helps"
                        # ⚠ `_bo_duoi_site` bọc CẢ chuỗi, không chỉ nhánh cuối: đo được đuôi
                        # `| Green SM` lọt vào qua nhánh **`heading`** (một số chunk mang
                        # nguyên `<title>` của trang làm heading), không phải qua `records`.
                        # Cắt ở nhánh nào cũng sẽ sót nhánh khác.
                        title = _bo_duoi_site(str((heading if not la_chung_chung else "")
                                    or payload.get("title")
                                    or _ten_tai_lieu(str(source_url), self._ten_theo_slug)
                                    or payload.get("chunk_summary")
                                    or "Chính sách GSM").strip())
                        if len(text) < 40 and la_chung_chung:
                            continue
                        source_id = str(payload.get("chunk_id") or payload.get("source_id") or point.id)
                        vec_score = float(getattr(point, "score", 0.0))

                        doc_obj = {
                            "source_id": source_id,
                            "title": title,
                            "text": text,
                            "source_url": str(source_url),
                            "f0_tracks": p_tracks,
                            "excerpt": _doan_lien_quan(text, query_text),
                            "match_score": round(vec_score, 4),
                            "retrieved_at": "qdrant_live",
                        }
                        doc_by_id[source_id] = doc_obj
                        candidate_docs.append(doc_obj)

                    # Also include local records in candidate pool for comprehensive BM25 coverage
                    for r in self.records:
                        r_tracks = _normalize_f0_tracks(r.get("f0_tracks"))
                        if norm_track and norm_track not in r_tracks:
                            continue
                        s_id = str(r.get("source_id") or "kb_doc")
                        if s_id not in doc_by_id:
                            r_text = str(r.get("text") or r.get("main_text") or "")
                            doc_obj = {
                                "source_id": s_id,
                                "title": str(r.get("title") or "Chính sách Green SM"),
                                "text": r_text,
                                "source_url": str(r.get("source_url") or "https://www.greensm.com/vn-vi/helps"),
                                "f0_tracks": r_tracks,
                                "excerpt": _doan_lien_quan(r_text, query_text),
                                "match_score": 0.0,
                                "retrieved_at": "local_kb",
                            }
                            doc_by_id[s_id] = doc_obj
                            candidate_docs.append(doc_obj)

                    if candidate_docs:
                        # Rank dense
                        dense_sorted = sorted(
                            [d for d in candidate_docs if d["retrieved_at"] == "qdrant_live"],
                            key=lambda x: -x["match_score"]
                        )
                        for rank_idx, doc in enumerate(dense_sorted, start=1):
                            dense_ranks[doc["source_id"]] = rank_idx

                        # Rank sparse using Okapi BM25
                        bm25 = OkapiBM25(candidate_docs)
                        bm25_scores = bm25.score(q_tokens)
                        sparse_tuples = sorted(
                            [(bm25_scores[i], candidate_docs[i]["source_id"]) for i in range(len(candidate_docs))],
                            key=lambda x: -x[0]
                        )
                        sparse_ranks: dict[str, int] = {}
                        for rank_idx, (bm_sc, s_id) in enumerate(sparse_tuples, start=1):
                            if bm_sc > 0.0:
                                sparse_ranks[s_id] = rank_idx

                        # Merge rankings via Reciprocal Rank Fusion (RRF)
                        rrf_scores = reciprocal_rank_fusion(dense_ranks, sparse_ranks, k=60)
                        
                        fused_docs = sorted(
                            candidate_docs,
                            key=lambda d: -rrf_scores.get(d["source_id"], 0.0)
                        )
                        out = []
                        for d in fused_docs[:top_k]:
                            doc_copy = dict(d)
                            doc_copy["rrf_score"] = round(rrf_scores.get(d["source_id"], 0.0), 5)
                            out.append(doc_copy)
                        if len(out) >= top_k:
                            return out
                        elif out:
                            local_supp = self._local_bm25_search(norm_track, q_clean, top_k - len(out), as_of_date=as_of_date)
                            return out + local_supp
            except Exception as exc:
                _LOG.warning("Qdrant/RRF search failed, falling back to local BM25: %s", exc)

        # 2. Local BM25 Fallback with Temporal Filtering
        return self._local_bm25_search(norm_track, q_clean, top_k, as_of_date=as_of_date)

    def _local_bm25_search(self, track: str | None, query_text: str, top_k: int, as_of_date: str | None = None) -> list[dict]:
        """Local Okapi BM25 search over fallback corpus with temporal validity filtering."""
        pool = [r for r in self.records if not track or track in r.get("f0_tracks", ["platform", "rto", "core_owned"])]

        if as_of_date:
            date_str = as_of_date[:10]
            valid_pool = []
            for r in pool:
                eff_from = r.get("effective_from")
                eff_until = r.get("effective_until")
                if eff_from and date_str < eff_from:
                    continue
                if eff_until and date_str > eff_until:
                    continue
                valid_pool.append(r)
            pool = valid_pool

        if not pool:
            return []

        q_tokens = OkapiBM25.tokenize(query_text)
        bm25 = OkapiBM25(pool)
        scores = bm25.score(q_tokens)

        scored = [(scores[i], pool[i]) for i in range(len(pool)) if scores[i] > 0]
        scored.sort(key=lambda x: -x[0])

        out = []
        for score, r in scored[:top_k]:
            text = r.get("text") or r.get("main_text", "")
            out.append({
                "source_id": str(r.get("source_id") or "kb_doc"),
                "title": str(r.get("title", "Chính sách Green SM")),
                # 🔴 Bản ghi MOCK thì KHÔNG cấp URL — `F-312-05`.
                #
                # Đo 2026-09-02: hai bản ghi trong `FALLBACK_POLICY_DOCS` mang URL **bịa** và trả
                # **404**. `UPDATE-302` vừa biến trích dẫn thành link bấm được, nên tài xế bấm vào
                # một trang lỗi — tệ hơn hẳn không có link.
                #
                # ⚠ KHÔNG trỏ sang trang hub cho có: nội dung hai bản ghi ấy **không nằm trên trang
                # nào cả** (kiểm: trang thật về đảm bảo thu nhập không hề nhắc ADR/CDR). Trỏ sang
                # một trang không chứa điều mình vừa trích là trích dẫn SAI — hỏng nặng hơn 404,
                # vì 404 ít nhất còn hỏng ra mặt.
                #
                # Rỗng ⇒ `workflows` render nhãn TRẦN, không có link (`url_chinh` falsy).
                "source_url": ("" if r.get("nguon_mock")
                               else str(r.get("source_url") or "https://www.greensm.com/vn-vi/helps")),
                "f0_tracks": r.get("f0_tracks", []),
                "excerpt": _doan_lien_quan(text, query_text),
                "match_score": round(float(score), 4),
                "retrieved_at": "local_bm25",
            })
        return out


def query_policy_kb(query: str, track: str | None = None, top_k: int = 3) -> list[dict]:
    """Helper for direct tool execution."""
    kb = PolicyKB()
    res = kb.retrieve(track, query, top_k=top_k)
    return res if isinstance(res, list) else []

