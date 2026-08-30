"""Tests für den VOLTA-Frame-Codec.

Die Pakete in ``TestEchteGeraetedaten`` stammen aus dem Mitschnitt eines echten
Geräts. Die übrigen Erwartungswerte kommen aus den Literalen im
Frontend-Bundle - nicht aus dieser Implementierung, sonst würde der Test nur
sich selbst bestätigen.
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
# dataclass(slots=True) schlägt den Namen in sys.modules nach, also vor exec eintragen.
sys.modules[_spec.name] = p
_spec.loader.exec_module(p)


def frame(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr.replace(" ", ""))


class TestFirmwareErkennung:
    def test_datum_wird_aus_revision_gelesen(self):
        assert p.parse_software_revision("20260626") == 20260626
        assert p.parse_software_revision("v20260826-beta") == 20260826

    def test_ohne_datum_none(self):
        assert p.parse_software_revision("") is None
        assert p.parse_software_revision(None) is None
        assert p.parse_software_revision("1.2.3") is None

    def test_neuere_firmware_kann_zehntelgrad(self):
        assert p.supports_deci("20260626") is True
        assert p.supports_deci("20270101") is True

    def test_aeltere_firmware_kann_es_nicht(self):
        assert p.supports_deci("20260625") is False
        assert p.supports_deci(None) is False


class TestTempSkalierung:
    def test_zehntelgrad_umrechnung(self):
        assert p.celsius_to_deci(290) == 2900
        assert p.deci_to_celsius(2900) == 290.0
        assert p.deci_to_celsius(2185) == 218.5

    def test_neue_firmware_schreibt_zehntelgrad_direkt(self):
        assert p.encode_wire_temp(2900, True) == 2900
        assert p.decode_wire_temp(2900, True) == 2900

    def test_alte_firmware_schreibt_ganzgrad(self):
        assert p.encode_wire_temp(2900, False) == 290
        assert p.decode_wire_temp(290, False) == 2900

    @pytest.mark.parametrize("supports_f", [True, False])
    def test_decode_ist_invers_zu_encode(self, supports_f):
        for celsius in range(200, 321, 10):
            deci = p.celsius_to_deci(celsius)
            assert p.decode_wire_temp(p.encode_wire_temp(deci, supports_f), supports_f) == deci


class TestEchteGeraetedaten:
    """Mitschnitt eines echten Geräts, Firmware mit Zehntelgrad."""

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

    def test_alle_presettemperaturen_liegen_im_geraetebereich(self):
        # Der stärkste Beleg dafür, dass die Skalierung stimmt: mit der alten
        # Interpretation (mal 10) lägen alle Werte weit ausserhalb.
        raw = frame("b1 13 00 08 84 0b b8 0c 1c 0c 80 0c 80 08 0a 0f 1e 1b b1")
        _, temps, _ = p.decode_preset(raw)
        for deci in temps:
            assert p.TOP_TEMP_MIN <= p.deci_to_celsius(deci) <= p.TOP_TEMP_MAX

    def test_preset_name_wird_gelesen(self):
        raw = frame("b3 14 00 44 61 72 6b 20 4c 65 61 66 00 00 00 00 00 00 00 b3")
        assert p.decode_option_name(raw) == (0, 0, "Dark Leaf")

    def test_name_der_genau_16_byte_fuellt(self):
        raw = frame("b3 14 04 41 6c 20 46 61 68 6b 65 72 20 47 72 61 70 69 6f b3")
        assert p.decode_option_name(raw) == (4, 0, "Al Fahker Grapio")

    def test_leerer_namensteil2(self):
        raw = frame("b4 14 00 " + "00 " * 16 + "b4")
        assert p.decode_option_name(raw) == (0, 1, "")

    def test_seitenkurve_langformat(self):
        raw = frame("b5 0e 00 07 6c 06 40 06 72 06 72 06 40 b5")
        slot, temps = p.decode_side_curve(raw)
        assert slot == 0
        assert temps == [1900, 1600, 1650, 1650, 1600]
        for deci in temps:
            assert p.SIDE_TEMP_MIN <= p.deci_to_celsius(deci) <= p.SIDE_TEMP_MAX

    def test_messwerte_sind_ganzgrad(self):
        """Beleg aus dem Mitschnitt: das abkühlende Gerät meldete oben 94 °C.

        Als Zehntelgrad gelesen wären das 9,4 °C - unter Zimmertemperatur und
        damit ausgeschlossen. Messwerte werden deshalb nicht skaliert.
        """
        s = p.decode_device_state(frame("ba 09 00 5e 06 a4 05 00 01"))
        assert s.real_top_temp == 94
        assert s.custom_side_temp_c == 170.0  # Sollwert dagegen in Zehntelgrad

    def test_telemetrie_zaehler_steht_wenn_nicht_geheizt(self):
        # heizt=0, elapsed bleibt stehen (4080 s = 68:00).
        packet = frame("b9 00 21 00 6d 0b 54 03 1e 0f f0 05 02 00 00 01")
        t = p.decode_telemetry(packet)
        assert t.start_heating == 0
        assert t.elapsed == 4080
        assert t.battery == 33
        assert t.real_side_temp == 109
        assert t.set_temp_c == 290.0

    def test_unbekannter_opcode_182_wird_ignoriert(self):
        # b6 05 03 e8 b6 kam im Mitschnitt vor. 0xB6 ist 182 und steht in keiner
        # Opcode-Tabelle des Bundles - Bedeutung unbekannt, wird verworfen.
        assert p.decode_device_state(frame("b6 05 03 e8 b6")) is None
        assert p.decode_telemetry(frame("b6 05 03 e8 b6")) is None


class TestParameterZusammenfuehrung:
    """Ein Kommando überschreibt alle Felder, die sich auf zwei Pakete verteilen.

    Werte aus einem echten Mitschnitt (Firmware 20260817).
    """

    TELEMETRIE = frame("b9 00 07 00 66 0b 54 02 1e 00 00 05 00 00 00 00 05 01 00")
    STATUS = frame("ba 09 00 61 06 a4 05 00 01")

    def _params(self):
        t = p.decode_telemetry(self.TELEMETRIE)
        s = p.decode_device_state(self.STATUS)
        return p.params_from_state(t, s)

    def test_telemetriefelder_werden_uebernommen(self):
        params = self._params()
        assert params.top_temp == 2900
        assert params.hold_time == 30
        assert params.preset_choose == 5
        assert params.heat_control == 0
        assert params.motor_level == 5
        assert params.audio_switch == 1
        assert params.light_mode == 2

    def test_felder_aus_dem_geraetestatus_werden_uebernommen(self):
        """Regression: diese drei stehen nicht in der Telemetrie.

        Wurden sie nur daraus gebaut, landeten stillschweigend die Defaults im
        Frame - side_temp 2000 statt 1700 und preset_show 1 statt 5. Der
        Dry-Run gegen echte Hardware hat genau das aufgedeckt.
        """
        params = self._params()
        assert params.side_temp == 1700
        assert params.preset_show == 5
        assert params.screen_saver == 0

    def test_defaults_landen_nicht_im_frame(self):
        default = p.DeviceParameter()
        params = self._params()
        assert params.side_temp != default.side_temp
        assert params.preset_show != default.preset_show

    def test_frame_traegt_die_echten_werte(self):
        f = self._params().encode(supports_f=True)
        assert f[5:7] == frame("06 a4")  # side_temp 1700, nicht 2000
        assert f[14] == 5                # preset_show 5, nicht 1

    def test_echo_veraendert_nichts(self):
        """Der Parametersatz kodiert und wieder eingelesen ergibt dasselbe."""
        params = self._params()
        f = params.encode(supports_f=True)
        assert f[3:5] == frame("0b 54")   # top_temp 2900
        assert f[7] == 30                 # hold_time
        assert f[8] == 5                  # preset_choose
        assert f[9] == 0                  # heat_control bleibt aus


class TestGoldenFrame:
    """Kompletter Abgleich gegen ein reales Gerät (Firmware 20260817).

    Die beiden Eingangspakete und der erwartete Frame stammen aus demselben
    Mitschnitt. Jedes der 18 Bytes wurde einzeln gegen das gehalten, was das
    Gerät im selben Moment über sich meldete:

        lightMode 2, Soll 290,0 °C, Seite-Soll 170,0 °C, holdTime 30 min,
        Preset 5, heizt 0, boost 0, motor 5, ton 1, Einheit °C, Anzeige 5,
        Bildschirmschoner 0, Pause 0

    Damit ist der Encoder statisch vollständig belegt: der Frame ist ein
    exaktes Abbild des Gerätezustands, ein Echo verstellt nichts.
    """

    TELEMETRIE = frame("b9 00 14 00 28 0b 54 02 1e 00 00 05 00 00 00 00 05 01 00")
    STATUS = frame("ba 09 00 27 06 a4 05 00 01")
    ERWARTET = frame("a9 12 02 0b 54 06 a4 1e 05 00 00 05 01 00 05 00 00 a9")

    def test_frame_stimmt_byte_fuer_byte(self):
        t = p.decode_telemetry(self.TELEMETRIE)
        s = p.decode_device_state(self.STATUS)
        assert p.params_from_state(t, s).encode(supports_f=True) == self.ERWARTET

    def test_dekodierte_werte_entsprechen_der_geraeteanzeige(self):
        t = p.decode_telemetry(self.TELEMETRIE)
        s = p.decode_device_state(self.STATUS)
        assert (t.battery, t.set_temp_c, t.real_side_temp) == (20, 290.0, 40)
        assert (t.light_mode, t.boost_count, t.motor_level) == (2, 0, 5)
        assert (t.audio_switch, t.temp_unit, t.set_time) == (1, 0, 30)
        assert (t.heat_preset, t.start_heating, t.pause_state) == (5, 0, 0)
        assert (s.real_top_temp, s.custom_side_temp_c) == (39, 170.0)
        assert (s.preset_show, s.screen_saver, s.wifi_connected) == (5, 0, 1)


class TestDeviceParameter:
    def test_frame_ist_genau_18_byte(self):
        # Das Bundle schreibt 18 Byte, nicht 19.
        assert len(p.DeviceParameter().encode()) == 18

    def test_laengenbyte_entspricht_gesamtlaenge(self):
        assert p.DeviceParameter().encode()[1] == 18

    def test_opcode_steht_vorne_und_hinten(self):
        f = p.DeviceParameter().encode()
        assert f[0] == f[-1] == 169

    def test_feldpositionen(self):
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

    def test_alte_firmware_bekommt_ganzgrad(self):
        f = p.DeviceParameter(top_temp=2900).encode(supports_f=False)
        assert f[3:5] == frame("01 22")  # 290

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"top_temp": 1999},   # unter 200 °C
            {"top_temp": 3201},   # über 320 °C
            {"top_temp": 290},    # Grad statt Zehntelgrad übergeben
            {"side_temp": 999},
            {"hold_time": 29},
            {"hold_time": 121},
            {"motor_level": 6},
            {"heat_control": 2},
        ],
    )
    def test_werte_ausserhalb_der_bereiche_fliegen(self, kwargs):
        with pytest.raises(ValueError):
            p.DeviceParameter(**kwargs).encode()


class TestTelemetry:
    def _packet(self, extra=b""):
        return (
            bytes([185, 0, 40])   # opcode, laenge, battery 40
            + bytes([0, 180])      # real_side_temp 180 °C
            + bytes([0x0B, 0x54])  # set_temp 2900 -> 290 °C
            + bytes([3])           # light_mode
            + bytes([30])          # set_time 30 min
            + bytes([0x0D, 0xE2])  # elapsed 3554 Sekunden
            + bytes([5])           # heat_preset
            + bytes([2])           # boost_count
            + bytes([1])           # start_heating
            + bytes([0])           # pause_state
            + bytes([1])           # temp_ready
            + extra
        )

    def test_werte_entsprechen_dem_mitschnitt(self):
        t = p.decode_telemetry(self._packet())
        assert t.battery == 40
        assert t.set_temp == 2900
        assert t.set_temp_c == 290.0
        assert t.real_side_temp == 180
        assert t.elapsed == 3554     # Sekunden, nicht Minuten
        assert t.set_time == 30      # Minuten
        assert t.heat_preset == 5
        assert t.start_heating == 1
        assert t.temp_ready == 1
        assert t.motor_level is None

    def test_langes_paket_liefert_zusatzfelder(self):
        t = p.decode_telemetry(self._packet(bytes([3, 1, 0])))
        assert (t.motor_level, t.audio_switch, t.temp_unit) == (3, 1, 0)

    def test_ungueltige_seitentemperatur_wird_verworfen(self):
        packet = bytearray(self._packet())
        packet[3], packet[4] = 1, 255  # 511, ausserhalb 0-250
        assert p.decode_telemetry(bytes(packet)).real_side_temp is None

    def test_falscher_opcode_gibt_none(self):
        packet = bytearray(self._packet())
        packet[0] = 186
        assert p.decode_telemetry(bytes(packet)) is None

    def test_zu_kurzes_paket_gibt_none(self):
        assert p.decode_telemetry(bytes([185, 0, 50])) is None

    def test_flags_maskieren_nur_bit0(self):
        packet = bytearray(self._packet())
        packet[13] = 0xFE
        assert p.decode_telemetry(bytes(packet)).start_heating == 0


class TestDeviceState:
    def test_dekodiert_felder(self):
        # Opcode 186 ist 0xBA, nicht 0xB6.
        # Oben 260 °C (Ganzgrad), Seiten-Soll 1700 Zehntelgrad = 170 °C
        s = p.decode_device_state(frame("ba 09 01 04 06 a4 05 01 01"))
        assert s.real_top_temp == 260
        assert s.custom_side_temp == 1700
        assert s.custom_side_temp_c == 170.0
        assert s.preset_show == 5
        assert s.wifi_connected == 1

    def test_zu_kurz_gibt_none(self):
        assert p.decode_device_state(frame("ba 00 01 2c")) is None


class TestWeitereKommandos:
    def test_delete_preset_entspricht_bundle_literal(self):
        assert p.encode_delete_preset(3) == bytes([162, 4, 3, 162])

    def test_preset_frame_ist_19_byte(self):
        f = p.encode_preset(2, [2000, 2200, 2400, 2600, 2800], [10] * 5)
        assert len(f) == 19
        assert f[1] == 19
        assert f[0] == f[18] == 161
        assert f[2] == 2
        assert f[3:5] == frame("07 d0")
        assert list(f[13:18]) == [10] * 5

    def test_preset_rundlauf_mit_echten_werten(self):
        # Senden und Antworten nutzen verschiedene Opcodes (161 raus, 177 rein),
        # bei identischem Aufbau. Für den Rundlauf wird der Opcode getauscht.
        temps, times = [2180, 3000, 3100, 3200, 3200], [8, 10, 15, 30, 27]
        sent = bytearray(p.encode_preset(0, temps, times))
        sent[0] = sent[18] = p.RSP_PRESET_READ
        assert p.decode_preset(bytes(sent)) == (0, temps, times)

    def test_preset_braucht_fuenf_werte(self):
        with pytest.raises(ValueError):
            p.encode_preset(0, [2000], [10])

    @staticmethod
    def _as_reply(frame_bytes: bytes, opcode: int) -> bytes:
        """Gesendeten Namensframe in die Antwortform bringen (167/168 -> 179/180)."""
        reply = bytearray(frame_bytes)
        reply[0] = reply[19] = opcode
        return bytes(reply)

    def test_option_name_rundlauf(self):
        part1, part2 = p.encode_option_name(0, "Dark Leaf")
        assert p.decode_option_name(self._as_reply(part1, p.RSP_OPTION_NAME_1)) == (
            0,
            0,
            "Dark Leaf",
        )
        assert p.decode_option_name(self._as_reply(part2, p.RSP_OPTION_NAME_2)) == (0, 1, "")

    def test_option_name_ueber_16_byte(self):
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

    def test_gesendeter_namensframe_nutzt_kommando_opcodes(self):
        part1, part2 = p.encode_option_name(0, "Dark Leaf")
        assert part1[0] == part1[19] == p.CMD_OPTION_NAME_1
        assert part2[0] == part2[19] == p.CMD_OPTION_NAME_2
