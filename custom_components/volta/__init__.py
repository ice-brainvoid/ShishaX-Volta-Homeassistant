"""ShishaX VOLTA - local control over BLE."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, WATCHDOG_INTERVAL
from .coordinator import VoltaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(f"VOLTA {address} not in range")

    coordinator = VoltaCoordinator(hass, address, entry.title)
    try:
        await coordinator.async_connect(ble_device)
    except Exception as err:  # bleak raises very differently depending on the backend
        raise ConfigEntryNotReady(f"Connection to {address} failed: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reconnect as soon as the device advertises again.
    @callback
    def _on_advertisement(service_info, change) -> None:
        hass.async_create_task(coordinator.async_ensure_connected(service_info.device))

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _on_advertisement,
            {"address": address.upper(), "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    # Second path: the callback only fires while the device advertises, so retry
    # on a schedule as well. Costs nothing when already connected or out of range.
    async def _watchdog(_now) -> None:
        await coordinator.async_ensure_connected()

    entry.async_on_unload(async_track_time_interval(hass, _watchdog, WATCHDOG_INTERVAL))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: VoltaCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()
    return unloaded
