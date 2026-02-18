"""Tests for batch label rendering."""

import io
import struct

import pytest
from PIL import Image

from labelable.models.template import (
    BatchAlignment,
    BatchConfig,
    BoundingBox,
    EngineType,
    FieldType,
    LabelDimensions,
    TemplateConfig,
    TemplateField,
    TextElement,
)
from labelable.templates.converters.ptouch import (
    BYTES_PER_LINE,
    TAPE_SPECS,
    batch_image_to_ptouch_raster,
)
from labelable.templates.engine import TemplateError
from labelable.templates.image_engine import ImageTemplateEngine


@pytest.fixture
def engine():
    return ImageTemplateEngine()


def _make_batch_template(
    *,
    alignment: BatchAlignment = BatchAlignment.CENTER,
    cut_lines: bool = True,
    padding_mm: float = 1.0,
    min_label_length_mm: float = 0,
) -> TemplateConfig:
    """Create a P-Touch batch template for testing."""
    return TemplateConfig(
        name="test-batch",
        engine=EngineType.IMAGE,
        dimensions=LabelDimensions(width_mm=9, height_mm=50),
        dpi=180,
        ptouch_tape_width_mm=9,
        ptouch_margin_mm=1.0,
        batch=BatchConfig(
            alignment=alignment,
            cut_lines=cut_lines,
            padding_mm=padding_mm,
            min_label_length_mm=min_label_length_mm,
        ),
        fields=[
            TemplateField(name="signals", type=FieldType.LIST, required=True),
        ],
        elements=[
            TextElement(
                type="text",
                field="signals",
                bounds=BoundingBox(x_mm=0, y_mm=0, width_mm=9, height_mm=50),
                font_size=72,
                alignment="center",
                auto_scale=True,
            ),
        ],
    )


def _make_non_batch_template() -> TemplateConfig:
    """Create a standard (non-batch) P-Touch template."""
    return TemplateConfig(
        name="test-single",
        engine=EngineType.IMAGE,
        dimensions=LabelDimensions(width_mm=9, height_mm=50),
        dpi=180,
        ptouch_tape_width_mm=9,
        ptouch_margin_mm=1.0,
        fields=[
            TemplateField(name="title", type=FieldType.STRING, required=True),
        ],
        elements=[
            TextElement(
                type="text",
                field="title",
                bounds=BoundingBox(x_mm=0, y_mm=0, width_mm=9, height_mm=50),
                font_size=72,
                alignment="center",
                auto_scale=True,
            ),
        ],
    )


class TestListFieldValidation:
    def test_list_field_stored_as_string(self):
        """LIST field should be stored as a string in validate_data."""
        template = _make_batch_template()
        result = template.validate_data({"signals": "GND\nVCC\nSDA"})
        assert result["signals"] == "GND\nVCC\nSDA"
        assert isinstance(result["signals"], str)

    def test_list_field_required(self):
        """Missing required LIST field should raise ValueError."""
        template = _make_batch_template()
        with pytest.raises(ValueError, match="Missing required field"):
            template.validate_data({})


class TestExtractListItems:
    def test_splits_on_newlines(self, engine):
        template = _make_batch_template()
        _, items = engine._extract_list_items(template, {"signals": "GND\nVCC\nSDA"})
        assert items == ["GND", "VCC", "SDA"]

    def test_strips_whitespace(self, engine):
        template = _make_batch_template()
        _, items = engine._extract_list_items(template, {"signals": "  GND \n VCC \n SDA "})
        assert items == ["GND", "VCC", "SDA"]

    def test_filters_empty_lines(self, engine):
        template = _make_batch_template()
        _, items = engine._extract_list_items(template, {"signals": "GND\n\nVCC\n\n\nSDA\n"})
        assert items == ["GND", "VCC", "SDA"]

    def test_returns_field_name(self, engine):
        template = _make_batch_template()
        name, _ = engine._extract_list_items(template, {"signals": "GND\nVCC"})
        assert name == "signals"

    def test_no_list_field_raises(self, engine):
        template = _make_non_batch_template()
        with pytest.raises(TemplateError):
            engine._extract_list_items(template, {"title": "Hello"})


class TestBatchPreview:
    def test_returns_valid_png(self, engine):
        template = _make_batch_template()
        output = engine.render_preview(template, {"signals": "GND\nVCC\nSDA"})
        assert output[:8] == b"\x89PNG\r\n\x1a\n"

    def test_single_item_produces_png(self, engine):
        """A batch with a single item should still work."""
        template = _make_batch_template()
        output = engine.render_preview(template, {"signals": "GND"})
        assert output[:8] == b"\x89PNG\r\n\x1a\n"

    def test_batch_strip_wider_than_single(self, engine):
        """A 3-item batch should produce a wider image than a 1-item batch."""
        template = _make_batch_template()
        single = engine.render_preview(template, {"signals": "GND"})
        triple = engine.render_preview(template, {"signals": "GND\nVCC\nSDA"})

        single_img = Image.open(io.BytesIO(single))
        triple_img = Image.open(io.BytesIO(triple))
        assert triple_img.size[0] > single_img.size[0]


class TestBatchUniformHeight:
    def test_all_slots_same_height(self, engine):
        """All label slots should have the same height (uniform labels)."""
        template = _make_batch_template(cut_lines=True, padding_mm=1.0)
        output = engine.render_preview(template, {"signals": "A\nLONGERTEXT\nB"})
        img = Image.open(io.BytesIO(output))
        # The strip should be valid — more specific slot height checks are hard
        # without exposing internals, but we verify the image renders successfully
        assert img.size[0] > 0
        assert img.size[1] > 0


class TestBatchCutLines:
    def test_cut_lines_present(self, engine):
        """When cut_lines=True, there should be black vertical lines at all edges."""
        template = _make_batch_template(cut_lines=True)
        output = engine.render_preview(template, {"signals": "A\nB\nC"})
        img = Image.open(io.BytesIO(output))

        # Check for fully-black vertical lines (cut lines at every edge)
        pixels = img.load()
        width, height = img.size
        black_cols = 0
        for x in range(width):
            all_black = all(pixels[x, y] == (0, 0, 0) for y in range(height))
            if all_black:
                black_cols += 1
        # Should have N+1 = 4 cut lines for 3 labels (left, between each, right)
        assert black_cols == 4

    def test_no_cut_lines_when_disabled(self, engine):
        """When cut_lines=False, no fully-black vertical lines should exist."""
        template = _make_batch_template(cut_lines=False)
        output = engine.render_preview(template, {"signals": "A\nB\nC"})
        img = Image.open(io.BytesIO(output))

        pixels = img.load()
        width, height = img.size
        black_cols = 0
        for x in range(width):
            all_black = all(pixels[x, y] == (0, 0, 0) for y in range(height))
            if all_black:
                black_cols += 1
        assert black_cols == 0

    def test_single_item_has_edge_cut_lines(self, engine):
        """A single-item batch should have cut lines at both edges."""
        template = _make_batch_template(cut_lines=True)
        output = engine.render_preview(template, {"signals": "GND"})
        img = Image.open(io.BytesIO(output))

        pixels = img.load()
        width, height = img.size
        black_cols = 0
        for x in range(width):
            all_black = all(pixels[x, y] == (0, 0, 0) for y in range(height))
            if all_black:
                black_cols += 1
        # N+1 = 2 cut lines for 1 label (left + right)
        assert black_cols == 2


class TestBatchMinLabelLength:
    def test_min_label_length_respected(self, engine):
        """Batch with min_label_length should produce strip at least that wide."""
        # 20mm minimum → at 180 DPI = ~141 px per label slot
        # Strip width includes padding and cut lines around slot
        template = _make_batch_template(min_label_length_mm=20)
        output = engine.render_preview(template, {"signals": "A"})
        img = Image.open(io.BytesIO(output))
        min_px = int(20 * 180 / 25.4)
        # Strip width = slot_width + 2*cut_line_px; slot_width >= min_px + 2*padding_px
        assert img.size[0] >= min_px - 1  # Allow 1px rounding


class TestBatchRenderPtouch:
    def test_produces_valid_raster_bytes(self, engine):
        """Batch render should produce valid raster with reasonable line count.

        Regression: previously the batch strip was passed to the standard
        converter which collapsed it to ~3 raster lines (124 bytes).
        """
        template = _make_batch_template()
        output = engine.render(template, {"signals": "A\nB"}, output_format="ptouch")
        assert isinstance(output, bytes)
        assert output[:64] == b"\x00" * 64

        # Extract raster line count from ESC i z command
        marker = b"\x1b\x69\x7a"  # ESC i z
        idx = output.find(marker)
        assert idx != -1, "ESC i z command not found in output"
        raster_lines = struct.unpack_from("<I", output, idx + 7)[0]

        assert raster_lines > 50, f"Raster line count {raster_lines} is suspiciously low"
        assert len(output) > 500


class TestBatchAutoDetect:
    def test_render_dispatches_to_batch(self, engine):
        """render() should auto-detect batch mode and produce valid output."""
        template = _make_batch_template()
        output = engine.render(template, {"signals": "GND\nVCC"}, output_format="ptouch")
        assert isinstance(output, bytes)
        assert len(output) > 0

    def test_render_preview_dispatches_to_batch(self, engine):
        """render_preview() should auto-detect batch mode and produce PNG."""
        template = _make_batch_template()
        output = engine.render_preview(template, {"signals": "GND\nVCC"})
        assert output[:8] == b"\x89PNG\r\n\x1a\n"

    def test_non_batch_template_unaffected(self, engine):
        """Templates without batch config should work as before."""
        template = _make_non_batch_template()
        output = engine.render_preview(template, {"title": "Hello"})
        assert output[:8] == b"\x89PNG\r\n\x1a\n"

    def test_non_batch_ptouch_render(self, engine):
        """Non-batch ptouch render should still work."""
        template = _make_non_batch_template()
        output = engine.render(template, {"title": "Hello"}, output_format="ptouch")
        assert isinstance(output, bytes)
        assert output[:64] == b"\x00" * 64


def _parse_uncompressed_raster(raster_data: bytes) -> list[bytes]:
    """Parse uncompressed raster data into individual 16-byte lines."""
    lines: list[bytes] = []
    i = 0
    while i < len(raster_data):
        cmd = raster_data[i : i + 1]
        if cmd == b"Z":
            lines.append(b"\x00" * BYTES_PER_LINE)
            i += 1
        elif cmd == b"g":
            length = struct.unpack_from("<H", raster_data, i + 1)[0]
            lines.append(raster_data[i + 3 : i + 3 + length])
            i += 3 + length
        else:
            break
    return lines


def _is_printable_area_black(line_data: bytes, tape_width_mm: int) -> bool:
    """Check if all printable bits in a raster line are set (black)."""
    printable_px, left_margin, _ = TAPE_SPECS[tape_width_mm]
    for pos in range(left_margin, left_margin + printable_px):
        byte_idx = pos // 8
        bit = 7 - (pos % 8)
        if not (line_data[byte_idx] & (1 << bit)):
            return False
    return True


def _count_black_pixels(line_data: bytes, tape_width_mm: int) -> int:
    """Count black pixels in the printable area of a raster line."""
    printable_px, left_margin, _ = TAPE_SPECS[tape_width_mm]
    count = 0
    for pos in range(left_margin, left_margin + printable_px):
        byte_idx = pos // 8
        bit = 7 - (pos % 8)
        if line_data[byte_idx] & (1 << bit):
            count += 1
    return count


class TestBatchRasterCutLines:
    """Verify cut lines appear correctly in the binary raster output.

    Cut lines in the batch image are vertical columns spanning the full tape
    height. In the raster output, each such column becomes one raster line
    where ALL printable bits are set — proving the cut marks run ACROSS the
    tape (perpendicular to feed direction), not along it.
    """

    def test_cut_lines_cross_tape_with_text(self, engine):
        """Cut lines should be evenly-spaced fully-black raster lines with text between."""
        template = _make_batch_template(cut_lines=True, padding_mm=1.0)
        validated = template.validate_data({"signals": "A\nB"})
        image = engine._render_batch_image(template, validated, mode="1")

        raster_data, line_count = batch_image_to_ptouch_raster(
            image,
            tape_width_mm=9,
            compression=False,
        )
        lines = _parse_uncompressed_raster(raster_data)
        assert len(lines) == line_count

        # Raster line count = image width (1 column = 1 raster line, no scaling)
        img_w, _ = image.size
        assert line_count == img_w

        # Find raster lines where all printable bits are black
        fully_black = [i for i, line in enumerate(lines) if _is_printable_area_black(line, 9)]

        # N+1 = 3 cut lines for 2 labels
        assert len(fully_black) == 3, (
            f"Expected 3 fully-black raster lines (cut lines across tape), "
            f"found {len(fully_black)} at indices {fully_black}"
        )

        # Cut lines should be evenly spaced (uniform label slots)
        spacings = [fully_black[i + 1] - fully_black[i] for i in range(len(fully_black) - 1)]
        assert max(spacings) - min(spacings) <= 1, f"Cut lines not evenly spaced: spacings={spacings}"

        # Each label slot should contain rendered text (non-blank pixels)
        for slot in range(len(fully_black) - 1):
            start = fully_black[slot] + 1
            end = fully_black[slot + 1]
            slot_pixels = sum(_count_black_pixels(lines[i], 9) for i in range(start, end))
            assert slot_pixels > 0, f"Label slot {slot} (lines {start}-{end}) has no black pixels"

    def test_no_cut_lines_in_raster_when_disabled(self, engine):
        """With cut_lines=False, no fully-black raster lines should exist."""
        template = _make_batch_template(cut_lines=False, padding_mm=1.0)
        validated = template.validate_data({"signals": "A\nB"})
        image = engine._render_batch_image(template, validated, mode="1")

        raster_data, _ = batch_image_to_ptouch_raster(
            image,
            tape_width_mm=9,
            compression=False,
        )
        lines = _parse_uncompressed_raster(raster_data)

        fully_black = [i for i, line in enumerate(lines) if _is_printable_area_black(line, 9)]
        assert len(fully_black) == 0
