"""Tests for the image template engine."""

import pytest

from labelable.models.template import (
    BoundingBox,
    EngineType,
    HorizontalAlignment,
    LabelDimensions,
    LabelShape,
    TemplateConfig,
    TemplateField,
    TextElement,
    VerticalAlignment,
)
from labelable.templates.image_engine import ImageTemplateEngine


@pytest.fixture
def image_engine():
    """Create an image template engine instance."""
    return ImageTemplateEngine()


@pytest.fixture
def rectangular_template():
    """Create a simple rectangular template."""
    return TemplateConfig(
        name="test-rect",
        description="Test rectangular template",
        engine=EngineType.IMAGE,
        shape=LabelShape.RECTANGLE,
        dimensions=LabelDimensions(width_mm=50, height_mm=25),
        dpi=203,
        fields=[
            TemplateField(name="title", type="string", required=True),
        ],
        elements=[
            TextElement(
                type="text",
                field="title",
                bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=10),
                font_size=14,
                alignment=HorizontalAlignment.CENTER,
                vertical_align=VerticalAlignment.MIDDLE,
            ),
        ],
    )


@pytest.fixture
def circular_template():
    """Create a simple circular template."""
    return TemplateConfig(
        name="test-circle",
        description="Test circular template",
        engine=EngineType.IMAGE,
        shape=LabelShape.CIRCLE,
        dimensions=LabelDimensions(diameter_mm=50),
        dpi=203,
        fields=[
            TemplateField(name="title", type="string", required=True),
        ],
        elements=[
            TextElement(
                type="text",
                field="title",
                bounds=BoundingBox(x_mm=5, y_mm=20, width_mm=40, height_mm=10),
                font_size=14,
                alignment=HorizontalAlignment.CENTER,
                vertical_align=VerticalAlignment.MIDDLE,
                circle_aware=True,
            ),
        ],
    )


class TestImageTemplateEngine:
    """Tests for ImageTemplateEngine."""

    def test_supports_zpl(self, image_engine):
        """Engine should support ZPL printers."""
        assert image_engine.supports_printer_type("zpl")
        assert image_engine.supports_printer_type("ZPL")

    def test_supports_epl2(self, image_engine):
        """Engine should support EPL2 printers."""
        assert image_engine.supports_printer_type("epl2")
        assert image_engine.supports_printer_type("EPL2")

    def test_supports_ptouch(self, image_engine):
        """Engine should support P-Touch printers."""
        assert image_engine.supports_printer_type("ptouch")
        assert image_engine.supports_printer_type("PTOUCH")

    def test_render_rectangular_to_zpl(self, image_engine, rectangular_template):
        """Render rectangular template to ZPL format."""
        context = {"title": "Hello World"}
        output = image_engine.render(rectangular_template, context, output_format="zpl")

        assert isinstance(output, bytes)
        assert b"^XA" in output
        assert b"^GFA" in output
        assert b"^XZ" in output

    def test_render_zpl_print_width(self, image_engine, rectangular_template):
        """print_width_mm becomes ^PW in dots at the template's dpi."""
        template = rectangular_template.model_copy(update={"print_width_mm": 104.0, "dpi": 203})
        output = image_engine.render(template, {"title": "Hello World"}, output_format="zpl")

        assert b"^PW831" in output  # 104 mm * 203 / 25.4 = 831.2

    def test_render_rectangular_to_epl2(self, image_engine, rectangular_template):
        """Render rectangular template to EPL2 format."""
        context = {"title": "Hello World"}
        output = image_engine.render(rectangular_template, context, output_format="epl2")

        assert isinstance(output, bytes)
        assert b"N\n" in output
        assert b"GW" in output
        assert b"P1" in output

    def test_render_circular_to_zpl(self, image_engine, circular_template):
        """Render circular template to ZPL format."""
        context = {"title": "Test"}
        output = image_engine.render(circular_template, context, output_format="zpl")

        assert isinstance(output, bytes)
        assert b"^XA" in output
        assert b"^GFA" in output

    def test_render_preview_png(self, image_engine, rectangular_template):
        """Render template to PNG preview."""
        context = {"title": "Preview Test"}
        output = image_engine.render_preview(rectangular_template, context, format="PNG")

        assert isinstance(output, bytes)
        # PNG magic bytes
        assert output[:8] == b"\x89PNG\r\n\x1a\n"

    def test_render_preview_circular_png(self, image_engine, circular_template):
        """Render circular template to PNG preview."""
        context = {"title": "Circle"}
        output = image_engine.render_preview(circular_template, context, format="PNG")

        assert isinstance(output, bytes)
        # PNG magic bytes
        assert output[:8] == b"\x89PNG\r\n\x1a\n"

    def test_render_with_static_text(self, image_engine):
        """Render template with static text element."""
        template = TemplateConfig(
            name="test-static",
            engine=EngineType.IMAGE,
            dimensions=LabelDimensions(width_mm=50, height_mm=25),
            dpi=203,
            elements=[
                TextElement(
                    type="text",
                    static_text="Static Label",
                    bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=46, height_mm=10),
                    font_size=12,
                ),
            ],
        )

        output = image_engine.render(template, {}, output_format="zpl")
        assert isinstance(output, bytes)
        assert b"^GFA" in output

    def test_render_missing_field_uses_empty_string(self, image_engine, rectangular_template):
        """Missing optional fields should render as empty."""
        # Make the field not required
        rectangular_template.fields[0].required = False
        rectangular_template.fields[0].default = ""

        output = image_engine.render(rectangular_template, {}, output_format="zpl")
        assert isinstance(output, bytes)

    def test_render_validates_required_fields(self, image_engine, rectangular_template):
        """Should raise error for missing required fields."""
        from labelable.templates.engine import TemplateError

        with pytest.raises(TemplateError):
            image_engine.render(rectangular_template, {}, output_format="zpl")

    def test_render_ptouch_produces_bytes(self, image_engine):
        """Render template to P-Touch raster format."""
        template = TemplateConfig(
            name="test-ptouch",
            engine=EngineType.IMAGE,
            dimensions=LabelDimensions(width_mm=24, height_mm=80),
            dpi=180,
            ptouch_tape_width_mm=24,
            fields=[
                TemplateField(name="title", type="string", required=True),
            ],
            elements=[
                TextElement(
                    type="text",
                    field="title",
                    bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=20, height_mm=10),
                    font_size=14,
                ),
            ],
        )

        output = image_engine.render(template, {"title": "Hello"}, output_format="ptouch")
        assert isinstance(output, bytes)
        assert len(output) > 0

    def test_ptouch_starts_with_init(self, image_engine):
        """P-Touch output should start with 64 null bytes (init sequence)."""
        template = TemplateConfig(
            name="test-ptouch-init",
            engine=EngineType.IMAGE,
            dimensions=LabelDimensions(width_mm=24, height_mm=40),
            dpi=180,
            ptouch_tape_width_mm=24,
            fields=[
                TemplateField(name="title", type="string", required=True),
            ],
            elements=[
                TextElement(
                    type="text",
                    field="title",
                    bounds=BoundingBox(x_mm=2, y_mm=2, width_mm=20, height_mm=10),
                    font_size=14,
                ),
            ],
        )

        output = image_engine.render(template, {"title": "Test"}, output_format="ptouch")
        # First 64 bytes should be null (CMD_INITIALIZE)
        assert output[:64] == b"\x00" * 64


class TestTemplateFontPaths:
    """A template's font_paths must only affect that template's renders."""

    def test_font_paths_do_not_leak_to_later_templates(self, rectangular_template, tmp_path):
        engine = ImageTemplateEngine(custom_font_paths=["templates/fonts"])
        default_manager = engine._font_manager

        with_paths = rectangular_template.model_copy(update={"name": "with-paths", "font_paths": [str(tmp_path)]})
        engine.render(with_paths, {"title": "A"}, output_format="zpl")
        template_manager = engine._font_manager
        assert template_manager is not default_manager

        engine.render(rectangular_template, {"title": "B"}, output_format="zpl")
        assert engine._font_manager is default_manager
        assert engine._text_renderer.font_manager is default_manager

    def test_template_font_manager_also_searches_app_fonts(self, rectangular_template, tmp_path):
        engine = ImageTemplateEngine(custom_font_paths=["templates/fonts"])
        with_paths = rectangular_template.model_copy(update={"font_paths": [str(tmp_path)]})
        engine.render(with_paths, {"title": "A"}, output_format="zpl")
        searched = [str(p) for p in engine._font_manager._custom_paths]  # type: ignore[attr-defined]
        assert str(tmp_path) in searched
        assert "templates/fonts" in searched
