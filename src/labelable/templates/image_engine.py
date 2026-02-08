"""Image-based template engine for rendering labels as bitmaps."""

import io
import logging
from typing import Any

from PIL import Image, ImageDraw

from labelable.models.template import (
    DataMatrixElement,
    LabelShape,
    QRCodeElement,
    TemplateConfig,
    TextElement,
)
from labelable.templates.converters import image_to_epl2, image_to_zpl
from labelable.templates.elements import (
    DataMatrixElementRenderer,
    QRCodeElementRenderer,
    TextElementRenderer,
)
from labelable.templates.engine import BaseTemplateEngine, TemplateError
from labelable.templates.fonts import FontManager, get_font_manager

logger = logging.getLogger(__name__)


class ImageTemplateEngine(BaseTemplateEngine):
    """Image-based template engine that renders labels using PIL.

    Renders labels as bitmap images that are then converted to
    printer-specific formats (ZPL, EPL2).

    Supports:
    - Rectangular and circular labels
    - Text with wrapping, auto-scaling, and circle-aware layout
    - QR codes and DataMatrix barcodes
    - Custom fonts via system font discovery or custom paths
    """

    SUPPORTED_TYPES = {"zpl", "epl2"}

    def __init__(self, custom_font_paths: list[str] | None = None) -> None:
        """Initialize image template engine.

        Args:
            custom_font_paths: Additional paths to search for fonts.
        """
        self._font_manager = get_font_manager(custom_font_paths)

        # Initialize element renderers
        self._text_renderer = TextElementRenderer(self._font_manager)
        self._qrcode_renderer = QRCodeElementRenderer(self._font_manager)
        self._datamatrix_renderer = DataMatrixElementRenderer(self._font_manager)

    def render(
        self,
        template: TemplateConfig,
        context: dict[str, Any],
        output_format: str = "zpl",
    ) -> bytes:
        """Render a template to printer commands.

        Args:
            template: Template configuration with elements.
            context: Dictionary of field values.
            output_format: Output format ("zpl" or "epl2").

        Returns:
            Printer commands as bytes.

        Raises:
            TemplateError: If rendering fails.
        """
        try:
            # Validate context
            validated_context = template.validate_data(context)

            # Create font manager with template-specific paths
            font_manager = self._font_manager
            if template.font_paths:
                font_manager = FontManager(template.font_paths)
                self._text_renderer = TextElementRenderer(font_manager)
                self._qrcode_renderer = QRCodeElementRenderer(font_manager)
                self._datamatrix_renderer = DataMatrixElementRenderer(font_manager)

            # Create image
            image = self._create_image(template)
            draw = ImageDraw.Draw(image)

            # Render each element
            for element in template.elements:
                self._render_element(draw, image, element, validated_context, template)

            # Apply circular mask if needed
            if template.shape == LabelShape.CIRCLE:
                image = self._apply_circle_mask(image, template)

            # Convert to output format
            if output_format.lower() == "epl2":
                return image_to_epl2(image)
            else:
                # Convert mm offsets to dots
                offset_x = int(template.label_offset_x_mm * template.dpi / 25.4)
                offset_y = int(template.label_offset_y_mm * template.dpi / 25.4)
                return image_to_zpl(
                    image,
                    label_offset_x=offset_x,
                    label_offset_y=offset_y,
                    darkness=template.darkness,
                )

        except ValueError as e:
            raise TemplateError(f"Invalid template data: {e}") from e
        except Exception as e:
            raise TemplateError(f"Failed to render image template: {e}") from e

    def render_preview(
        self,
        template: TemplateConfig,
        context: dict[str, Any],
        format: str = "PNG",
    ) -> bytes:
        """Render a template to a preview image.

        Args:
            template: Template configuration with elements.
            context: Dictionary of field values.
            format: Image format (PNG, JPEG, etc.).

        Returns:
            Image data as bytes.

        Raises:
            TemplateError: If rendering fails.
        """
        try:
            # Validate context
            validated_context = template.validate_data(context)

            # Create font manager with template-specific paths
            font_manager = self._font_manager
            if template.font_paths:
                font_manager = FontManager(template.font_paths)
                self._text_renderer = TextElementRenderer(font_manager)
                self._qrcode_renderer = QRCodeElementRenderer(font_manager)
                self._datamatrix_renderer = DataMatrixElementRenderer(font_manager)

            # Create image (RGB for preview)
            image = self._create_image(template, mode="RGB")
            draw = ImageDraw.Draw(image)

            # Render each element
            for element in template.elements:
                self._render_element(draw, image, element, validated_context, template)

            # Apply circular mask if needed
            if template.shape == LabelShape.CIRCLE:
                image = self._apply_circle_mask(image, template, preview=True)

            # Convert to bytes
            buffer = io.BytesIO()
            image.save(buffer, format=format)
            return buffer.getvalue()

        except ValueError as e:
            raise TemplateError(f"Invalid template data: {e}") from e
        except Exception as e:
            raise TemplateError(f"Failed to render preview: {e}") from e

    def supports_printer_type(self, printer_type: str) -> bool:
        """Check if this engine supports the given printer type."""
        return printer_type.lower() in self.SUPPORTED_TYPES

    def _create_image(self, template: TemplateConfig, mode: str = "1") -> Image.Image:
        """Create a new image for the template.

        Args:
            template: Template configuration.
            mode: PIL image mode ("1" for 1-bit, "RGB" for preview).

        Returns:
            New PIL Image.
        """
        dpi = template.dpi

        if template.shape == LabelShape.CIRCLE and template.dimensions.diameter_mm:
            # Circular label
            diameter_px = int(template.dimensions.diameter_mm * dpi / 25.4)
            width_px = diameter_px
            height_px = diameter_px
        else:
            # Rectangular label
            width_px = int(template.dimensions.width_mm * dpi / 25.4)
            height_px = int(template.dimensions.height_mm * dpi / 25.4)

        # Create image with white background
        if mode == "1":
            return Image.new("1", (width_px, height_px), color=1)  # 1 = white
        else:
            return Image.new(mode, (width_px, height_px), color="white")

    def _render_element(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: TextElement | QRCodeElement | DataMatrixElement,
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render a single element.

        Args:
            draw: PIL ImageDraw object.
            image: PIL Image.
            element: Element to render.
            context: Template context.
            template: Template configuration.
        """
        if isinstance(element, TextElement):
            self._text_renderer.render(draw, image, element, context, template)
        elif isinstance(element, QRCodeElement):
            self._qrcode_renderer.render(draw, image, element, context, template)
        elif isinstance(element, DataMatrixElement):
            self._datamatrix_renderer.render(draw, image, element, context, template)

    def _apply_circle_mask(
        self,
        image: Image.Image,
        template: TemplateConfig,
        preview: bool = False,
    ) -> Image.Image:
        """Apply circular mask to image.

        Args:
            image: Image to mask.
            template: Template configuration.
            preview: If True, use alpha channel for transparency.

        Returns:
            Masked image.
        """
        if not template.dimensions.diameter_mm:
            return image

        width, height = image.size

        # Create circular mask
        mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, width - 1, height - 1], fill=255)

        if preview:
            # For preview, use RGBA with alpha
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            # Create output with transparent background
            output = Image.new("RGBA", (width, height), (255, 255, 255, 0))
            output.paste(image, mask=mask)
            return output
        else:
            # For printing, just apply mask (white outside circle)
            output = Image.new("1", (width, height), 1)  # White background
            output.paste(image, mask=mask)
            return output
