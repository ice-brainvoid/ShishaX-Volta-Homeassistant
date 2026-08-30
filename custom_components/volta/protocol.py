"""Frame codec for the VOLTA BLE protocol.

Plain Python with no Home Assistant dependency so it can be tested in isolation.
See VOLTA-BLE-PROTOCOL.md for how this was derived from the frontend bundle.

Units — the most common source of mistakes:

* **Setpoints** (target temperature, preset side curves) are held internally in
  **tenths of a degree**. 290 °C is 2900. The bundle calls this "deci" itself.
* **Measurements** (``real_top_temp``, ``real_side_temp``) arrive in **whole
  degrees** and are not scaled.
* Whether tenths also travel on the wire depends on the firmware: ``supports_f``
  is true from software revision 20260626 onwards. Older devices send whole
  degrees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

SERVICE_UUID: Final = "59462f12-9543-9999-12c8-58b459a2712d"
CHAR_UUID: Final = "33333333-2222-2222-1111-111100000000"
DEVICE_INFO_SERVICE: Final = "0000180a-0000-1000-8000-00805f9b34fb"
SOFTWARE_REV_CHAR: Final = "00002a28-0000-1000-8000-00805f9b34fb"
NAME_PREFIXES: Final = ("VOLTA_", "ESP32_")

# From this software revision the device transmits tenths instead of whole degrees.
SUPPORTS_F_FROM: Final = 20260626
LONG_PASSWORD_FROM: Final = "20260826"

# Outgoing (host -> device)
CMD_USER_TEMP_TIME: Final = 161
CMD_DELETE_PRESET: Final = 162
CMD_WIFI_SSID_1: Final = 163
CMD_WIFI_SSID_2: Final = 164
CMD_WIFI_PASSWORD: Final = 165
CMD_OTA_START: Final = 166
CMD_OPTION_NAME_1: Final = 167
CMD_OPTION_NAME_2: Final = 168
CMD_DEVICE_PARAMETER: Final = 169
CMD_SYNC_TIME: Final = 170
CMD_WIFI_SCAN_REQUEST: Final = 172
CMD_WIFI_STATUS_REQUEST: Final = 173
CMD_SIDE_TEMP_WRITE: Final = 193
CMD_SKIP_STAGE: Final = 242
CMD_FACTORY_RESET: Final = 246

# Incoming (device -> host)
RSP_PRESET_READ: Final = 177
RSP_OPTION_NAME_1: Final = 179
RSP_OPTION_NAME_2: Final = 180
RSP_SIDE_CURVE: Final = 181
RSP_WIFI_SCAN: Final = 183
RSP_WIFI_STATUS: Final = 184
RSP_TELEMETRY: Final = 185
RSP_DEVICE_STATE: Final = 186

# Limits in degrees Celsius, as the device validates them itself.
TOP_TEMP_MIN: Final = 200
TOP_TEMP_MAX: Final = 320
SIDE_TEMP_MIN: Final = 100
SIDE_TEMP_MAX: Final = 240
SET_TIME_MIN: Final = 30
SET_TIME_MAX: Final = 120
BOOST_MAX: Final = 12
# The app's UI constant is 5, but the frame builder itself validates 0-9, so
# that is what the device accepts.
LIGHT_MODE_UI_MAX: Final = 5
LIGHT_MODE_MAX: Final = 9
MOTOR_LEVEL_MAX: Final = 5

# Established by trying every value on a real device. Modes 6-9 are reachable
# over the protocol but the vendor app never offers them.
LIGHT_MODES: Final = {
    0: "off",
    1: "breathing",
    2: "color_cycle",
    3: "white",
    4: "blue",
    5: "green",
    6: "purple",
    7: "yellow",
    8: "orange",
    9: "red",
}
LIGHT_MODE_BY_NAME: Final = {name: mode for mode, name in LIGHT_MODES.items()}
HEAT_PRESET_MAX: Final = 14
PRESET_SHOW_MIN: Final = 1
PRESET_SHOW_MAX: Final = 15

# Delay between selecting a preset and starting the heater. The device discards
# the second frame if it arrives too early.
START_SEQUENCE_DELAY: Final = 0.2


def parse_software_revision(revision: str | None) -> int | None:
    """Extract the eight-digit date from the software revision string."""
    if not revision:
        return None
    digits = ""
    for char in str(revision):
        if char.isdigit():
            digits += char
            if len(digits) == 8:
                return int(digits)
        else:
            digits = ""
    return None


def supports_deci(revision: str | None) -> bool:
    """``supportsF`` in the bundle: newer firmware transmits tenths of a degree."""
    parsed = parse_software_revision(revision)
    return parsed is not None and parsed >= SUPPORTS_F_FROM


def celsius_to_deci(celsius: float) -> int:
    return round(celsius * 10)


def deci_to_celsius(deci: int) -> float:
    return deci / 10


def encode_wire_temp(deci: int, supports_f: bool) -> int:
    """Tenths of a degree -> wire value (``Fn`` in the bundle)."""
    return deci if supports_f else (deci + 5) // 10


def decode_wire_temp(wire: int, supports_f: bool) -> int:
    """Wire value -> tenths of a degree (``ur``). Inverse of :func:`encode_wire_temp`."""
    return wire if supports_f else wire * 10


def _be16(value: int) -> tuple[int, int]:
    return (value >> 8) & 0xFF, value & 0xFF


def _check(name: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        raise ValueError(f"{name}={value} outside {low}-{high}")


@dataclass(slots=True)
class DeviceParameter:
    """The central control command. Sets setpoints and starts or stops heating.

    ``top_temp`` and ``side_temp`` are tenths of a degree, as in the original.
    """

    top_temp: int = 2800
    side_temp: int = 2000
    hold_time: int = 60
    preset_choose: int = 0
    heat_control: int = 0
    boost_count: int = 0
    motor_level: int = 0
    light_mode: int = 0
    audio_switch: int = 0
    temp_unit: int = 0
    preset_show: int = 1
    screen_saver: int = 0
    pause_state: int = 0

    def encode(self, supports_f: bool = True) -> bytes:
        _check("top_temp", self.top_temp, TOP_TEMP_MIN * 10, TOP_TEMP_MAX * 10)
        _check("side_temp", self.side_temp, SIDE_TEMP_MIN * 10, SIDE_TEMP_MAX * 10)
        _check("hold_time", self.hold_time, SET_TIME_MIN, SET_TIME_MAX)
        _check("preset_choose", self.preset_choose, 0, 15)
        _check("boost_count", self.boost_count, 0, BOOST_MAX)
        _check("motor_level", self.motor_level, 0, MOTOR_LEVEL_MAX)
        _check("light_mode", self.light_mode, 0, LIGHT_MODE_MAX)
        _check("preset_show", self.preset_show, PRESET_SHOW_MIN, PRESET_SHOW_MAX)
        for name in ("heat_control", "audio_switch", "temp_unit", "screen_saver", "pause_state"):
            value = getattr(self, name)
            if value not in (0, 1):
                raise ValueError(f"{name}={value} must be 0 or 1")

        top_hi, top_lo = _be16(encode_wire_temp(self.top_temp, supports_f))
        side_hi, side_lo = _be16(encode_wire_temp(self.side_temp, supports_f))
        return bytes(
            (
                CMD_DEVICE_PARAMETER,
                18,
                self.light_mode,
                top_hi,
                top_lo,
                side_hi,
                side_lo,
                self.hold_time,
                self.preset_choose,
                self.heat_control,
                self.boost_count,
                self.motor_level,
                self.audio_switch,
                self.temp_unit,
                self.preset_show,
                self.screen_saver,
                self.pause_state,
                CMD_DEVICE_PARAMETER,
            )
        )


@dataclass(slots=True)
class Telemetry:
    """Decoded ``TELEMETRY`` packet (opcode 185)."""

    battery: int
    set_temp: int          # tenths of a degree
    light_mode: int
    set_time: int          # minutes
    elapsed: int           # seconds
    heat_preset: int
    boost_count: int
    start_heating: int
    pause_state: int
    temp_ready: int
    real_side_temp: int | None = None  # whole degrees
    motor_level: int | None = None
    audio_switch: int | None = None
    temp_unit: int | None = None

    @property
    def set_temp_c(self) -> float:
        return deci_to_celsius(self.set_temp)


def decode_telemetry(data: bytes, supports_f: bool = True) -> Telemetry | None:
    """``gD()`` in the bundle. Returns ``None`` when the packet does not match."""
    if len(data) < 16 or data[0] != RSP_TELEMETRY:
        return None

    side = data[3] << 8 | data[4]
    result = Telemetry(
        battery=data[2],
        set_temp=decode_wire_temp(data[5] << 8 | data[6], supports_f),
        light_mode=data[7],
        set_time=data[8],
        elapsed=data[9] << 8 | data[10],
        heat_preset=data[11],
        boost_count=data[12],
        start_heating=data[13] & 1,
        pause_state=data[14] & 1,
        temp_ready=data[15] & 1,
        # Measurement in whole degrees; outside 0-250 the device reports nothing valid.
        real_side_temp=side if 0 <= side <= 250 else None,
    )
    if len(data) >= 19:
        result.motor_level = data[16]
        result.audio_switch = data[17] & 1
        result.temp_unit = data[18] & 1
    return result


@dataclass(slots=True)
class DeviceState:
    """Decoded ``DEVICE_STATE`` packet (opcode 186)."""

    real_top_temp: int       # whole degrees
    custom_side_temp: int    # tenths of a degree
    preset_show: int
    screen_saver: int
    wifi_connected: int

    @property
    def custom_side_temp_c(self) -> float:
        return deci_to_celsius(self.custom_side_temp)


def decode_device_state(data: bytes, supports_f: bool = True) -> DeviceState | None:
    """``uk()`` in the bundle."""
    if len(data) < 9 or data[0] != RSP_DEVICE_STATE:
        return None
    return DeviceState(
        # Measurement, deliberately unscaled in the original.
        real_top_temp=data[2] << 8 | data[3],
        custom_side_temp=decode_wire_temp(data[4] << 8 | data[5], supports_f),
        preset_show=data[6],
        screen_saver=data[7] & 1,
        wifi_connected=data[8] & 1,
    )


def params_from_state(telemetry: Telemetry, device_state: DeviceState) -> DeviceParameter:
    """Build the complete parameter set from both status packets.

    ``DEVICE_PARAMETER`` overwrites every field at once, but those fields are
    split across two packets: ``side_temp``, ``preset_show`` and ``screen_saver``
    exist **only** in ``DEVICE_STATE``. Building the set from telemetry alone
    silently sends defaults for those and reconfigures the device. Both packets
    are therefore required arguments.
    """
    return DeviceParameter(
        top_temp=telemetry.set_temp,
        hold_time=telemetry.set_time,
        preset_choose=telemetry.heat_preset,
        heat_control=telemetry.start_heating,
        pause_state=telemetry.pause_state,
        boost_count=telemetry.boost_count,
        light_mode=telemetry.light_mode,
        motor_level=telemetry.motor_level or 0,
        audio_switch=telemetry.audio_switch or 0,
        temp_unit=telemetry.temp_unit or 0,
        side_temp=device_state.custom_side_temp,
        preset_show=device_state.preset_show,
        screen_saver=device_state.screen_saver,
    )


def decode_preset(
    data: bytes, supports_f: bool = True
) -> tuple[int, list[int], list[int]] | None:
    """``vD()`` in the bundle -> (slot, 5 temperatures in tenths, 5 durations in minutes)."""
    if len(data) < 19 or data[0] != RSP_PRESET_READ:
        return None
    temps = [
        decode_wire_temp(data[3 + i * 2] << 8 | data[4 + i * 2], supports_f) for i in range(5)
    ]
    return data[2], temps, list(data[13:18])


def decode_side_curve(data: bytes) -> tuple[int, list[int]] | None:
    """``bD()`` in the bundle. Knows a long and a short format."""
    if len(data) < 8 or data[0] != RSP_SIDE_CURVE:
        return None
    if (data[1] >= 14 or len(data) >= 14) and len(data) >= 13:
        return data[2], [data[3 + i * 2] << 8 | data[4 + i * 2] for i in range(5)]

    has_slot = data[1] == 9
    offset = 3 if has_slot else 2
    temps = [(data[offset + i] if len(data) > offset + i else 0) * 10 for i in range(5)]
    return (data[2] if has_slot else 0), temps


def decode_option_name(data: bytes) -> tuple[int, int, str] | None:
    """Name fragment -> (slot, part, text). Part 0 is bytes 0-15, part 1 bytes 16-31."""
    if len(data) < 20 or data[0] not in (RSP_OPTION_NAME_1, RSP_OPTION_NAME_2):
        return None
    part = 0 if data[0] == RSP_OPTION_NAME_1 else 1
    text = bytes(data[3:19]).rstrip(b"\x00").decode("utf-8", errors="replace")
    return data[2], part, text


def encode_preset(
    slot: int, temps: list[int], times: list[int], supports_f: bool = True
) -> bytes:
    """``USER_TEMP_TIME`` (161). ``temps`` in tenths of a degree, ``times`` in minutes."""
    _check("slot", slot, 0, HEAT_PRESET_MAX)
    if len(temps) != 5 or len(times) != 5:
        raise ValueError("temps and times each need 5 entries")
    for temp in temps:
        _check("temp", temp, TOP_TEMP_MIN * 10, TOP_TEMP_MAX * 10)
    for minutes in times:
        _check("time", minutes, 0, 120)

    frame = bytearray(19)
    frame[0] = CMD_USER_TEMP_TIME
    frame[1] = 19
    frame[2] = slot
    for i, temp in enumerate(temps):
        frame[3 + i * 2], frame[4 + i * 2] = _be16(encode_wire_temp(temp, supports_f))
    for i, minutes in enumerate(times):
        frame[13 + i] = minutes & 0xFF
    frame[18] = CMD_USER_TEMP_TIME
    return bytes(frame)


def encode_delete_preset(slot: int) -> bytes:
    """``DELETE_PRESET`` (162)."""
    _check("slot", slot, 0, HEAT_PRESET_MAX)
    return bytes((CMD_DELETE_PRESET, 4, slot, CMD_DELETE_PRESET))


def encode_option_name(slot: int, name: str) -> tuple[bytes, bytes]:
    """``OPTION_NAME`` (167/168) - name split across two frames of 16 bytes."""
    _check("slot", slot, 0, HEAT_PRESET_MAX)
    raw = name.encode("utf-8")[:32]

    def part(opcode: int, offset: int) -> bytes:
        frame = bytearray(20)
        frame[0] = opcode
        frame[1] = 20
        frame[2] = slot
        for i in range(16):
            frame[3 + i] = raw[offset + i] if offset + i < len(raw) else 0
        frame[19] = opcode
        return bytes(frame)

    return part(CMD_OPTION_NAME_1, 0), part(CMD_OPTION_NAME_2, 16)


def encode_skip_stage() -> bytes:
    """``SKIP_STAGE`` (242) - skip one heating stage."""
    return bytes((CMD_SKIP_STAGE, 4, 1, CMD_SKIP_STAGE))
