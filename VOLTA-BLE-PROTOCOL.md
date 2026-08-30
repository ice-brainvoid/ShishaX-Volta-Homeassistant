# VOLTA BLE protocol

Reconstructed from the frontend bundle of https://www.shishax.app
(`/assets/index-CBvDkveJ.js`, retrieved 2026-08-29). Every statement below is
taken from the minified source; the originating function is named each time.

**Verified against real hardware** (a production device, firmware 20260817).
Presets, names, side curves, telemetry and device status all decode correctly.
The evidence that measurements are whole degrees: a cooling device reported a top
temperature of 94 °C — read as tenths that would be 9.4 °C, below room
temperature and therefore impossible.

The **write path is confirmed** as well: a `DEVICE_PARAMETER` frame changing the
target temperature from 290 °C to 280 °C was accepted and applied, and telemetry
reported the new value immediately.

## Transport

| | |
|---|---|
| Method | BLE GATT (Web Bluetooth in the app, `bleak` here) |
| Advertised name | prefix `VOLTA_` or `ESP32_` |
| Service | `59462f12-9543-9999-12c8-58b459a2712d` |
| Characteristic | `33333333-2222-2222-1111-111100000000` (write + notify) |
| Additional | `0000180a` Device Information, `00002a28` Software Revision |

A single characteristic carries both directions. Its properties on a real device
are `read, indicate, write, notify`.

**Write with response.** The app's fallback chain is `writeValue` →
`writeValueWithResponse` → `writeValueWithoutResponse`, and the first of those is
a write *with* response. Sending without response is the last resort, and the
bundle itself notes it "may be fake-success". That is not a theoretical concern:
a frame written without response was accepted by the BLE stack with no error and
then silently dropped by the device. Always write with response.

**There is no local IP interface.** The device can join Wi-Fi, but then it only
speaks outbound to `wss://api.sxbrowser.com/api/ws` and opens no local port.

## Frame layout

```
[0]   opcode
[1]   length
[2..] payload
[n-1] opcode again (trailer / integrity marker)
```

16-bit values are **big-endian** (`v>>8&255, v&255`). There is no checksum.

> The length in `[1]` is not defined identically for every command. Implement
> the frames below from their exact literals rather than deriving a rule.

## Temperature scaling

This is the biggest trap in the protocol, and getting it wrong costs a factor
of 100.

**Setpoints** (target temperature, preset and side curves) are held internally in
**tenths of a degree**. 290 °C is `2900`. The bundle calls this "deci" itself —
its range check throws `topTemp out of deci range 2000-3200`.

**Measurements** (`realTopTemp`, `realSideTemp`) are **whole degrees** and are
not scaled at all.

```js
Hl = t => Math.floor((t + 5) / 10)
Fn = (deci, supportsF) => supportsF ? deci : Hl(deci)     // internal → wire
ur = (wire, supportsF) => supportsF ? wire : wire * 10    // wire → internal
```

`supportsF` is **not** the °C/°F setting. It is a firmware capability, derived
from the Software Revision string (`0x2A28`), which contains a `YYYYMMDD` date:

```js
supportsF = revision >= 20260626
```

- **Newer firmware** (`supportsF = true`): tenths go on the wire unchanged.
  `0x0B54` = 2900 = 290.0 °C. Resolution 0.1 °C.
- **Older firmware**: the wire carries whole degrees; `Fn` divides by ten first.
  Resolution 1 °C.

From revision `20260826` the device also accepts longer Wi-Fi passwords.

The `tempUnit` field (byte 13 in the command, byte 18 in telemetry) is something
else entirely: it only controls whether the **display** shows °C or °F.

## Outgoing commands (host → device)

| Opcode | Name |
|---|---|
| 161 | `USER_TEMP_TIME` |
| 162 | `DELETE_PRESET` |
| 163 / 164 | `WIFI_SSID_PART1` / `PART2` |
| 165 | `WIFI_PASSWORD` |
| 166 | `OTA_START` |
| 167 / 168 | `OPTION_NAME_PART1` / `PART2` |
| 169 | `DEVICE_PARAMETER` — the central control command |
| 170 | `SYNC_TIME` |
| 172 | `WIFI_SCAN_REQUEST` |
| 173 | `WIFI_STATUS_REQUEST` |
| 193 | `SIDE_TEMP_WRITE` |
| 242 | `SKIP_STAGE` |
| 246 | `FACTORY_RESET` |

### 169 `DEVICE_PARAMETER` — 18 bytes

The only command a Home Assistant integration really needs: it sets the target
temperature, duration and preset, and starts or stops heating.

```
[0]  169
[1]  18
[2]  lightMode        0–5
[3]  topTemp   HIGH   } BE16, Fn(deci), valid 2000–3200 (200–320 °C)
[4]  topTemp   LOW    }
[5]  sideTemp  HIGH   } BE16, Fn(deci), valid 1000–2400 (100–240 °C)
[6]  sideTemp  LOW    }
[7]  holdTime         30–120 (minutes)
[8]  presetChoose     0–15
[9]  heatControl      0 = off, 1 = heat
[10] boostCount       0–12
[11] motorLevel       0–5
[12] audioSwitch      0/1
[13] tempUnit         0 = °C, 1 = °F (display only)
[14] presetShow       1–15
[15] screenSaverSwitch 0/1
[16] pauseState       0/1
[17] 169
```

Field positions for `heatControl`, `audioSwitch`, `tempUnit`, `screenSaverSwitch`
and `pauseState` are confirmed by the bundle's own validation loop, which carries
the field names; `holdTime`, `presetChoose`, `boostCount`, `motorLevel` and
`presetShow` by their named range checks.

> **The fields come from two different packets.** `topTemp`, `holdTime`,
> `presetChoose`, `heatControl`, `pauseState`, `boostCount`, `lightMode`,
> `motorLevel`, `audioSwitch` and `tempUnit` are available from `TELEMETRY` (185).
> But `sideTemp`, `presetShow` and `screenSaverSwitch` appear **only** in
> `DEVICE_STATE` (186). Since one frame overwrites everything, a client that
> builds the command from telemetry alone silently sends defaults for those three.
> On a real device this moved the side temperature from 170 °C to 200 °C. Wait for
> both packets before sending anything.

### Acknowledgement

After accepting a `DEVICE_PARAMETER` frame the device sends back a **single
byte, `0xA9`** — the bare command opcode. That is the only positive confirmation
available; a dropped frame produces no reply at all.

### A changed temperature clears the preset

Confirmed on hardware:

| Frame | Result |
|---|---|
| `topTemp` changed, `presetChoose=5` | `heatPreset` becomes **0** — the slot in byte 8 is ignored |
| `topTemp` unchanged, `presetChoose=5` | `heatPreset` becomes **5** — the selection is adopted |

So `presetChoose` works exactly as documented; a manual temperature simply
overrides the preset curve and drops the selection. Selecting a preset therefore
has to happen in a frame that leaves `topTemp` at its current value — which is
automatic for any client that rebuilds the parameter set from the device's own
state before each write.

The original app knows about this. Its post-write verification compares sent
against received for `lightMode`, `heatControl`, `audioSwitch`, `tempUnit`,
`motorLevel`, `boostCount` and `pauseState` — and deliberately leaves out
`presetChoose` and `topTemp`. Do not treat a mismatch on those two as a failure.

### Starting the heater — two stages

The device will not accept a preset change and a heat start in one frame:

1. If `presetChoose` changes: first send `DEVICE_PARAMETER` with the new preset
   and `heatControl=0, pauseState=0`
2. Wait **200 ms**
3. Then send `DEVICE_PARAMETER` with `heatControl=1`

If the preset does not change, step 3 alone is enough.

**Stopping:** a single frame with `heatControl=0, pauseState=0`.

### 161 `USER_TEMP_TIME` — 19 bytes (write a preset)

```
[0] 161 · [1] 19 · [2] slotIndex
[3..12]  5 × BE16 stage temperature (Fn-scaled, deci)
[13..17] 5 × stage duration (1 byte, minutes)
[18] 161
```

### 193 `SIDE_TEMP_WRITE` — 9 bytes

```
[0] 193 · [1] 9 · [2] slotIndex · [3..7] 5 × temperature (1 byte, Fn-scaled) · [8] 193
```

### 162 `DELETE_PRESET` — 4 bytes

```
[0] 162 · [1] 4 · [2] slotIndex · [3] 162
```

### 167 / 168 `OPTION_NAME` — 20 bytes each

Name up to 32 bytes of UTF-8, split across two frames of 16 bytes, zero-padded.

```
[0] 167 or 168 · [1] 20 · [2] slotIndex · [3..18] 16 bytes UTF-8 · [19] opcode
```

### 163 / 164 / 165 Wi-Fi

SSID and password up to 34 bytes of UTF-8 each. The SSID is split across two
frames of 17 bytes (offsets 0 and 17). The password uses one frame; payload is
17 bytes for passwords of 16 bytes or less, otherwise 34. The length in `[1]` is
`payload + 3`.

## Incoming packets (device → host)

Dispatched by `_handleIncoming()` on `packet[0]`.

| Opcode | Name in the app's log | Meaning |
|---|---|---|
| 177 | `PRESET_READ` | preset temperatures and durations |
| 179 / 180 | `OPTION_NAME_*_REPLY` | preset name |
| 181 | `SIDE_CURVE` | side temperature curve |
| 183 | — | Wi-Fi scan result |
| 184 | — | Wi-Fi status |
| 185 | `TELEMETRY` | **main status packet** |
| 186 | `DEVICE_STATE` | measured top temperature, Wi-Fi flag |

Note that outgoing and incoming opcodes differ for the same concept — the host
sends 161/167/168, the device replies with 177/179/180.

### 185 `TELEMETRY` — min. 16 bytes

Source: `gD()`

```
[2]      battery          0–100 (255 = invalid, ignore)
[3..4]   realSideTemp     BE16, whole degrees, valid only within 0–250
[5..6]   setTemp          BE16, ur → tenths
[7]      lightMode
[8]      setTime          minutes
[9..10]  elapsed          BE16, seconds
[11]     heatPreset       0–14
[12]     boostCount
[13] &1  startHeating
[14] &1  pauseState
[15] &1  tempReady        target reached
```

From length ≥ 19 onward:

```
[16]     motorLevel
[17] &1  audioSwitch
[18] &1  tempUnit
```

`elapsed` only counts up while heating and freezes at `startHeating=0`.
`tempReady` stays at `1` after a session ends and is meaningless then — evaluate
it only together with `startHeating=1`.

### 186 `DEVICE_STATE` — min. 9 bytes

Source: `uk()`

```
[2..3]   realTopTemp      BE16, whole degrees (deliberately unscaled)
[4..5]   customSideTemp   BE16, ur → tenths
[6]      presetShow
[7] &1   screenSaverSwitch
[8] &1   wifiConnected
```

> Opcode 186 is **0xBA**, not 0xB6.

### Opcode 182 (0xB6) — undocumented

A real device emits `b6 05 03 e8 b6` immediately before the first telemetry
packet. Opcode 182 appears in none of the bundle's tables and the app does not
evaluate it. Payload is `0x03E8` = 1000. Meaning unknown; discarded.

### 181 `SIDE_CURVE` — two formats

Source: `bD()`

- **Long** (`[1] ≥ 14` or length ≥ 14, and length ≥ 13):
  `[2]` slotIndex, then 5 × BE16 starting at `[3]`
- **Short** (`[1] == 9`): `[2]` slotIndex, then 5 × 1 byte from `[3]`, each × 10

### 177 `PRESET_READ` — min. 19 bytes

Source: `vD()`

```
[2]      slotIndex
[3..12]  5 × BE16 temperature, ur → tenths
[13..17] 5 × duration (minutes)
```

## Value ranges

Degrees Celsius, as the device validates them itself:

```
topTemp     200–320      sideTemp    100–240
holdTime     30–120      boost         0–12
lightMode     0–5        motorLevel    0–5
heatPreset    0–14       presetShow    1–15
```

## Cloud path (alternative, not recommended)

REST `https://api.sxbrowser.com/api`, WebSocket `wss://api.sxbrowser.com/api/ws`.
Authentication: Supabase login (`voevtprqmjkwydnxdmgx.supabase.co`) yields a JWT,
stored in `localStorage` as `sx_cloud_jwt` and sent as `Authorization: Bearer …`.

```
GET    /devices                      device list
POST   /devices                      {device_id, name} — bind device
DELETE /devices/{id}                 unbind
POST   /devices/probe                {device_id} → {online}
POST   /devices/{id}/control         control command
POST   /devices/{id}/query_info      status query
GET    /firmware/info
POST   /ota/trigger                  {device_id}
```

BLE is preferable for Home Assistant: local, no vendor dependency, no JWT
rotation, and nothing breaks when the backend changes.
