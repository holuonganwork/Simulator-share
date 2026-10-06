"""Unit test truc tiep cho agent/verification.py (khong can store/du lieu that)."""

from __future__ import annotations

from lcx_core.agent.verification import verify_message


def test_message_without_numbers_always_passes():
    result = verify_message("Xin chao, khong co so lieu gi o day ca.", source_texts=[])
    assert result.ok


def test_number_present_in_source_passes():
    result = verify_message(
        "Ty le huy la 0.22, vuot muc thong thuong.", source_texts=["cancellation_rate: 0.22"]
    )
    assert result.ok


def test_number_absent_from_source_is_rejected():
    result = verify_message("Ty le huy la 0.99.", source_texts=["cancellation_rate: 0.22"])
    assert not result.ok
    assert "0.99" in result.unverified_numbers


def test_multiple_numbers_all_must_be_verifiable():
    result = verify_message(
        "Chuyen 12 co 5 diem dung.", source_texts=["trip 12 co 5 diem dung"]
    )
    assert result.ok

    result2 = verify_message(
        "Chuyen 12 co 99 diem dung.", source_texts=["trip 12 co 5 diem dung"]
    )
    assert not result2.ok
    assert result2.unverified_numbers == ["99"]
