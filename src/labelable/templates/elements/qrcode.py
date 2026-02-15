"""QR code element renderer."""

import logging
from typing import Any

from PIL import Image, ImageDraw

from labelable.models.template import QRCodeElement, TemplateConfig
from labelable.templates.elements.base import BaseElementRenderer
from labelable.templates.fonts import FontManager

logger = logging.getLogger(__name__)

# Error correction level mapping
ERROR_CORRECTION_MAP = {
    "L": 1,  # ~7%
    "M": 0,  # ~15% (qrcode library uses 0 for M)
    "Q": 3,  # ~25%
    "H": 2,  # ~30%
}


class QRCodeElementRenderer(BaseElementRenderer):
    """Renders QR code elements."""

    def __init__(self, font_manager: FontManager) -> None:
        super().__init__(font_manager)
        self._qrcode_available = True
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self._qrcode_available = False
            logger.warning("qrcode library not available - QR codes will not render")

    def render(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: QRCodeElement,
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render QR code element onto image."""
        if not self._qrcode_available:
            logger.warning("Cannot render QR code - qrcode library not installed")
            return

        # Get data from context and apply prefix/suffix
        field_value = str(context.get(element.field, ""))
        if not field_value:
            return

        data = f"{element.prefix}{field_value}{element.suffix}"

        import qrcode

        dpi = template.dpi

        # Convert center position and size to pixels
        center_x = self.mm_to_px(element.x_mm, dpi)
        center_y = self.mm_to_px(element.y_mm, dpi)
        size = self.mm_to_px(element.size_mm, dpi)

        # Map error correction level
        error_correction = ERROR_CORRECTION_MAP.get(
            element.error_correction.value,
            0,  # 0 = ERROR_CORRECT_M
        )

        # Generate QR code
        qr = qrcode.QRCode(
            version=None,  # Auto-size
            error_correction=error_correction,  # type: ignore[arg-type]
            box_size=10,
            border=0,  # No border - we control positioning
        )
        qr.add_data(data)
        qr.make(fit=True)

        # Create QR image
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Convert to PIL Image if needed
        if hasattr(qr_img, "get_image"):
            qr_image: Image.Image = qr_img.get_image()
        else:
            qr_image = qr_img  # type: ignore[assignment]

        # Resize to specified size
        qr_image = qr_image.resize((size, size), Image.Resampling.NEAREST)

        # Convert to mode "1" (1-bit pixels) for consistency
        if qr_image.mode != "1":
            qr_image = qr_image.convert("1")

        # Calculate top-left corner from center position
        paste_x = center_x - size // 2
        paste_y = center_y - size // 2

        # Paste onto main image
        image.paste(qr_image, (paste_x, paste_y))
