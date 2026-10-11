"""Convert PIL images to Brother P-Touch raster format."""

import struct

from PIL import Image

# Print head is 128 pixels wide (16 bytes per raster line) at 180 DPI
PRINT_HEAD_PIXELS = 128
BYTES_PER_LINE = PRINT_HEAD_PIXELS // 8  # 16

# Minimum ink coverage (0-255) for a downscaled pixel to stay black.
INK_COVERAGE_THRESHOLD = 64

# Tape width → (printable pixels, left margin, right margin)
# Values come from the ptouch-bridge interface spec §4 (128-pin head).
TAPE_SPECS: dict[int, tuple[int, int, int]] = {
    6: (32, 48, 48),
    9: (50, 39, 39),
    12: (70, 29, 29),
    18: (112, 8, 8),
    24: (128, 0, 0),
}


def _packbits_encode(data: bytes) -> bytes:
    """Encode data using TIFF PackBits compression.

    PackBits encoding:
    - Run of 2+ identical bytes: (257-count, byte) where count is 2..128
    - Literal run of non-repeating bytes: (count-1, byte1, byte2, ...)
      where count is 1..128

    Args:
        data: Raw bytes to compress.

    Returns:
        PackBits-compressed bytes.
    """
    if not data:
        return b""

    result = bytearray()
    i = 0
    n = len(data)

    while i < n:
        # Check for a run of identical bytes
        run_byte = data[i]
        run_len = 1
        while i + run_len < n and data[i + run_len] == run_byte and run_len < 128:
            run_len += 1

        if run_len >= 2:
            # Encode as repeat: (257 - run_len) & 0xFF, byte
            result.append((257 - run_len) & 0xFF)
            result.append(run_byte)
            i += run_len
        else:
            # Collect literal (non-repeating) bytes
            literal_start = i
            literal_len = 1
            i += 1

            while i < n and literal_len < 128:
                # Check if next bytes form a run of 2+
                if i + 1 < n and data[i] == data[i + 1]:
                    break
                literal_len += 1
                i += 1

            # Encode as literal: (literal_len - 1), byte1, byte2, ...
            result.append(literal_len - 1)
            result.extend(data[literal_start : literal_start + literal_len])

    return bytes(result)


def _resize_preserving_ink(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Downscale 1-bit label art without losing thin features.

    NEAREST point-samples, so any feature narrower than the sampling stride
    can fall between samples and vanish outright - a 1px cut line simply
    disappears. Label art is mostly thin features, so instead the ink
    coverage is area-averaged and any destination pixel with meaningful
    coverage is kept as ink. A 1px line survives as a line (occasionally one
    pixel wider) rather than dropping out.

    Args:
        image: 1-bit image (mode "1", 0 = ink).
        size: Target (width, height).

    Returns:
        Resized mode "1" image.
    """
    # Ink -> 255 so BOX averaging measures coverage, not brightness.
    to_coverage = [255 if v < 128 else 0 for v in range(256)]
    # Keep anything with >=25% coverage. A 1px feature straddling two
    # destination pixels lands around 40% in each, so it survives in both
    # rather than being lost to a 50% threshold.
    to_ink = [0 if v >= INK_COVERAGE_THRESHOLD else 255 for v in range(256)]

    coverage = image.convert("L").point(to_coverage)
    coverage = coverage.resize(size, Image.Resampling.BOX)
    return coverage.point(to_ink).convert("1")


def _check_tape_width(tape_width_mm: int) -> tuple[int, int, int]:
    """Look up the tape spec, raising ValueError for unsupported widths."""
    if tape_width_mm not in TAPE_SPECS:
        raise ValueError(f"Unsupported tape width: {tape_width_mm}mm. Supported: {sorted(TAPE_SPECS.keys())}")
    return TAPE_SPECS[tape_width_mm]


def encode_raster_rows(rows: list[bytes], compression: bool = True) -> bytes:
    """Encode 16-byte raster rows as PTCBP Z/G/g line commands.

    Args:
        rows: Raster rows, each exactly BYTES_PER_LINE bytes.
        compression: Use TIFF PackBits compression for non-blank lines.

    Returns:
        Concatenated line commands.
    """
    raster_bytes = bytearray()

    for line_data in rows:
        if not any(line_data):
            # Z command = blank raster line
            raster_bytes.extend(b"Z")
        elif compression:
            compressed = _packbits_encode(line_data)
            raster_bytes.extend(b"G")
            raster_bytes.extend(struct.pack("<H", len(compressed)))
            raster_bytes.extend(compressed)
        else:
            raster_bytes.extend(b"g")
            raster_bytes.extend(struct.pack("<H", len(line_data)))
            raster_bytes.extend(line_data)

    return bytes(raster_bytes)


def _strip_to_rows(image: Image.Image, pin_offset: int) -> list[bytes]:
    """Rasterise a strip laid out with width = feed direction, height = tape width.

    Each image column becomes one 16-byte raster row; image row 0 maps to
    print head pin ``pin_offset``. The caller must ensure the strip is already
    scaled so that ``pin_offset + height <= PRINT_HEAD_PIXELS``.

    Args:
        image: 1-bit strip, width = feed direction, height = tape width.
        pin_offset: Print head pin that image row 0 maps to.

    Returns:
        List of rows, each exactly BYTES_PER_LINE (16) bytes, MSB first,
        1 = black.
    """
    img_w, img_h = image.size
    pixels: list[int] = list(image.getdata())  # type: ignore[arg-type]
    rows: list[bytes] = []

    for col in range(img_w):
        line_data = bytearray(BYTES_PER_LINE)

        for row in range(img_h):
            head_pos = pin_offset + row

            pixel_idx = row * img_w + col
            # PIL mode "1": 0 = black, non-zero = white
            # P-Touch: 1 = black, 0 = white
            if pixels[pixel_idx] == 0:
                byte_idx = head_pos // 8
                bit = 7 - (head_pos % 8)
                line_data[byte_idx] |= 1 << bit

        rows.append(bytes(line_data))

    return rows


def image_to_raster_rows(
    image: Image.Image,
    tape_width_mm: int = 24,
) -> list[bytes]:
    """Convert a PIL image to uncompressed 128-pin raster rows.

    Single-label templates are authored portrait: x is the tape width axis,
    y is the tape feed axis. Rotating 90 CCW and mirroring produces the same
    layout a batch strip already uses (width = feed, height = tape width),
    which is then rasterised column by column. Pins outside the tape's
    printable window are always zero.

    Args:
        image: PIL Image to convert (should be pre-cropped to content).
        tape_width_mm: Tape width in mm (6, 9, 12, 18, or 24).

    Returns:
        List of rows, each exactly BYTES_PER_LINE (16) bytes, MSB first,
        1 = black.

    Raises:
        ValueError: If tape width is not supported.
    """
    printable_px, left_margin, _right_margin = _check_tape_width(tape_width_mm)

    # Convert to 1-bit
    if image.mode != "1":
        image = image.convert("1")

    # Rotate 90 CCW into the batch strip layout: authored x (the tape width
    # axis) becomes the print head axis, authored y becomes the feed axis.
    # This must be a pure rotation - combining it with a mirror, as an earlier
    # version did, is a reflection and prints every glyph back to front.
    rotated = image.rotate(90, expand=True)

    # Scale down to fit the printable window, preserving aspect ratio.
    rot_w, rot_h = rotated.size
    if rot_h > printable_px:
        scale = printable_px / rot_h
        new_w = max(1, int(rot_w * scale))
        rotated = _resize_preserving_ink(rotated, (new_w, printable_px))
        rot_w, rot_h = rotated.size

    # Centre the content within the tape's printable pin window
    pin_offset = left_margin + (printable_px - rot_h) // 2

    return _strip_to_rows(rotated, pin_offset)


def batch_image_to_raster_rows(
    image: Image.Image,
    tape_width_mm: int = 24,
) -> list[bytes]:
    """Convert a horizontal batch strip to uncompressed 128-pin raster rows.

    The batch strip is already laid out with width = feed direction and
    height = tape width, so it only needs scaling to the printable window
    before being rasterised column by column.

    Args:
        image: Horizontal batch strip (width=feed, height=tape_width).
        tape_width_mm: Tape width in mm (6, 9, 12, 18, or 24).

    Returns:
        List of rows, each exactly BYTES_PER_LINE (16) bytes.

    Raises:
        ValueError: If tape width is not supported.
    """
    printable_px, left_margin, _right_margin = _check_tape_width(tape_width_mm)

    # Convert to 1-bit
    if image.mode != "1":
        image = image.convert("1")

    img_w, img_h = image.size

    # Scale to the printable window preserving aspect ratio. Scaling only the
    # tape axis - as an earlier version did - leaves the feed axis at 1:1 and
    # prints every label stretched lengthways by (img_h / printable_px); on
    # 9 mm tape that is 63px -> 50 pins, a visible 1.26x distortion.
    if img_h != printable_px:
        scale = printable_px / img_h
        new_w = max(1, round(img_w * scale))
        image = _resize_preserving_ink(image, (new_w, printable_px))

    return _strip_to_rows(image, left_margin)


def image_to_ptouch_raster(
    image: Image.Image,
    tape_width_mm: int = 24,
    compression: bool = True,
) -> tuple[bytes, int]:
    """Convert a PIL image to P-Touch raster line commands.

    Thin Z/G/g encoder over :func:`image_to_raster_rows`.

    Args:
        image: PIL Image to convert (should be pre-cropped to content).
        tape_width_mm: Tape width in mm (6, 9, 12, 18, or 24).
        compression: Use TIFF PackBits compression.

    Returns:
        Tuple of (raster_bytes, raster_line_count).

    Raises:
        ValueError: If tape width is not supported.
    """
    rows = image_to_raster_rows(image, tape_width_mm=tape_width_mm)
    return encode_raster_rows(rows, compression=compression), len(rows)


def batch_image_to_ptouch_raster(
    image: Image.Image,
    tape_width_mm: int = 24,
    compression: bool = True,
) -> tuple[bytes, int]:
    """Convert a horizontal batch strip to P-Touch raster line commands.

    Thin Z/G/g encoder over :func:`batch_image_to_raster_rows`.

    Args:
        image: Horizontal batch strip (width=feed, height=tape_width).
        tape_width_mm: Tape width in mm (6, 9, 12, 18, or 24).
        compression: Use TIFF PackBits compression.

    Returns:
        Tuple of (raster_bytes, raster_line_count).

    Raises:
        ValueError: If tape width is not supported.
    """
    rows = batch_image_to_raster_rows(image, tape_width_mm=tape_width_mm)
    return encode_raster_rows(rows, compression=compression), len(rows)
