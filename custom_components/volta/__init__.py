"""ShishaX VOLTA - local control over BLE."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
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

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _make_reconnect_callback(coordinator),
            {"address": address.upper(), "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )
    return True


def _make_reconnect_callback(coordinator: VoltaCoordinator):
    """Reconnect after a dropped connection as soon as the device advertises again."""

    async def _reconnect(service_info, change) -> None:
        if coordinator.available:
            return
        try:
            await coordinator.async_connect(service_info.device)
        except Exception as err:
            _LOGGER.debug("Reconnect failed: %s", err)

    def _callback(service_info, change) -> None:
        coordinator.hass.async_create_task(_reconnect(service_info, change))

    return _callback


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: VoltaCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()
    return unloaded
