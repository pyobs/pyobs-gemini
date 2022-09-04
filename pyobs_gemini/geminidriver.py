import asyncio
import enum
import datetime
import logging
from typing import Optional, Any, Dict, List, cast
import numpy as np
import aioserial
from itertools import chain

from pyobs_gemini.api import gemini_commands, gemini_cmd, gemini_parse_output

log = logging.getLogger(__name__)


class GeminiTransaction:
    def __init__(
        self,
        tid: int = 0,
        cmd: Optional[str] = None,
        raw_cmd: Optional[str] = None,
        response: Optional[Dict[str, Any]] = None,
        errors: Optional[Dict[str, Any]] = None,
    ):
        """Constructor for a gemini transaction dictionary"""
        self.transaction = {"id": tid, "cmd": cmd, "raw_cmd": raw_cmd}
        self.response = {} if response is None else response
        self.errors = {} if errors is None else errors
        self.time = datetime.datetime.now()
        self.data: Dict[Vocab, Any] = {}


def has_transaction_error(d: Optional[GeminiTransaction], keys: Optional[List[str]] = None) -> bool:
    """Is there a problem with this transaction dictionary?"""
    if d is None:
        return True
    if keys is None:
        return len(d.errors) > 0
    else:
        for key in keys:
            if key in d.errors:
                return True
        return False


def dict_union(*args: Any) -> Dict[str, Any]:
    return dict(chain.from_iterable(d.items() for d in args if d is not None))


class GeminiCommException(Exception):
    pass


class Vocab(enum.Enum):
    """
    Vocabulary used for YAML configuration and status keywords.
    CURRENT_* are internal Optec keywords.
    FOCUS_MM and POSANG_DEG are calibrated quantity keys.

    YAML syntax for configuration dictionaries:
        FOCUS | ROTATION :
            HOME : bool
            INITIAL : float
            OFFSET : float
        SERIAL :
            PORT : str
            BAUDRATE : int
            TIMEOUT : float
    """

    FOCUS = "focus"
    ROTATION = "rotation"
    HUB = "hub"
    HOME = "home"
    INITIAL = "initial"
    OFFSET = "offset"
    SERIAL = "serial"
    PORT = "port"
    BAUDRATE = "baudrate"
    TIMEOUT = "timeout"
    CURRENT_TEMP = "CurrTemp"
    CURRENT_PA = "CurentPA"
    CURRENT_STEP = "CurrStep"
    FOCUS_MM = "focus_mm"
    POSANG_DEG = "pa_deg"
    MAX_STEPS = "MaxSteps"


class GeminiDriver:
    """
    Simple driver for operating the GEMINI (one needs a central driver to
    handle the serial I/O and the transaction ID's).
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200, timeout: float = 0.2):
        # lock
        self._lock = asyncio.Lock()

        # init
        self._serial: Optional[aioserial.AioSerial] = None
        self.transID = 0
        self.transactions = np.array([None for i in range(99)], dtype=dict)  # CIRCULAR BUFFER
        self.focus_model: Optional[Dict[str, float]] = None
        self.rotation_model: Optional[Dict[str, float]] = None
        self.focus_offset = 0.0
        self.rotation_offset = 0.0

        # serial connection
        self.serial = aioserial.AioSerial(port=port, timeout=timeout, baudrate=baudrate)

    async def _send_to_gemini(self, dev: str, cmd: str, *args: Any) -> GeminiTransaction:
        """
        Send the GEMINI device dev="F"|"R" the command "cmd", using any
        optional arguments.

        RETURN:
            Returns a GeminiTransaction dictionary.
        """

        # acquire lock
        async with self._lock:
            # increase ID
            self.transID += 1
            if self.transID > 99:
                self.transID = 1  # API USES 1-99

            # proper command?
            if cmd not in gemini_commands:
                return GeminiTransaction(tid=self.transID, cmd=cmd, errors={"unknown command": cmd})

            # get command format
            fmt = gemini_commands[cmd]["format"]

            # construct command
            try:
                if fmt is not None:
                    rcmd = gemini_cmd(cmd, *args, dev=dev, trans_id=self.transID)
                else:
                    rcmd = gemini_cmd(cmd, dev=dev, trans_id=self.transID)
            except ValueError as e:
                return GeminiTransaction(tid=self.transID, cmd=cmd, errors={"command error": str(e)})

            # send command
            await self.serial.write_async(bytes(rcmd, "utf8"))

            # get response
            outp = await self.serial.readlines_async()
            response, errors = gemini_parse_output(cmd, outp)

            # info
            d = GeminiTransaction(
                tid=self.transID,
                cmd=cmd,
                raw_cmd=rcmd,
                response=response,
                errors=errors,
            )

            # add to list of transactions
            self.transactions = np.roll(self.transactions, 1)
            self.transactions[0] = d

            # return resulting transaction
            return d

    def _steps_to_mm(self, steps: int) -> float:
        """Calculate the focus [mm] from the internal steps"""
        f: float = np.nan
        if self.focus_model is not None:
            f = self.focus_model["offset"] + self.focus_model["scale"] * steps
            f = max(self.focus_model["min"], min(self.focus_model["max"], f))
        return f

    def _mm_to_steps(self, focus: float) -> int:
        """Calculate the focus in steps for a given distance [mm]"""
        if self.focus_model is None:
            s = -999999
        else:
            s = int((focus - self.focus_model["offset"]) / self.focus_model["scale"])
            s = max(0, min(int(self.focus_model["max_steps"]), s))
        return s

    def _intern_to_extern(self, pa_int: int) -> float:
        """
        Calculate the external position angle [deg]
        for a given internal position angle [millideg]
        """
        m = self.rotation_model
        pa_ext: float = np.nan
        if m is not None:
            pa_ext = m["offset"] + m["scale"] * pa_int + self.rotation_offset
        return pa_ext

    def _extern_to_intern(self, pa_ext: float) -> int:
        """Calculate the internal position angle [millideg] for a given external position angle [deg]"""
        m = self.rotation_model
        if m is None:
            pa_int = -999999
        else:
            pa_int = int((pa_ext - self.rotation_offset - m["offset"]) / m["scale"])
            if pa_int < 0:
                pa_int += 360000
            if pa_int > 360000:
                pa_int -= 360000
        return pa_int

    async def calibrate(self) -> bool:
        """Calibrates the focus and rotation scales from steps to mm/deg."""

        # get focuser configuration
        cfg = await self.get_focus_config()
        response = cfg.response
        if has_transaction_error(cfg, keys=[Vocab.MAX_STEPS.value]):
            logging.error("could not get focus MaxSteps", str(cfg.errors))
            return False
        if Vocab.MAX_STEPS.value not in response:
            logging.error('"MaxSteps" not found in focus configuration!')
            return False

        # conversion between hardware and mm focus: mm = offset + scale * steps
        min_fsteps = 0
        max_fsteps = response[Vocab.MAX_STEPS.value]
        frange = 60.0  # mm
        fscale = frange / (max_fsteps - min_fsteps)
        foffset = 0.0 - fscale * 0.5 * (min_fsteps + max_fsteps)
        self.focus_model = {
            "offset": foffset,
            "scale": fscale,
            "min": -0.5 * frange,
            "max": +0.5 * frange,
            "accur": fscale * 2,
            "max_steps": max_fsteps,
        }

        # conversion between hardware and position angle: deg = offset + scale * millideg
        # 	native is 0-360000 milli-degrees, convert to -180 to +180 degrees
        min_rsteps = 0
        max_rsteps = 360000
        rrange = 360.0  # DEG
        self.rotation_model = {
            "offset": -180.0,
            "scale": 0.001,
            "min": -180.0,
            "max": +180.0,
            "accur": 0.002,
            "max_steps": max_rsteps,
        }

        return True

    async def halt_focus(self) -> bool:
        """Stops the focus motion."""
        try:
            await self._send_to_gemini("F", "DOHALT")
        except GeminiCommException as e:
            log.error(str(e))
            return False
        return True

    async def halt_rotation(self) -> bool:
        """Stops the rotation motion."""
        try:
            await self._send_to_gemini("R", "DOHALT")
        except GeminiCommException as e:
            log.error(str(e))
            return False
        return True

    async def _get_status(self, dev: str = "F") -> GeminiTransaction:
        """Gets status in the form of a transaction dictionary."""
        return await self._send_to_gemini(dev, "GETSTA")

    async def get_focus_status(self) -> GeminiTransaction:
        """
        Gets status of the focuser in the form of a transaction dictionary.
        In addtion, the calibrated focus in mm is added.
        """
        try:
            finfo = await self._get_status("F")
        except GeminiCommException as e:
            log.error(str(e))
            return GeminiTransaction(
                tid=self.transID,
                cmd="get_focus_status",
                errors={"GeminiCommException": str(e)},
            )

        if not has_transaction_error(finfo, keys=[Vocab.CURRENT_STEP.value]):
            focus_mm = self._steps_to_mm(finfo.response[Vocab.CURRENT_STEP.value])
            finfo.data[Vocab.FOCUS_MM] = focus_mm

        return finfo

    async def get_rotation_status(self) -> GeminiTransaction:
        """
        Gets status of the rotator in the form of a transaction dictionary.
        In addtion, the calibrated parallactic rotation angle in deg is added.
        """
        try:
            rinfo = await self._get_status("R")
        except GeminiCommException as e:
            log.error(str(e))
            return GeminiTransaction(
                tid=self.transID,
                cmd="get_rotation_status",
                errors={"GeminiCommException": str(e)},
            )

        if not has_transaction_error(rinfo, keys=[Vocab.CURRENT_PA.value]):
            pos_int = rinfo.response[Vocab.CURRENT_PA.value]
            pos_ext = self._intern_to_extern(pos_int)
            rinfo.data[Vocab.POSANG_DEG] = pos_ext

        return rinfo

    async def get_hub_status(self) -> GeminiTransaction:
        """Gets status of the GEMINI hub in the form of a transaction dictionary."""
        try:
            hinfo = await self._get_status("H")
        except GeminiCommException as e:
            log.error(str(e))
            return GeminiTransaction(
                tid=self.transID,
                cmd="get_rotation_status",
                errors={"GeminiCommException": str(e)},
            )

        return hinfo

    def get_focus_accuracy(self) -> float:
        if self.focus_model is None:
            return 0.0
        else:
            return self.focus_model["accur"]

    def get_rotation_accuracy(self) -> float:
        if self.rotation_model is None:
            return 0.0
        else:
            return self.rotation_model["accur"]

    async def _get_config(self, dev: str = "F") -> GeminiTransaction:
        """Gets configuration of the device in the form of a transaction dictionary."""
        return await self._send_to_gemini(dev, "GETCFG")

    async def get_focus_config(self) -> GeminiTransaction:
        """Gets configuration of the focuser in the form of a transaction dictionary."""
        try:
            return await self._get_config("F")
        except GeminiCommException as e:
            log.error(str(e))
            return GeminiTransaction(
                tid=self.transID,
                cmd="get_focus_config",
                errors={"GeminiCommException": str(e)},
            )

    async def get_rotation_config(self) -> GeminiTransaction:
        """Gets configuration of the rotator in the form of a transaction dictionary."""
        try:
            return await self._get_config("R")
        except GeminiCommException as e:
            log.error(str(e))
            return GeminiTransaction(
                tid=self.transID,
                cmd="get_focus_config",
                errors={"GeminiCommException": str(e)},
            )

    async def get_hub_config(self) -> GeminiTransaction:
        """Gets configuration of the hub in the form of a transaction dictionary."""
        try:
            return await self._get_config("H")
        except GeminiCommException as e:
            log.error(str(e))
            return GeminiTransaction(
                tid=self.transID,
                cmd="get_hub_config",
                errors={"GeminiCommException": str(e)},
            )

    async def start_home_focus(self) -> bool:
        """Starts the homing of the focuser, returns a transaction dictionary."""
        try:
            await self._send_to_gemini("F", "DOHOME")
            return True
        except GeminiCommException as e:
            log.error("Could not home focus: " + str(e))
            return False

    async def focus_is_homed(self) -> bool:
        """Whether focus is homed or not."""

        # get current status and check
        result = await self.get_focus_status()
        if has_transaction_error(result, keys=["Is Homed"]):
            return False
        elif "Is Homed" not in result.response:
            log.error('"Is Homed" not in response')
            return False
        else:
            return cast(bool, result.response["Is Homed"])

    async def start_home_rotation(self) -> bool:
        """Starts the homing of the rotator, returns a transation dictionary."""
        try:
            log.info("Homing rotation...")
            await self._send_to_gemini("R", "DOHOME")
            return True
        except GeminiCommException as e:
            log.error("Could not home rotation: " + str(e))
            return False

    async def rotation_is_homed(self) -> bool:
        """Whether rotation is homed or not."""

        # get current status and check
        result = await self.get_rotation_status()
        if has_transaction_error(result, keys=["Is Homed", "GeminiCommException"]):
            return False
        elif "Is Homed" not in result.response:
            log.error('"Is Homed" not in response')
            return False
        else:
            return cast(bool, result.response["Is Homed"])

    async def set_led(self, level: int) -> bool:
        """Sets the brightness of the hub's LED."""
        level = max(0, min(99, level))
        try:
            await self._send_to_gemini("H", "SETLED", level)
            return True
        except GeminiCommException as e:
            log.error(str(e))
            return False

    async def reboot_hub(self) -> bool:
        try:
            await self._send_to_gemini("H", "REBOOT")
            return True
        except GeminiCommException as e:
            log.error(str(e))
            return False

    async def reverse_rotation(self, flag: bool = False) -> bool:
        try:
            await self._send_to_gemini("R", "SETREV", int(flag))
            return True
        except GeminiCommException as e:
            log.error(str(e))
            return False

    async def set_focus(self, focus: float) -> bool:
        """Set focus to the given value in mm."""
        # CALCULATE INTERNAL FOCUS VALUE
        steps = self._mm_to_steps(focus)

        # SEND COMMAND TO FOCUS
        try:
            await self._send_to_gemini("F", "MOVABS", steps)
        except GeminiCommException as e:
            log.error(str(e))
            return False
        return True

    async def get_focus(self) -> float:
        """Get present focus value in mm."""
        try:
            result = await self._send_to_gemini("F", "GETSTA")
        except GeminiCommException as e:
            log.error(str(e))
            return float(np.nan)
        if Vocab.CURRENT_STEP.value in result.response:
            f = self._steps_to_mm(result.response[Vocab.CURRENT_STEP.value])
        else:
            f = np.nan
        return f

    async def set_rotation(self, pa_ext: float) -> bool:
        """Set the external position angle to the given value in degrees."""
        # CALCULATE INTERNAL POSITION ANGLE
        pa_int = self._extern_to_intern(pa_ext)

        # SEND COMMAND TO FOCUSER
        try:
            result = await self._send_to_gemini("R", "MOVEPA", pa_int)
        except GeminiCommException as e:
            log.error(str(e))
            return False
        if has_transaction_error(result):
            log.error("error while moving rotation")
            return False
        return True

    async def get_rotation(self) -> float:
        """Get present calibrated position angle [deg]."""
        try:
            result = await self._send_to_gemini("R", "GETSTA")
        except GeminiCommException as e:
            log.error(str(e))
            return False
        response = result.response
        if Vocab.CURRENT_PA.value in response:
            pa_ext = self._intern_to_extern(response["CurentPA"])
        else:
            pa_ext = np.nan
        return pa_ext

    async def get_temperature(self) -> float:
        """Get internal temperature in deg C."""
        result = await self.get_focus_status()
        response = result.response
        if "CurrTemp" in response:
            return cast(float, response["CurrTemp"])
        else:
            return float(np.nan)


__all__ = ["Vocab", "GeminiCommException", "GeminiDriver"]
