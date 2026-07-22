import asyncio
import logging
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from pyobs.events import Event, MotionStatusChangedEvent
from pyobs.interfaces import (
    FitsHeaderEntry,
    FocuserState,
    ICalibrate,
    IFitsHeaderBefore,
    IFocuser,
    IPointingRaDec,
    IRotation,
    RaDecState,
    RotationState,
)
from pyobs.mixins import FitsNamespaceMixin, MotionStatusMixin
from pyobs.modules import Module, timeout
from pyobs.utils.enums import MotionStatus
from pyobs.utils.parallel import event_wait
from pyobs.utils.threads import LockWithAbort
from pyobs.utils.time import Time

from .geminidriver import GeminiCommException, GeminiDriver, Vocab

log = logging.getLogger(__name__)


class GeminiFocuserRotator(
    FitsNamespaceMixin,
    MotionStatusMixin,
    Module,
    IRotation,
    IFocuser,
    ICalibrate,
    IPointingRaDec,
    IFitsHeaderBefore,
):
    """Pyobs module for operating an Optec Inc GEMINI focuser/rotator."""

    def __init__(
        self,
        serial_config: dict[str, Any] | None = None,
        fits_config: dict[str, Any] | None = None,
        focus_offset: float = 0.0,
        rotation_offset: float = 0.0,
        *args: Any,
        **kwargs: Any,
    ):
        Module.__init__(self, *args, **kwargs)

        # add thread func
        self.add_background_task(self._gdriver_update_func, True)
        self.add_background_task(self._rotation_tracker_func, True)

        # store
        self.focus = 0.0
        self.rotation = 0.0
        self.follow = None

        # FOCUSING STUFF
        self._focus_lock = asyncio.Lock()
        self._focus_abort = asyncio.Event()
        self._focus_accur = 0.0  # MM
        self._focus_offset = focus_offset

        # ROTATOR STUFF
        self._rotation_lock = asyncio.Lock()
        self._rotation_abort = asyncio.Event()
        self._skycoord: SkyCoord | None = None
        self._rotation_accur = 0.0  # DEG
        self._rotation_offset = rotation_offset

        # TEMPERATURE SENSOR
        self._T = None

        # SERIAL CONFIGURATION DICTIONARY
        if serial_config is None:
            self._serial_config = {
                "port": "/dev/ttyUSB0",
                "baudrate": 115200,
                "timeout": 0.1,
            }
        else:
            self._serial_config = serial_config

        # driver
        self._driver: GeminiDriver | None = None

        # FITS HEADER CONFIGURATION
        if fits_config is None:
            self._fits_config = {
                "focus": ("GEM-FOCU", "focus of the Gemini focusser [mm]"),
                "focus-offset": ("GEM-FOFF", "constant Gemini focus offset [mm]"),
                "focus-motion": (
                    "GEM-FMOT",
                    "motion status of the Gemini focusser [mm]",
                ),
                "rotation": ("GEM-ROTA", "angle of the Gemini rotator [mm]"),
                "rotation-offset": ("GEM-ROFF", "constant Gemini rotation offset [mm]"),
                "rotation-motion": (
                    "GEM-RMOT",
                    "motion status of the Gemini rotator [mm]",
                ),
                "temperature": ("GEM-TEMP", "temperature of the Gemini sensor [C]"),
            }
        else:
            self._fits_config = fits_config

        # mixins
        FitsNamespaceMixin.__init__(self, *args, **kwargs)
        MotionStatusMixin.__init__(self, **kwargs, motion_status_interfaces=["IFocuser", "IRotation"])

    async def open(self) -> None:
        """Open module."""
        await Module.open(self)

        # subscribe to events
        if self.comm:
            if self.follow:
                await self.comm.register_event(MotionStatusChangedEvent, self._telescope_event)

        # create driver and open it
        self._driver = GeminiDriver(**self._serial_config)

        # calibrate
        log.info("Calibrating unit...")
        await self._driver.calibrate()
        self._focus_accur = self._driver.get_focus_accuracy()
        self._rotation_accur = self._driver.get_rotation_accuracy()

        # open mixins
        await MotionStatusMixin.open(self)

    async def close(self) -> None:
        """Close module."""
        await Module.close(self)

        log.info("Closing hardware connection...")
        if self._driver is not None:
            self._driver = None

    @timeout(600000)
    async def calibrate(self, **kwargs: Any) -> None:
        """Calibrate the device."""
        if self._driver is None:
            return

        # reset
        self._skycoord = None

        # acquire focus lock
        async with LockWithAbort(self._focus_lock, self._focus_abort):
            # acquire rotation lock
            async with LockWithAbort(self._rotation_lock, self._rotation_abort):
                # start homing for both
                log.info("Start homing focus...")
                if not self._driver.start_home_focus():
                    raise ValueError("Could not start homing for focus.")
                log.info("Start homing rotation...")
                if not self._driver.start_home_rotation():
                    raise ValueError("Could not start homing for rotation.")

                # wait for both
                while True:
                    # both homed?
                    if self._driver.focus_is_homed() and self._driver.rotation_is_homed():
                        # finished
                        log.info("Homing successful.")
                        return

                    # abort any?
                    if self._focus_abort.is_set() or self._rotation_abort.is_set():
                        log.warning("Homing aborted.")
                        return

                    # sleep a little (can only wait on unset events
                    if not self._focus_abort.is_set():
                        await event_wait(self._focus_abort, 1)
                    else:
                        await event_wait(self._rotation_abort, 1)

    async def _gdriver_update_func(self) -> None:
        log.info("Starting GEMINI driver update thread...")

        while True:
            # do update
            await self._update_status()

            # sleep a little
            await asyncio.sleep(1)

    async def _update_status(self) -> None:
        if self._driver is not None:
            # get data
            fdict = await self._driver.get_focus_status()
            rdict = await self._driver.get_rotation_status()

            # get current focus and rotation
            self.focus = fdict.data[Vocab.FOCUS_MM]
            self.rotation = rdict.data[Vocab.POSANG_DEG]

            # get temp
            # TODO: find out
            # self._T = fdict.response[Vocab.CURRENT_TEMP.value]

            # publish current focus/rotation state
            await self.comm.set_state(IFocuser, FocuserState(focus=self.focus, focus_offset=self._focus_offset))
            await self.comm.set_state(IRotation, RotationState(rotation=self.rotation))

            # get motion status
            await self._change_motion_status(self._motion_status(fdict.response), interface="IFocuser")
            await self._change_motion_status(self._motion_status(rdict.response), interface="IRotation")

    def _motion_status(self, stat: dict[str, Any]) -> MotionStatus:
        """
        Extracts the IMotion status from a dictionary returned
        by the driver's status method.
        """
        if "IsHoming" in stat and stat["IsHoming"]:
            return MotionStatus.INITIALIZING
        if "IsMoving" in stat and stat["IsMoving"]:
            return MotionStatus.SLEWING
        if self._skycoord is None:
            return MotionStatus.IDLE
        else:
            return MotionStatus.TRACKING

    @timeout(300000)
    async def set_focus(self, focus: float, **kwargs: Any) -> None:
        """Sets new focus.

        Args:
            focus: New focus value.

        Raises:
            MoveError: If telescope cannot be moved.
            InterruptedError: If movement was aborted.
        """
        if self._driver is None:
            return

        # acquire lock
        async with LockWithAbort(self._focus_lock, self._focus_abort):
            # set focus value
            try:
                log.info("Setting focus to %.2f...", focus)
                await self._driver.set_focus(focus)
            except GeminiCommException:
                log.exception("Could not set new focus.")

            # sleep a little and force update
            await event_wait(self._focus_abort, 1)
            await self._update_status()

            while not self._focus_abort.is_set() and self.motion_status("IFocuser") == MotionStatus.SLEWING:
                # sleep a little
                await event_wait(self._focus_abort, 1)

            # aborted?
            if self._focus_abort.is_set():
                raise InterruptedError("Setting focus was interrupted.")

        # success
        log.info("Successfully set new focus.")

    @timeout(300000)
    async def set_rotation(self, angle: float, **kwargs: Any) -> None:
        """Sets the rotation angle to the given value in degrees."""
        if self._driver is None:
            return

        # acquire lock
        async with LockWithAbort(self._rotation_lock, self._rotation_abort):
            # set focus value
            try:
                log.info("Setting rotation to %.2f...", angle)
                await self._driver.set_rotation(angle)
            except GeminiCommException:
                log.exception("Could not set new rotation.")

            # sleep a little and force update
            await event_wait(self._rotation_abort, 1)
            await self._update_status()

            while not self._rotation_abort.is_set() and self.motion_status("IRotation") == MotionStatus.SLEWING:
                # sleep a little
                await event_wait(self._rotation_abort, 1)

            # aborted?
            if self._rotation_abort.is_set():
                raise InterruptedError("Setting rotation was interrupted.")

        # success
        log.info("Successfully set new rotation.")

    async def stop_motion(self, device: str | None = None, **kwargs: Any) -> None:
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """
        if device is None or device == "IRotation":
            # need to stop tracking?
            if self._skycoord is not None:
                self._skycoord = None
                log.info("Stopped parallactic angle tracking.")

    async def move_radec(self, ra: float, dec: float, **kwargs: Any) -> None:
        """Tracks the position angle of a rotator for an alt-az telescope."""
        if self._driver is None:
            return

        # first, reset tracking
        self._skycoord = None

        # valid coordinates?
        if ra < 0.0 or ra > 360.0 or np.abs(dec) >= 90.0:
            raise ValueError(f"RA, Dec out of limits ({ra:.2f}, {dec:.2f}).")
        skycoord = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")

        # get parallactic angle
        pa = self.observer.parallactic_angle(Time.now(), skycoord).degree

        # initial rotation
        await self.set_rotation(pa + self._rotation_offset)

        # start tracking and log it
        self._skycoord = skycoord
        await self.comm.set_state(IPointingRaDec, RaDecState(ra=ra, dec=dec))
        log.info(
            "Started target tracking of parallactic angle at %s...",
            self._skycoord.to_string(),
        )

    async def _rotation_tracker_func(self) -> None:
        if self._driver is None:
            return

        # log
        log.info("Starting rotation tracking thread...")

        while True:
            # do we have a sky coord to track?
            if self._skycoord is not None:
                # get parallactic angle
                pa = self.observer.parallactic_angle(Time.now(), self._skycoord).degree

                # need to rotate? (self.rotation is kept fresh by _gdriver_update_func)
                if np.abs(pa - self.rotation) > self._rotation_accur:
                    # rotate
                    await self._driver.set_rotation(pa + self._rotation_offset)

            # sleep a little
            await asyncio.sleep(1)

    async def get_fits_header_before(
        self, namespaces: list[str] | None = None, **kwargs: Any
    ) -> dict[str, FitsHeaderEntry]:
        """Returns FITS header for the current status of this module.

        Args:
            namespaces: If given, only return FITS headers for the given namespaces.

        Returns:
            Dictionary containing FITS headers.
        """
        hdr: dict[str, FitsHeaderEntry] = {}

        # SET FOCUS HEADERS
        if "focus" in self._fits_config:
            key, comment = self._fits_config["focus"]
            hdr[key] = FitsHeaderEntry(value=self.focus, comment=comment)
        if "focus-motion" in self._fits_config:
            key, comment = self._fits_config["focus-motion"]
            hdr[key] = FitsHeaderEntry(value=self.motion_status("IFocuser").value, comment=comment)
        if "focus-offset" in self._fits_config:
            key, comment = self._fits_config["focus-offset"]
            hdr[key] = FitsHeaderEntry(value=self._focus_offset, comment=comment)

        # SET ROTATION HEADERS
        if "rotation" in self._fits_config:
            key, comment = self._fits_config["rotation"]
            hdr[key] = FitsHeaderEntry(value=self.rotation, comment=comment)
        if "rotation-motion" in self._fits_config:
            key, comment = self._fits_config["rotation-motion"]
            hdr[key] = FitsHeaderEntry(value=self.motion_status("IRotation").value, comment=comment)
        if "rotation-offset" in self._fits_config:
            key, comment = self._fits_config["rotation-offset"]
            hdr[key] = FitsHeaderEntry(value=self._rotation_offset, comment=comment)

        # TEMPERATURE SENSOR
        if "temperature" in self._fits_config:
            key, comment = self._fits_config["temperature"]
            hdr[key] = FitsHeaderEntry(value=self._T, comment=comment)

        # return it
        return self._filter_fits_namespace(hdr, namespaces=namespaces, **kwargs)

    async def set_focus_offset(self, offset: float, **kwargs: Any) -> None:
        """Sets focus offset.

        Args:
            offset: New focus offset.

        Raises:
            ValueError: If given value is invalid.
            MoveError: If telescope cannot be moved.
        """
        pass

    async def init(self, **kwargs: Any) -> None:
        """Initialize device.

        Raises:
            InitError: If device could not be initialized.
        """
        pass

    async def park(self, **kwargs: Any) -> None:
        """Park device.

        Raises:
            ParkError: If device could not be parked.
        """
        pass

    async def _telescope_event(self, ev: Event, sender: str) -> bool:
        """Moving events from telescope.

        Args:
            event: A MotionStatusChangedEvent.
            sender: Who sent it.
        """

        # first check sender against self.follow
        if self.follow is None or self.follow != sender:
            return False

        # we want the ITelescope event and anything except TRACKING and SLEWING (might end up in race condition)
        if isinstance(ev, MotionStatusChangedEvent):
            if "ITelescope" in ev.interfaces and ev.interfaces["ITelescope"] not in [
                MotionStatus.TRACKING.value,
                MotionStatus.SLEWING.value,
            ]:
                # are we currently tracking?
                if self._skycoord is not None:
                    # stop it
                    log.info("Received event that telescope is not tracking anymore, stopping derotator movement...")
                    await self.stop_motion("IRotation")

        return True


__all__ = ["GeminiFocuserRotator"]
