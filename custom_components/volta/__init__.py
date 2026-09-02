"""ShishaX VOLTA - local control over BLE."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
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
    """Set up the entry.

    Setup deliberately does not depend on the device being reachable. The VOLTA
    switches its radio off entirely while asleep, so requiring a connection here
    would fail every time Home Assistant restarts with the hookah switched off,
    and leave the entry needing a manual reload. Instead the entry always comes
    up, entities stay unavailable until a connection exists, and the callback
    and watchdog below connect as soon as the device is back.
    """
    address: str = entry.data[CONF_ADDRESS]
    coordinator = VoltaCoordinator(hass, address, entry.title)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Connect as soon as the device advertises again.
    @callback
    def _on_advertisement(service_info, change) -> None:
        entry.async_create_background_task(
            hass,
            coordinator.async_ensure_connected(service_info.device),
            "volta-connect-on-advertisement",
        )

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

    # One attempt right away, allowed to fail - the device is often asleep.
    entry.async_create_background_task(
        hass, coordinator.async_ensure_connected(), "volta-initial-connect"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: VoltaCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()
    return unloaded
