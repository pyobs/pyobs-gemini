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


def test_constructor_threads_mixin_kwargs_cooperatively() -> None:
    """Regression test for the cooperative-super()-chain fix: GeminiFocuserRotator(Module,
    FitsNamespaceMixin, MotionStatusMixin, ...) used to call Module.__init__ first, then
    FitsNamespaceMixin.__init__/MotionStatusMixin.__init__ explicitly at the end -- the live
    gemini.yaml config sets fits_namespaces, which must still reach FitsNamespaceMixin through
    the single super().__init__() call, not get lost or leak to object.__init__(). If either
    mixin's __init__ never actually ran, motion_status()/_filter_fits_namespace() below raise
    AttributeError instead of returning a value."""
    from pyobs.utils.enums import MotionStatus

    gemini = GeminiFocuserRotator(fits_namespaces={"sbig8300": None}, rotation_offset=90.0)

    assert gemini.motion_status() == MotionStatus.UNKNOWN
    filtered = gemini._filter_fits_namespace({}, sender="sbig8300")
    assert filtered == {}
    assert gemini._rotation_offset == 90.0
