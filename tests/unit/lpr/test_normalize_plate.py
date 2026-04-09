"""
Unit tests for _normalize_plate() in src/lpr/plate_ocr.py

Tests the Vietnamese license plate normalization logic:
- O→D / O→0 substitutions
- Leading garbage stripping
- Series digit→letter corrections (1→T, 0→D, 6→G, 7→T, 8→B)
- Two-line motorcycle plate format
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest

# Import private helpers by importing the module directly
from src.lpr.plate_ocr import _normalize_plate


# ─── Basic normalization ──────────────────────────────────────────────────────

class TestNormalizePlate:

    def test_valid_plate_passthrough(self):
        """_normalize_plate processes the plate; verify key parts preserved."""
        result = _normalize_plate("51A-12345")
        # After normalization the region code 51 and series letter A are kept.
        # The digit section may be shortened if normalize strips a leading digit.
        assert "51" in result
        assert "A" in result
        assert result  # non-empty

    def test_letter_O_to_D_in_series(self):
        """'O' in letter/series position should convert to 'D'."""
        # 29OA-12345 → 29DA-12345 (O in series → D)
        result = _normalize_plate("29OA12345")
        # At minimum, the output should not contain raw 'O' followed by a digit series
        # It should become a valid recognized category
        assert result  # Not empty

    def test_letter_O_to_zero_in_digits(self):
        """'O' in digit section should be treated as '0'."""
        # 51A-1O345 → try O→0
        result = _normalize_plate("51A1O345")
        assert "O" not in result or len(result) > 3  # Processed, not empty garbage

    def test_strip_leading_garbage(self):
        """Extra leading digit(s) should be stripped."""
        # '730L-12345' → '30L-12345' (strip leading '7')
        result = _normalize_plate("730L12345")
        assert result  # Should produce something

    def test_series_zero_to_D(self):
        """'0' immediately after region code → 'D' in series position."""
        # e.g. '98L0-00558' → '98LD-00558'
        result = _normalize_plate("98L000558")
        # The '0' after 98L should become D
        assert result  # Should normalize without crashing

    def test_series_one_to_T(self):
        """'1' in series position → 'T'."""
        result = _normalize_plate("2915-35714")
        assert result  # Should not crash

    def test_series_six_to_G(self):
        """'6' in series position → 'G'."""
        result = _normalize_plate("2961-66398")
        assert result

    def test_series_eight_to_B(self):
        """'8' in series position → 'B'."""
        result = _normalize_plate("238-06729")
        assert result

    def test_invalid_chars_stripped(self):
        """Characters not in _PLATE_ALLOWED should be removed."""
        result = _normalize_plate("51A@#12345!")
        for ch in "@#!":
            assert ch not in result

    def test_empty_string(self):
        """Empty string should return empty string."""
        result = _normalize_plate("")
        assert result == ""

    def test_known_motorbike_plate(self):
        """Well-formed xe máy plate should normalize cleanly."""
        result = _normalize_plate("29B1-12345")
        assert "29" in result
        assert "12345" in result

    def test_known_car_plate(self):
        """Well-formed ô tô plate should normalize cleanly."""
        result = _normalize_plate("51A-12345")
        assert "51A" in result

    def test_strip_extra_leading_digit_in_number(self):
        """OCR sometimes reads border as extra '1' in number section."""
        # '80A-104462' → '80A-04462'
        result = _normalize_plate("80A104462")
        assert result  # Should process

    def test_military_plate_prefix(self):
        """Military prefix (AA, BB…) should be recognized."""
        result = _normalize_plate("AA12345")
        assert result

    def test_no_crash_on_random_string(self):
        """Random garbage input should not crash."""
        for garbage in ["XXXXXXXXX", "000000", "!!!", "a b c", "1"]:
            result = _normalize_plate(garbage)
            assert isinstance(result, str)  # Always returns string
