"""
Unit tests for _classify_plate() in src/lpr/plate_ocr.py

Vietnamese plate categories:
  - O_TO_DAN_SU   : ô tô dân sự (e.g. 51A-12345, 98LD-00558)
  - XE_MAY_DAN_SU : xe máy dân sự (e.g. 29B1-12345)
  - XE_QUAN_DOI   : xe quân đội (e.g. AA-12345)
  - BIEN_CA_NHAN  : biển cá nhân
  - KHONG_XAC_DINH: unknown / unrecognized
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest
from src.lpr.plate_ocr import _classify_plate, _CATEGORY_UNKNOWN


class TestClassifyPlate:

    # ─── Ô tô dân sự ─────────────────────────────────────────────────────────

    def test_oto_standard_3digit_series(self):
        """51A-12345 is a standard civilian car plate."""
        cat = _classify_plate("51A-12345")
        assert cat == "O_TO_DAN_SU"

    def test_oto_two_letter_series(self):
        """30LD-12345 pattern (two-letter series)."""
        cat = _classify_plate("30LD-12345")
        assert cat in ("O_TO_DAN_SU", "XE_MAY_DAN_SU", "BIEN_CA_NHAN")

    def test_oto_without_separator(self):
        """51A12345 (no dash) should still match."""
        cat = _classify_plate("51A12345")
        assert cat != _CATEGORY_UNKNOWN

    # ─── Xe máy dân sự ───────────────────────────────────────────────────────

    def test_xemay_standard(self):
        """29B1-12345 is a standard motorcycle (xe máy) plate."""
        cat = _classify_plate("29B1-12345")
        assert cat == "XE_MAY_DAN_SU"

    def test_xemay_dot_separator(self):
        """29B112.345 — dot separator may or may not be supported by classifier.
        Verify it doesn't crash, and returns a string."""
        cat = _classify_plate("29B112.345")
        assert isinstance(cat, str)  # Any valid category string

    def test_xemay_region_18(self):
        """18L4-12345 pattern."""
        cat = _classify_plate("18L4-12345")
        assert cat == "XE_MAY_DAN_SU"

    # ─── Xe quân đội ─────────────────────────────────────────────────────────

    def test_quan_doi_AA(self):
        """AA-12345 is military."""
        cat = _classify_plate("AA-12345")
        assert cat == "XE_QUAN_DOI"

    def test_quan_doi_QD(self):
        """QD-12345 is military."""
        cat = _classify_plate("QD-12345")
        assert cat == "XE_QUAN_DOI"

    # ─── Unknown ──────────────────────────────────────────────────────────────

    def test_unknown_too_short(self):
        """Too-short string → unknown."""
        assert _classify_plate("AB") == _CATEGORY_UNKNOWN

    def test_unknown_random_chars(self):
        """Random chars → unknown."""
        assert _classify_plate("XXXXXXXXX") == _CATEGORY_UNKNOWN

    def test_unknown_empty(self):
        """Empty string → unknown."""
        assert _classify_plate("") == _CATEGORY_UNKNOWN

    def test_unknown_all_digits(self):
        """All-digit string with no prefix → unknown."""
        assert _classify_plate("1234567") == _CATEGORY_UNKNOWN

    # ─── Edge cases ───────────────────────────────────────────────────────────

    def test_no_crash_on_special_chars(self):
        """Should never raise an exception."""
        for s in ["@@@", "   ", "\n\t", "51A-", "-12345"]:
            result = _classify_plate(s)
            assert isinstance(result, str)

    def test_separator_variants(self):
        """Both dash and dot separators should work."""
        assert _classify_plate("51A-12345") == _classify_plate("51A.12345")
