"""One-shot actions: boost and skip stage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VoltaCoordinator
from .entity import VoltaEntity


@dataclass(frozen=True, kw_only=True)
class VoltaButtonDescription(ButtonEntityDescription):
    press: Callable[[VoltaCoordinator], Awaitable[None]]
    # Most buttons need a live connection; reconnecting is the exception.
    needs_connection: bool = True


BUTTONS: tuple[VoltaButtonDescription, ...] = (
    VoltaButtonDescription(
        key="boost",
        translation_key="boost",
        press=lambda c: c.async_boost(),
    ),
    VoltaButtonDescription(
        key="skip_stage",
        translation_key="skip_stage",
        press=lambda c: c.async_skip_stage(),
    ),
    VoltaButtonDescription(
        key="reconnect",
        translation_key="reconnect",
        entity_category=EntityCategory.DIAGNOSTIC,
        needs_connection=False,
        press=lambda c: c.async_force_reconnect(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VoltaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(VoltaButton(coordinator, description) for description in BUTTONS)


class VoltaButton(VoltaEntity, ButtonEntity):
    entity_description: VoltaButtonDescription

    def __init__(
        self, coordinator: VoltaCoordinator, description: VoltaButtonDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def available(self) -> bool:
        if not self.entity_description.needs_connection:
            return True
        return super().available

    async def async_press(self) -> None:
        await self.entity_description.press(self.coordinator)
