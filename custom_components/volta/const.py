"""Konstanten der VOLTA-Integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "volta"

CONF_ADDRESS: Final = "address"

# Das Gerät pusht Telemetrie von selbst. Der Timeout greift nur, wenn der
# Stream abreisst, ohne dass die BLE-Verbindung fällt.
STALE_AFTER: Final = 120

CONNECT_TIMEOUT: Final = 20
