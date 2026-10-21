"""Raster image element renderer."""

import base64
import binascii
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from labelable.models.template import (
    HorizontalAlignment,
    ImageElement,
    ImageFit,
    TemplateConfig,
    VerticalAlignment,
)
from labelable.templates.converters.palette import dither_to_palette
from labelable.templates.elements.base import BaseElementRenderer
from labelable.templates.fonts import FontManager

# Fraction of the free space left of / above the image, per alignment
_H_ALIGN = {HorizontalAlignment.LEFT: 0.0, HorizontalAlignment.CENTER: 0.5, HorizontalAlignment.RIGHT: 1.0}
_V_ALIGN = {VerticalAlignment.TOP: 0.0, VerticalAlignment.MIDDLE: 0.5, VerticalAlignment.BOTTOM: 1.0}

# Alpha at or above this is opaque; anything below leaves the canvas as is
_ALPHA_THRESHOLD = 128


class ImageElementRenderer(BaseElementRenderer):
    """Renders raster images, dithered to the template palette.

    On a template without a palette that is black and white, so the same
    element works on thermal labels and colour displays.
    """

    def __init__(self, font_manager: FontManager, image_paths: Sequence[str | Path] | None = None) -> None:
        super().__init__(font_manager)
        self._image_paths = [Path(p) for p in (image_paths or [])]

    def render(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: ImageElement,
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render image element onto image."""
        source = self._load(element, context)
        if source is None:
            return

        x, y, width, height = self.get_bounds_px(element.bounds, template.dpi)
        if width <= 0 or height <= 0:
            return

        # Honour camera orientation before scaling
        source = ImageOps.exif_transpose(source).convert("RGBA")
        fitted, (offset_x, offset_y) = self._fit(source, width, height, element)

        # Flatten onto the background so partial alpha dithers against it
        alpha = fitted.getchannel("A")
        flat = Image.new("RGB", fitted.size, template.color_rgb(template.background))
        flat.paste(fitted, mask=alpha)

        reduced = dither_to_palette(flat, template.palette_colors(), element.dither).convert("RGB")
        if image.mode == "1":
            # Pixels are exactly black or white, so this is a lossless threshold
            reduced = reduced.convert("L").convert("1", dither=Image.Dither.NONE)
        elif reduced.mode != image.mode:
            reduced = reduced.convert(image.mode)

        opaque = alpha.point([0] * _ALPHA_THRESHOLD + [255] * (256 - _ALPHA_THRESHOLD))
        image.paste(reduced, (x + offset_x, y + offset_y), mask=opaque)

    def _load(self, element: ImageElement, context: dict[str, Any]) -> Image.Image | None:
        """Open the element's image, or None when its field is empty."""
        if element.source is not None:
            return self._open(self._resolve_source(element.source).read_bytes())

        assert element.field is not None
        value = context.get(element.field)
        if value is None or value == "":
            return None
        if isinstance(value, Image.Image):
            return value
        if isinstance(value, (bytes, bytearray)):
            return self._open(bytes(value))
        return self._open(self._decode_base64(str(value)))

    @staticmethod
    def _open(data: bytes) -> Image.Image:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (OSError, Image.DecompressionBombError) as e:
            raise ValueError(f"Could not decode image: {e}") from e
        return image

    @staticmethod
    def _decode_base64(value: str) -> bytes:
        """Decode base64, with or without a ``data:image/...;base64,`` prefix."""
        if value.startswith("data:"):
            value = value.partition(",")[2]
        try:
            return base64.b64decode(value, validate=True)
        except binascii.Error as e:
            raise ValueError(f"Image field is not valid base64: {e}") from e

    def _resolve_source(self, source: str) -> Path:
        """Find ``source`` inside the image paths, refusing anything outside them.

        Templates can be created over the API, so a source must not reach
        arbitrary files on the host.
        """
        if not self._image_paths:
            raise ValueError(f"Image source '{source}' needs image_paths configured on the engine")
        for base in self._image_paths:
            base = base.resolve()
            candidate = (base / source).resolve()
            if candidate.is_relative_to(base) and candidate.is_file():
                return candidate
        raise ValueError(f"Image source '{source}' not found in image paths")

    @staticmethod
    def _fit(
        source: Image.Image,
        width: int,
        height: int,
        element: ImageElement,
    ) -> tuple[Image.Image, tuple[int, int]]:
        """Scale to the bounds per ``element.fit``; return image and offset within bounds.

        LANCZOS is right here: this is continuous-tone art about to be
        dithered, not thin 1-bit features.
        """
        h_frac = _H_ALIGN[element.alignment]
        v_frac = _V_ALIGN[element.vertical_align]

        if element.fit == ImageFit.STRETCH:
            return source.resize((width, height), Image.Resampling.LANCZOS), (0, 0)
        if element.fit == ImageFit.COVER:
            fitted = ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS, centering=(h_frac, v_frac))
            return fitted, (0, 0)

        fitted = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
        offset = (round((width - fitted.width) * h_frac), round((height - fitted.height) * v_frac))
        return fitted, offset
