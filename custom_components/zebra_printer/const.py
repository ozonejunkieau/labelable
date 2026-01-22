"""Constants for the Zebra Printer integration."""

from __future__ import annotations

DOMAIN = "zebra_printer"
DEFAULT_PORT = 9100
SCAN_INTERVAL = 30
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 3

# Zebra OUI prefixes (from IEEE database)
ZEBRA_OUI_PREFIXES = [
    "00:07:4D",
    "00:1E:8F",
    "00:23:68",
    "8C:C8:F4",
    "AC:3F:A4",
    "CC:78:AB",
    "10:7B:EF",
    "24:A4:3C",
    "34:0B:40",
]

# Protocol types
PROTOCOL_ZPL = "zpl"
PROTOCOL_EPL2 = "epl2"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PROTOCOL = "protocol"
CONF_THERMAL_TRANSFER = "thermal_transfer_capable"

# ZPL Commands
ZPL_HOST_STATUS = "~HS"
ZPL_HOST_IDENTIFICATION = "~HI"
ZPL_ODOMETER = "~HQOD"
ZPL_EXTENDED_STATUS = "~HQES"
ZPL_CALIBRATE = "~JC"
ZPL_FEED_LABEL = "^XA^XZ"

# Error flags from ~HQES response (bitmask positions)
# Format: (nibble_position, bit_value, description)
# Nibble 1 is rightmost, nibble 8 is leftmost in group 1
ERROR_FLAGS = {
    # Nibble 1 (bits 0-3)
    0x00000001: "Media Out",
    0x00000002: "Ribbon Out",
    0x00000004: "Head Open",
    0x00000008: "Cutter Fault",
    # Nibble 2 (bits 4-7)
    0x00000010: "Printhead Over Temperature",
    0x00000020: "Motor Over Temperature",
    0x00000040: "Bad Printhead Element",
    0x00000080: "Printhead Detection Error",
    # Nibble 3 (bits 8-11)
    0x00000100: "Invalid Firmware Configuration",
    0x00000200: "Printhead Thermistor Open",
    # Nibble 4 (bits 12-15) - KR403 only
    0x00001000: "Paper Jam during Retract",
    0x00002000: "Presenter Not Running",
    0x00004000: "Paper Feed Error",
    0x00008000: "Clear Paper Path Failed",
    # Nibble 5 (bits 16-19)
    0x00010000: "Paused",
    0x00020000: "Retract Function Timed Out",
    0x00040000: "Black Mark Calibrate Error",
    0x00080000: "Black Mark Not Found",
}

# Warning flags from ~HQES response (bitmask positions)
WARNING_FLAGS = {
    # Nibble 1 (bits 0-3)
    0x00000001: "Need to Calibrate Media",
    0x00000002: "Clean Printhead",
    0x00000004: "Replace Printhead",
    0x00000008: "Paper Near End",
    # Nibble 2 (bits 4-7) - KR403 sensor warnings
    0x00000010: "Sensor 1 - Paper Before Head",
    0x00000020: "Sensor 2 - Black Mark",
    0x00000040: "Sensor 3 - Paper After Head",
    0x00000080: "Sensor 4 - Loop Ready",
    # Nibble 3 (bits 8-11) - KR403 sensor warnings
    0x00000100: "Sensor 5 - Presenter",
    0x00000200: "Sensor 6 - Retract Ready",
    0x00000400: "Sensor 7 - In Retract",
    0x00000800: "Sensor 8 - At Bin",
}

# EPL2 Commands
EPL2_STATUS = "UQ"
EPL2_CALIBRATE = "xa"
EPL2_FEED_LABEL = "P1"

# Sensor keys
SENSOR_MODEL = "model"
SENSOR_FIRMWARE = "firmware"
SENSOR_HEAD_DISTANCE = "head_distance"
SENSOR_PRINT_SPEED = "print_speed"
SENSOR_DARKNESS = "darkness"
SENSOR_LABEL_LENGTH = "label_length"
SENSOR_PRINT_WIDTH = "print_width"
SENSOR_PRINT_MODE = "print_mode"
SENSOR_PRINT_METHOD = "print_method"
SENSOR_ERRORS = "errors"
SENSOR_WARNINGS = "warnings"
SENSOR_LANGUAGE = "language"

# Binary sensor keys
BINARY_SENSOR_READY = "ready"
BINARY_SENSOR_HEAD_OPEN = "head_open"
BINARY_SENSOR_PAPER_OUT = "paper_out"
BINARY_SENSOR_RIBBON_OUT = "ribbon_out"
BINARY_SENSOR_PAUSED = "paused"
BINARY_SENSOR_BUFFER_FULL = "buffer_full"
BINARY_SENSOR_HAS_ERROR = "has_error"

# Print modes
PRINT_MODE_TEAR = "tear_off"
PRINT_MODE_PEEL = "peel_off"
PRINT_MODE_REWIND = "rewind"
PRINT_MODE_CUTTER = "cutter"
PRINT_MODE_DELAYED_CUT = "delayed_cut"
PRINT_MODE_RFID = "rfid"
PRINT_MODE_APPLICATOR = "applicator"
PRINT_MODE_UNKNOWN = "unknown"
