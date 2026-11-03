"""Modbus framing against the frames verified live on object 190 (2026-11-03).

Loads modbus.py by path so the integration package (and Home Assistant) is not
needed: `python tests/test_modbus.py` or `pytest tests`."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).parents[1] / "custom_components/larnitech/modbus.py"
_spec = importlib.util.spec_from_file_location("larnitech_modbus", _PATH)
modbus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modbus)

READ_REG_6 = bytes.fromhex("010300060001640B")
WRITE_REG_1_35 = bytes.fromhex("010600010023 99D3".replace(" ", ""))


def test_build_matches_live_frames():
    assert modbus.build_request(1, 3, 6, count=1) == READ_REG_6
    assert modbus.build_request(1, 6, 1, values=[35]) == WRITE_REG_1_35


def test_read_answer():
    body = bytes.fromhex("01030200 14".replace(" ", ""))
    assert modbus.parse_answer(READ_REG_6, body + modbus.crc16(body)) == [20]
    # Wrong CRC, other slave, other function: not our answer.
    assert modbus.parse_answer(READ_REG_6, body + b"\x00\x00") is None
    assert modbus.parse_answer(READ_REG_6, b"\x02" + body[1:] + modbus.crc16(b"\x02" + body[1:])) is None
    assert modbus.parse_answer(READ_REG_6, WRITE_REG_1_35) is None


def test_write_echo_with_trailing_zeros():
    assert modbus.parse_answer(WRITE_REG_1_35, WRITE_REG_1_35 + b"\x00\x00") == []
    request = modbus.build_request(1, 16, 10, values=[1, 2])
    head = request[:6]
    assert modbus.parse_answer(request, head + modbus.crc16(head)) == []


def test_exception_answer():
    body = bytes([1, 0x83, 2])
    try:
        modbus.parse_answer(READ_REG_6, body + modbus.crc16(body))
    except modbus.ModbusExceptionResponse as err:
        assert err.code == 2
    else:
        raise AssertionError("exception answer not raised")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
