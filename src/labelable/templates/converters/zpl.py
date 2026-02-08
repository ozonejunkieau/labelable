"""Convert PIL images to ZPL ^GFA commands."""

from PIL import Image


def image_to_zpl(
    image: Image.Image,
    label_offset_x: int = 0,
    label_offset_y: int = 0,
    darkness: int | None = None,
) -> bytes:
    """Convert a PIL image to ZPL ^GFA graphic command.

    The image is converted to 1-bit (black and white) and encoded
    as a ZPL Graphics Field ASCII (^GFA) command.

    ZPL ^GFA format:
    ^GFA,<total_bytes>,<total_bytes>,<bytes_per_row>,<hex_data>

    Args:
        image: PIL Image to convert.
        label_offset_x: Horizontal label offset in dots (for ^LH command).
        label_offset_y: Vertical label offset in dots (for ^LH command).
        darkness: Print darkness 0-30 (for ~SD command).

    Returns:
        ZPL commands as bytes.
    """
    # Convert to 1-bit black and white
    # In thermal printing: 0 = white (no print), 1 = black (print)
    # PIL mode "1": 0 = black, 255 = white
    # So we need to invert for ZPL where 1 = print (black)
    if image.mode != "1":
        image = image.convert("1")

    width, height = image.size

    # Calculate bytes per row (must be byte-aligned)
    bytes_per_row = (width + 7) // 8
    total_bytes = bytes_per_row * height

    # Get pixel data
    pixels: list[int] = list(image.getdata())  # type: ignore[arg-type]

    # Build hex data
    hex_data = []
    for y in range(height):
        row_bytes = []
        for byte_idx in range(bytes_per_row):
            byte_val = 0
            for bit in range(8):
                pixel_x = byte_idx * 8 + bit
                if pixel_x < width:
                    pixel_idx = y * width + pixel_x
                    # PIL: 0 = black, non-zero = white
                    # ZPL: 1 bit = black (print), 0 bit = white (no print)
                    if pixels[pixel_idx] == 0:  # Black pixel in PIL
                        byte_val |= 1 << (7 - bit)
            row_bytes.append(byte_val)
        hex_data.extend(row_bytes)

    # Convert to uppercase hex string
    hex_string = "".join(f"{b:02X}" for b in hex_data)

    # Build ZPL command
    zpl_parts = ["^XA"]

    # Add darkness setting if specified
    if darkness is not None:
        zpl_parts.append(f"~SD{darkness}")

    # Add label home offset if specified
    if label_offset_x or label_offset_y:
        zpl_parts.append(f"^LH{label_offset_x},{label_offset_y}")

    zpl_parts.append(f"^FO0,0^GFA,{total_bytes},{total_bytes},{bytes_per_row},{hex_string}")
    zpl_parts.append("^XZ")

    zpl = "\n".join(zpl_parts) + "\n"

    return zpl.encode("ascii")


def image_to_zpl_compressed(image: Image.Image) -> bytes:
    """Convert a PIL image to ZPL with compression.

    Uses ZPL's run-length compression for smaller output.
    This is useful for images with large areas of solid color.

    Args:
        image: PIL Image to convert.

    Returns:
        ZPL commands as bytes.
    """
    # For now, use uncompressed format
    # TODO: Implement ZPL compression (Z64, LZ77, etc.)
    return image_to_zpl(image)
