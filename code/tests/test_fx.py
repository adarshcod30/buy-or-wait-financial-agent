import datetime as dt

import pytest

from buyorwait.fx import Converter, FxError


def test_exact_and_inverse_and_hub():
    rates = {(dt.date(2024, 3, 15), "USD", "IDR"): 15833.33, (dt.date(2024, 3, 15), "USD", "EUR"): 0.92}
    c = Converter(rates)
    assert c.convert(2, "USD", "IDR", dt.date(2024, 3, 15)) == (31666.66, "exact")
    v, how = c.convert(15833.33, "IDR", "USD", dt.date(2024, 3, 15))
    assert abs(v - 1.0) < 1e-9 and how == "exact-inverse"
    v, how = c.convert(1, "EUR", "IDR", dt.date(2024, 3, 15))
    assert how.startswith("via-USD") and abs(v - 15833.33 / 0.92) < 1e-6


def test_nearest_earlier_rate_and_missing():
    rates = {(dt.date(2024, 3, 15), "USD", "INR"): 84.0}
    c = Converter(rates)
    v, how = c.convert(1, "USD", "INR", dt.date(2024, 4, 1))
    assert v == 84.0 and how.startswith("nearest-earlier")
    with pytest.raises(FxError):
        c.convert(1, "USD", "INR", dt.date(2024, 1, 1))
