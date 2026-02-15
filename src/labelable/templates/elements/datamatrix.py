"""DataMatrix element renderer."""

import logging
from typing import Any

from PIL import Image, ImageDraw

from labelable.models.template import DataMatrixElement, TemplateConfig
from labelable.templates.elements.base import BaseElementRenderer
from labelable.templates.fonts import FontManager

logger = logging.getLogger(__name__)


class DataMatrixElementRenderer(BaseElementRenderer):
    """Renders DataMatrix barcode elements."""

    def __init__(self, font_manager: FontManager) -> None:
        super().__init__(font_manager)
        self._pylibdmtx_available = True
        try:
            from pylibdmtx import pylibdmtx  # noqa: F401
        except ImportError:
            self._pylibdmtx_available = False
            logger.warning("pylibdmtx library not available - DataMatrix codes will not render")

    def render(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: DataMatrixElement,
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render DataMatrix element onto image."""
        if not self._pylibdmtx_available:
            logger.warning("Cannot render DataMatrix - pylibdmtx library not installed")
            return

        # Get data from context and apply prefix/suffix
        field_value = str(context.get(element.field, ""))
        if not field_value:
            return

        data = f"{element.prefix}{field_value}{element.suffix}"

        from pylibdmtx import pylibdmtx

        dpi = template.dpi

        # Convert center position and size to pixels
        center_x = self.mm_to_px(element.x_mm, dpi)
        center_y = self.mm_to_px(element.y_mm, dpi)
        size = self.mm_to_px(element.size_mm, dpi)

        # Encode data to DataMatrix
        try:
            encoded = pylibdmtx.encode(data.encode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to encode DataMatrix: {e}")
            return

        # Create PIL image from encoded data
        dm_image = Image.frombytes("RGB", (encoded.width, encoded.height), encoded.pixels)

        # Convert to 1-bit
        dm_image = dm_image.convert("1")

        # Resize to specified size
        dm_image = dm_image.resize((size, size), Image.Resampling.NEAREST)

        # Calculate top-left corner from center position
        paste_x = center_x - size // 2
        paste_y = center_y - size // 2

        # Paste onto main image
        image.paste(dm_image, (paste_x, paste_y))
