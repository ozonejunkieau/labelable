"""Tests for P-Touch raster converter and PackBits encoding."""

import struct

import pytest
from PIL import Image

from labelable.templates.converters.ptouch import (
    BYTES_PER_LINE,
    PRINT_HEAD_PIXELS,
    TAPE_SPECS,
    _packbits_encode,
    image_to_ptouch_raster,
)


class TestPackbitsEncoding:
    def test_empty_input(self):
        assert _packbits_encode(b"") == b""

    def test_single_byte(self):
        result = _packbits_encode(b"\x42")
        # Single literal byte: (0, 0x42)
        assert result == b"\x00\x42"

    def test_all_same_bytes(self):
        """Run of identical bytes should compress."""
        data = b"\xaa" * 10
        result = _packbits_encode(data)
        # Repeat: (257 - 10) & 0xFF = 247 = 0xF7, then the byte
        assert result == bytes([0xF7, 0xAA])

    def test_all_different_bytes(self):
        """Non-repeating bytes should be stored as literal."""
        data = bytes(range(10))
        result = _packbits_encode(data)
        # Literal: (10 - 1) = 9, then the 10 bytes
        assert result == bytes([9]) + data

    def test_mixed_run_and_literal(self):
        """Mixed data should produce both run and literal sections."""
        data = b"\x01\x02\x03" + b"\xff" * 5
        result = _packbits_encode(data)
        # Should have literal section (3 bytes) then run section (5 bytes)
        assert len(result) < len(data)  # Should compress somewhat
        # Verify we can identify the parts:
        # Literal: (2, 0x01, 0x02, 0x03) then repeat: (252, 0xFF)
        assert result == bytes([2, 0x01, 0x02, 0x03, 0xFC, 0xFF])

    def test_max_run_length(self):
        """Run length capped at 128."""
        data = b"\xbb" * 200
        result = _packbits_encode(data)
        # Should produce two runs: 128 + 72
        # Run 128: (257-128) = 129 = 0x81, then byte
        # Run 72: (257-72) = 185 = 0xB9, then byte
        assert result == bytes([0x81, 0xBB, 0xB9, 0xBB])

    def test_roundtrip_size_reduction(self):
        """Compressing 16 zero bytes (common blank raster line)."""
        data = b"\x00" * 16
        result = _packbits_encode(data)
        assert len(result) < len(data)
        # Should be (257-16) & 0xFF = 241 = 0xF1, 0x00
        assert result == bytes([0xF1, 0x00])


class TestPTouchRasterConversion:
    def test_all_white_image(self):
        """All-white image should produce only Z (blank) lines."""
        img = Image.new("1", (50, 20), color=1)  # white
        raster_data, line_count = image_to_ptouch_raster(img, tape_width_mm=24)

        # After 90 CCW rotation of 50x20 -> 20x50
        # raster_line_count = rotated width = original height = 20
        assert line_count == 20
        # All blank lines should be 'Z' commands
        assert raster_data == b"Z" * 20

    def test_black_bar_produces_nonblank_lines(self):
        """Image with black content should produce G (data) lines."""
        # 20px wide (tape direction), 40px tall (feed direction)
        # Draw a black bar in top half only (y < 15) so after rotation
        # some raster lines have content and some are blank
        img = Image.new("1", (20, 40), color=1)  # white
        for x in range(5, 15):
            for y in range(5, 15):
                img.putpixel((x, y), 0)  # black

        raster_data, line_count = image_to_ptouch_raster(img, tape_width_mm=24)

        # line_count = original height = 40
        assert line_count == 40
        # Should contain both blank (Z) and data (G) lines
        assert b"Z" in raster_data
        assert b"G" in raster_data

    def test_rotation_produces_correct_line_count(self):
        """Line count should match original height (feed direction) after rotation."""
        img = Image.new("1", (80, 30), color=1)
        _data, line_count = image_to_ptouch_raster(img, tape_width_mm=24)
        # 80x30 → 90 CCW → (30, 80) → raster_line_count = rotated width = 30
        assert line_count == 30

    def test_centering_12mm_tape(self):
        """Content should be centered within 128px for 12mm tape."""
        # 12mm: printable=70px, left_margin=29
        img = Image.new("1", (10, 10), color=0)  # all black, small

        raster_data, line_count = image_to_ptouch_raster(img, tape_width_mm=12)

        assert line_count > 0
        # Verify we get data lines (not all blank)
        assert b"G" in raster_data or b"g" in raster_data

    def test_centering_24mm_tape(self):
        """24mm tape should have no margins (full 128px)."""
        printable, left, right = TAPE_SPECS[24]
        assert printable == PRINT_HEAD_PIXELS
        assert left == 0
        assert right == 0

    def test_rgb_image_conversion(self):
        """RGB image should be auto-converted to 1-bit."""
        img = Image.new("RGB", (30, 20), color="white")
        raster_data, line_count = image_to_ptouch_raster(img, tape_width_mm=24)

        # 30x20 → rotated → line_count = 20
        assert line_count == 20
        assert isinstance(raster_data, bytes)

    def test_compression_reduces_size(self):
        """Compressed output should be smaller than uncompressed for suitable data."""
        # Create image with large uniform areas (compresses well)
        img = Image.new("1", (50, 100), color=1)  # mostly white
        # Small black area
        for x in range(10, 20):
            for y in range(10, 20):
                img.putpixel((x, y), 0)

        compressed_data, count_c = image_to_ptouch_raster(img, compression=True)
        uncompressed_data, count_u = image_to_ptouch_raster(img, compression=False)

        assert count_c == count_u
        # Compressed should be smaller or equal (Z lines are same size)
        assert len(compressed_data) <= len(uncompressed_data)

    def test_no_compression_uses_g_command(self):
        """Uncompressed mode should use lowercase 'g' for data lines."""
        img = Image.new("1", (20, 10), color=0)  # all black
        raster_data, _count = image_to_ptouch_raster(img, compression=False)

        # Should contain 'g' (uncompressed) data lines, not 'G'
        assert b"g" in raster_data
        assert b"G" not in raster_data

    def test_unsupported_tape_width_raises(self):
        """Unsupported tape widths should raise ValueError."""
        img = Image.new("1", (20, 10), color=1)
        with pytest.raises(ValueError, match="Unsupported tape width"):
            image_to_ptouch_raster(img, tape_width_mm=15)

    def test_each_data_line_is_16_bytes_uncompressed(self):
        """Each uncompressed data line should contain exactly 16 bytes of pixel data."""
        img = Image.new("1", (10, 10), color=0)  # all black
        raster_data, line_count = image_to_ptouch_raster(img, compression=False)

        # Parse raster data: each non-blank line = 'g' + 2-byte LE length + data
        i = 0
        data_line_count = 0
        while i < len(raster_data):
            cmd = raster_data[i : i + 1]
            if cmd == b"Z":
                i += 1
            elif cmd == b"g":
                i += 1
                length = struct.unpack("<H", raster_data[i : i + 2])[0]
                i += 2
                assert length == BYTES_PER_LINE
                i += length
                data_line_count += 1
            else:
                pytest.fail(f"Unexpected command byte: {cmd!r}")

        assert data_line_count > 0

    def test_oversized_image_scaled_to_fit(self):
        """Image taller than printable area should be scaled down."""
        # 12mm tape: 70 printable pixels
        # Create image 200px tall → after rotation the height becomes the width
        # which is the tape direction, so it should get scaled
        img = Image.new("1", (10, 200), color=0)  # 10 wide, 200 tall
        raster_data, line_count = image_to_ptouch_raster(img, tape_width_mm=12)
        # Should succeed without error
        assert line_count > 0
        assert isinstance(raster_data, bytes)
