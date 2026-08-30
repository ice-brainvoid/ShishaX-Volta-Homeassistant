# ShishaX VOLTA for Home Assistant

Local control of the [ShishaX VOLTA](https://shishax.com/collections/volta) hookah
heater over Bluetooth Low Energy. No vendor cloud, no account, no internet
connection required.

The BLE protocol was reconstructed from the official web app's JavaScript bundle
and verified against a real device. The full protocol reference is in
[VOLTA-BLE-PROTOCOL.md](VOLTA-BLE-PROTOCOL.md).

> **Project status — please read before installing.**
> Reading and writing are both confirmed against real hardware. Telemetry,
> presets and temperatures decode correctly, and every control has been
> exercised on a device: target temperature, preset selection, start, stop,
> pause, resume, boost, skip stage, head vibration and light mode. This
> integration controls an appliance that reaches 320 °C / 608 °F — the plate went
> from 29 °C to 279 °C in about a minute during testing. Do not leave it
> unattended while you are testing the controls for the first time.
> See [Safety](#safety).

---

## Why Bluetooth and not a local HTTP API

There isn't one. The ShishaX web app does not talk to the VOLTA over your
network — it connects directly via Web Bluetooth. The device can join your Wi-Fi,
but it opens no listening ports; it only makes an outbound connection to the
vendor's cloud. BLE is therefore the only local path, and this integration takes
it.

## Requirements

- Home Assistant **2024.8** or newer
- A Bluetooth adapter that Home Assistant can reach the VOLTA with

The second point is the one that trips people up. Home Assistant needs to be
within BLE range of the device — roughly the same room, or one wall away.

| Your setup | What you need |
|---|---|
| HA on hardware with built-in Bluetooth, near the hookah | Nothing extra |
| HA too far away, or in a VM / container without Bluetooth passthrough | An [ESPHome Bluetooth Proxy](#setting-up-a-bluetooth-proxy) |

**Shelly devices cannot be used as a proxy.** Shelly Gen2 firmware forwards BLE
*advertisements* only. This integration needs a real, connectable GATT session,
which in Home Assistant only ESPHome proxies provide.

## Installation

### Via HACS (recommended)

1. In Home Assistant, go to **HACS → Integrations**
2. Open the three-dot menu in the top right → **Custom repositories**
3. Add `https://github.com/ice-brainvoid/ShishaX-Volta-Homeassistant`
   with category **Integration**
4. Find **ShishaX VOLTA** in the list and click **Download**
5. Restart Home Assistant

### Manually

Copy the `custom_components/volta` folder into your Home Assistant `config`
directory, so that you end up with `config/custom_components/volta/`, then
restart Home Assistant.

## Setup

With the VOLTA powered on and in range, Home Assistant should discover it
automatically and offer it under **Settings → Devices & Services**.

If it does not appear:

1. **Settings → Devices & Services → Add Integration**
2. Search for **ShishaX VOLTA**
3. Pick your device from the list

The device advertises as `VOLTA_XXXXXXXX`, where the suffix is the second half of
its MAC address. No pairing code and no ShishaX account are needed — the account
only exists for the vendor cloud, which this integration does not use.

## Entities

| Entity | Type | Notes |
|---|---|---|
| Heater | `climate` | Target temperature 200–320 °C, heat on/off |
| Pause | `switch` | On means the session is held; off resumes it |
| Vibration | `number` | Strength of the head's vibration, 0–5 |
| Light mode | `select` | Off, breathing, colour cycle, white, blue, green, purple, yellow, orange, red |
| Hold time | `number` | 30–120 minutes |
| Boost | `button` | Raises the boost counter by one |
| Skip stage | `button` | Advances to the next stage of the preset curve |
| Battery | `sensor` | Percent |
| Top temperature | `sensor` | Measured, °C |
| Side temperature | `sensor` | Measured, °C |
| Target side temperature | `sensor` | Diagnostic |
| Runtime | `sensor` | Seconds; counts only while heating |
| Preset slot | `sensor` | Diagnostic |
| Temperature reached | `binary_sensor` | Only meaningful while heating |
| Wi-Fi | `binary_sensor` | Whether the device itself is on Wi-Fi |

Turning the pause switch **off** resumes the session, which starts the heater.
It is therefore ignored unless the device really is paused, so an automation
cannot light an idle heater as a side effect.

The `climate` entity reports `heating` while ramping up and `idle` once the
target is reached.

## Setting up a Bluetooth proxy

If Home Assistant cannot see the VOLTA directly, a cheap ESP32 running ESPHome
bridges the gap. Any classic ESP32 works — around €10–15.

> Make sure you buy a **classic ESP32** (ESP32-WROOM-32). An **ESP32-S2 has no
> Bluetooth at all** and will not work. Product titles are easy to confuse.

A ready-to-use configuration is included as
[`esphome/volta-proxy.yaml`](esphome/volta-proxy.yaml). Flash it through the
ESPHome dashboard, fill in your `secrets.yaml`, and plug the board in near the
hookah. Home Assistant picks up the proxy automatically; no change to this
integration is needed.

The important part of that config is:

```yaml
bluetooth_proxy:
  active: true
```

Without `active: true` the proxy only forwards advertisements and this
integration cannot connect.

## Testing without Home Assistant

A standalone command-line tool is included, useful for confirming the device is
reachable and for reporting bugs.

```bash
python3 -m venv venv
./venv/bin/pip install bleak
./venv/bin/python tools/volta_cli.py scan
```

Watch live telemetry:

```bash
./venv/bin/python tools/volta_cli.py monitor
```

Without flags the tool only listens. Each flag changes just its own field and
everything else mirrors the device's current state, so a `--motor` while heating
will not switch the heater off:

```
--set-temp N   target temperature in °C
--preset N     select preset slot 0-14
--motor N      head vibration strength 0-5
--light N      light mode 0-9
--boost        raise the boost counter by one
--pause        pause a running session
--resume       resume from pause
--skip-stage   skip the current heating stage
--start        start heating
--stop         stop heating
```

To inspect the exact bytes that *would* be sent, without sending them:

```bash
./venv/bin/python tools/volta_cli.py monitor --echo --dry-run
```

**On macOS**, run this from Terminal.app or iTerm, not from an editor or an
automated shell. A bare `python` binary has no `NSBluetoothAlwaysUsageDescription`
in its Info.plist, and macOS kills the process with SIGABRT the moment it starts
scanning. Your terminal will prompt for Bluetooth access on first use.

## Device quirks

These caught us out during reverse engineering and are worth knowing if you plan
to contribute.

**Setpoints are in tenths of a degree, measurements are in whole degrees.**
A 290 °C target is `2900` on the wire, but a measured 94 °C is just `94`. Whether
tenths actually reach the wire depends on firmware: from software revision
`20260626` onward, yes; before that the device transmits whole degrees. The
integration reads the revision from GATT characteristic `0x2A28` on connect and
adapts.

**Starting the heater takes two frames.** When the preset slot changes, the
device expects one frame selecting the preset with the heater still off, a ~200 ms
pause, and only then the frame that starts heating. A single combined frame is
silently discarded.

**Every command overwrites the entire parameter set.** There is no way to change
just the target temperature — one `DEVICE_PARAMETER` frame sets every field at
once.

Worse, those fields are split across *two* different status packets. The target
temperature, hold time and preset come from telemetry (opcode 185), but the side
temperature, `presetShow` and the screen-saver flag exist **only** in the device
status packet (opcode 186). Building a command from telemetry alone silently
writes defaults into the other fields — on a real device that quietly moved the
side temperature from 170 °C to 200 °C.

The integration therefore refuses to send anything until **both** packets have
arrived, and rebuilds the full parameter set from both on every update. If you
are writing your own client, this is the mistake to avoid.

**The device has light modes the vendor app hides.** `lightMode` accepts 0–9,
but the official app only offers 0–5. Modes 6 to 9 are purple, yellow, orange
and red — reachable over the protocol, not through the app. The `Light mode`
select offers all ten.

**Commands must be written with response.** A write *without* response is
accepted by the BLE stack without any error and then silently discarded by the
device. It looks like success and does nothing.

**Setting a temperature deselects the preset.** After changing the target
temperature the device reports preset slot 0, whatever slot was active before —
a manual temperature overrides the preset curve, and the official app treats it
the same way. Expect the `Preset slot` sensor to drop to 0 after any temperature
change. Selecting a preset on its own works fine: in a frame that leaves the
temperature alone, the device adopts the slot.

## Safety

This integration controls a heating appliance capable of 320 °C. The write path
has not yet been validated against real hardware.

- Do not automate the heater unattended until you have confirmed the controls
  behave correctly on your device
- Keep the heating plate clear while testing
- `tools/volta_cli.py monitor --echo --dry-run` prints a full byte-by-byte
  breakdown of any frame before it is sent — use it first

The authors are not affiliated with ShishaX. Use at your own risk; you may void
your warranty.

## Troubleshooting

**The device is never discovered.** Confirm the VOLTA is powered on — it stops
advertising when off. Then check that Home Assistant actually has a working
Bluetooth adapter under **Settings → Devices & Services → Bluetooth**. In a VM or
container without passthrough there will be none; use a proxy.

**It connects, then drops.** Usually weak signal. Check the RSSI reported by
`tools/volta_cli.py scan`; anything weaker than about −80 dBm is marginal. Move
the proxy closer.

**Entities show as unavailable.** The integration reconnects on its own when the
device starts advertising again. If it stays unavailable, the device is likely
off or out of range.

**Nothing happens when I change the temperature.** The integration will not send
anything until it has received a telemetry packet, because a command overwrites
every field at once. Give it a few seconds after connecting.

## Development

The protocol codec has no Home Assistant dependency and can be tested on its own:

```bash
./venv/bin/pip install pytest
./venv/bin/python -m pytest tests/ -q
```

Several test cases use packet captures from a real device, so they verify the
decoder against reality rather than against itself. Contributions are welcome —
particularly confirmation of the write path on other firmware revisions, and
captures from devices older than revision `20260626`.

## License

MIT — see [LICENSE](LICENSE).
