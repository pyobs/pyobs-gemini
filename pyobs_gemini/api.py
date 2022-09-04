"""
Contains the OPTEC API for the GEMINI focusser/rotator, Rev. 2.1

The GEMINI commands have the following syntax:

    <{F|R|H}{d}{ii}{cmd}{data}>

where
    F 	:	focusser
    R	:	rotator
    H	:	hub
    {d}	:	device ID, must be = 1 for GEMINI !!!
    {ii}	:	transaction ID from 01 to 99
    {cmd}	:	command
   {data}	:	option input data field

The GEMINI responses have the syntax:

   !{ii}
   {response}
   {END|SET}

"""
from typing import Dict, Any, Tuple, List

# A COMMAND CONSISTS OF name,device,command_string,input_format,output_format,end_string,help
gemini_commands: Dict[str, Dict[str, Any]] = {
    "CENTER": {
        "cmd": "center",
        "device": "F",
        "format": None,
        "formats": None,
        "end": "END",
        "help": "center the focuser",
    },
    "DOHALT": {
        "cmd": "halt",
        "device": "FR",
        "format": None,
        "formats": None,
        "end": "END",
        "help": "immediately stop motion",
    },
    "DOHOME": {
        "cmd": "home",
        "device": "FR",
        "format": None,
        "formats": None,
        "end": "END",
        "help": "move to home position",
    },
    "GETCFG": {
        "cmd": "get configuration settings",
        "device": "FRH",
        "format": None,
        "formats": [
            "BLCompON = {0:d}",
            "BLCSteps = {0:d}",
            "Dev Type = {0}",
            "HOnStart = {0:d}",
            "MaxSteps = {0:d}",
            "Nickname = {0}",
            "CurrenTC = {0}",
            "TC Start = {0:d}",
            "TCMode A = {0:d}",
            "TCMode B = {0:d}",
            "TCMode C = {0:d}",
            "TCMode D = {0:d}",
            "TCMode E = {0:d}",
            "TComp On = {0:d}",
            "iReverse = {0:d}",
            "MaxSpeed = {0:d}",
            "PAOffset = {0:d}",
            "Firmware = {0}",
            "LEDBrite = {0:d}",
            "HandCtrl = {0:d}",
            "Wired IP = {0}",
            "WiFi Mod = {0:d}",
            "WiFiConn = {0:d}",
            "WiFiFVOK = {0:d}",
            "WiFiFirm = {0}",
            "WiFiSSID = {0}",
            "WiFiAddr = {0}",
            "WiFiSecM = {0}",
            "WiFiSecK = {0}",
        ],
        "end": "END",
        "help": "get the configuration parameters (6 common, 8 focus, 3 rotator, 12 hub)",
    },
    "GETDNN": {
        "cmd": "get device nickname",
        "device": "FR",
        "format": None,
        "formats": ["Nickname = {0}"],
        "end": "END",
        "help": "get the nickname of the device",
    },
    "GETSTA": {
        "cmd": "get status",
        "device": "FRH",
        "format": None,
        "formats": [
            "CurentPA = {0:0d}",
            "CurrStep = {0:0d}",
            "CurrTemp = {0:f}",
            "Is Homed = {0:0d}",
            "IsHoming = {0:0d}",
            "IsMoving = {0:0d}",
            "TargetPA = {0:0d}",
            "TargStep = {0:0d}",
            "TempProb = {0:0d}",
        ],
        "end": "END",
        "help": "get the device status",
    },
    "MOVABS": {
        "cmd": "move absolute position",
        "device": "F",
        "format": "{0:0d}",
        "formats": None,
        "end": "END",
        "help": "move focuser to an absolute position in encoder steps",
    },
    "MOVEPA": {
        "cmd": "move absolute position angle",
        "device": "R",
        "format": "{0:06d}",
        "formats": None,
        "end": "END",
        "help": "rotate to an absolute position in milli-degrees (0 to 359999)",
    },
    "REBOOT": {
        "cmd": "reboot",
        "device": "H",
        "format": None,
        "formats": None,
        "end": "SET",
        "help": "soft reboot of hub",
    },
    "RESETH": {
        "cmd": "reset to factory defaults",
        "device": "H",
        "format": None,
        "formats": None,
        "end": "SET",
        "help": "reset to factory default settings",
    },
    "SETBCE": {
        "cmd": "set backlash compensation enabled",
        "device": "FR",
        "format": "{0:d}",
        "formats": None,
        "end": "SET",
        "help": "set whether backlash compensation is enabled (0=no,1=yes)",
    },
    "SETBCS": {
        "cmd": "set backlash compensation steps",
        "device": "FR",
        "format": "{0:0d}",
        "formats": None,
        "end": "SET",
        "help": "sets the number of steps used for backlash compensation (0-99)",
    },
    "SETHOS": {
        "cmd": "set home on start",
        "device": "FR",
        "format": "{0:d}",
        "formats": None,
        "end": "SET",
        "help": "set whether the device is homed upon starting",
    },
    "SETLED": {
        "cmd": "set LED brightness",
        "device": "H",
        "format": "{0:02d}",
        "formats": None,
        "end": "SET",
        "help": "sets the hub LED brightness in percent (0=off)",
    },
    "SETDNN": {
        "cmd": "set device nickname",
        "device": "FR",
        "format": "{0:16s}",
        "formats": None,
        "end": "SET",
        "help": "set the device nickname",
    },
    "SETTCE": {
        "cmd": "set temperature compensation enabled",
        "device": "F",
        "format": "{0:d}",
        "formats": None,
        "end": "SET",
        "help": "set whether the temparature compensation should be used (0=no,1=yes)",
    },
    "SETTCM": {
        "cmd": "set active temperature compensation mode",
        "device": "F",
        "format": "{0}",
        "formats": None,
        "end": "SET",
        "help": "set the temparature compensation model (A|B|C|D|E)",
    },
    "SETTCC": {
        "cmd": "set temperature compensation coefficient",
        "device": "F",
        "format": "{0}{1:+04d}",
        "formats": None,
        "end": "SET",
        "help": "set the temparature compensation coefficent by #,value",
    },
    "SETTCS": {
        "cmd": "set temperature compensation at start enabled",
        "device": "F",
        "format": "{0:d}",
        "formats": None,
        "end": "SET",
        "help": "set whether to use temparature compensation at start",
    },
    "SETREV": {
        "cmd": "set reverse property",
        "device": "R",
        "format": "{0:d}",
        "formats": None,
        "end": "SET",
        "help": "set whether to reverse all angles",
    },
}


# NOTE: ALL INTERNAL AND WIFI COMMANDS MISSING FROM THIS LIST!


def gemini_cmd(cmd: str, *args: Any, dev: str = "", trans_id: int = 0) -> str:
    """
    Constructs a GEMINI command for the device 'F' (focuser),
    'R' (rotator), or 'H' (hub) for an assumed device ID=1, the
    transaction ID, and whatever input args are needed.
    """
    if cmd not in gemini_commands:
        raise ValueError("{0} is not a GEMINI command!".format(cmd))
    info = gemini_commands[cmd]
    if dev not in info["device"]:
        raise ValueError("{0} does not match GEMINI device {1}".format(dev, info["device"]))

    s1 = "<{0}1{1:02d}".format(dev, trans_id)
    s2 = cmd
    if info["format"] is None:
        s3 = ""
    else:
        try:
            s3 = info["format"].format(*args)
        except:
            raise ValueError("format {0} does not work for {1}".format(info["format"], cmd))
    s4 = ">"
    return s1 + s2 + s3 + s4


def gemini_parse_output(cmd: str, raw_response: List[bytes]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Parse the raw GEMINI output, a list of bytearray's from serial.readline,
    into a dictionary.
    Raises ValueError if the response is an error message or unparseable.
    """

    # check
    if cmd not in gemini_commands:
        raise ValueError(f"{cmd} is not a GEMINI command!")
    info = gemini_commands[cmd]
    if "end" not in info:
        raise IndexError("ending is not in info!")
    results: Dict[str, Any] = {}

    # decode and remove newlines
    try:
        rsp = [r.decode("utf8").replace("\\n", "") for r in raw_response]
    except UnicodeDecodeError:
        raise ValueError("{0!r} cannot be decoded".format(raw_response))

    # check for syntax
    if len(rsp) == 0 or not rsp[0].strip().startswith("!"):
        raise ValueError("unable to parse GEMINI response " + str(raw_response))

    # get transaction ID
    try:
        trans_id = int(rsp[0].strip()[1:])
    except:
        trans_id = None
    results["trans_id"] = trans_id
    errors = {}

    # get any output fields
    if info["formats"] is not None:
        # loop lines of output
        for r in rsp[1:-1]:
            rkey, rdata = r.split("=")
            if rkey.startswith("ERROR"):
                err = rkey[6:].strip()
                errors[err] = rdata.strip()
            else:
                # loop expected lines of output
                for f in info["formats"]:
                    pkey, pfmt = f.split("=")
                    if pkey == rkey:
                        key = pkey.strip()
                        try:
                            if "f" in pfmt:
                                # float
                                results[key] = float(rdata)
                            elif "d" in pfmt:
                                # integer
                                results[key] = int(rdata)
                            else:
                                # string
                                results[key] = rdata.strip()
                            break
                        except ValueError:
                            errors[key] = rdata
    return results, errors
