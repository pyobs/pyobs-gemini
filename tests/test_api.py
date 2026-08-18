"""Unit tests for the pure GEMINI command/response API (no serial I/O)."""

import pytest

from pyobs_gemini.api import gemini_cmd, gemini_parse_output


def test_cmd_without_args() -> None:
    assert gemini_cmd("DOHOME", dev="F", trans_id=1) == "<F101DOHOME>"


def test_cmd_with_arg() -> None:
    assert gemini_cmd("MOVABS", 1000, dev="F", trans_id=1) == "<F101MOVABS1000>"


def test_cmd_padded_arg() -> None:
    assert gemini_cmd("SETREV", 1, dev="R", trans_id=5) == "<R105SETREV1>"


def test_cmd_unknown_command() -> None:
    with pytest.raises(ValueError):
        gemini_cmd("NOPE", dev="F")


def test_cmd_wrong_device() -> None:
    with pytest.raises(ValueError):
        gemini_cmd("MOVABS", 1000, dev="R")


def test_parse_getsta() -> None:
    raw = [b"!01\n", b"CurentPA = 180000\n", b"CurrStep = 12345\n", b"END\n"]
    results, errors = gemini_parse_output("GETSTA", raw)
    assert errors == {}
    assert results["trans_id"] == 1
    assert results["CurentPA"] == 180000
    assert results["CurrStep"] == 12345


def test_parse_bad_response() -> None:
    with pytest.raises(ValueError):
        gemini_parse_output("GETSTA", [b"not a gemini response\n"])


def test_parse_unknown_command() -> None:
    with pytest.raises(ValueError):
        gemini_parse_output("NOPE", [b"!01\n"])
