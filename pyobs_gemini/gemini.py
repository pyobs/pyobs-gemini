import asyncio
import logging
from typing import Optional, List, Any, Dict, Tuple
import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u

from pyobs.events import MotionStatusChangedEvent, Event
from pyobs.mixins import FitsNamespaceMixin, MotionStatusMixin
from pyobs.modules import Module, timeout
from pyobs.utils.enums import MotionStatus
from pyobs.utils.parallel import event_wait
from pyobs.utils.threads import LockWithAbort
from pyobs.interfaces import IRotation, IMotion, IFocuser, ICalibrate, IFitsHeaderBefore, IPointingRaDec
from pyobs.utils.time import Time

from .geminidriver import GeminiDriver, GeminiCommException, Vocab

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
        serial_config: Optional[Dict[str, Any]] = None,
        fits_config: Optional[Dict[str, Any]] = None,
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
        self._skycoord: Optional[SkyCoord] = None
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
        self._driver: Optional[GeminiDriver] = None

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
                # self.comm.register_event(TelescopeMovingEvent, self._telescope_event)
                self.comm.register_event(MotionStatusChangedEvent, self._telescope_event)

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

            # get motion status
            await self._change_motion_status(self._motion_status(fdict.response), interface="IFocuser")
            await self._change_motion_status(self._motion_status(rdict.response), interface="IRotation")

    def _motion_status(self, stat: Dict[str, Any]) -> MotionStatus:
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

            while not self._focus_abort.is_set() and await self.get_motion_status("IFocuser") == MotionStatus.SLEWING:
                # sleep a little
                await event_wait(self._focus_abort, 1)

            # aborted?
            if self._focus_abort.is_set():
                raise InterruptedError("Setting focus was interrupted.")

        # success
        log.info("Successfully set new focus.")

    async def get_focus(self, **kwargs: Any) -> float:
        """Return current focus.

        Returns:
            Current focus.
        """
        return self.focus

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

            while (
                not self._rotation_abort.is_set() and await self.get_motion_status("IRotation") == MotionStatus.SLEWING
            ):
                # sleep a little
                await event_wait(self._rotation_abort, 1)

            # aborted?
            if self._rotation_abort.is_set():
                raise InterruptedError("Setting rotation was interrupted.")

        # success
        log.info("Successfully set new rotation.")

    async def get_rotation(self, **kwargs: Any) -> float:
        """Returns the current rotation angle."""
        return await self._driver.get_rotation()

    async def stop_motion(self, device: Optional[str] = None, **kwargs: Any) -> None:
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
            raise ValueError("RA, Dec out of limits (%.2f, %.2f)." % (ra, dec))
        skycoord = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")

        # get parallactic angle
        pa = self.observer.parallactic_angle(Time.now(), skycoord).degree

        # initial rotation
        await self.set_rotation(pa + self._rotation_offset)

        # start tracking and log it
        self._skycoord = skycoord
        log.info(
            "Started target tracking of parallactic angle at %s...",
            self._skycoord.to_string(),
        )

    async def get_radec(self, **kwargs: Any) -> Tuple[float, float]:
        return 0.0, 0.0

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

                # get rotation
                rot = await self.get_rotation()

                # need to rotate?
                if np.abs(pa - rot) > self._rotation_accur:
                    # rotate
                    await self._driver.set_rotation(pa + self._rotation_offset)

            # sleep a little
            await asyncio.sleep(1)

    async def get_fits_header_before(
        self, namespaces: Optional[List[str]] = None, **kwargs: Any
    ) -> Dict[str, Tuple[Any, str]]:
        """Returns FITS header for the current status of this module.

        Args:
            namespaces: If given, only return FITS headers for the given namespaces.

        Returns:
            Dictionary containing FITS headers.
        """
        hdr = {}

        # SET FOCUS HEADERS
        if "focus" in self._fits_config:
            key, comment = self._fits_config["focus"]
            hdr[key] = self.focus, comment
        if "focus-motion" in self._fits_config:
            key, comment = self._fits_config["focus-motion"]
            hdr[key] = (await self.get_motion_status("IFocuser")).value, comment
        if "focus-offset" in self._fits_config:
            key, comment = self._fits_config["focus-offset"]
            hdr[key] = self._focus_offset, comment

        # SET ROTATION HEADERS
        if "rotation" in self._fits_config:
            key, comment = self._fits_config["rotation"]
            hdr[key] = self.rotation, comment
        if "rotation-motion" in self._fits_config:
            key, comment = self._fits_config["rotation-motion"]
            hdr[key] = (await self.get_motion_status("IRotation")).value, comment
        if "rotation-offset" in self._fits_config:
            key, comment = self._fits_config["rotation-offset"]
            hdr[key] = self._rotation_offset, comment

        # TEMPERATURE SENSOR
        if "temperature" in self._fits_config:
            key, comment = self._fits_config["temperature"]
            hdr[key] = self._T, comment

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

    async def get_focus_offset(self, **kwargs: Any) -> float:
        """Return current focus offset.

        Returns:
            Current focus offset.
        """
        return 0.0

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

    async def is_ready(self, **kwargs: Any) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            Whether device is ready
        """
        return True

    async def _telescope_event(self, ev: Event, sender: str) -> bool:
        """Moving events from telescope.

        Args:
            event: Either a MotionStatusChangedEvent or a TelescopeMovingEvent.
            sender: Who sent it.
        """

        # first check sender against self.follow
        if self.follow is None or self.follow != sender:
            return False

        # what kind of event is it?
        if isinstance(ev, TelescopeMovingEvent):
            # do we have RA/Dec?
            if ev.ra is not None and ev.dec is not None:
                # start tracking!
                log.info("Received an event that telescope is about to start tracking. Following it... ")
                await self.track(ev.ra, ev.dec)

            else:
                # presumably slewing to fixed coordinates
                # are we currently tracking?
                if self._skycoord is not None:
                    log.info("Received event that telescope is not tracking anymore, stopping derotator movement...")
                    await self.stop_motion("IRotation")

        elif isinstance(ev, MotionStatusChangedEvent):
            # we want the ITelescope event and anything except TRACKING and SLEWING (might end up in race condition)
            if "ITelescope" in ev.interfaces and ev.interfaces["ITelescope"] not in [
                MotionStatus.TRACKING.value,
                MotionStatus.SLEWING.value,
            ]:
                # are we currently tracking?
                if self._skycoord is not None:
                    # stop it
                    log.info("Received event that telescope is not tracking anymore, stopping derotator movement...")
                    await self.stop_motion("IRotation")


__all__ = ["GeminiFocuserRotator"]
