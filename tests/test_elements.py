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

    def test_line_spacing_default(self, font_manager, template, image_and_draw):
        """Default line spacing should be 1.0."""
        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=20, height_mm=20),
        )
        assert element.line_spacing == 1.0

    def test_line_spacing_renders_wrapped_text(self, font_manager, template, image_and_draw):
        """Line spacing should work with wrapped text."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=15, height_mm=20),
            font_size=14,
            wrap=True,
            line_spacing=1.5,  # 50% extra space between lines
        )

        renderer.render(draw, image, element, {"title": "Line one and line two"}, template)

        pixels = list(image.getdata())
        assert 0 in pixels  # Text was rendered

    def test_line_spacing_increases_gap(self, font_manager, template):
        """Higher line spacing should result in more vertical space used."""
        from PIL import Image, ImageDraw

        renderer = TextElementRenderer(font_manager)
        text = "First line second line"

        # Render with normal spacing
        img1 = Image.new("1", (200, 200), color=1)
        draw1 = ImageDraw.Draw(img1)
        element1 = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=15, height_mm=30),
            font_size=14,
            wrap=True,
            line_spacing=1.0,
        )
        renderer.render(draw1, img1, element1, {"title": text}, template)

        # Render with increased spacing
        img2 = Image.new("1", (200, 200), color=1)
        draw2 = ImageDraw.Draw(img2)
        element2 = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=15, height_mm=30),
            font_size=14,
            wrap=True,
            line_spacing=2.0,  # Double spacing
        )
        renderer.render(draw2, img2, element2, {"title": text}, template)

        # Both should render text
        pixels1 = list(img1.getdata())
        pixels2 = list(img2.getdata())
        assert 0 in pixels1
        assert 0 in pixels2

        # Find the vertical extent of black pixels in each image
        def get_vertical_extent(img):
            width, height = img.size
            min_y, max_y = height, 0
            for y in range(height):
                for x in range(width):
                    if img.getpixel((x, y)) == 0:
                        min_y = min(min_y, y)
                        max_y = max(max_y, y)
            return max_y - min_y if max_y > min_y else 0

        extent1 = get_vertical_extent(img1)
        extent2 = get_vertical_extent(img2)

        # With double line spacing, vertical extent should be larger
        assert extent2 > extent1

    def test_line_spacing_with_auto_scale(self, font_manager, template, image_and_draw):
        """Line spacing should work together with auto-scale."""
        image, draw = image_and_draw
        renderer = TextElementRenderer(font_manager)

        element = TextElement(
            field="title",
            bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=20, height_mm=15),
            font_size=48,
            wrap=True,
            auto_scale=True,
            line_spacing=1.3,
        )

        renderer.render(draw, image, element, {"title": "Auto scale with spacing"}, template)

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
