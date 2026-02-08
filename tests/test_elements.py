"""Tests for element renderers."""

import pytest
from PIL import Image, ImageDraw

from labelable.models.template import (
    BoundingBox,
    DataMatrixElement,
    EngineType,
    ErrorCorrectionLevel,
    HorizontalAlignment,
    LabelDimensions,
    LabelShape,
    QRCodeElement,
    TemplateConfig,
    TextElement,
    VerticalAlignment,
)
from labelable.templates.elements import (
    DataMatrixElementRenderer,
    QRCodeElementRenderer,
    TextElementRenderer,
)
from labelable.templates.fonts import FontManager


@pytest.fixture
def font_manager():
    """Create a font manager instance."""
    return FontManager()


@pytest.fixture
def template():
    """Create a basic template for testing."""
    return TemplateConfig(
        name="test",
        engine=EngineType.IMAGE,
        dimensions=LabelDimensions(width_mm=50, height_mm=25),
        dpi=203,
    )


@pytest.fixture
def circular_template():
    """Create a circular template for testing."""
    return TemplateConfig(
        name="test-circle",
        engine=EngineType.IMAGE,
        shape=LabelShape.CIRCLE,
        dimensions=LabelDimensions(diameter_mm=50),
        dpi=203,
    )


@pytest.fixture
def image_and_draw():
    """Create a test image and draw object."""
    img = Image.new("1", (400, 200), color=1)  # White background
    draw = ImageDraw.Draw(img)
    return img, draw


class TestTextElementRenderer:
    """Tests for TextElementRenderer."""

    def test_render_basic_text(self, font_manager, template, image_and_draw):
        """Render basic text."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=10),
            font_size=14,
        )

        renderer.render(draw, image, element, {"title": "Hello"}, template)

        # Check that something was drawn (image should have black pixels)
        pixels = list(image.getdata())
        assert 0 in pixels  # Should have black pixels

    def test_render_static_text(self, font_manager, template, image_and_draw):
        """Render static text."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            static_text="Static Text",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=10),
            font_size=14,
        )

        renderer.render(draw, image, element, {}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_render_empty_text_does_nothing(self, font_manager, template, image_and_draw):
        """Empty text should not draw anything."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=10),
            font_size=14,
        )

        renderer.render(draw, image, element, {"title": ""}, template)

        # Image should be all white (no black pixels)
        pixels = list(image.getdata())
        assert all(p == 1 for p in pixels)

    def test_word_wrap_splits_text(self, font_manager, template, image_and_draw):
        """Word wrap should split long text into multiple lines."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=20, height_mm=20),
            font_size=14,
            wrap=True,
        )

        renderer.render(draw, image, element, {"title": "This is a very long text that should wrap"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_horizontal_alignment_center(self, font_manager, template, image_and_draw):
        """Center alignment should center text."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=10),
            font_size=14,
            alignment=HorizontalAlignment.CENTER,
        )

        renderer.render(draw, image, element, {"title": "Center"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_horizontal_alignment_right(self, font_manager, template, image_and_draw):
        """Right alignment should right-align text."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=10),
            font_size=14,
            alignment=HorizontalAlignment.RIGHT,
        )

        renderer.render(draw, image, element, {"title": "Right"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_vertical_alignment_middle(self, font_manager, template, image_and_draw):
        """Middle vertical alignment should center text vertically."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=20),
            font_size=14,
            vertical_align=VerticalAlignment.MIDDLE,
        )

        renderer.render(draw, image, element, {"title": "Middle"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_vertical_alignment_bottom(self, font_manager, template, image_and_draw):
        """Bottom vertical alignment should bottom-align text."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=20),
            font_size=14,
            vertical_align=VerticalAlignment.BOTTOM,
        )

        renderer.render(draw, image, element, {"title": "Bottom"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_auto_scale_reduces_font_size(self, font_manager, template, image_and_draw):
        """Auto-scale should reduce font size for long text."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=10, height_mm=5),
            font_size=48,  # Very large font
            auto_scale=True,
        )

        # Should not raise - auto-scale will reduce font size to fit
        renderer.render(draw, image, element, {"title": "Long text here"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_circle_aware_wrapping(self, font_manager, circular_template, image_and_draw):
        """Circle-aware wrapping should work for circular templates."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=5, y_mm=20, width_mm=40, height_mm=10),
            font_size=12,
            alignment=HorizontalAlignment.CENTER,
            wrap=True,
            circle_aware=True,
        )

        renderer.render(draw, image, element, {"title": "Circle aware text"}, circular_template)

        pixels = list(image.getdata())
        assert 0 in pixels


class TestQRCodeElementRenderer:
    """Tests for QRCodeElementRenderer."""

    def test_render_qrcode(self, font_manager, template):
        """Render QR code."""
        renderer = QRCodeElementRenderer(font_manager)

        if not renderer._qrcode_available:
            pytest.skip("qrcode library not available")

        image = Image.new("1", (400, 200), color=1)
        draw = ImageDraw.Draw(image)

        element = QRCodeElement(
            field="code",
            x_mm=12,
            y_mm=12,
            size_mm=20,
            error_correction=ErrorCorrectionLevel.M,
        )

        renderer.render(draw, image, element, {"code": "https://example.com"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_render_qrcode_error_levels(self, font_manager, template):
        """Test different error correction levels."""
        renderer = QRCodeElementRenderer(font_manager)

        if not renderer._qrcode_available:
            pytest.skip("qrcode library not available")

        for level in [ErrorCorrectionLevel.L, ErrorCorrectionLevel.M, ErrorCorrectionLevel.Q, ErrorCorrectionLevel.H]:
            image = Image.new("1", (400, 200), color=1)
            draw = ImageDraw.Draw(image)

            element = QRCodeElement(
                field="code",
                x_mm=12,
                y_mm=12,
                size_mm=20,
                error_correction=level,
            )

            renderer.render(draw, image, element, {"code": "test"}, template)

            pixels = list(image.getdata())
            assert 0 in pixels

    def test_empty_data_does_nothing(self, font_manager, template):
        """Empty data should not render anything."""
        renderer = QRCodeElementRenderer(font_manager)

        if not renderer._qrcode_available:
            pytest.skip("qrcode library not available")

        image = Image.new("1", (400, 200), color=1)
        draw = ImageDraw.Draw(image)

        element = QRCodeElement(
            field="code",
            x_mm=12,
            y_mm=12,
            size_mm=20,
        )

        renderer.render(draw, image, element, {"code": ""}, template)

        # Image should be all white
        pixels = list(image.getdata())
        assert all(p == 1 for p in pixels)


class TestDataMatrixElementRenderer:
    """Tests for DataMatrixElementRenderer."""

    def test_render_datamatrix(self, font_manager, template):
        """Render DataMatrix."""
        renderer = DataMatrixElementRenderer(font_manager)

        if not renderer._pylibdmtx_available:
            pytest.skip("pylibdmtx library not available")

        image = Image.new("1", (400, 200), color=1)
        draw = ImageDraw.Draw(image)

        element = DataMatrixElement(
            field="code",
            x_mm=12,
            y_mm=12,
            size_mm=20,
        )

        renderer.render(draw, image, element, {"code": "DM123456"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels

    def test_empty_data_does_nothing(self, font_manager, template):
        """Empty data should not render anything."""
        renderer = DataMatrixElementRenderer(font_manager)

        if not renderer._pylibdmtx_available:
            pytest.skip("pylibdmtx library not available")

        image = Image.new("1", (400, 200), color=1)
        draw = ImageDraw.Draw(image)

        element = DataMatrixElement(
            field="code",
            x_mm=12,
            y_mm=12,
            size_mm=20,
        )

        renderer.render(draw, image, element, {"code": ""}, template)

        # Image should be all white
        pixels = list(image.getdata())
        assert all(p == 1 for p in pixels)
