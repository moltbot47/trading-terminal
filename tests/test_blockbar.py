"""Tests for the blockBar JavaScript function logic (Python-side equivalent).

Since blockBar lives in JS, we test the equivalent Python logic to verify
edge cases: negative values, zero max, normal values.
"""


def block_bar(v: float, max_val: float, w: int = 15) -> tuple[int, int, str]:
    """Python equivalent of the JS blockBar function.

    Returns (filled, empty, color_class).
    """
    ratio = max(0, min(1, v / max_val)) if max_val > 0 else 0
    f = round(ratio * w)
    e = w - f
    c = "c-green" if ratio > 0.8 else "c-yellow" if ratio > 0.5 else "c-red"
    return f, e, c


def test_normal_values():
    """Normal positive values should produce correct fill ratio."""
    f, e, c = block_bar(75, 100)
    assert f + e == 15
    assert f == 11  # round(0.75 * 15) = 11
    assert c == "c-yellow"


def test_full_bar():
    """Value equal to max should fill completely."""
    f, e, c = block_bar(100, 100)
    assert f == 15
    assert e == 0
    assert c == "c-green"


def test_empty_bar():
    """Zero value should produce empty bar."""
    f, e, c = block_bar(0, 100)
    assert f == 0
    assert e == 15
    assert c == "c-red"


def test_zero_max():
    """Zero max should produce empty bar (no division by zero)."""
    f, e, c = block_bar(50, 0)
    assert f == 0
    assert e == 15
    assert c == "c-red"


def test_negative_value():
    """Negative value should clamp to 0 (empty bar)."""
    f, e, c = block_bar(-10, 100)
    assert f == 0
    assert e == 15
    assert c == "c-red"


def test_value_exceeds_max():
    """Value exceeding max should clamp to 1.0 (full bar)."""
    f, e, c = block_bar(200, 100)
    assert f == 15
    assert e == 0
    assert c == "c-green"


def test_custom_width():
    """Custom bar width should be respected."""
    f, e, c = block_bar(50, 100, w=10)
    assert f + e == 10
    assert f == 5


def test_small_ratio_red():
    """Ratios <= 0.5 should be red."""
    _, _, c = block_bar(30, 100)
    assert c == "c-red"


def test_medium_ratio_yellow():
    """Ratios between 0.5 and 0.8 should be yellow."""
    _, _, c = block_bar(65, 100)
    assert c == "c-yellow"


def test_high_ratio_green():
    """Ratios above 0.8 should be green."""
    _, _, c = block_bar(90, 100)
    assert c == "c-green"
