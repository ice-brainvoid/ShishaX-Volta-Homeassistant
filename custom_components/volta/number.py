"""Adjustable values: head vibration and hold time."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as p
from .const import DOMAIN
from .coordinator import VoltaCoordinator
from .entity import VoltaEntity


@dataclass(frozen=True, kw_only=True)
class VoltaNumberDescription(NumberEntityDescription):
    value: Callable[[VoltaCoordinator], float | None]
    set_value: Callable[[VoltaCoordinator, int], Awaitable[None]]


NUMBERS: tuple[VoltaNumberDescription, ...] = (
    VoltaNumberDescription(
        key="vibration",
        translation_key="vibration",
        native_min_value=0,
        native_max_value=p.MOTOR_LEVEL_MAX,
        native_step=1,
        mode=NumberMode.SLIDER,
        value=lambda c: c.telemetry.motor_level if c.telemetry else None,
        set_value=lambda c, v: c.async_set_motor_level(v),
    ),
    VoltaNumberDescription(
        key="hold_time",
        translation_key="hold_time_setting",
        native_min_value=p.SET_TIME_MIN,
        native_max_value=p.SET_TIME_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        value=lambda c: c.telemetry.set_time if c.telemetry else None,
        set_value=lambda c, v: c.async_set_hold_time(v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VoltaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(VoltaNumber(coordinator, description) for description in NUMBERS)


class VoltaNumber(VoltaEntity, NumberEntity):
    entity_description: VoltaNumberDescription

    def __init__(
        self, coordinator: VoltaCoordinator, description: VoltaNumberDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_value(self.coordinator, int(value))
