"""Reduce RGB images to a display palette (e-paper and other colour panels).

The result is a mode "P" image whose palette indices follow the palette list
order, so whatever writes the panel can map index N straight to ink N.
"""

import io

from PIL import Image

from labelable.models.template import DitherMode, PaletteColor


def _palette_image(colors: list[tuple[int, int, int]]) -> Image.Image:
    """A 1x1 "P" image carrying exactly these palette entries, for quantize()."""
    image = Image.new("P", (1, 1))
    image.putpalette([channel for rgb in colors for channel in rgb])
    return image


def quantize_to_palette(image: Image.Image, palette: list[PaletteColor]) -> Image.Image:
    """Map every pixel to its nearest nominal palette colour, without dithering.

    Used for rendered label art, which is drawn in exact palette colours so
    the mapping is lossless.
    """
    return image.convert("RGB").quantize(
        palette=_palette_image([c.rgb for c in palette]),
        dither=Image.Dither.NONE,
    )


def dither_to_palette(
    image: Image.Image,
    palette: list[PaletteColor],
    dither: DitherMode = DitherMode.FLOYD_STEINBERG,
) -> Image.Image:
    """Reduce an image (e.g. a photo) to the palette.

    Pixels are matched, and Floyd-Steinberg error is diffused, against each
    ink's measured colour (``PaletteColor.dither_rgb``) - what the panel can
    really show. The returned image carries the nominal colours, so indices
    and palette are the same as ``quantize_to_palette`` produces.
    """
    pil_dither = Image.Dither.FLOYDSTEINBERG if dither == DitherMode.FLOYD_STEINBERG else Image.Dither.NONE
    reduced = image.convert("RGB").quantize(
        palette=_palette_image([c.dither_rgb for c in palette]),
        dither=pil_dither,
    )
    reduced.putpalette([channel for c in palette for channel in c.rgb])
    return reduced


def image_to_png(image: Image.Image) -> bytes:
    """Encode a device image ("1" or palette "P") as PNG, keeping index order."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
