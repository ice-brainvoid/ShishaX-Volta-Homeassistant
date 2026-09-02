"""BLE connection and state handling for the VOLTA."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from . import protocol as p
from .const import CONNECT_ATTEMPTS, CONNECT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class VoltaCoordinator:
    """Holds the BLE connection and the merged device state.

    ``DEVICE_PARAMETER`` always overwrites every field at once. This class
    therefore maintains a complete parameter set built from telemetry and device
    status, into which individual changes are merged - exactly what the original
    app does.

    Temperatures are handled in tenths of a degree throughout; only the
    properties at the bottom expose degrees Celsius.
    """

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        self.hass = hass
        self.address = address
        self.name = name

        self.telemetry: p.Telemetry | None = None
        self.device_state: p.DeviceState | None = None
        # slot -> per-stage durations in minutes, collected from the preset
        # packets the device pushes after connecting. Needed to work out which
        # stage is running, since the device reports no stage of its own.
        self.preset_times: dict[int, list[int]] = {}
        self.available = False
        self.software_revision: str | None = None

        self._client: BleakClient | None = None
        self._supports_f = True
        self._params: p.DeviceParameter | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        # Guards against the watchdog and the Bluetooth callback both
        # starting an attempt; a connect can take up to CONNECT_TIMEOUT.
        self._connecting = False
        # One command overwrites every field, and those fields are split across
        # both packets. Both must have arrived before anything may be sent.
        self._state_complete = asyncio.Event()

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity for state changes."""
        self._listeners.append(update_callback)

        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def _notify_listeners(self) -> None:
        for update_callback in self._listeners:
            update_callback()

    async def async_connect(self, ble_device: BLEDevice) -> None:
        """Connect, read the firmware revision and subscribe to notifications."""
        async with self._lock:
            if self._client is not None and self._client.is_connected:
                return

            self._client = await establish_connection(
                BleakClient,
                ble_device,
                self.name,
                self._on_disconnect,
                timeout=CONNECT_TIMEOUT,
                max_attempts=CONNECT_ATTEMPTS,
            )
            await self._read_software_revision()
            await self._client.start_notify(p.CHAR_UUID, self._on_notify)
            self.available = True
            _LOGGER.debug("%s: connected, notifications active", self.name)

        # The device pushes telemetry and status on its own; without both we do
        # not know the parameter set and must not send anything.
        try:
            await asyncio.wait_for(self._state_complete.wait(), timeout=15)
        except TimeoutError:
            _LOGGER.warning(
                "%s: incomplete state after connecting (telemetry=%s, status=%s) - "
                "control stays locked",
                self.name,
                self.telemetry is not None,
                self.device_state is not None,
            )

    async def async_ensure_connected(self, ble_device: BLEDevice | None = None) -> bool:
        """Connect unless a live connection already exists.

        Safe to call as often as wanted - both the Bluetooth callback and the
        watchdog use this. Returns whether a connection is up afterwards.
        """
        # A dropped link does not always reach the disconnect callback, so
        # verify rather than trusting the flag.
        if self._client is not None and not self._client.is_connected:
            self.available = False

        if self.available or self._connecting:
            return self.available

        if ble_device is None:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address.upper(), connectable=True
            )
        if ble_device is None:
            _LOGGER.debug("%s: not in range, nothing to connect to", self.name)
            return False

        self._connecting = True
        try:
            await self.async_connect(ble_device)
        except Exception as err:
            _LOGGER.debug("%s: connection attempt failed: %s", self.name, err)
        finally:
            self._connecting = False
        return self.available

    async def _read_software_revision(self) -> None:
        """The firmware revision decides whether temperatures travel as tenths."""
        try:
            raw = await self._client.read_gatt_char(p.SOFTWARE_REV_CHAR)
            self.software_revision = raw.decode("utf-8", errors="replace").strip()
        except Exception as err:
            # The original assumes the older whole-degree variant in this case.
            _LOGGER.debug("%s: software revision unreadable (%s)", self.name, err)
            self.software_revision = None

        self._supports_f = p.supports_deci(self.software_revision)
        _LOGGER.debug(
            "%s: firmware %s -> tenths of a degree=%s",
            self.name,
            self.software_revision or "(unknown)",
            self._supports_f,
        )

    async def async_disconnect(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.disconnect()
                self._client = None
            self.available = False

    @callback
    def _on_disconnect(self, _client: BleakClient) -> None:
        _LOGGER.debug("%s: disconnected", self.name)
        self.available = False
        self._client = None
        # After a reconnect, only send again once both packets have arrived anew.
        self._state_complete.clear()
        self._params = None
        self._notify_listeners()

    @callback
    def _on_notify(self, _sender, data: bytearray) -> None:
        """Handle an incoming packet and mirror it into the parameter set."""
        raw = bytes(data)
        if not raw:
            return

        if raw[0] == p.RSP_TELEMETRY:
            telemetry = p.decode_telemetry(raw, self._supports_f)
            if telemetry is None:
                return
            self.telemetry = telemetry

        elif raw[0] == p.RSP_DEVICE_STATE:
            state = p.decode_device_state(raw, self._supports_f)
            if state is None:
                return
            self.device_state = state

        elif raw[0] == p.RSP_PRESET_READ:
            decoded = p.decode_preset(raw, self._supports_f)
            if decoded is not None:
                slot, _temps, times = decoded
                self.preset_times[slot] = times
            return

        else:
            # Name and Wi-Fi packets are of no interest to the entities.
            return

        if self.telemetry is not None and self.device_state is not None:
            self._params = p.params_from_state(self.telemetry, self.device_state)
            self._state_complete.set()

        self.available = True
        self._notify_listeners()

    async def _write(self, frame: bytes) -> None:
        if self._client is None or not self._client.is_connected:
            raise RuntimeError("not connected")
        # Write with response first, mirroring the original app. A write without
        # response can report success while the device silently drops the frame,
        # which is exactly what happened during testing.
        try:
            await self._client.write_gatt_char(p.CHAR_UUID, frame, response=True)
        except Exception as err:
            _LOGGER.debug("%s: write with response failed (%s), retrying without", self.name, err)
            await self._client.write_gatt_char(p.CHAR_UUID, frame, response=False)

    async def async_send_params(self, **changes) -> None:
        """Overwrite the parameter set with changes and send it.

        Temperatures in ``changes`` are tenths of a degree.
        """
        if self._params is None:
            raise RuntimeError("parameter set incomplete - telemetry and device status missing")

        async with self._lock:
            params = replace(self._params, **changes)
            await self._write(params.encode(self._supports_f))
            self._params = params

    async def async_set_target_temperature(self, celsius: float) -> None:
        await self.async_send_params(top_temp=p.celsius_to_deci(celsius))

    async def async_start_heating(self, **changes) -> None:
        """Start heating.

        On a preset change the device requires two frames: first the selection
        with ``heat_control=0``, then after a short pause the actual start. A
        single frame is discarded.
        """
        if self._params is None:
            raise RuntimeError("parameter set incomplete - telemetry and device status missing")

        # heat_control and pause_state are decided by this method itself.
        changes.pop("heat_control", None)
        changes.pop("pause_state", None)
        preset_changed = changes.get("preset_choose", self._params.preset_choose) != (
            self._params.preset_choose
        )

        if preset_changed:
            await self.async_send_params(**changes, heat_control=0, pause_state=0)
            await asyncio.sleep(p.START_SEQUENCE_DELAY)

        await self.async_send_params(**changes, heat_control=1, pause_state=0)

    async def async_stop_heating(self) -> None:
        await self.async_send_params(heat_control=0, pause_state=0)

    async def async_pause(self) -> None:
        """Pause a running session.

        Pausing means heater off *and* the paused flag set. The device ignores
        pauseState while heatControl is still 1. Stopping differs only in
        leaving pauseState at 0, which ends the session instead of holding it.
        """
        await self.async_send_params(heat_control=0, pause_state=1)

    async def async_resume(self) -> None:
        await self.async_send_params(heat_control=1, pause_state=0)

    async def async_set_motor_level(self, level: int) -> None:
        """Set the vibration strength of the head (0-5)."""
        await self.async_send_params(motor_level=level)

    async def async_set_light_mode(self, mode: int) -> None:
        """Set the light mode (0-9)."""
        await self.async_send_params(light_mode=mode)

    async def async_set_hold_time(self, minutes: int) -> None:
        """Set how long a session holds its target temperature (30-120 minutes)."""
        await self.async_send_params(hold_time=minutes)

    @property
    def is_paused(self) -> bool:
        return bool(self.telemetry and self.telemetry.pause_state)

    @property
    def current_stage(self) -> int | None:
        """Stage of the running preset curve, counting from 1."""
        if self.telemetry is None or not self.telemetry.start_heating:
            return None
        times = self.preset_times.get(self.telemetry.heat_preset)
        if not times:
            return None
        return p.current_stage(self.telemetry.elapsed, times)

    @property
    def stage_count(self) -> int | None:
        if self.telemetry is None:
            return None
        times = self.preset_times.get(self.telemetry.heat_preset)
        return len(times) if times else None

    async def async_boost(self) -> None:
        """Raise the boost counter by one, as the app does."""
        if self._params is None:
            raise RuntimeError("parameter set incomplete - telemetry and device status missing")
        await self.async_send_params(
            boost_count=min(self._params.boost_count + 1, p.BOOST_MAX)
        )

    async def async_skip_stage(self) -> None:
        """Skip the current heating stage. A separate opcode, not a parameter frame."""
        async with self._lock:
            await self._write(p.encode_skip_stage())

    @property
    def current_temperature(self) -> float | None:
        """Measured temperature in °C. Measurements arrive in whole degrees.

        Prefers the top temperature and falls back to the side temperature.
        """
        if self.device_state is not None and self.device_state.real_top_temp:
            return float(self.device_state.real_top_temp)
        if self.telemetry is not None and self.telemetry.real_side_temp is not None:
            return float(self.telemetry.real_side_temp)
        return None

    @property
    def target_temperature(self) -> float | None:
        """Target temperature in °C."""
        return self.telemetry.set_temp_c if self.telemetry else None

    @property
    def is_heating(self) -> bool:
        return bool(self.telemetry and self.telemetry.start_heating)
