"""Sensors for battery, temperatures and session runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VoltaCoordinator
from .entity import VoltaEntity


@dataclass(frozen=True, kw_only=True)
class VoltaSensorDescription(SensorEntityDescription):
    value: Callable[[VoltaCoordinator], float | None]


SENSORS: tuple[VoltaSensorDescription, ...] = (
    VoltaSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # The device reports 255 when no reading is available.
        value=lambda c: (
            c.telemetry.battery
            if c.telemetry and c.telemetry.battery != 255
            else None
        ),
    ),
    VoltaSensorDescription(
        key="top_temperature",
        translation_key="top_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda c: c.device_state.real_top_temp if c.device_state else None,
    ),
    VoltaSensorDescription(
        key="side_temperature",
        translation_key="side_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda c: c.telemetry.real_side_temp if c.telemetry else None,
    ),
    VoltaSensorDescription(
        key="elapsed",
        translation_key="elapsed",
        # Seconds, not minutes - in a capture the value ticks up once per second.
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda c: c.telemetry.elapsed if c.telemetry else None,
    ),
    VoltaSensorDescription(
        key="target_side_temperature",
        translation_key="target_side_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: c.device_state.custom_side_temp_c if c.device_state else None,
    ),
    VoltaSensorDescription(
        key="preset_slot",
        translation_key="preset_slot",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: c.telemetry.heat_preset if c.telemetry else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VoltaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(VoltaSensor(coordinator, description) for description in SENSORS)


class VoltaSensor(VoltaEntity, SensorEntity):
    entity_description: VoltaSensorDescription

    def __init__(
        self, coordinator: VoltaCoordinator, description: VoltaSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self.coordinator)
