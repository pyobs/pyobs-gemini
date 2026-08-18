"""Unit tests for the pure conversion logic in GeminiDriver, with the serial port
mocked out. Hardware I/O (talking to the device) is out of scope.
"""

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from pyobs_gemini.geminidriver import GeminiDriver, GeminiTransaction, dict_union, has_transaction_error


def make_driver() -> GeminiDriver:
    with patch("aioserial.AioSerial"):
        return GeminiDriver()


def test_steps_to_mm_and_clamp() -> None:
    driver = make_driver()
    driver.focus_model = {"offset": 0.0, "scale": 2.0, "min": -100.0, "max": 100.0, "max_steps": 100}
    assert driver._steps_to_mm(5) == pytest.approx(10.0)
    assert driver._steps_to_mm(100) == 100.0  # 200 clamped to max
    assert driver._steps_to_mm(-100) == -100.0  # -200 clamped to min


def test_mm_to_steps_and_clamp() -> None:
    driver = make_driver()
    driver.focus_model = {"offset": 0.0, "scale": 2.0, "min": -100.0, "max": 100.0, "max_steps": 100}
    assert driver._mm_to_steps(10.0) == 5
    assert driver._mm_to_steps(999.0) == 100  # clamped to max_steps
    assert driver._mm_to_steps(-999.0) == 0  # clamped to 0


def test_steps_to_mm_without_model() -> None:
    driver = make_driver()
    assert np.isnan(driver._steps_to_mm(5))


def test_rotation_conversions() -> None:
    driver = make_driver()
    driver.rotation_model = {"offset": -180.0, "scale": 0.001, "min": -180.0, "max": 180.0, "max_steps": 360000}
    assert driver._intern_to_extern(180000) == pytest.approx(0.0)
    assert driver._extern_to_intern(0.0) == 180000


def test_has_transaction_error() -> None:
    assert has_transaction_error(None) is True

    transaction = GeminiTransaction()
    assert has_transaction_error(transaction) is False

    transaction.errors["foo"] = "bar"
    assert has_transaction_error(transaction) is True
    assert has_transaction_error(transaction, keys=["baz"]) is False
    assert has_transaction_error(transaction, keys=["foo"]) is True


def test_dict_union() -> None:
    assert dict_union({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert dict_union(None, {"b": 2}) == {"b": 2}


@pytest.mark.asyncio
async def test_calibrate_builds_models() -> None:
    driver = make_driver()
    driver.serial = AsyncMock()
    driver.serial.readlines_async.return_value = [b"!01\n", b"MaxSteps = 100000\n", b"END\n"]

    assert await driver.calibrate() is True
    assert driver.focus_model is not None
    assert driver.focus_model["max_steps"] == 100000
    assert driver.focus_model["scale"] == pytest.approx(60.0 / 100000)
    assert driver.focus_model["offset"] == pytest.approx(-30.0)
    assert driver.rotation_model is not None
    assert driver.rotation_model["max_steps"] == 360000
