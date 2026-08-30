"""BLE-Verbindung und Zustandsführung für den VOLTA."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from homeassistant.core import HomeAssistant, callback

from . import protocol as p
from .const import CONNECT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class VoltaCoordinator:
    """Hält die BLE-Verbindung und den zusammengeführten Gerätezustand.

    ``DEVICE_PARAMETER`` überschreibt immer alle Felder auf einmal. Deshalb wird
    hier aus Telemetrie und Gerätestatus ein vollständiger Parametersatz gepflegt,
    in den einzelne Änderungen hineingemischt werden - genauso wie es die
    Original-App macht.

    Temperaturen laufen intern durchgehend in Zehntelgrad; nur die Properties
    am Ende geben Grad Celsius nach aussen.
    """

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        self.hass = hass
        self.address = address
        self.name = name

        self.telemetry: p.Telemetry | None = None
        self.device_state: p.DeviceState | None = None
        self.available = False
        self.software_revision: str | None = None

        self._client: BleakClient | None = None
        self._supports_f = True
        self._params: p.DeviceParameter | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        # Ein Kommando überschreibt alle Felder, und die verteilen sich auf beide
        # Pakete. Vor dem ersten Senden müssen deshalb beide eingetroffen sein.
        self._state_complete = asyncio.Event()

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Entity für Zustandsänderungen registrieren."""
        self._listeners.append(update_callback)

        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def _notify_listeners(self) -> None:
        for update_callback in self._listeners:
            update_callback()

    async def async_connect(self, ble_device: BLEDevice) -> None:
        """Verbinden, Firmware-Stand lesen und Notifications abonnieren."""
        async with self._lock:
            if self._client is not None and self._client.is_connected:
                return

            self._client = await establish_connection(
                BleakClient,
                ble_device,
                self.name,
                self._on_disconnect,
                timeout=CONNECT_TIMEOUT,
            )
            await self._read_software_revision()
            await self._client.start_notify(p.CHAR_UUID, self._on_notify)
            self.available = True
            _LOGGER.debug("%s: verbunden, Notifications aktiv", self.name)

        # Das Gerät pusht Telemetrie und Status von selbst; ohne beide kennen wir
        # den Parametersatz nicht und dürfen nichts senden.
        try:
            await asyncio.wait_for(self._state_complete.wait(), timeout=15)
        except TimeoutError:
            _LOGGER.warning(
                "%s: unvollständiger Zustand nach dem Verbinden "
                "(Telemetrie=%s, Status=%s) - Steuerung bleibt gesperrt",
                self.name,
                self.telemetry is not None,
                self.device_state is not None,
            )

    async def _read_software_revision(self) -> None:
        """Firmware-Stand bestimmt, ob Temperaturen in Zehntelgrad übertragen werden."""
        try:
            raw = await self._client.read_gatt_char(p.SOFTWARE_REV_CHAR)
            self.software_revision = raw.decode("utf-8", errors="replace").strip()
        except Exception as err:
            # Das Original nimmt in diesem Fall die alte Ganzgrad-Variante an.
            _LOGGER.debug("%s: Software-Revision nicht lesbar (%s)", self.name, err)
            self.software_revision = None

        self._supports_f = p.supports_deci(self.software_revision)
        _LOGGER.debug(
            "%s: Firmware %s -> Zehntelgrad=%s",
            self.name,
            self.software_revision or "(unbekannt)",
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
        _LOGGER.debug("%s: Verbindung getrennt", self.name)
        self.available = False
        self._client = None
        # Nach dem Reconnect erst wieder senden, wenn beide Pakete neu da sind.
        self._state_complete.clear()
        self._params = None
        self._notify_listeners()

    @callback
    def _on_notify(self, _sender, data: bytearray) -> None:
        """Eingehendes Paket verarbeiten und in den Parametersatz spiegeln."""
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

        else:
            # Preset-, Namens- und WLAN-Pakete interessieren die Entities nicht.
            return

        if self.telemetry is not None and self.device_state is not None:
            self._params = p.params_from_state(self.telemetry, self.device_state)
            self._state_complete.set()

        self.available = True
        self._notify_listeners()

    async def _write(self, frame: bytes) -> None:
        if self._client is None or not self._client.is_connected:
            raise RuntimeError("nicht verbunden")
        await self._client.write_gatt_char(p.CHAR_UUID, frame, response=False)

    async def async_send_params(self, **changes) -> None:
        """Parametersatz mit Änderungen überschreiben und senden.

        Temperaturen in ``changes`` sind Zehntelgrad.
        """
        if self._params is None:
            raise RuntimeError(
                "Parametersatz unvollständig - Telemetrie und Gerätestatus fehlen noch"
            )

        async with self._lock:
            params = replace(self._params, **changes)
            await self._write(params.encode(self._supports_f))
            self._params = params

    async def async_set_target_temperature(self, celsius: float) -> None:
        await self.async_send_params(top_temp=p.celsius_to_deci(celsius))

    async def async_start_heating(self, **changes) -> None:
        """Heizen starten.

        Bei einem Presetwechsel verlangt das Gerät zwei Frames: erst die Auswahl
        mit ``heat_control=0``, dann nach kurzer Pause der eigentliche Start.
        Ein einzelner Frame wird verworfen.
        """
        if self._params is None:
            raise RuntimeError(
                "Parametersatz unvollständig - Telemetrie und Gerätestatus fehlen noch"
            )

        # heat_control und pause_state bestimmt diese Methode selbst.
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

    async def async_skip_stage(self) -> None:
        async with self._lock:
            await self._write(p.encode_skip_stage())

    @property
    def current_temperature(self) -> float | None:
        """Ist-Temperatur in °C. Messwerte kommen in Ganzgrad.

        Bevorzugt die Temperatur oben; fällt sonst auf die Seitentemperatur zurück.
        """
        if self.device_state is not None and self.device_state.real_top_temp:
            return float(self.device_state.real_top_temp)
        if self.telemetry is not None and self.telemetry.real_side_temp is not None:
            return float(self.telemetry.real_side_temp)
        return None

    @property
    def target_temperature(self) -> float | None:
        """Zieltemperatur in °C."""
        return self.telemetry.set_temp_c if self.telemetry else None

    @property
    def is_heating(self) -> bool:
        return bool(self.telemetry and self.telemetry.start_heating)
