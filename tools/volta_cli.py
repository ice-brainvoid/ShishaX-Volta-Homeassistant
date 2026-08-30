#!/usr/bin/env python3
"""Standalone tool for exercising the VOLTA protocol - without Home Assistant.

With no flags it only listens. Commands are sent only when a flag asks for
one, and each flag changes just its own field - everything else mirrors the
device's current state. That is deliberate: the device heats to 320 °C, and
an accidental start while debugging would be unpleasant.

    python tools/volta_cli.py scan
    python tools/volta_cli.py monitor
    python tools/volta_cli.py monitor --echo --dry-run
    python tools/volta_cli.py monitor --start --set-temp 280
    python tools/volta_cli.py monitor --motor 3
    python tools/volta_cli.py monitor --pause
    python tools/volta_cli.py monitor --skip-stage
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
            print(f"Device {address} not found.")
        return device

    print(f"Scanning for a VOLTA ({timeout:.0f}s) ...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        if name.startswith(p.NAME_PREFIXES) or p.SERVICE_UUID in [
            u.lower() for u in (adv.service_uuids or [])
        ]:
            print(f"Found: {name}  {device.address}  {adv.rssi} dBm")
            return device
    print("No VOLTA in range.")
    return None


async def cmd_scan(args) -> int:
    return 0 if await find_device(args.address, args.timeout) else 1


async def read_revision(client: BleakClient) -> tuple[str | None, bool]:
    """Read the firmware revision. Decides whether tenths travel on the wire."""
    try:
        raw = await client.read_gatt_char(p.SOFTWARE_REV_CHAR)
        revision = raw.decode("utf-8", errors="replace").strip()
    except Exception as err:
        print(f"Software revision unreadable ({err}) - assuming older whole-degree firmware.")
        return None, False

    supports_f = p.supports_deci(revision)
    print(f"Firmware: {revision}  ->  tenths of a degree on the wire: {supports_f}")
    return revision, supports_f


async def write_frame(client: BleakClient, frame: bytes) -> None:
    """Send a frame, with response first.

    The original app tries writeValue() first, which is a write *with* response.
    A write without response is its last resort and the bundle notes it "may be
    fake-success" - it can report success while the device drops the frame.
    """
    try:
        await client.write_gatt_char(p.CHAR_UUID, frame, response=True)
        print("    (written with response)")
    except Exception as err:
        print(f"    write with response failed: {err}")
        await client.write_gatt_char(p.CHAR_UUID, frame, response=False)
        print("    (written without response - may be silently dropped)")


def _describe_characteristic(client: BleakClient) -> None:
    """Print what the characteristic actually supports, for diagnosis."""
    char = client.services.get_characteristic(p.CHAR_UUID)
    if char is None:
        print("Characteristic not found.")
        return
    print(f"Characteristic properties: {', '.join(char.properties)}")


def _explain_frame(frame: bytes, params: p.DeviceParameter) -> None:
    """Break the frame down byte by byte so it can be checked before sending."""
    labels = [
        "opcode 169",
        f"length {frame[1]}",
        "lightMode",
        "topTemp HIGH",
        "topTemp LOW",
        "sideTemp HIGH",
        "sideTemp LOW",
        "holdTime",
        "presetChoose",
        "heatControl  <<< switches the heater",
        "boostCount",
        "motorLevel",
        "audioSwitch",
        "tempUnit",
        "presetShow",
        "screenSaver",
        "pauseState",
        "opcode 169",
    ]
    print(f"    Frame ({len(frame)} bytes): {frame.hex(' ')}")
    for i, (byte, label) in enumerate(zip(frame, labels)):
        print(f"      [{i:>2}] 0x{byte:02x} {byte:>4}   {label}")
    print(
        f"    -> target {p.deci_to_celsius(params.top_temp):.1f}°C, "
        f"side {p.deci_to_celsius(params.side_temp):.1f}°C, "
        f"heating={params.heat_control}, paused={params.pause_state}"
    )


async def cmd_monitor(args) -> int:
    device = await find_device(args.address, args.timeout)
    if device is None:
        return 1

    latest: dict[str, object] = {}
    names: dict[int, list[str]] = {}
    ready = asyncio.Event()

    print(f"Connecting to {device.address} ...")
    async with BleakClient(device) as client:
        _revision, supports_f = await read_revision(client)
        _describe_characteristic(client)

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
                    f"[{_stamp()}] TELEMETRY  battery {t.battery}%  "
                    f"target {t.set_temp_c:.1f}°C  side {t.real_side_temp}°C  "
                    f"preset {t.heat_preset}  runtime {minutes}:{seconds:02d} "
                    f"of {t.set_time}m  "
                    f"heating={t.start_heating} paused={t.pause_state} ready={t.temp_ready}"
                    # These five appear nowhere else, so every byte of an echo
                    # can be checked against the device state.
                    f"  |  light={t.light_mode} boost={t.boost_count} "
                    f"motor={t.motor_level} audio={t.audio_switch} unit={t.temp_unit}"
                )
            elif raw[0] == p.RSP_DEVICE_STATE:
                s = p.decode_device_state(raw, supports_f)
                if s is None:
                    return
                latest["device_state"] = s
                if "telemetry" in latest:
                    ready.set()
                print(
                    f"[{_stamp()}] STATUS     top {s.real_top_temp}°C  "
                    f"side target {s.custom_side_temp_c:.1f}°C  "
                    f"display {s.preset_show}  wifi={s.wifi_connected}"
                    f"  |  screensaver={s.screen_saver}"
                )
            elif raw[0] == p.RSP_PRESET_READ:
                decoded = p.decode_preset(raw, supports_f)
                if decoded is None:
                    return
                slot, temps, times = decoded
                stages = "  ".join(
                    f"{p.deci_to_celsius(t):.0f}°/{m}m" for t, m in zip(temps, times)
                )
                print(f"[{_stamp()}] PRESET {slot:<2}  {stages}")
            elif raw[0] in (p.RSP_OPTION_NAME_1, p.RSP_OPTION_NAME_2):
                decoded = p.decode_option_name(raw)
                if decoded is None:
                    return
                slot, part, text = decoded
                parts = names.setdefault(slot, ["", ""])
                parts[part] = text
                # Only print once both halves have arrived, otherwise it doubles.
                full = "".join(parts)
                if part == 1 and full:
                    print(f"[{_stamp()}] NAME   {slot:<2}  {full!r}")
            elif raw[0] == p.RSP_SIDE_CURVE:
                decoded = p.decode_side_curve(raw)
                if decoded is None:
                    return
                slot, temps = decoded
                stages = "  ".join(f"{p.deci_to_celsius(t):.0f}°" for t in temps)
                print(f"[{_stamp()}] SIDE   {slot:<2}  {stages}")
            elif len(raw) == 1 and raw[0] == p.CMD_DEVICE_PARAMETER:
                # The device echoes the bare command opcode to acknowledge a
                # DEVICE_PARAMETER frame it accepted.
                print(f"[{_stamp()}] ACK        DEVICE_PARAMETER accepted")
            else:
                print(f"[{_stamp()}] 0x{raw[0]:02X} ({raw[0]})  {raw.hex(' ')}")

        await client.start_notify(p.CHAR_UUID, on_notify)
        print("Connected. Waiting for telemetry (Ctrl-C to quit).\n")

        wants_param_frame = any(
            (args.echo, args.start, args.stop, args.set_temp, args.boost,
             args.pause, args.resume, args.preset is not None,
             args.motor is not None, args.light is not None)
        )
        if wants_param_frame:
            try:
                await asyncio.wait_for(ready.wait(), timeout=20)
            except TimeoutError:
                have = ", ".join(latest) or "nothing"
                print(f"State incomplete (have: {have}) - sending nothing.")
                return 1

            t, s = latest["telemetry"], latest["device_state"]
            # Merge both packets. side_temp, preset_show and screen_saver exist
            # only in the device status - built from telemetry alone the frame
            # would silently reset them to defaults.
            params = p.params_from_state(t, s)

            # Every field starts out mirroring the device, so only what is asked
            # for changes. In particular heat_control is touched by --start and
            # --stop alone; otherwise a --motor while heating would switch the
            # heater off as a side effect.
            if args.set_temp:
                params = replace(params, top_temp=p.celsius_to_deci(args.set_temp))
            if args.preset is not None:
                params = replace(params, preset_choose=args.preset)
            if args.motor is not None:
                params = replace(params, motor_level=args.motor)
            if args.light is not None:
                params = replace(params, light_mode=args.light)
            if args.boost:
                # The app increments the counter, capped at the maximum.
                params = replace(
                    params, boost_count=min(params.boost_count + 1, p.BOOST_MAX)
                )
                print(f">>> BOOST: boostCount {t.boost_count} -> {params.boost_count}")
            if args.pause:
                params = replace(params, pause_state=1)
            if args.resume:
                params = replace(params, pause_state=0)
            if args.start:
                params = replace(params, heat_control=1, pause_state=0)
            if args.stop:
                params = replace(params, heat_control=0, pause_state=0)

            if args.echo:
                print(">>> ECHO: unchanged parameter set, nothing is altered")

            frame = params.encode(supports_f)
            _explain_frame(frame, params)

            if args.dry_run:
                print(">>> DRY RUN: nothing sent.")
            else:
                print(f">>> sending {frame.hex(' ')}")
                await write_frame(client, frame)

        if args.skip_stage:
            # A separate opcode, not a DEVICE_PARAMETER frame.
            skip = p.encode_skip_stage()
            print(f">>> SKIP STAGE  {skip.hex(' ')}")
            if args.dry_run:
                print(">>> DRY RUN: nothing sent.")
            else:
                await write_frame(client, skip)

        try:
            while client.is_connected:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="VOLTA BLE tool")
    parser.add_argument("--address", help="BLE address; scans when omitted")
    parser.add_argument("--timeout", type=float, default=12.0)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="scan only")

    monitor = sub.add_parser("monitor", help="connect and listen")
    monitor.add_argument(
        "--set-temp",
        type=int,
        help=f"target temperature in °C, {p.TOP_TEMP_MIN}-{p.TOP_TEMP_MAX}",
    )
    monitor.add_argument(
        "--preset",
        type=int,
        help=f"select preset slot {0}-{p.HEAT_PRESET_MAX}",
    )
    monitor.add_argument(
        "--motor", type=int, help=f"head vibration level 0-{p.MOTOR_LEVEL_MAX}"
    )
    monitor.add_argument(
        "--light", type=int, help=f"light mode 0-{p.LIGHT_MODE_MAX}"
    )
    monitor.add_argument(
        "--boost", action="store_true", help="raise the boost counter by one"
    )
    monitor.add_argument("--pause", action="store_true", help="pause a running session")
    monitor.add_argument("--resume", action="store_true", help="resume from pause")
    monitor.add_argument(
        "--skip-stage", action="store_true", help="skip the current heating stage"
    )
    monitor.add_argument("--start", action="store_true", help="start heating")
    monitor.add_argument("--stop", action="store_true", help="stop heating")
    monitor.add_argument(
        "--echo",
        action="store_true",
        help="send the current parameter set back unchanged (encoder test, no effect)",
    )
    monitor.add_argument(
        "--dry-run",
        action="store_true",
        help="only break the frame down and print it, send nothing",
    )

    args = parser.parse_args()
    handler = {"scan": cmd_scan, "monitor": cmd_monitor}[args.command]
    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
