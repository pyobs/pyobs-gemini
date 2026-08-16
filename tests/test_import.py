"""Smoke tests: import every public module and instantiate the driver class without
hardware, asserting it advertises the interfaces it claims.

No serial device is involved: the GeminiDriver (and its serial port) is only created
inside open(), so instantiation is safe.
"""

from pyobs.interfaces import ICalibrate, IFitsHeaderBefore, IFocuser, IPointingRaDec, IRotation
from pyobs.modules import Module

from pyobs_gemini import GeminiFocuserRotator


def test_import_api_and_driver_modules() -> None:
    from pyobs_gemini import api, geminidriver  # noqa: F401

    assert api.gemini_cmd is not None
    assert geminidriver.GeminiDriver is not None


def test_instantiate_gemini() -> None:
    gemini = GeminiFocuserRotator()
    assert isinstance(gemini, Module)
    assert isinstance(gemini, IRotation)
    assert isinstance(gemini, IFocuser)
    assert isinstance(gemini, ICalibrate)
    assert isinstance(gemini, IPointingRaDec)
    assert isinstance(gemini, IFitsHeaderBefore)
