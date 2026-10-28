"""Printer configuration models."""

import logging
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class PrinterType(StrEnum):
    """Supported printer types."""

    ZPL = "zpl"
    EPL2 = "epl2"
    PTOUCH = "ptouch"


class ConnectionType(StrEnum):
    """Supported connection types."""

    TCP = "tcp"
    SERIAL = "serial"
    USB = "usb"
    HA = "ha"
    BRIDGE = "bridge"
    USB_BRIDGE = "usb_bridge"


class TCPConnection(BaseModel):
    """TCP/IP connection configuration."""

    type: Literal["tcp"] = "tcp"
    host: str
    port: int = 9100


class SerialConnection(BaseModel):
    """Serial port connection configuration."""

    type: Literal["serial"] = "serial"
    device: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1


class USBConnection(BaseModel):
    """USB connection configuration (for P-Touch printers)."""

    type: Literal["usb"] = "usb"
    vendor_id: int = 0x04F9  # Brother
    product_id: int = 0x20AF  # PT-P710BT


class HAConnection(BaseModel):
    """Home Assistant zebra_printer integration connection.

    Uses the HA zebra_printer integration as a transport layer.
    """

    type: Literal["ha"] = "ha"
    device_id: str  # HA config entry ID or device ID
    ha_url: str = "http://supervisor/core"
    ha_token: str | None = None  # Optional if running as addon (uses SUPERVISOR_TOKEN)


class BridgeConnection(BaseModel):
    """Bridge daemon connection for remote P-Touch USB printers.

    The bridge daemon runs on a machine with the USB printer attached
    and polls the Labelable server for print jobs. No inbound ports needed.
    """

    type: Literal["bridge"] = "bridge"
    serial_number: str  # USB serial for identity across restarts/IP changes
    tape_width_mm: int | None = None


class UsbBridgeConnection(BaseModel):
    """ESP32-P4 labelable-usb network bridge.

    One board drives several USB printers (one P-Touch, up to three Zebra ZPL
    and three Zebra EPL2) through a hub and exposes an HTTP API:
      GET  /status  (unauthenticated)
      POST /print   (bearer token)
      GET  /config  (bearer token, ZPL and EPL2 only)

    Every call takes ``?printer=<id>`` to pick the printer. The printer
    ``type`` selects the payload: raster rows for ptouch, raw ZPL for zpl and
    raw EPL2 for epl2.

    ``ptouch_bridge`` and ``zebra_bridge`` (the older single-printer
    firmwares) are deprecated aliases; see ``PrinterConfig``.
    """

    type: Literal["usb_bridge"] = "usb_bridge"
    host: str
    port: int = 80
    # Optional in config; falls back to LABELABLE_USB_BRIDGE_TOKEN
    token: str | None = None
    # Printer id from the bridge (e.g. "zpl-46J152601234"). Optional only
    # while the bridge has a single printer of the right kind.
    printer: str | None = None
    # Fallback media type when the device has never polled the printer
    # (ptouch). Live status always wins. 3 = non-laminated.
    media_type: int = 3
    # Skip the device's tape width validation (ptouch). Genuinely unsafe -
    # content outside the printable pin window is silently discarded.
    force: bool = False
    # How long to wait for a job to finish once admitted (zpl, epl2). Unset
    # picks a default above the firmware's own job timeout (300 s ZPL,
    # 120 s EPL2), or a long job is reported failed here while it is still
    # printing.
    completion_timeout_s: float | None = None
    # Set when this came from a deprecated alias; names that alias's old
    # token environment variable, which stays honoured as a fallback.
    legacy_token_env: str | None = Field(default=None, exclude=True)


# Deprecated connection types that normalise into usb_bridge:
# alias -> (printer type it allowed, its old token environment variable)
_BRIDGE_ALIASES: dict[str, tuple[PrinterType, str]] = {
    "ptouch_bridge": (PrinterType.PTOUCH, "LABELABLE_PTOUCH_BRIDGE_TOKEN"),
    "zebra_bridge": (PrinterType.ZPL, "LABELABLE_ZEBRA_BRIDGE_TOKEN"),
}
_warned_aliases: set[str] = set()


ConnectionConfig = Annotated[
    TCPConnection | SerialConnection | USBConnection | HAConnection | BridgeConnection | UsbBridgeConnection,
    Field(discriminator="type"),
]


class HealthcheckConfig(BaseModel):
    """Healthcheck configuration for a printer."""

    interval: int = Field(default=60, ge=1)  # Seconds between status checks; 0 busy-looped
    command: str | None = None  # Custom command (default depends on printer type)


class PrinterConfig(BaseModel):
    """Configuration for a single printer."""

    name: str
    type: PrinterType
    connection: ConnectionConfig
    enabled: bool = True
    healthcheck: HealthcheckConfig = HealthcheckConfig()

    @model_validator(mode="before")
    @classmethod
    def _normalise_bridge_aliases(cls, data: Any) -> Any:
        """Rewrite the deprecated ptouch_bridge / zebra_bridge into usb_bridge."""
        if not isinstance(data, dict):
            return data
        conn = data.get("connection")
        if not isinstance(conn, dict) or conn.get("type") not in _BRIDGE_ALIASES:
            return data
        alias = conn["type"]
        only_type, token_env = _BRIDGE_ALIASES[alias]
        # The old firmwares each drove one kind of printer; keep rejecting
        # a mismatch rather than let it through the broader usb_bridge.
        if data.get("type") != only_type:
            raise ValueError(f"{alias} connection only supports type '{only_type.value}', not '{data.get('type')}'")
        if alias not in _warned_aliases:
            _warned_aliases.add(alias)
            logger.warning(
                f"Connection type '{alias}' is deprecated; use 'usb_bridge' (labelable-usb firmware) instead"
            )
        return {**data, "connection": {**conn, "type": "usb_bridge", "legacy_token_env": token_env}}

    @model_validator(mode="after")
    def _check_connection_matches_type(self) -> "PrinterConfig":
        # A mismatch used to load fine and fail later: type zpl over a P-Touch
        # bridge sent ZPL to a P-Touch, and type zpl over usb read offline forever.
        allowed = _TYPES_FOR_CONNECTION[self.connection.type]
        if self.type not in allowed:
            names = ", ".join(f"'{t.value}'" for t in allowed)
            raise ValueError(f"{self.connection.type} connection only supports type {names}, not '{self.type.value}'")
        return self


# Printer types each connection can actually drive
_TYPES_FOR_CONNECTION: dict[str, tuple[PrinterType, ...]] = {
    "tcp": (PrinterType.ZPL, PrinterType.EPL2),
    "serial": (PrinterType.ZPL, PrinterType.EPL2),
    "ha": (PrinterType.ZPL, PrinterType.EPL2),
    "usb": (PrinterType.PTOUCH,),
    "bridge": (PrinterType.PTOUCH,),
    "usb_bridge": (PrinterType.PTOUCH, PrinterType.ZPL, PrinterType.EPL2),
}
