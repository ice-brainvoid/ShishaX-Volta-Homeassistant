"""Binary states: target temperature reached, paused, Wi-Fi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VoltaCoordinator
from .entity import VoltaEntity


@dataclass(frozen=True, kw_only=True)
class VoltaBinarySensorDescription(BinarySensorEntityDescription):
    value: Callable[[VoltaCoordinator], bool | None]


BINARY_SENSORS: tuple[VoltaBinarySensorDescription, ...] = (
    VoltaBinarySensorDescription(
        key="temp_ready",
        translation_key="temp_ready",
        value=lambda c: bool(c.telemetry.temp_ready) if c.telemetry else None,
    ),
    VoltaBinarySensorDescription(
        key="wifi",
        translation_key="wifi",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: bool(c.device_state.wifi_connected) if c.device_state else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VoltaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        VoltaBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class VoltaBinarySensor(VoltaEntity, BinarySensorEntity):
    entity_description: VoltaBinarySensorDescription

    def __init__(
        self, coordinator: VoltaCoordinator, description: VoltaBinarySensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.coordinator)
