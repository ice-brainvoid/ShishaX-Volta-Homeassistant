"""Pause switch for a running session."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VoltaCoordinator
from .entity import VoltaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VoltaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VoltaPause(coordinator)])


class VoltaPause(VoltaEntity, SwitchEntity):
    """On means the session is paused.

    Pausing switches the heater off and sets the paused flag; resuming switches
    it back on. Because resuming starts the heater, turning this off is ignored
    unless the device really is paused - otherwise an automation could light the
    heater on an idle device as a side effect.
    """

    _attr_translation_key = "paused"

    def __init__(self, coordinator: VoltaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_pause"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.telemetry is None:
            return None
        return self.coordinator.is_paused

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_pause()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self.coordinator.is_paused:
            _LOGGER.debug(
                "%s: not paused, ignoring resume so the heater is not started",
                self.coordinator.name,
            )
            return
        await self.coordinator.async_resume()
