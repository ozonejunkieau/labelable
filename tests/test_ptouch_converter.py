"""Tests for P-Touch raster converter and PackBits encoding."""

import hashlib
import struct

import pytest
from PIL import Image

from labelable.templates.converters.ptouch import (
    BYTES_PER_LINE,
    PRINT_HEAD_PIXELS,
    TAPE_SPECS,
    _packbits_encode,
    batch_image_to_ptouch_raster,
    batch_image_to_raster_rows,
    encode_raster_rows,
    image_to_ptouch_raster,
    image_to_raster_rows,
)


def _pin_set(row: bytes) -> set[int]:
    """Return the set of head pin indices set in a 16-byte raster row."""
    return {n for n in range(PRINT_HEAD_PIXELS) if row[n // 8] & (1 << (7 - (n % 8)))}


def _dense_image(w: int, h: int, seed: int = 0) -> Image.Image:
    """Deterministic mixed-content 1-bit image."""
    img = Image.new("1", (w, h), color=1)
    for x in range(w):
        for y in range(h):
            if (x * 7 + y * 13 + seed) % 11 < 4:
                img.putpixel((x, y), 0)
    return img


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


class TestRasterRows:
    """Row-level assertions on the uncompressed 128-pin rows."""

    @pytest.mark.parametrize("tape", sorted(TAPE_SPECS))
    def test_every_row_is_16_bytes(self, tape):
        img = _dense_image(20, 30, seed=tape)
        rows = image_to_raster_rows(img, tape_width_mm=tape)
        assert rows
        assert all(len(r) == BYTES_PER_LINE for r in rows)

        batch_rows = batch_image_to_raster_rows(img, tape_width_mm=tape)
        assert batch_rows
        assert all(len(r) == BYTES_PER_LINE for r in batch_rows)

    @pytest.mark.parametrize("tape", sorted(TAPE_SPECS))
    def test_margin_pins_are_zero(self, tape):
        """Pins outside the tape's printable window must never be set."""
        printable, left, right = TAPE_SPECS[tape]
        assert left + printable + right == PRINT_HEAD_PIXELS

        # All-black image: every printable pin is a candidate.
        img = Image.new("1", (20, 30), color=0)
        for rows in (
            image_to_raster_rows(img, tape_width_mm=tape),
            batch_image_to_raster_rows(img, tape_width_mm=tape),
        ):
            for row in rows:
                pins = _pin_set(row)
                assert all(left <= p < left + printable for p in pins)

    def test_9mm_zero_pins_explicit(self):
        """9mm tape: only pins 39-88 are printable (spec section 4).

        Bits 0-38 and 89-127 must be zero in every row; content set there is
        silently discarded by the hardware and shifts the image off-tape.
        """
        img = Image.new("1", (20, 30), color=0)  # all black
        for rows in (
            image_to_raster_rows(img, tape_width_mm=9),
            batch_image_to_raster_rows(img, tape_width_mm=9),
        ):
            all_pins: set[int] = set()
            for row in rows:
                pins = _pin_set(row)
                assert not (pins & set(range(0, 39)))
                assert not (pins & set(range(89, 128)))
                all_pins |= pins
            assert all_pins  # something is actually printed

    def test_tape_specs_match_bridge_spec(self):
        """Margin table must match ptouch-bridge IMPLEMENTATION.md section 4."""
        assert TAPE_SPECS[6] == (32, 48, 48)
        assert TAPE_SPECS[9] == (50, 39, 39)
        assert TAPE_SPECS[12] == (70, 29, 29)
        assert TAPE_SPECS[18] == (112, 8, 8)
        assert TAPE_SPECS[24] == (128, 0, 0)

    def test_all_white_rows_are_zero(self):
        img = Image.new("1", (20, 30), color=1)
        rows = image_to_raster_rows(img, tape_width_mm=9)
        assert rows
        assert all(row == b"\x00" * BYTES_PER_LINE for row in rows)

    def test_unsupported_tape_width_raises(self):
        img = Image.new("1", (20, 10), color=1)
        with pytest.raises(ValueError, match="Unsupported tape width"):
            image_to_raster_rows(img, tape_width_mm=15)
        with pytest.raises(ValueError, match="Unsupported tape width"):
            batch_image_to_raster_rows(img, tape_width_mm=15)


class TestEncoderRoundTrip:
    """The Z/G/g encoders must stay byte-identical to the pre-refactor output."""

    @staticmethod
    def _reference_encode(rows: list[bytes], compression: bool) -> bytes:
        """Re-implementation of the pre-refactor inline encoding loop."""
        out = bytearray()
        for line_data in rows:
            is_blank = all(b == 0 for b in line_data)
            if is_blank:
                out.extend(b"Z")
            elif compression:
                compressed = _packbits_encode(bytes(line_data))
                out.extend(b"G")
                out.extend(struct.pack("<H", len(compressed)))
                out.extend(compressed)
            else:
                out.extend(b"g")
                out.extend(struct.pack("<H", len(line_data)))
                out.extend(line_data)
        return bytes(out)

    @pytest.mark.parametrize("tape", sorted(TAPE_SPECS))
    @pytest.mark.parametrize("compression", [True, False])
    def test_single_encoder_matches_reference(self, tape, compression):
        img = _dense_image(20, 40, seed=tape)
        rows = image_to_raster_rows(img, tape_width_mm=tape)
        data, count = image_to_ptouch_raster(img, tape_width_mm=tape, compression=compression)
        assert count == len(rows)
        assert data == self._reference_encode(rows, compression)
        assert data == encode_raster_rows(rows, compression=compression)

    @pytest.mark.parametrize("tape", sorted(TAPE_SPECS))
    @pytest.mark.parametrize("compression", [True, False])
    def test_batch_encoder_matches_reference(self, tape, compression):
        img = _dense_image(30, 17, seed=tape + 1)
        rows = batch_image_to_raster_rows(img, tape_width_mm=tape)
        data, count = batch_image_to_ptouch_raster(img, tape_width_mm=tape, compression=compression)
        assert count == len(rows)
        assert data == self._reference_encode(rows, compression)

    # Digests captured from the pre-refactor implementation, for the batch
    # path only. The single-label digests were deliberately dropped: they
    # pinned output in which the feed extent was written to the print head
    # axis and the tape extent to the feed axis. Orientation is now asserted
    # structurally by TestSingleLabelOrientation instead of by hash.
    GOLDEN = {
        ("b", 9, 20, 40, True): "773d1a40fbe2b7699a529361d5f73c41612a2ab206177ea8bd4de9cc1bf125b5",
        ("b", 18, 30, 17, False): "ac08033593355e978bc9a5556b4a4375ad653c802a43fd89daab416607f3aa23",
        ("b", 24, 5, 5, True): "e4f3076c459b5cdb3f75bdeedca2d7c14d4d63a91c4a4adfa01d6f8cd21f3797",
    }

    @pytest.mark.parametrize("key,digest", sorted(GOLDEN.items()))
    def test_matches_pre_refactor_digests(self, key, digest):
        kind, tape, w, h, compression = key
        img = _dense_image(w, h, seed=tape + w + h)
        fn = image_to_ptouch_raster if kind == "s" else batch_image_to_ptouch_raster
        data, _count = fn(img, tape_width_mm=tape, compression=compression)
        assert hashlib.sha256(data).hexdigest() == digest


class TestSingleLabelOrientation:
    """Single-label geometry: pins carry the tape axis, rows the feed axis.

    Single-label templates are authored portrait (x = tape width,
    y = feed). The converter rotates that into the batch strip layout, so
    a rotated single label and the equivalent batch strip must rasterise
    to the same thing.
    """

    @staticmethod
    def _to_authored(strip: Image.Image) -> Image.Image:
        """Invert the converter's rotation, turning a strip into a label.

        Forward transform is ``rot90(authored)``, so the inverse is
        ``rot270(strip)``.
        """
        return strip.rotate(-90, expand=True)

    @pytest.mark.parametrize("tape", sorted(TAPE_SPECS))
    def test_matches_batch_path_for_equivalent_input(self, tape):
        """A label and the strip it rotates into must produce identical rows."""
        printable_px, _left, _right = TAPE_SPECS[tape]
        # Height already equals the printable window, so neither path rescales
        # and the two are directly comparable.
        strip = _dense_image(37, printable_px, seed=tape)
        authored = self._to_authored(strip)
        assert authored.size == (printable_px, 37)

        assert image_to_raster_rows(authored, tape_width_mm=tape) == batch_image_to_raster_rows(
            strip, tape_width_mm=tape
        )

    @pytest.mark.parametrize("tape", sorted(TAPE_SPECS))
    def test_row_count_tracks_the_feed_axis(self, tape):
        """Row count follows the authored height, not its width."""
        printable_px, _left, _right = TAPE_SPECS[tape]
        # Authored portrait: width = tape axis (already printable-sized so no
        # rescale), height = feed. A long label must yield many rows.
        authored = _dense_image(printable_px, 200, seed=tape)
        rows = image_to_raster_rows(authored, tape_width_mm=tape)
        assert len(rows) == 200

    @pytest.mark.parametrize("tape", sorted(TAPE_SPECS))
    def test_long_label_is_not_clipped_into_the_margin(self, tape):
        """Every set pin stays inside the printable window, however long."""
        printable_px, left, _right = TAPE_SPECS[tape]
        authored = _dense_image(printable_px, 300, seed=tape + 3)
        rows = image_to_raster_rows(authored, tape_width_mm=tape)

        window = set(range(left, left + printable_px))
        for row in rows:
            assert _pin_set(row) <= window

    def test_asymmetric_marker_lands_on_the_feed_axis(self):
        """A mark at the top of the label prints at the start of the tape."""
        tape = 9
        printable_px, left, _right = TAPE_SPECS[tape]
        # Portrait label, black bar across the full tape width at the top.
        authored = Image.new("1", (printable_px, 100), color=1)
        for x in range(printable_px):
            for y in range(10):
                authored.putpixel((x, y), 0)

        rows = image_to_raster_rows(authored, tape_width_mm=tape)
        assert len(rows) == 100

        # Authored y is the feed axis, so a bar at the top of the label
        # prints in the first rows, spanning the full printable window.
        assert _pin_set(rows[0]) == set(range(left, left + printable_px))
        assert _pin_set(rows[9]) == set(range(left, left + printable_px))
        # ...and nothing after it.
        assert all(not _pin_set(r) for r in rows[10:])

    def test_content_is_centred_in_the_printable_window(self):
        """A label narrower than the tape sits centred, not flush to one edge."""
        tape = 24  # printable 128, left margin 0
        printable_px, left, _right = TAPE_SPECS[tape]
        narrow = 40
        authored = Image.new("1", (narrow, 20), color=0)  # all black

        rows = image_to_raster_rows(authored, tape_width_mm=tape)
        pins = _pin_set(rows[0])
        expected_offset = left + (printable_px - narrow) // 2
        assert pins == set(range(expected_offset, expected_offset + narrow))

    def test_transform_is_a_rotation_not_a_reflection(self):
        """An orientation-reversing transform would print every glyph mirrored.

        Three corners of the authored label pin the mapping down. Walking
        left-to-right along the label's top edge must walk consistently
        along one direction of the print head axis, and a reflection
        (rotate + mirror, as an earlier version used) would reverse the
        handedness relative to the feed axis.
        """
        tape = 24  # printable 128, left margin 0 - no scaling, no offset
        w, h = 40, 60
        img = Image.new("1", (w, h), color=1)
        img.putpixel((0, 0), 0)  # top-left
        img.putpixel((w - 1, 0), 0)  # top-right
        img.putpixel((0, h - 1), 0)  # bottom-left

        rows = image_to_raster_rows(img, tape_width_mm=tape)

        def only_pin(row_idx: int) -> set[int]:
            return _pin_set(rows[row_idx])

        # Authored y is the feed axis: the two top corners share row 0,
        # the bottom-left corner is at the last row.
        top = only_pin(0)
        assert len(top) == 2
        assert only_pin(h - 1) == {max(top)}

        # Authored x maps to the pin axis reversed (rotate 90 CCW), so
        # top-left sits at the high pin and top-right at the low pin.
        # Crucially the *span* is the label width - a reflection would
        # place the bottom-left corner against the opposite pin instead.
        assert max(top) - min(top) == w - 1
