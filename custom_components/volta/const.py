"""Constants for the VOLTA integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "volta"

CONF_ADDRESS: Final = "address"

# The device pushes telemetry on its own. This timeout only matters when the
# stream stops without the BLE connection dropping.
STALE_AFTER: Final = 120

CONNECT_TIMEOUT: Final = 20
