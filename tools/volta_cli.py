#!/usr/bin/env python3
"""Standalone-Werkzeug zum Prüfen des VOLTA-Protokolls - ohne Home Assistant.

Ohne Argumente wird nur mitgelesen. Steuerbefehle gehen ausschliesslich mit den
Flags --set-temp / --start / --stop raus. Das ist Absicht: das Gerät heizt bis
320 °C, ein versehentlicher Start beim Debuggen wäre unschön.

    python tools/volta_cli.py scan
    python tools/volta_cli.py monitor
    python tools/volta_cli.py monitor --start --set-temp 280
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import pathlib
import sys
from dataclasses import replace
from datetime import datetime

from bleak import BleakClient, BleakScanner

_spec = importlib.util.spec_from_file_location(
    "volta_protocol",
    pathlib.Path(__file__).parent.parent / "custom_components" / "volta" / "protocol.py",
)
p = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = p
_spec.loader.exec_module(p)


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def find_device(address: str | None, timeout: float):
    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        if device is None:
            print(f"Gerät {address} nicht gefunden.")
        return device

    print(f"Suche VOLTA ({timeout:.0f}s) ...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        if name.startswith(p.NAME_PREFIXES) or p.SERVICE_UUID in [
            u.lower() for u in (adv.service_uuids or [])
        ]:
            print(f"Gefunden: {name}  {device.address}  {adv.rssi} dBm")
            return device
    print("Kein VOLTA in Reichweite.")
    return None


async def cmd_scan(args) -> int:
    return 0 if await find_device(args.address, args.timeout) else 1


async def read_revision(client: BleakClient) -> tuple[str | None, bool]:
    """Firmware-Stand lesen. Entscheidet über Zehntelgrad auf dem Draht."""
    try:
        raw = await client.read_gatt_char(p.SOFTWARE_REV_CHAR)
        revision = raw.decode("utf-8", errors="replace").strip()
    except Exception as err:
        print(f"Software-Revision nicht lesbar ({err}) - nehme alte Ganzgrad-Firmware an.")
        return None, False

    supports_f = p.supports_deci(revision)
    print(f"Firmware: {revision}  ->  Zehntelgrad auf dem Draht: {supports_f}")
    return revision, supports_f


def _explain_frame(frame: bytes, params: p.DeviceParameter) -> None:
    """Frame Byte für Byte aufschlüsseln, damit man ihn vor dem Senden prüfen kann."""
    labels = [
        "opcode 169",
        f"länge {frame[1]}",
        "lightMode",
        "topTemp HIGH",
        "topTemp LOW",
        "sideTemp HIGH",
        "sideTemp LOW",
        "holdTime",
        "presetChoose",
        "heatControl  <<< schaltet die Heizung",
        "boostCount",
        "motorLevel",
        "audioSwitch",
        "tempUnit",
        "presetShow",
        "screenSaver",
        "pauseState",
        "opcode 169",
    ]
    print(f"    Frame ({len(frame)} Byte): {frame.hex(' ')}")
    for i, (byte, label) in enumerate(zip(frame, labels)):
        print(f"      [{i:>2}] 0x{byte:02x} {byte:>4}   {label}")
    print(
        f"    -> Ziel {p.deci_to_celsius(params.top_temp):.1f}°C, "
        f"Seite {p.deci_to_celsius(params.side_temp):.1f}°C, "
        f"Heizen={params.heat_control}, Pause={params.pause_state}"
    )


async def cmd_monitor(args) -> int:
    device = await find_device(args.address, args.timeout)
    if device is None:
        return 1

    latest: dict[str, object] = {}
    names: dict[int, list[str]] = {}
    ready = asyncio.Event()

    print(f"Verbinde mit {device.address} ...")
    async with BleakClient(device) as client:
        _revision, supports_f = await read_revision(client)

        def on_notify(_sender, data: bytearray) -> None:
            raw = bytes(data)
            if not raw:
                return

            if raw[0] == p.RSP_TELEMETRY:
                t = p.decode_telemetry(raw, supports_f)
                if t is None:
                    return
                latest["telemetry"] = t
                if "device_state" in latest:
                    ready.set()
                minutes, seconds = divmod(t.elapsed, 60)
                print(
                    f"[{_stamp()}] TELEMETRIE  Akku {t.battery}%  "
                    f"Soll {t.set_temp_c:.1f}°C  Seite {t.real_side_temp}°C  "
                    f"Preset {t.heat_preset}  Laufzeit {minutes}:{seconds:02d} "
                    f"von {t.set_time}m  "
                    f"heizt={t.start_heating} pause={t.pause_state} bereit={t.temp_ready}"
                    # Diese vier stehen sonst nur im Frame - so lässt sich jedes
                    # Byte eines Echos gegen den Gerätezustand prüfen.
                    f"  |  licht={t.light_mode} boost={t.boost_count} "
                    f"motor={t.motor_level} ton={t.audio_switch} einheit={t.temp_unit}"
                )
            elif raw[0] == p.RSP_DEVICE_STATE:
                s = p.decode_device_state(raw, supports_f)
                if s is None:
                    return
                latest["device_state"] = s
                if "telemetry" in latest:
                    ready.set()
                print(
                    f"[{_stamp()}] STATUS      Oben {s.real_top_temp}°C  "
                    f"Seite-Soll {s.custom_side_temp_c:.1f}°C  "
                    f"Anzeige {s.preset_show}  WLAN={s.wifi_connected}"
                    f"  |  bildschirmschoner={s.screen_saver}"
                )
            elif raw[0] == p.RSP_PRESET_READ:
                decoded = p.decode_preset(raw, supports_f)
                if decoded is None:
                    return
                slot, temps, times = decoded
                grad = "  ".join(f"{p.deci_to_celsius(t):.0f}°/{m}m" for t, m in zip(temps, times))
                print(f"[{_stamp()}] PRESET {slot:<2}   {grad}")
            elif raw[0] in (p.RSP_OPTION_NAME_1, p.RSP_OPTION_NAME_2):
                decoded = p.decode_option_name(raw)
                if decoded is None:
                    return
                slot, part, text = decoded
                parts = names.setdefault(slot, ["", ""])
                parts[part] = text
                # Erst ausgeben, wenn beide Hälften da sind - sonst doppelt.
                full = "".join(parts)
                if part == 1 and full:
                    print(f"[{_stamp()}] NAME   {slot:<2}   {full!r}")
            elif raw[0] == p.RSP_SIDE_CURVE:
                decoded = p.decode_side_curve(raw)
                if decoded is None:
                    return
                slot, temps = decoded
                grad = "  ".join(f"{p.deci_to_celsius(t):.0f}°" for t in temps)
                print(f"[{_stamp()}] SEITE  {slot:<2}   {grad}")
            else:
                print(f"[{_stamp()}] 0x{raw[0]:02X} ({raw[0]})  {raw.hex(' ')}")

        await client.start_notify(p.CHAR_UUID, on_notify)
        print("Verbunden. Warte auf Telemetrie (Strg-C beendet).\n")

        if args.echo or args.start or args.stop or args.set_temp:
            try:
                await asyncio.wait_for(ready.wait(), timeout=20)
            except TimeoutError:
                have = ", ".join(latest) or "nichts"
                print(f"Zustand unvollständig (habe: {have}) - sende nichts.")
                return 1

            t, s = latest["telemetry"], latest["device_state"]
            # Beide Pakete zusammenführen. side_temp, preset_show und screen_saver
            # stehen nur im Gerätestatus - allein aus der Telemetrie gebaut würde
            # der Frame sie stillschweigend auf Defaults setzen.
            params = p.params_from_state(t, s)
            if args.set_temp:
                params = replace(params, top_temp=p.celsius_to_deci(args.set_temp))
            if not args.echo:
                # --echo lässt den Heizzustand, wie er ist.
                params = replace(
                    params, heat_control=1 if args.start else 0, pause_state=0
                )
            frame = params.encode(supports_f)
            if args.echo:
                print(">>> ECHO: unveränderter Parametersatz, nichts wird verstellt")
            _explain_frame(frame, params)

            if args.dry_run:
                print(">>> DRY-RUN: nichts gesendet.")
            else:
                print(f">>> sende {frame.hex(' ')}")
                await client.write_gatt_char(p.CHAR_UUID, frame, response=False)

        try:
            while client.is_connected:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="VOLTA BLE-Werkzeug")
    parser.add_argument("--address", help="BLE-Adresse, sonst wird gesucht")
    parser.add_argument("--timeout", type=float, default=12.0)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="nur suchen")

    monitor = sub.add_parser("monitor", help="verbinden und mitlesen")
    monitor.add_argument(
        "--set-temp",
        type=int,
        help=f"Zieltemperatur in °C, {p.TOP_TEMP_MIN}-{p.TOP_TEMP_MAX}",
    )
    monitor.add_argument("--start", action="store_true", help="Heizen starten")
    monitor.add_argument("--stop", action="store_true", help="Heizen stoppen")
    monitor.add_argument(
        "--echo",
        action="store_true",
        help="aktuellen Parametersatz unverändert zurücksenden (Encoder-Test ohne Wirkung)",
    )
    monitor.add_argument(
        "--dry-run",
        action="store_true",
        help="Frame nur aufschlüsseln und anzeigen, nichts senden",
    )

    args = parser.parse_args()
    handler = {"scan": cmd_scan, "monitor": cmd_monitor}[args.command]
    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
