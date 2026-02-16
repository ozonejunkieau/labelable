"""Brother P-Touch raster protocol (PTCBP) constants and status parsing."""

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

# Protocol commands
CMD_INITIALIZE = b"\x00" * 64  # Flush/clear buffer
CMD_RESET = b"\x1b\x40"  # ESC @ — reset printer
CMD_ENTER_RASTER = b"\x1b\x69\x61\x01"  # ESC i a 0x01 — enter raster mode
CMD_STATUS_REQUEST = b"\x1b\x69\x53"  # ESC i S — request status

STATUS_RESPONSE_LENGTH = 32


class Error1(IntFlag):
    """Error flags from status byte 8."""

    NONE = 0x00
    NO_MEDIA = 0x01
    CUTTER_JAM = 0x04
    WEAK_BATTERY = 0x08
    HIGH_VOLTAGE = 0x40


class Error2(IntFlag):
    """Error flags from status byte 9."""

    NONE = 0x00
    WRONG_MEDIA = 0x01
    COVER_OPEN = 0x10
    OVERHEAT = 0x20


class MediaKind(IntEnum):
    """Media type from status byte 11."""

    NO_MEDIA = 0x00
    LAMINATED_TAPE = 0x01
    NON_LAMINATED_TAPE = 0x03
    HEAT_SHRINK_TUBE = 0x11
    FLEXIBLE_TAPE = 0x14
    INCOMPATIBLE_TAPE = 0xFF

    @classmethod
    def _missing_(cls, value: object) -> "MediaKind":
        return cls.INCOMPATIBLE_TAPE


class StatusType(IntEnum):
    """Status type from status byte 18."""

    REPLY = 0x00
    COMPLETED = 0x01
    ERROR = 0x02
    TURNED_OFF = 0x04
    NOTIFICATION = 0x05
    PHASE_CHANGE = 0x06

    @classmethod
    def _missing_(cls, value: object) -> "StatusType":
        return cls.ERROR


# Tape colour lookup (byte 24)
TAPE_COLOURS: dict[int, str] = {
    0x01: "White",
    0x02: "Other",
    0x03: "Clear",
    0x04: "Red",
    0x05: "Blue",
    0x06: "Yellow",
    0x07: "Green",
    0x08: "Black",
    0x09: "Clear (white text)",
    0x20: "Matte white",
    0x21: "Matte clear",
    0x22: "Matte silver",
    0x23: "Satin gold",
    0x24: "Satin silver",
    0x30: "Blue (D)",
    0x31: "Red (D)",
    0x40: "Fluorescent orange",
    0x41: "Fluorescent yellow",
    0x62: "Berry pink",
    0x63: "Light gray",
    0x64: "Lime green",
    0xF0: "Cleaning",
    0xF1: "Stencil",
    0xFF: "Incompatible",
}

# Text colour lookup (byte 25)
TEXT_COLOURS: dict[int, str] = {
    0x01: "White",
    0x04: "Red",
    0x05: "Blue",
    0x08: "Black",
    0x0A: "Gold",
    0x62: "Berry pink",
    0xF0: "Cleaning",
    0xF1: "Stencil",
    0x02: "Other",
    0xFF: "Incompatible",
}


@dataclass(frozen=True)
class StatusResponse:
    """Parsed 32-byte P-Touch status response."""

    error1: Error1
    error2: Error2
    media_width_mm: int
    media_kind: MediaKind
    status_type: StatusType
    tape_colour: str
    text_colour: str

    @property
    def has_errors(self) -> bool:
        return self.error1 != Error1.NONE or self.error2 != Error2.NONE

    @property
    def error_descriptions(self) -> list[str]:
        descriptions: list[str] = []
        for flag in Error1:
            if flag != Error1.NONE and flag in self.error1:
                descriptions.append(flag.name or str(flag))
        for flag in Error2:
            if flag != Error2.NONE and flag in self.error2:
                descriptions.append(flag.name or str(flag))
        return descriptions


# Raster printing commands
CMD_COMPRESSION_TIFF = b"\x4d\x02"  # M 0x02 — TIFF packbits compression
CMD_COMPRESSION_NONE = b"\x4d\x00"  # M 0x00 — no compression
CMD_MARGIN_ZERO = b"\x1b\x69\x64\x00\x00"  # ESC i d 0x0000 — zero margin
CMD_PRINT_AND_FEED = b"\x1a"  # SUB — print label and feed
CMD_STATUS_NOTIFY_OFF = b"\x1b\x69\x21\x00"  # ESC i ! 0x00 — disable auto status
CMD_ADVANCED_NO_CHAIN = b"\x1b\x69\x4b\x08"  # ESC i K 0x08 — cut each (no chain)
CMD_ADVANCED_CHAIN = b"\x1b\x69\x4b\x00"  # ESC i K 0x00 — chain printing (no cut)


def build_print_info(
    media_width_mm: int,
    raster_lines: int,
    auto_cut: bool = True,
) -> bytes:
    """Build ESC i z (print information) command.

    This 13-byte command tells the printer about the media and print job.

    Args:
        media_width_mm: Tape width in mm.
        raster_lines: Number of raster lines in the image.
        auto_cut: Whether to auto-cut after printing.

    Returns:
        13-byte print info command.
    """
    # Validity flags: bit 6 = width valid, bit 1 = quality priority
    # 0x86 = recovery on, width valid, quality priority
    validity = 0x86
    if not auto_cut:
        validity = 0x86  # same flags, auto-cut is in mode command

    # Raster lines as little-endian 32-bit
    raster_le = struct.pack("<I", raster_lines)

    return bytes(
        [
            0x1B,
            0x69,
            0x7A,  # ESC i z
            validity,  # print info validity
            0x00,  # media type (continuous tape = 0x00)
            media_width_mm,  # media width in mm
            0x00,  # media length (0 for continuous)
            raster_le[0],  # raster lines LE byte 0
            raster_le[1],  # raster lines LE byte 1
            raster_le[2],  # raster lines LE byte 2
            raster_le[3],  # raster lines LE byte 3
            0x00,  # page number (starting page)
            0x00,  # 0 = no half-cut at end
        ]
    )


def build_mode_command(auto_cut: bool = True) -> bytes:
    """Build ESC i M (mode) command.

    Args:
        auto_cut: Whether to enable auto-cut.

    Returns:
        3-byte mode command.
    """
    mode = 0x40 if auto_cut else 0x00  # bit 6 = auto-cut
    return bytes([0x1B, 0x69, 0x4D, mode])


def build_margin_command(auto_cut: bool = True) -> bytes:
    """Build ESC i d (margin/feed) command.

    Sets the feed amount in dots. With auto-cut, use a small margin (14 dots).
    Without auto-cut, use zero margin.

    Args:
        auto_cut: Whether auto-cut is enabled.

    Returns:
        5-byte margin command.
    """
    margin_dots = 14 if auto_cut else 0
    return bytes([0x1B, 0x69, 0x64]) + struct.pack("<H", margin_dots)


def build_print_job(
    raster_data: bytes,
    raster_line_count: int,
    media_width_mm: int,
    auto_cut: bool = True,
    chain_print: bool = False,
    compression: bool = True,
) -> bytes:
    """Assemble a complete P-Touch print job.

    Sequence: init → raster mode → status notify off → print info → mode →
    advanced mode → margin → compression → raster data → print+feed

    Args:
        raster_data: Pre-encoded raster data (G/Z/g lines).
        raster_line_count: Number of raster lines.
        media_width_mm: Tape width in mm.
        auto_cut: Whether to auto-cut after printing.
        chain_print: Hold label in printer (don't feed). Useful for
            printing multiple labels with minimal gap.
        compression: Whether TIFF compression was used for raster data.

    Returns:
        Complete print job as bytes.
    """
    parts = [
        CMD_INITIALIZE,  # Flush buffer (64 null bytes)
        CMD_RESET,  # ESC @ — reset printer
        CMD_ENTER_RASTER,  # ESC i a 0x01 — raster mode
        CMD_STATUS_NOTIFY_OFF,  # ESC i ! 0x00 — disable auto status during print
        build_print_info(media_width_mm, raster_line_count, auto_cut),
        build_mode_command(auto_cut),
        CMD_ADVANCED_CHAIN if chain_print else CMD_ADVANCED_NO_CHAIN,
        build_margin_command(auto_cut),
        CMD_COMPRESSION_TIFF if compression else CMD_COMPRESSION_NONE,
        raster_data,
        CMD_PRINT_AND_FEED,
    ]
    return b"".join(parts)


def parse_status(data: bytes) -> StatusResponse:
    """Parse a 32-byte P-Touch status response.

    Raises:
        ValueError: If data is not exactly 32 bytes.
    """
    if len(data) != STATUS_RESPONSE_LENGTH:
        raise ValueError(f"Expected {STATUS_RESPONSE_LENGTH} bytes, got {len(data)}")

    return StatusResponse(
        error1=Error1(data[8]),
        error2=Error2(data[9]),
        media_width_mm=data[10],
        media_kind=MediaKind(data[11]),
        status_type=StatusType(data[18]),
        tape_colour=TAPE_COLOURS.get(data[24], f"Unknown (0x{data[24]:02X})"),
        text_colour=TEXT_COLOURS.get(data[25], f"Unknown (0x{data[25]:02X})"),
    )
