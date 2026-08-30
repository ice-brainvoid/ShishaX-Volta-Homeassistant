"""Tests for the VOLTA frame codec.

The packets in ``TestRealDeviceData`` and ``TestGoldenFrame`` come from captures
of a real device. The remaining expected values are taken from the literals in
the frontend bundle - not from this implementation, otherwise the tests would
only be confirming themselves.
"""

import importlib.util
import pathlib
import sys

import pytest

_spec = importlib.util.spec_from_file_location(
    "volta_protocol",
    pathlib.Path(__file__).parent.parent / "custom_components" / "volta" / "protocol.py",
)
p = importlib.util.module_from_spec(_spec)
# dataclass(slots=True) looks the name up in sys.modules, so register before exec.
sys.modules[_spec.name] = p
_spec.loader.exec_module(p)


def frame(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr.replace(" ", ""))


class TestFirmwareDetection:
    def test_date_is_read_from_revision(self):
        assert p.parse_software_revision("20260626") == 20260626
        assert p.parse_software_revision("v20260826-beta") == 20260826

    def test_without_a_date_returns_none(self):
        assert p.parse_software_revision("") is None
        assert p.parse_software_revision(None) is None
        assert p.parse_software_revision("1.2.3") is None

    def test_newer_firmware_supports_tenths(self):
        assert p.supports_deci("20260626") is True
        assert p.supports_deci("20270101") is True

    def test_older_firmware_does_not(self):
        assert p.supports_deci("20260625") is False
        assert p.supports_deci(None) is False


class TestTemperatureScaling:
    def test_tenths_conversion(self):
        assert p.celsius_to_deci(290) == 2900
        assert p.deci_to_celsius(2900) == 290.0
        assert p.deci_to_celsius(2185) == 218.5

    def test_new_firmware_writes_tenths_directly(self):
        assert p.encode_wire_temp(2900, True) == 2900
        assert p.decode_wire_temp(2900, True) == 2900

    def test_old_firmware_writes_whole_degrees(self):
        assert p.encode_wire_temp(2900, False) == 290
        assert p.decode_wire_temp(290, False) == 2900

    @pytest.mark.parametrize("supports_f", [True, False])
    def test_decode_is_the_inverse_of_encode(self, supports_f):
        for celsius in range(200, 321, 10):
            deci = p.celsius_to_deci(celsius)
            assert p.decode_wire_temp(p.encode_wire_temp(deci, supports_f), supports_f) == deci


class TestRealDeviceData:
    """Capture from a real device, firmware transmitting tenths of a degree."""

    def test_preset_slot0_dark_leaf(self):
        raw = frame("b1 13 00 08 84 0b b8 0c 1c 0c 80 0c 80 08 0a 0f 1e 1b b1")
        slot, temps, times = p.decode_preset(raw)
        assert slot == 0
        assert temps == [2180, 3000, 3100, 3200, 3200]
        assert [p.deci_to_celsius(t) for t in temps] == [218, 300, 310, 320, 320]
        assert times == [8, 10, 15, 30, 27]

    def test_preset_slot4(self):
        raw = frame("b1 13 04 0c 80 0b 54 0a 28 09 60 0a 28 08 14 14 0a 0a b1")
        slot, temps, times = p.decode_preset(raw)
        assert slot == 4
        assert [p.deci_to_celsius(t) for t in temps] == [320, 290, 260, 240, 260]
        assert times == [8, 20, 20, 10, 10]

    def test_all_preset_temperatures_are_within_device_limits(self):
        # The strongest evidence that the scaling is right: under the earlier
        # interpretation (times ten) every value would be far out of range.
        raw = frame("b1 13 00 08 84 0b b8 0c 1c 0c 80 0c 80 08 0a 0f 1e 1b b1")
        _, temps, _ = p.decode_preset(raw)
        for deci in temps:
            assert p.TOP_TEMP_MIN <= p.deci_to_celsius(deci) <= p.TOP_TEMP_MAX

    def test_preset_name_is_read(self):
        raw = frame("b3 14 00 44 61 72 6b 20 4c 65 61 66 00 00 00 00 00 00 00 b3")
        assert p.decode_option_name(raw) == (0, 0, "Dark Leaf")

    def test_name_filling_exactly_16_bytes(self):
        raw = frame("b3 14 04 41 6c 20 46 61 68 6b 65 72 20 47 72 61 70 69 6f b3")
        assert p.decode_option_name(raw) == (4, 0, "Al Fahker Grapio")

    def test_empty_second_name_part(self):
        raw = frame("b4 14 00 " + "00 " * 16 + "b4")
        assert p.decode_option_name(raw) == (0, 1, "")

    def test_side_curve_long_format(self):
        raw = frame("b5 0e 00 07 6c 06 40 06 72 06 72 06 40 b5")
        slot, temps = p.decode_side_curve(raw)
        assert slot == 0
        assert temps == [1900, 1600, 1650, 1650, 1600]
        for deci in temps:
            assert p.SIDE_TEMP_MIN <= p.deci_to_celsius(deci) <= p.SIDE_TEMP_MAX

    def test_measurements_are_whole_degrees(self):
        """Evidence from a capture: a cooling device reported 94 °C at the top.

        Read as tenths that would be 9.4 °C - below room temperature and
        therefore impossible. Measurements are consequently not scaled.
        """
        s = p.decode_device_state(frame("ba 09 00 5e 06 a4 05 00 01"))
        assert s.real_top_temp == 94
        assert s.custom_side_temp_c == 170.0  # the setpoint, in tenths

    def test_runtime_counter_freezes_when_not_heating(self):
        # heating=0, elapsed stays put (4080 s = 68:00).
        packet = frame("b9 00 21 00 6d 0b 54 03 1e 0f f0 05 02 00 00 01")
        t = p.decode_telemetry(packet)
        assert t.start_heating == 0
        assert t.elapsed == 4080
        assert t.battery == 33
        assert t.real_side_temp == 109
        assert t.set_temp_c == 290.0

    def test_unknown_opcode_182_is_ignored(self):
        # b6 05 03 e8 b6 appeared in a capture. 0xB6 is 182 and is in none of
        # the bundle's opcode tables - meaning unknown, so it is discarded.
        assert p.decode_device_state(frame("b6 05 03 e8 b6")) is None
        assert p.decode_telemetry(frame("b6 05 03 e8 b6")) is None


class TestParameterMerge:
    """A command overwrites all fields, and they are split across two packets.

    Values from a real capture (firmware 20260817).
    """

    TELEMETRY = frame("b9 00 07 00 66 0b 54 02 1e 00 00 05 00 00 00 00 05 01 00")
    STATUS = frame("ba 09 00 61 06 a4 05 00 01")

    def _params(self):
        t = p.decode_telemetry(self.TELEMETRY)
        s = p.decode_device_state(self.STATUS)
        return p.params_from_state(t, s)

    def test_telemetry_fields_are_carried_over(self):
        params = self._params()
        assert params.top_temp == 2900
        assert params.hold_time == 30
        assert params.preset_choose == 5
        assert params.heat_control == 0
        assert params.motor_level == 5
        assert params.audio_switch == 1
        assert params.light_mode == 2

    def test_device_status_fields_are_carried_over(self):
        """Regression: these three are absent from telemetry.

        Built from telemetry alone the defaults silently ended up in the frame -
        side_temp 2000 instead of 1700 and preset_show 1 instead of 5. A dry run
        against real hardware exposed exactly that.
        """
        params = self._params()
        assert params.side_temp == 1700
        assert params.preset_show == 5
        assert params.screen_saver == 0

    def test_defaults_do_not_reach_the_frame(self):
        default = p.DeviceParameter()
        params = self._params()
        assert params.side_temp != default.side_temp
        assert params.preset_show != default.preset_show

    def test_frame_carries_the_real_values(self):
        f = self._params().encode(supports_f=True)
        assert f[5:7] == frame("06 a4")  # side_temp 1700, not 2000
        assert f[14] == 5                # preset_show 5, not 1

    def test_echo_changes_nothing(self):
        """The parameter set encoded and read back yields the same values."""
        params = self._params()
        f = params.encode(supports_f=True)
        assert f[3:5] == frame("0b 54")   # top_temp 2900
        assert f[7] == 30                 # hold_time
        assert f[8] == 5                  # preset_choose
        assert f[9] == 0                  # heat_control stays off


class TestGoldenFrame:
    """Full comparison against a real device (firmware 20260817).

    Both input packets and the expected frame come from the same capture. Each
    of the 18 bytes was checked individually against what the device reported
    about itself at that moment:

        lightMode 2, target 290.0 °C, side target 170.0 °C, holdTime 30 min,
        preset 5, heating 0, boost 0, motor 5, audio 1, unit °C, display 5,
        screen saver 0, paused 0

    That makes the encoder statically complete: the frame is a faithful echo of
    the device state, so an echo alters nothing.
    """

    TELEMETRY = frame("b9 00 14 00 28 0b 54 02 1e 00 00 05 00 00 00 00 05 01 00")
    STATUS = frame("ba 09 00 27 06 a4 05 00 01")
    EXPECTED = frame("a9 12 02 0b 54 06 a4 1e 05 00 00 05 01 00 05 00 00 a9")

    def test_frame_matches_byte_for_byte(self):
        t = p.decode_telemetry(self.TELEMETRY)
        s = p.decode_device_state(self.STATUS)
        assert p.params_from_state(t, s).encode(supports_f=True) == self.EXPECTED

    def test_decoded_values_match_the_device_readout(self):
        t = p.decode_telemetry(self.TELEMETRY)
        s = p.decode_device_state(self.STATUS)
        assert (t.battery, t.set_temp_c, t.real_side_temp) == (20, 290.0, 40)
        assert (t.light_mode, t.boost_count, t.motor_level) == (2, 0, 5)
        assert (t.audio_switch, t.temp_unit, t.set_time) == (1, 0, 30)
        assert (t.heat_preset, t.start_heating, t.pause_state) == (5, 0, 0)
        assert (s.real_top_temp, s.custom_side_temp_c) == (39, 170.0)
        assert (s.preset_show, s.screen_saver, s.wifi_connected) == (5, 0, 1)


class TestDeviceParameter:
    def test_frame_is_exactly_18_bytes(self):
        # The bundle writes 18 bytes, not 19.
        assert len(p.DeviceParameter().encode()) == 18

    def test_length_byte_equals_total_length(self):
        assert p.DeviceParameter().encode()[1] == 18

    def test_opcode_appears_first_and_last(self):
        f = p.DeviceParameter().encode()
        assert f[0] == f[-1] == 169

    def test_field_positions(self):
        f = p.DeviceParameter(
            top_temp=2900,
            side_temp=2000,
            hold_time=60,
            preset_choose=3,
            heat_control=1,
            boost_count=2,
            motor_level=4,
            light_mode=5,
            audio_switch=1,
            temp_unit=0,
            preset_show=7,
            screen_saver=1,
            pause_state=0,
        ).encode(supports_f=True)
        assert f[2] == 5
        assert f[3:5] == frame("0b 54")   # 2900
        assert f[5:7] == frame("07 d0")   # 2000
        assert f[7] == 60
        assert f[8] == 3
        assert f[9] == 1
        assert f[10] == 2
        assert f[11] == 4
        assert f[12] == 1
        assert f[13] == 0
        assert f[14] == 7
        assert f[15] == 1
        assert f[16] == 0

    def test_light_mode_range_follows_the_frame_builder(self):
        """The builder validates 0-9, not the UI constant of 5."""
        assert p.LIGHT_MODE_MAX == 9
        assert p.LIGHT_MODE_UI_MAX == 5
        assert p.DeviceParameter(light_mode=9).encode()[2] == 9
        with pytest.raises(ValueError):
            p.DeviceParameter(light_mode=10).encode()

    def test_old_firmware_receives_whole_degrees(self):
        f = p.DeviceParameter(top_temp=2900).encode(supports_f=False)
        assert f[3:5] == frame("01 22")  # 290

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"top_temp": 1999},   # below 200 °C
            {"top_temp": 3201},   # above 320 °C
            {"top_temp": 290},    # degrees passed instead of tenths
            {"side_temp": 999},
            {"hold_time": 29},
            {"hold_time": 121},
            {"motor_level": 6},
            {"heat_control": 2},
        ],
    )
    def test_values_outside_the_limits_raise(self, kwargs):
        with pytest.raises(ValueError):
            p.DeviceParameter(**kwargs).encode()


class TestTelemetry:
    def _packet(self, extra=b""):
        return (
            bytes([185, 0, 40])   # opcode, length, battery 40
            + bytes([0, 180])      # real_side_temp 180 °C
            + bytes([0x0B, 0x54])  # set_temp 2900 -> 290 °C
            + bytes([3])           # light_mode
            + bytes([30])          # set_time 30 min
            + bytes([0x0D, 0xE2])  # elapsed 3554 seconds
            + bytes([5])           # heat_preset
            + bytes([2])           # boost_count
            + bytes([1])           # start_heating
            + bytes([0])           # pause_state
            + bytes([1])           # temp_ready
            + extra
        )

    def test_values_match_the_capture(self):
        t = p.decode_telemetry(self._packet())
        assert t.battery == 40
        assert t.set_temp == 2900
        assert t.set_temp_c == 290.0
        assert t.real_side_temp == 180
        assert t.elapsed == 3554     # seconds, not minutes
        assert t.set_time == 30      # minutes
        assert t.heat_preset == 5
        assert t.start_heating == 1
        assert t.temp_ready == 1
        assert t.motor_level is None

    def test_long_packet_yields_extra_fields(self):
        t = p.decode_telemetry(self._packet(bytes([3, 1, 0])))
        assert (t.motor_level, t.audio_switch, t.temp_unit) == (3, 1, 0)

    def test_invalid_side_temperature_is_discarded(self):
        packet = bytearray(self._packet())
        packet[3], packet[4] = 1, 255  # 511, outside 0-250
        assert p.decode_telemetry(bytes(packet)).real_side_temp is None

    def test_wrong_opcode_returns_none(self):
        packet = bytearray(self._packet())
        packet[0] = 186
        assert p.decode_telemetry(bytes(packet)) is None

    def test_too_short_packet_returns_none(self):
        assert p.decode_telemetry(bytes([185, 0, 50])) is None

    def test_flags_mask_bit0_only(self):
        packet = bytearray(self._packet())
        packet[13] = 0xFE
        assert p.decode_telemetry(bytes(packet)).start_heating == 0


class TestDeviceState:
    def test_fields_are_decoded(self):
        # Opcode 186 is 0xBA, not 0xB6.
        # Top 260 °C (whole degrees), side setpoint 1700 tenths = 170 °C
        s = p.decode_device_state(frame("ba 09 01 04 06 a4 05 01 01"))
        assert s.real_top_temp == 260
        assert s.custom_side_temp == 1700
        assert s.custom_side_temp_c == 170.0
        assert s.preset_show == 5
        assert s.wifi_connected == 1

    def test_too_short_returns_none(self):
        assert p.decode_device_state(frame("ba 00 01 2c")) is None


class TestOtherCommands:
    def test_delete_preset_matches_the_bundle_literal(self):
        assert p.encode_delete_preset(3) == bytes([162, 4, 3, 162])

    def test_skip_stage_matches_the_bundle_literal(self):
        # Bundle: new Uint8Array([xt.SKIP_STAGE, 4, 1, xt.SKIP_STAGE]) and its
        # own log line says "sent [F2 04 01 F2]".
        assert p.encode_skip_stage() == frame("f2 04 01 f2")

    def test_preset_frame_is_19_bytes(self):
        f = p.encode_preset(2, [2000, 2200, 2400, 2600, 2800], [10] * 5)
        assert len(f) == 19
        assert f[1] == 19
        assert f[0] == f[18] == 161
        assert f[2] == 2
        assert f[3:5] == frame("07 d0")
        assert list(f[13:18]) == [10] * 5

    def test_preset_round_trip_with_real_values(self):
        # Sending and replying use different opcodes (161 out, 177 in) with an
        # identical layout. The round trip swaps the opcode.
        temps, times = [2180, 3000, 3100, 3200, 3200], [8, 10, 15, 30, 27]
        sent = bytearray(p.encode_preset(0, temps, times))
        sent[0] = sent[18] = p.RSP_PRESET_READ
        assert p.decode_preset(bytes(sent)) == (0, temps, times)

    def test_preset_requires_five_values(self):
        with pytest.raises(ValueError):
            p.encode_preset(0, [2000], [10])

    @staticmethod
    def _as_reply(frame_bytes: bytes, opcode: int) -> bytes:
        """Turn a sent name frame into its reply form (167/168 -> 179/180)."""
        reply = bytearray(frame_bytes)
        reply[0] = reply[19] = opcode
        return bytes(reply)

    def test_option_name_round_trip(self):
        part1, part2 = p.encode_option_name(0, "Dark Leaf")
        assert p.decode_option_name(self._as_reply(part1, p.RSP_OPTION_NAME_1)) == (
            0,
            0,
            "Dark Leaf",
        )
        assert p.decode_option_name(self._as_reply(part2, p.RSP_OPTION_NAME_2)) == (0, 1, "")

    def test_option_name_longer_than_16_bytes(self):
        part1, part2 = p.encode_option_name(1, "A" * 20)
        assert p.decode_option_name(self._as_reply(part1, p.RSP_OPTION_NAME_1)) == (
            1,
            0,
            "A" * 16,
        )
        assert p.decode_option_name(self._as_reply(part2, p.RSP_OPTION_NAME_2)) == (
            1,
            1,
            "A" * 4,
        )

    def test_sent_name_frame_uses_command_opcodes(self):
        part1, part2 = p.encode_option_name(0, "Dark Leaf")
        assert part1[0] == part1[19] == p.CMD_OPTION_NAME_1
        assert part2[0] == part2[19] == p.CMD_OPTION_NAME_2


class TestSideCurve:
    def test_long_format(self):
        packet = bytes([181, 14, 2] + [0, 20, 0, 22, 0, 24, 0, 26, 0, 28])
        slot, temps = p.decode_side_curve(packet)
        assert slot == 2
        assert temps == [20, 22, 24, 26, 28]

    def test_short_format_scales_by_ten(self):
        packet = bytes([181, 9, 1, 20, 22, 24, 26, 28])
        slot, temps = p.decode_side_curve(packet)
        assert slot == 1
        assert temps == [200, 220, 240, 260, 280]
