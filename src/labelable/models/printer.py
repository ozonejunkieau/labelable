"""Printer configuration models."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


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
    PTOUCH_BRIDGE = "ptouch_bridge"
    ZEBRA_BRIDGE = "zebra_bridge"


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


class PTouchBridgeConnection(BaseModel):
    """ESP32-P4 P-Touch network bridge (ptouch-bridge firmware).

    The device hosts a Brother PT-P710BT over USB and exposes an HTTP API:
      GET  /status  (unauthenticated)
      POST /print   (bearer token)

    It does not speak PTCBP - it accepts pre-rasterised, uncompressed
    16-byte raster rows and builds the printer command stream itself.
    """

    type: Literal["ptouch_bridge"] = "ptouch_bridge"
    host: str
    port: int = 80
    # Optional in config; falls back to LABELABLE_PTOUCH_BRIDGE_TOKEN
    token: str | None = None
    # Fallback media type when the device has never polled the printer.
    # Live status always wins. 3 = non-laminated.
    media_type: int = 3
    # Skip the device's tape width validation. Genuinely unsafe - content
    # outside the printable pin window is silently discarded.
    force: bool = False


class ZebraBridgeConnection(BaseModel):
    """ESP32-P4 Zebra network bridge (zebra-bridge firmware).

    The device hosts a Zebra GK/GX-series printer over USB and exposes an
    HTTP API:
      GET  /status  (unauthenticated)
      POST /print   (bearer token, raw ZPL body)

    ZPL is streamed to the printer verbatim; the device decides completion by
    polling ~HS, so only ``type: zpl`` printers can use this connection.
    """

    type: Literal["zebra_bridge"] = "zebra_bridge"
    host: str
    port: int = 80
    # Optional in config; falls back to LABELABLE_ZEBRA_BRIDGE_TOKEN
    token: str | None = None
    # How long to wait for a job to finish once admitted. Must exceed the
    # firmware's own CONFIG_ZB_JOB_TIMEOUT_S (300 s by default), or a long
    # ^PQ batch is reported failed here while it is still printing.
    completion_timeout_s: float = 330.0


ConnectionConfig = Annotated[
    TCPConnection
    | SerialConnection
    | USBConnection
    | HAConnection
    | BridgeConnection
    | PTouchBridgeConnection
    | ZebraBridgeConnection,
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
    "ptouch_bridge": (PrinterType.PTOUCH,),
    "zebra_bridge": (PrinterType.ZPL,),
}
