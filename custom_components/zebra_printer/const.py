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

# ZPL Commands
ZPL_HOST_STATUS = "~HS"
ZPL_HOST_IDENTIFICATION = "~HI"
ZPL_ODOMETER = "~HQOD"
ZPL_CALIBRATE = "~JC"
ZPL_FEED_LABEL = "^XA^XZ"

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

# Binary sensor keys
BINARY_SENSOR_ONLINE = "online"
BINARY_SENSOR_HEAD_OPEN = "head_open"
BINARY_SENSOR_PAPER_OUT = "paper_out"
BINARY_SENSOR_RIBBON_OUT = "ribbon_out"
BINARY_SENSOR_PAUSED = "paused"
BINARY_SENSOR_BUFFER_FULL = "buffer_full"

# Print modes
PRINT_MODE_TEAR = "tear_off"
PRINT_MODE_PEEL = "peel_off"
PRINT_MODE_REWIND = "rewind"
PRINT_MODE_CUTTER = "cutter"
PRINT_MODE_DELAYED_CUT = "delayed_cut"
PRINT_MODE_RFID = "rfid"
PRINT_MODE_APPLICATOR = "applicator"
PRINT_MODE_UNKNOWN = "unknown"
