"""Tests for image to printer command converters."""

from PIL import Image

from labelable.templates.converters import image_to_epl2, image_to_zpl


class TestZPLConverter:
    """Tests for ZPL converter."""

    def test_simple_8x8_pattern(self):
        """Test 8x8 test pattern converts correctly."""
        # Create 8x8 image with checkerboard pattern
        image = Image.new("1", (8, 8), color=1)  # White
        pixels = image.load()

        # Draw a simple pattern - top row black
        for x in range(8):
            pixels[x, 0] = 0  # Black

        output = image_to_zpl(image)

        assert b"^XA" in output
        assert b"^GFA" in output
        assert b"^XZ" in output

        # Should have 8 bytes total (1 byte per row * 8 rows)
        # Format: ^GFA,total,total,bytes_per_row,hex_data
        assert b",8,8,1," in output

    def test_non_byte_aligned_width(self):
        """Test image with width not divisible by 8."""
        # Create 10x4 image - should be padded to 2 bytes per row
        image = Image.new("1", (10, 4), color=1)  # White
        pixels = image.load()

        # Draw some pixels
        pixels[0, 0] = 0  # Black pixel

        output = image_to_zpl(image)

        assert b"^XA" in output
        # 2 bytes per row * 4 rows = 8 bytes total
        assert b",8,8,2," in output

    def test_black_and_white_pixels(self):
        """Test that black and white pixels map correctly."""
        # Create small test image
        image = Image.new("1", (8, 1), color=1)  # All white
        pixels = image.load()

        # Make first pixel black
        pixels[0, 0] = 0

        output = image_to_zpl(image)

        # First pixel black = bit 7 set = 0x80
        assert b"80" in output

    def test_all_black_image(self):
        """Test all black image."""
        image = Image.new("1", (8, 1), color=0)  # All black

        output = image_to_zpl(image)

        # All 8 bits set = 0xFF
        assert b"FF" in output

    def test_all_white_image(self):
        """Test all white image."""
        image = Image.new("1", (8, 1), color=1)  # All white

        output = image_to_zpl(image)

        # No bits set = 0x00
        assert b"00" in output

    def test_rgb_image_converts(self):
        """Test that RGB images are converted to 1-bit."""
        image = Image.new("RGB", (8, 1), color="white")
        pixels = image.load()

        # Make first pixel black
        pixels[0, 0] = (0, 0, 0)

        output = image_to_zpl(image)

        assert b"^GFA" in output


class TestEPL2Converter:
    """Tests for EPL2 converter."""

    def test_simple_8x8_pattern(self):
        """Test 8x8 test pattern converts correctly."""
        # Create 8x8 image with all black first row
        image = Image.new("1", (8, 8), color=1)  # White
        pixels = image.load()

        for x in range(8):
            pixels[x, 0] = 0  # Black

        output = image_to_epl2(image)

        assert b"N\n" in output
        assert b"GW0,0,1,8," in output
        assert b"P1" in output

    def test_non_byte_aligned_width(self):
        """Test image with width not divisible by 8."""
        image = Image.new("1", (10, 4), color=1)
        pixels = image.load()

        pixels[0, 0] = 0  # Black pixel

        output = image_to_epl2(image)

        # 2 bytes per row * 4 rows
        assert b"GW0,0,2,4," in output

    def test_black_and_white_pixels(self):
        """Test that black and white pixels map correctly."""
        image = Image.new("1", (8, 1), color=1)  # All white
        pixels = image.load()

        pixels[0, 0] = 0  # First pixel black

        output = image_to_epl2(image)

        # Binary output should have 0x80 for first byte
        # Find the data portion after "GW0,0,1,1,"
        assert b"GW0,0,1,1," in output
        # The byte after the comma should be 0x80
        idx = output.find(b"GW0,0,1,1,") + len(b"GW0,0,1,1,")
        assert output[idx] == 0x80

    def test_all_black_image(self):
        """Test all black image."""
        image = Image.new("1", (8, 1), color=0)

        output = image_to_epl2(image)

        # Find data byte
        idx = output.find(b"GW0,0,1,1,") + len(b"GW0,0,1,1,")
        assert output[idx] == 0xFF

    def test_all_white_image(self):
        """Test all white image."""
        image = Image.new("1", (8, 1), color=1)

        output = image_to_epl2(image)

        # Find data byte
        idx = output.find(b"GW0,0,1,1,") + len(b"GW0,0,1,1,")
        assert output[idx] == 0x00

    def test_rgb_image_converts(self):
        """Test that RGB images are converted to 1-bit."""
        image = Image.new("RGB", (8, 1), color="white")
        pixels = image.load()

        pixels[0, 0] = (0, 0, 0)  # Black

        output = image_to_epl2(image)

        assert b"GW" in output
