"""Constants for the VOLTA integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "volta"

CONF_ADDRESS: Final = "address"

# The device pushes telemetry on its own. This timeout only matters when the
# stream stops without the BLE connection dropping.
STALE_AFTER: Final = 120

CONNECT_TIMEOUT: Final = 20

# bleak-retry-connector defaults to four tries per round. A device that is
# just waking up can need a few more, and the watchdog retries anyway.
CONNECT_ATTEMPTS: Final = 6

# The Bluetooth callback only fires while the device advertises. This second
# path retries on a fixed schedule, so a failed attempt or a missed
# advertisement does not leave the device unavailable indefinitely.
WATCHDOG_INTERVAL: Final = timedelta(seconds=60)
