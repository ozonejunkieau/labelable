# /// script
# requires-python = ">=3.13"
# dependencies = ["labelable"]
#
# [tool.uv.sources]
# labelable = { path = "../.." }
# ///
"""Render the e-paper shelf tag example for several items.

Run from anywhere:

    uv run examples/epaper/render.py

Writes one indexed PNG per item to examples/epaper/output/, plus an
uncalibrated render of the first item for comparison. Roboto is downloaded
from Google Fonts into examples/epaper/fonts/ on the first run.
"""

from pathlib import Path

import yaml
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from labelable.models.template import TemplateConfig
from labelable.templates.google_fonts import ensure_google_fonts
from labelable.templates.image_engine import ImageTemplateEngine

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
OUTPUT = HERE / "output"

ITEMS = [
    {
        "id": "A-001",
        "title": "Tomatoes",
        "description": "Vine ripened, grown locally",
        "price": "$4.50/kg",
        "fruit": (214, 40, 30),
    },
    {
        "id": "A-002",
        "title": "Lemons",
        "description": "Unwaxed, ideal for zest",
        "price": "$0.90 ea",
        "fruit": (245, 205, 40),
    },
    {
        "id": "A-003",
        "title": "Blueberries",
        "description": "125 g punnet",
        "price": "$3.20",
        # Blue is not one of the panel's inks: dithering approximates it
        "fruit": (60, 70, 160),
    },
]


def fruit_photo(colour: tuple[int, int, int], size: tuple[int, int] = (360, 320)) -> Image.Image:
    """A stand-in "photo": a shaded sphere with a highlight on a soft backdrop.

    Generated rather than shipped so the example has no binary inputs, but
    continuous-tone like a real photo, which is what dithering is for.
    """
    width, height = size
    backdrop = Image.linear_gradient("L").resize(size).point(lambda v: 150 + v // 3)
    image = Image.merge("RGB", (backdrop, backdrop, backdrop.point(lambda v: v - 20)))

    # Shading: darken the fruit colour towards the lower right
    sphere_box = (width * 0.18, height * 0.12, width * 0.82, height * 0.84)
    shade = Image.radial_gradient("L").resize((int(sphere_box[2] - sphere_box[0]),) * 2)
    shaded = Image.new("RGB", shade.size, colour)
    shaded = ImageChops.multiply(shaded, Image.merge("RGB", [shade.point(lambda v: 255 - v // 2)] * 3))

    mask = Image.new("L", shade.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, shade.width - 1, shade.height - 1), fill=255)
    image.paste(shaded, (int(sphere_box[0]), int(sphere_box[1])), mask.filter(ImageFilter.GaussianBlur(2)))

    # Specular highlight
    highlight = Image.new("L", size, 0)
    hx, hy = int(width * 0.38), int(height * 0.30)
    ImageDraw.Draw(highlight).ellipse((hx - 28, hy - 20, hx + 28, hy + 20), fill=200)
    highlight = highlight.filter(ImageFilter.GaussianBlur(14))
    return Image.composite(Image.new("RGB", size, "white"), image, highlight)


def main() -> None:
    ensure_google_fonts(["Roboto"], FONTS)
    engine = ImageTemplateEngine(custom_font_paths=[str(FONTS)])
    template = TemplateConfig.model_validate(yaml.safe_load((HERE / "shelf-tag.yaml").read_text()))

    OUTPUT.mkdir(exist_ok=True)
    for item in ITEMS:
        context = {key: value for key, value in item.items() if key != "fruit"}
        context["photo"] = fruit_photo(item["fruit"])
        image = engine.render_image(template, context)  # mode "P", palette in template order
        image.save(OUTPUT / f"{item['id']}.png")
        print(f"{item['id']}: {image.mode} {image.size[0]}x{image.size[1]} -> output/{item['id']}.png")

    # The same first tag with nominal colours only, to show what measured_rgb changes
    uncalibrated = template.model_copy(
        update={"palette": [c.model_copy(update={"measured_rgb": None}) for c in template.palette or []]}
    )
    first = ITEMS[0]
    context = {key: value for key, value in first.items() if key != "fruit"}
    context["photo"] = fruit_photo(first["fruit"])
    engine.render_image(uncalibrated, context).save(OUTPUT / f"{first['id']}-uncalibrated.png")
    print(f"{first['id']}: uncalibrated -> output/{first['id']}-uncalibrated.png")


if __name__ == "__main__":
    main()
