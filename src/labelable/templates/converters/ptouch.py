"""Convert PIL images to Brother P-Touch raster format."""

import struct

from PIL import Image

# Print head is 128 pixels wide (16 bytes per raster line) at 180 DPI
PRINT_HEAD_PIXELS = 128
BYTES_PER_LINE = PRINT_HEAD_PIXELS // 8  # 16

# Tape width → (printable pixels, left margin, right margin)
TAPE_SPECS: dict[int, tuple[int, int, int]] = {
    6: (32, 52, 44),
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


def image_to_ptouch_raster(
    image: Image.Image,
    tape_width_mm: int = 24,
    compression: bool = True,
) -> tuple[bytes, int]:
    """Convert a PIL image to P-Touch raster data.

    The image is rotated 90 CCW and mirrored horizontally, then centered
    within the 128px print head width according to tape margins.

    Args:
        image: PIL Image to convert (should be pre-cropped to content).
        tape_width_mm: Tape width in mm (6, 9, 12, 18, or 24).
        compression: Use TIFF PackBits compression.

    Returns:
        Tuple of (raster_bytes, raster_line_count).

    Raises:
        ValueError: If tape width is not supported.
    """
    if tape_width_mm not in TAPE_SPECS:
        raise ValueError(f"Unsupported tape width: {tape_width_mm}mm. Supported: {sorted(TAPE_SPECS.keys())}")

    printable_px, left_margin, _right_margin = TAPE_SPECS[tape_width_mm]

    # Convert to 1-bit
    if image.mode != "1":
        image = image.convert("1")

    # Rotate 90 CCW and mirror horizontally
    # This transforms the image so that columns become raster lines
    rotated = image.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)

    # Scale to fit printable area if wider than tape allows
    rot_w, rot_h = rotated.size
    if rot_h > printable_px:
        scale = printable_px / rot_h
        new_w = max(1, int(rot_w * scale))
        rotated = rotated.resize((new_w, printable_px), Image.NEAREST)
        rot_w, rot_h = rotated.size

    # Create 128px-wide canvas and center the content
    raster_line_count = rot_w
    canvas = Image.new("1", (PRINT_HEAD_PIXELS, raster_line_count), color=1)  # white
    canvas.paste(rotated, (left_margin, 0))

    # Convert to raster bytes
    pixels: list[int] = list(canvas.getdata())  # type: ignore[arg-type]
    raster_bytes = bytearray()

    for line_idx in range(raster_line_count):
        # Pack 128 pixels into 16 bytes, MSB first, 1=black 0=white
        line_data = bytearray(BYTES_PER_LINE)
        is_blank = True

        for byte_idx in range(BYTES_PER_LINE):
            byte_val = 0
            for bit in range(8):
                pixel_x = byte_idx * 8 + bit
                pixel_idx = line_idx * PRINT_HEAD_PIXELS + pixel_x
                # PIL mode "1": 0 = black, non-zero = white
                # P-Touch: 1 = black, 0 = white
                if pixels[pixel_idx] == 0:
                    byte_val |= 1 << (7 - bit)
            line_data[byte_idx] = byte_val
            if byte_val != 0:
                is_blank = False

        if is_blank:
            # Z command = blank raster line
            raster_bytes.extend(b"Z")
        elif compression:
            compressed = _packbits_encode(bytes(line_data))
            raster_bytes.extend(b"G")
            raster_bytes.extend(struct.pack("<H", len(compressed)))
            raster_bytes.extend(compressed)
        else:
            raster_bytes.extend(b"g")
            raster_bytes.extend(struct.pack("<H", len(line_data)))
            raster_bytes.extend(line_data)

    return bytes(raster_bytes), raster_line_count
