"""Light mode selection.

The app's own interface only offers modes 0-5, but the frame builder validates
0-9, so the higher modes are exposed here too. What each mode looks like is not
documented anywhere in the bundle.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as p
from .const import DOMAIN
from .coordinator import VoltaCoordinator
from .entity import VoltaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VoltaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VoltaLightMode(coordinator)])


class VoltaLightMode(VoltaEntity, SelectEntity):
    _attr_translation_key = "light_mode"
    _attr_options = [str(mode) for mode in range(p.LIGHT_MODE_MAX + 1)]

    def __init__(self, coordinator: VoltaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_light_mode"

    @property
    def current_option(self) -> str | None:
        if self.coordinator.telemetry is None:
            return None
        mode = str(self.coordinator.telemetry.light_mode)
        # A device on an unexpected mode should not make the entity invalid.
        return mode if mode in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_light_mode(int(option))
