"""Convert PIL images to EPL2 GW commands."""

from PIL import Image


def image_to_epl2(image: Image.Image) -> bytes:
    """Convert a PIL image to EPL2 GW graphic command.

    The image is converted to 1-bit (black and white) and encoded
    as an EPL2 Graphics Write (GW) command.

    EPL2 GW format:
    GW<x>,<y>,<bytes_per_row>,<height>,<binary_data>

    Note: EPL2 uses raw binary data, not hex encoding.

    Args:
        image: PIL Image to convert.

    Returns:
        EPL2 commands as bytes.
    """
    # Convert to 1-bit black and white
    if image.mode != "1":
        image = image.convert("1")

    width, height = image.size

    # Calculate bytes per row (must be byte-aligned)
    bytes_per_row = (width + 7) // 8

    # Get pixel data
    pixels: list[int] = list(image.getdata())  # type: ignore[arg-type]

    # Build binary data
    binary_data = bytearray()
    for y in range(height):
        for byte_idx in range(bytes_per_row):
            byte_val = 0
            for bit in range(8):
                pixel_x = byte_idx * 8 + bit
                if pixel_x < width:
                    pixel_idx = y * width + pixel_x
                    # PIL: 0 = black, non-zero = white
                    # EPL2: 1 bit = black (print), 0 bit = white (no print)
                    if pixels[pixel_idx] == 0:  # Black pixel in PIL
                        byte_val |= 1 << (7 - bit)
            binary_data.append(byte_val)

    # Build EPL2 command
    # N = Clear image buffer
    # GW = Graphics Write
    # P1 = Print 1 label
    header = f"N\nGW0,0,{bytes_per_row},{height},".encode("ascii")
    footer = b"\nP1\n"

    return header + bytes(binary_data) + footer
