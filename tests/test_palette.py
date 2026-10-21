"""Tests for colour palettes, the image element and device image output."""

import base64
import io

import pytest
import yaml
from PIL import Image
from pydantic import ValidationError

from labelable.models.template import (
    DitherMode,
    FieldType,
    PaletteColor,
    TemplateConfig,
)
from labelable.templates.converters.palette import dither_to_palette, quantize_to_palette
from labelable.templates.engine import TemplateError
from labelable.templates.image_engine import ImageTemplateEngine

BWRY = [(0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0)]


def _template(**overrides) -> TemplateConfig:
    data = {
        "name": "display",
        "engine": "image",
        "dimensions": {"width_mm": 40, "height_mm": 20},
        "dpi": 100,
        "fields": [{"name": "title", "required": False}],
        "elements": [],
    }
    data.update(overrides)
    return TemplateConfig.model_validate(data)


def _text(color: str = "black", **overrides) -> dict:
    element = {
        "type": "text",
        "field": "title",
        "bounds": {"x_mm": 0, "y_mm": 0, "width_mm": 40, "height_mm": 20},
        "font_size": 40,
        "color": color,
    }
    element.update(overrides)
    return element


def _image(**overrides) -> dict:
    element = {
        "type": "image",
        "field": "photo",
        "bounds": {"x_mm": 0, "y_mm": 0, "width_mm": 40, "height_mm": 20},
    }
    element.update(overrides)
    return element


def _png_b64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _rgb_palette(image: Image.Image) -> list[tuple[int, int, int]]:
    flat = image.getpalette() or []
    return [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]


def _indices(image: Image.Image) -> set[int]:
    return {index for _, index in image.getcolors(maxcolors=256) or []}


class TestPaletteModel:
    def test_preset_expands_in_index_order(self):
        template = _template(palette="bwry")
        assert [c.name for c in template.palette or []] == ["black", "white", "yellow", "red"]
        assert [c.rgb for c in template.palette or []] == BWRY

    def test_list_of_names_and_explicit_colours(self):
        template = _template(
            palette=["white", {"name": "ink", "rgb": "#102030", "measured_rgb": [20, 30, 40]}],
            background="white",
        )
        ink = (template.palette or [])[1]
        assert ink.rgb == (0x10, 0x20, 0x30)
        assert ink.dither_rgb == (20, 30, 40)

    def test_dither_rgb_falls_back_to_nominal(self):
        assert PaletteColor(name="red", rgb=(255, 0, 0)).dither_rgb == (255, 0, 0)

    @pytest.mark.parametrize(
        "palette",
        ["cmyk", ["white", "mauve"], ["white"], ["white", "white"], [{"name": "x", "rgb": "#12345"}]],
    )
    def test_invalid_palettes_rejected(self, palette):
        with pytest.raises(ValidationError):
            _template(palette=palette)

    def test_element_colour_must_be_in_palette(self):
        with pytest.raises(ValidationError, match="'red' is not in the palette"):
            _template(palette="bwy", elements=[_text("red")])

    def test_mono_template_allows_only_black_and_white(self):
        _template(elements=[_text("white")], background="black")
        with pytest.raises(ValidationError):
            _template(elements=[_text("red")])

    def test_background_must_be_in_palette(self):
        with pytest.raises(ValidationError, match="background"):
            _template(palette=["black", "red"])

    def test_json_dump_round_trips_through_yaml(self):
        template = _template(palette="bwr", elements=[_text("red")], background="white")
        dumped = yaml.safe_dump(template.model_dump(exclude_none=True, mode="json"))
        assert TemplateConfig.model_validate(yaml.safe_load(dumped)) == template

    def test_image_element_needs_exactly_one_source(self):
        with pytest.raises(ValidationError, match="exactly one"):
            _template(elements=[_image(source="logo.png")])
        with pytest.raises(ValidationError, match="exactly one"):
            _template(elements=[_image(field=None)])

    def test_image_field_value_passes_through(self):
        template = _template(fields=[{"name": "photo", "type": "image"}])
        image = Image.new("RGB", (2, 2))
        assert template.fields[0].type == FieldType.IMAGE
        assert template.validate_data({"photo": image})["photo"] is image


class TestPaletteConverters:
    def test_quantize_maps_exact_colours_to_their_index(self):
        palette = _template(palette="bwry").palette or []
        image = Image.new("RGB", (4, 1))
        for x, rgb in enumerate(BWRY):
            image.putpixel((x, 0), rgb)
        result = quantize_to_palette(image, palette)
        assert result.mode == "P"
        assert [result.getpixel((x, 0)) for x in range(4)] == [0, 1, 2, 3]

    def test_dither_matches_measured_colours_but_outputs_nominal(self):
        # The panel's "red" really looks dark brick. A brick-coloured source
        # pixel is nearer nominal black, but must still pick the red ink.
        palette = [
            PaletteColor(name="black", rgb=(0, 0, 0)),
            PaletteColor(name="white", rgb=(255, 255, 255)),
            PaletteColor(name="red", rgb=(255, 0, 0), measured_rgb=(110, 30, 30)),
        ]
        image = Image.new("RGB", (8, 8), (110, 30, 30))
        result = dither_to_palette(image, palette, DitherMode.NONE)
        assert _indices(result) == {2}
        assert _rgb_palette(result)[:3] == [(0, 0, 0), (255, 255, 255), (255, 0, 0)]

    def test_floyd_steinberg_mixes_inks_for_intermediate_colours(self):
        palette = _template().palette_colors()
        grey = Image.new("RGB", (32, 32), (128, 128, 128))
        assert _indices(dither_to_palette(grey, palette, DitherMode.FLOYD_STEINBERG)) == {0, 1}
        assert len(_indices(dither_to_palette(grey, palette, DitherMode.NONE))) == 1


class TestPaletteRendering:
    def test_render_png_is_indexed_in_palette_order(self):
        engine = ImageTemplateEngine()
        template = _template(palette="bwry", elements=[_text("red")])
        result = Image.open(io.BytesIO(engine.render(template, {"title": "Hi"}, output_format="png")))
        assert result.mode == "P"
        assert _rgb_palette(result)[:4] == BWRY
        # Red text on white, nothing else: no antialiasing fringe
        assert _indices(result) == {1, 3}

    def test_render_image_returns_device_image(self):
        engine = ImageTemplateEngine()
        colour = engine.render_image(_template(palette="bwr", elements=[_text("red")]), {"title": "Hi"})
        mono = engine.render_image(_template(elements=[_text()]), {"title": "Hi"})
        assert colour.mode == "P"
        assert mono.mode == "1"

    def test_preview_has_only_palette_colours(self):
        engine = ImageTemplateEngine()
        template = _template(palette="bwry", elements=[_text("yellow")], background="black")
        preview = Image.open(io.BytesIO(engine.render_preview(template, {"title": "Hello"}))).convert("RGB")
        colours = {rgb for _, rgb in preview.getcolors(maxcolors=1 << 16) or []}
        assert colours == {(0, 0, 0), (255, 255, 0)}

    def test_background_fills_canvas(self):
        engine = ImageTemplateEngine()
        image = engine.render_image(_template(palette="bwr", background="red"), {})
        assert _indices(image) == {2}

    def test_codes_use_element_colour_and_template_background(self):
        engine = ImageTemplateEngine()
        qr = {"type": "qrcode", "field": "title", "x_mm": 20, "y_mm": 10, "size_mm": 15, "color": "red"}
        template = _template(palette="bwry", elements=[qr], background="yellow")
        assert _indices(engine.render_image(template, {"title": "ABC"})) == {2, 3}

    def test_circle_outside_is_background_not_transparent(self):
        engine = ImageTemplateEngine()
        template = _template(
            palette="bwr",
            background="red",
            shape="circle",
            dimensions={"diameter_mm": 20},
        )
        image = engine.render_image(template, {})
        assert image.getpixel((0, 0)) == 2

    def test_palette_template_refuses_thermal_formats(self):
        engine = ImageTemplateEngine()
        with pytest.raises(TemplateError, match="renders only to png"):
            engine.render(_template(palette="bwr"), {}, output_format="zpl")

    def test_mono_png_is_one_bit(self):
        engine = ImageTemplateEngine()
        result = Image.open(io.BytesIO(engine.render(_template(elements=[_text()]), {"title": "Hi"}, "png")))
        assert result.mode == "1"

    def test_batch_strip_uses_palette(self):
        engine = ImageTemplateEngine()
        template = _template(
            palette="bwr",
            batch={},
            fields=[{"name": "items", "type": "list"}],
            elements=[_text("red", field="items")],
        )
        image = engine.render_image(template, {"items": "A\nB"})
        assert image.mode == "P"
        assert _indices(image) == {1, 2}


class TestImageElement:
    @pytest.fixture
    def engine(self):
        return ImageTemplateEngine()

    def _photo_template(self, **element) -> TemplateConfig:
        return _template(
            palette="bwry",
            fields=[{"name": "photo", "type": "image"}],
            elements=[_image(**element)],
        )

    @pytest.mark.parametrize("encoding", ["pil", "bytes", "base64", "data_uri"])
    def test_field_value_encodings(self, engine, encoding):
        red = Image.new("RGB", (10, 5), (255, 0, 0))
        b64 = _png_b64(red)
        value = {
            "pil": red,
            "bytes": base64.b64decode(b64),
            "base64": b64,
            "data_uri": f"data:image/png;base64,{b64}",
        }[encoding]
        image = engine.render_image(self._photo_template(fit="stretch"), {"photo": value})
        assert _indices(image) == {3}

    def test_empty_field_renders_nothing(self, engine):
        image = engine.render_image(self._photo_template(), {"photo": ""})
        assert _indices(image) == {1}

    def test_invalid_base64_fails(self, engine):
        with pytest.raises(TemplateError, match="base64"):
            engine.render_image(self._photo_template(), {"photo": "not base64!"})

    def test_undecodable_bytes_fail(self, engine):
        with pytest.raises(TemplateError, match="decode"):
            engine.render_image(self._photo_template(), {"photo": b"not an image"})

    def test_contain_respects_alignment(self, engine):
        # Square image in a 2:1 box leaves free space at the side
        square = Image.new("RGB", (10, 10), (0, 0, 0))
        image = engine.render_image(self._photo_template(alignment="left"), {"photo": square})
        width, height = image.size
        assert image.getpixel((0, height // 2)) == 0
        assert image.getpixel((width - 1, height // 2)) == 1

    def test_cover_fills_bounds(self, engine):
        square = Image.new("RGB", (10, 10), (255, 255, 0))
        image = engine.render_image(self._photo_template(fit="cover"), {"photo": square})
        assert _indices(image) == {2}

    def test_transparent_pixels_leave_canvas_alone(self, engine):
        template = _template(
            palette="bwry",
            fields=[{"name": "photo", "type": "image"}],
            background="yellow",
            elements=[_image(fit="stretch")],
        )
        clear = Image.new("RGBA", (10, 5), (255, 0, 0, 0))
        assert _indices(engine.render_image(template, {"photo": clear})) == {2}

    def test_mono_template_dithers_to_black_and_white(self, engine):
        template = _template(fields=[{"name": "photo", "type": "image"}], elements=[_image(fit="stretch")])
        grey = Image.new("RGB", (40, 20), (128, 128, 128))
        image = engine.render_image(template, {"photo": grey})
        assert image.mode == "1"
        assert {value for _, value in image.getcolors() or []} == {0, 255}

    def test_source_resolves_inside_image_paths(self, tmp_path):
        Image.new("RGB", (4, 4), (255, 0, 0)).save(tmp_path / "logo.png")
        engine = ImageTemplateEngine(image_paths=[tmp_path])
        template = _template(palette="bwry", elements=[_image(field=None, source="logo.png", fit="stretch")])
        assert _indices(engine.render_image(template, {})) == {3}

    @pytest.mark.parametrize("source", ["../secret.png", "/etc/hosts", "missing.png"])
    def test_source_outside_image_paths_rejected(self, tmp_path, source):
        images = tmp_path / "images"
        images.mkdir()
        Image.new("RGB", (4, 4)).save(tmp_path / "secret.png")
        engine = ImageTemplateEngine(image_paths=[images])
        template = _template(palette="bwry", elements=[_image(field=None, source=source)])
        with pytest.raises(TemplateError, match="not found in image paths"):
            engine.render_image(template, {})

    def test_source_without_image_paths_rejected(self):
        template = _template(palette="bwry", elements=[_image(field=None, source="logo.png")])
        with pytest.raises(TemplateError, match="image_paths"):
            ImageTemplateEngine().render_image(template, {})
