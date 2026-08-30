"""Climate entity for the VOLTA heater."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
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
    async_add_entities([VoltaClimate(coordinator)])


class VoltaClimate(VoltaEntity, ClimateEntity):
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = p.TOP_TEMP_MIN
    _attr_max_temp = p.TOP_TEMP_MAX
    # Setpoints travel in tenths of a degree, so a one-degree step is easily
    # representable - even on older firmware that transmits whole degrees.
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: VoltaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.address

    @property
    def current_temperature(self) -> float | None:
        return self.coordinator.current_temperature

    @property
    def target_temperature(self) -> float | None:
        return self.coordinator.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.coordinator.is_heating else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        telemetry = self.coordinator.telemetry
        if telemetry is None or not telemetry.start_heating:
            return HVACAction.OFF
        if telemetry.pause_state:
            return HVACAction.IDLE
        # tempReady means the target was reached and the device is only holding.
        return HVACAction.IDLE if telemetry.temp_ready else HVACAction.HEATING

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        telemetry = self.coordinator.telemetry
        if telemetry is None:
            return {}
        return {
            "preset_slot": telemetry.heat_preset,
            "hold_time_minutes": telemetry.set_time,
            "elapsed_seconds": telemetry.elapsed,
            "boost_count": telemetry.boost_count,
            "temp_ready": bool(telemetry.temp_ready),
            "software_revision": self.coordinator.software_revision,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.coordinator.async_set_target_temperature(float(temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.HEAT:
            await self.coordinator.async_start_heating()
        else:
            await self.coordinator.async_stop_heating()

    async def async_turn_on(self) -> None:
        await self.coordinator.async_start_heating()

    async def async_turn_off(self) -> None:
        await self.coordinator.async_stop_heating()
