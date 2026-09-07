"""Unit tests for pack_publish unit-conversion hardening."""

from outlier_scrapers.pack_market import _to_float


class TestToFloatAtRowLevel:
    """Validate _to_float behavior for recommended_units_pre_news parsing.

    pack_publish.py L452-454 formerly used bare float(), which crashes on
    non-numeric strings. The fix routes through _to_float instead.
    """

    def test_numeric_string(self):
        assert _to_float("1.5") == 1.5

    def test_integer_string(self):
        assert _to_float("3") == 3.0

    def test_zero_string(self):
        assert _to_float("0") == 0.0

    def test_negative_string(self):
        assert _to_float("-2.5") == -2.5

    def test_actual_float_passthrough(self):
        assert _to_float(2.0) == 2.0

    def test_actual_int_passthrough(self):
        assert _to_float(3) == 3.0

    def test_none_returns_none(self):
        assert _to_float(None) is None

    def test_empty_string_returns_none(self):
        assert _to_float("") is None

    def test_non_numeric_string_returns_none(self):
        assert _to_float("N/A") is None

    def test_whitespace_string_returns_none(self):
        assert _to_float("  ") is None

    def test_mixed_garbage_returns_none(self):
        assert _to_float("1.5 units") is None


class TestRowUnitsParsing:
    """Simulate the pack_publish row-level units assignment logic."""

    @staticmethod
    def _apply_units(row: dict) -> dict:
        """Mirror pack_publish.py L452-454 logic after the fix."""
        parsed_units = _to_float(row.get("recommended_units_pre_news"))
        if parsed_units is not None:
            row["units"] = parsed_units
        return row

    def test_numeric_units_assigned(self):
        row = {"recommended_units_pre_news": "1.5"}
        result = self._apply_units(row)
        assert result["units"] == 1.5

    def test_none_units_not_assigned(self):
        row = {"recommended_units_pre_news": None}
        result = self._apply_units(row)
        assert "units" not in result

    def test_empty_string_units_not_assigned(self):
        row = {"recommended_units_pre_news": ""}
        result = self._apply_units(row)
        assert "units" not in result

    def test_garbage_units_not_assigned(self):
        row = {"recommended_units_pre_news": "TBD"}
        result = self._apply_units(row)
        assert "units" not in result

    def test_missing_key_not_assigned(self):
        row = {}
        result = self._apply_units(row)
        assert "units" not in result

    def test_zero_units_assigned(self):
        """Zero is a valid float; should still be assigned."""
        row = {"recommended_units_pre_news": "0"}
        result = self._apply_units(row)
        assert result["units"] == 0.0
