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
    entities: list[VoltaEntity] = [
        VoltaBinarySensor(coordinator, description) for description in BINARY_SENSORS
    ]
    entities.append(VoltaConnected(coordinator))
    async_add_entities(entities)


class VoltaConnected(VoltaEntity, BinarySensorEntity):
    """Whether a BLE link to the device currently exists.

    Unlike every other entity this stays available while disconnected - the one
    thing that reports the connection must not vanish exactly when it drops.
    """

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VoltaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_connected"

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.available

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "disconnects": self.coordinator.disconnects,
            "last_connected": self.coordinator.last_connected,
            "last_disconnected": self.coordinator.last_disconnected,
        }


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
