"""Tests for template engines."""

import pytest

from labelable.models.printer import PrinterType
from labelable.models.template import (
    FieldType,
    LabelDimensions,
    TemplateConfig,
    TemplateField,
)
from labelable.templates.engine import TemplateError
from labelable.templates.jinja_engine import JinjaTemplateEngine


class TestJinjaTemplateEngine:
    @pytest.fixture
    def engine(self) -> JinjaTemplateEngine:
        return JinjaTemplateEngine()

    @pytest.fixture
    def zpl_template(self) -> TemplateConfig:
        return TemplateConfig(
            name="test-zpl",
            dimensions=LabelDimensions(width_mm=100, height_mm=50),
            supported_printers=[PrinterType.ZPL],
            fields=[
                TemplateField(name="name", type=FieldType.STRING, required=True),
                TemplateField(name="value", type=FieldType.INTEGER, required=False, default=0),
            ],
            template="""^XA
^FO50,50^A0N,30,30^FD{{ name }}^FS
^FO50,100^A0N,25,25^FDValue: {{ value }}^FS
^XZ""",
        )

    def test_supports_printer_type(self, engine: JinjaTemplateEngine):
        assert engine.supports_printer_type("zpl") is True
        assert engine.supports_printer_type("epl2") is True
        assert engine.supports_printer_type("ptouch") is False

    def test_render_simple(self, engine: JinjaTemplateEngine, zpl_template: TemplateConfig):
        result = engine.render(zpl_template, {"name": "Test Label"})
        assert b"^FDTest Label^FS" in result
        assert b"^FDValue: 0^FS" in result  # Default value

    def test_render_with_all_fields(self, engine: JinjaTemplateEngine, zpl_template: TemplateConfig):
        result = engine.render(zpl_template, {"name": "Product", "value": 42})
        assert b"^FDProduct^FS" in result
        assert b"^FDValue: 42^FS" in result

    def test_render_missing_required_field(self, engine: JinjaTemplateEngine, zpl_template: TemplateConfig):
        with pytest.raises(TemplateError, match="Missing required field"):
            engine.render(zpl_template, {})

    def test_render_with_conditional(self, engine: JinjaTemplateEngine):
        template = TemplateConfig(
            name="conditional",
            dimensions=LabelDimensions(width_mm=50, height_mm=25),
            supported_printers=[PrinterType.ZPL],
            fields=[
                TemplateField(name="show_extra", type=FieldType.BOOLEAN, default=False),
            ],
            template="""^XA
{% if show_extra %}^FO0,0^FDExtra^FS{% endif %}
^XZ""",
        )

        # Without extra
        result = engine.render(template, {})
        assert b"^FDExtra^FS" not in result

        # With extra
        result = engine.render(template, {"show_extra": True})
        assert b"^FDExtra^FS" in result

    def test_render_leftovers_gluten_logic(self, engine: JinjaTemplateEngine):
        """Test the gluten_free and caution field combinations for leftovers template."""
        template = TemplateConfig(
            name="leftovers-test",
            dimensions=LabelDimensions(width_mm=40, height_mm=28),
            supported_printers=[PrinterType.ZPL],
            fields=[
                TemplateField(name="name", type=FieldType.STRING, required=True),
                TemplateField(name="notes", type=FieldType.STRING, required=False, default=""),
                TemplateField(name="gluten_free", type=FieldType.BOOLEAN, required=True, default=False),
                TemplateField(
                    name="caution", type=FieldType.SELECT, required=False, default="", options=["", "DOG FOOD", "Spicy"]
                ),
                TemplateField(name="created_at", type=FieldType.DATETIME, format="%Y-%m-%d %H:%M"),
                TemplateField(name="created_by", type=FieldType.USER),
            ],
            template="""^XA
{% if gluten_free and caution %}^FD!!! GF {{ caution }} !!!^FS
{% elif gluten_free %}^FD!!! GF !!!^FS
{% elif caution %}^FD!!! GLU {{ caution }} !!!^FS
{% else %}^FD!!! GLUTEN !!!^FS
{% endif %}
^XZ""",
        )

        # Case 1: gluten_free=True, caution="DOG FOOD" -> "!!! GF DOG FOOD !!!"
        result = engine.render(template, {"name": "Test", "gluten_free": True, "caution": "DOG FOOD"})
        assert b"!!! GF DOG FOOD !!!" in result

        # Case 2: gluten_free=True, caution="" (none) -> "!!! GF !!!"
        result = engine.render(template, {"name": "Test", "gluten_free": True, "caution": ""})
        assert b"!!! GF !!!" in result
        assert b"GLU" not in result
        assert b"GLUTEN" not in result

        # Case 3: gluten_free=False, caution="Spicy" -> "!!! GLU Spicy !!!"
        result = engine.render(template, {"name": "Test", "gluten_free": False, "caution": "Spicy"})
        assert b"!!! GLU Spicy !!!" in result

        # Case 4: gluten_free=False, caution="" (none) -> "!!! GLUTEN !!!"
        result = engine.render(template, {"name": "Test", "gluten_free": False, "caution": ""})
        assert b"!!! GLUTEN !!!" in result
