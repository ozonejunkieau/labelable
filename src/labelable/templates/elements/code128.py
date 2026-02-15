"""Code 128 linear barcode element renderer."""

import logging
from typing import Any

from PIL import Image, ImageDraw

from labelable.models.template import Code128Element, TemplateConfig
from labelable.templates.elements.base import BaseElementRenderer
from labelable.templates.fonts import FontManager

logger = logging.getLogger(__name__)


class Code128ElementRenderer(BaseElementRenderer):
    """Renders Code 128 linear barcode elements."""

    def __init__(self, font_manager: FontManager) -> None:
        super().__init__(font_manager)
        self._barcode_available = True
        try:
            import barcode  # noqa: F401
        except ImportError:
            self._barcode_available = False
            logger.warning("python-barcode library not available - Code 128 barcodes will not render")

    def _crop_to_content(self, img: Image.Image) -> Image.Image:
        """Crop image to remove white padding around barcode.

        Args:
            img: PIL Image with potential white padding.

        Returns:
            Cropped image containing only the barcode content.
        """
        # Convert to grayscale for analysis
        gray = img.convert("L")
        width, height = img.size

        # Find bounding box of non-white content
        top = 0
        bottom = height - 1
        left = 0
        right = width - 1

        # Find top edge
        for y in range(height):
            row = [int(gray.getpixel((x, y))) for x in range(width)]  # type: ignore[arg-type]
            if any(p < 128 for p in row):
                top = y
                break

        # Find bottom edge
        for y in range(height - 1, -1, -1):
            row = [int(gray.getpixel((x, y))) for x in range(width)]  # type: ignore[arg-type]
            if any(p < 128 for p in row):
                bottom = y
                break

        # Find left edge
        for x in range(width):
            col = [int(gray.getpixel((x, y))) for y in range(height)]  # type: ignore[arg-type]
            if any(p < 128 for p in col):
                left = x
                break

        # Find right edge
        for x in range(width - 1, -1, -1):
            col = [int(gray.getpixel((x, y))) for y in range(height)]  # type: ignore[arg-type]
            if any(p < 128 for p in col):
                right = x
                break

        # Crop to content
        return img.crop((left, top, right + 1, bottom + 1))

    def render(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: Code128Element,
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render Code 128 barcode element onto image."""
        if not self._barcode_available:
            logger.warning("Cannot render Code 128 - python-barcode library not installed")
            return

        # Get data from context and apply prefix/suffix
        field_value = str(context.get(element.field, ""))
        if not field_value:
            return

        data = f"{element.prefix}{field_value}{element.suffix}"

        import barcode
        from barcode.writer import ImageWriter

        dpi = template.dpi

        # Convert position to pixels
        center_x = self.mm_to_px(element.x_mm, dpi)
        center_y = self.mm_to_px(element.y_mm, dpi)

        # Generate Code 128 barcode
        code128 = barcode.get_barcode_class("code128")

        # Create barcode with ImageWriter
        writer = ImageWriter()
        barcode_instance = code128(data, writer=writer)

        # Render to PIL image with specified dimensions
        # module_width and module_height are in mm for the ImageWriter
        barcode_img = barcode_instance.render(
            writer_options={
                "module_width": element.module_width_mm,
                "module_height": element.height_mm,
                "quiet_zone": 0,
                "write_text": False,
                "font_size": 0,
                "text_distance": 0,
                "dpi": dpi,
            }
        )

        # Crop to remove library's internal padding (preserves module width)
        barcode_img = self._crop_to_content(barcode_img)

        # Convert to 1-bit for consistency (threshold at 128)
        def threshold(x: int) -> int:
            return 0 if x < 128 else 255

        barcode_img = barcode_img.convert("L").point(threshold, "1")

        # Calculate top-left corner from center position
        paste_x = center_x - barcode_img.width // 2
        paste_y = center_y - barcode_img.height // 2

        # Paste onto main image
        image.paste(barcode_img, (paste_x, paste_y))
