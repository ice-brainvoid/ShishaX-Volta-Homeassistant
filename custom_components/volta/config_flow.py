"""Config-Flow: Gerät per Bluetooth finden und übernehmen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from . import protocol as p
from .const import DOMAIN


def _is_volta(info: BluetoothServiceInfoBleak) -> bool:
    name = info.name or ""
    return name.startswith(p.NAME_PREFIXES) or p.SERVICE_UUID in info.service_uuids


class VoltaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: BluetoothServiceInfoBleak | None = None
        self._candidates: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Automatische Erkennung durch den Bluetooth-Stack."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not _is_volta(discovery_info):
            return self.async_abort(reason="not_supported")

        self._discovered = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovered is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered.name,
                data={CONF_ADDRESS: self._discovered.address},
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._discovered.name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manuelle Auswahl aus den sichtbaren Geräten."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._candidates[address].name, data={CONF_ADDRESS: address}
            )

        configured = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address not in configured and _is_volta(info):
                self._candidates[info.address] = info

        if not self._candidates:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{info.name} ({address})"
                            for address, info in self._candidates.items()
                        }
                    )
                }
            ),
        )
