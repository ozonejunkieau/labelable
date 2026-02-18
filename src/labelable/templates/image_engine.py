"""Image-based template engine for rendering labels as bitmaps."""

import io
import logging
from typing import Any

from PIL import Image, ImageDraw

from labelable.models.template import (
    BatchAlignment,
    Code128Element,
    DataMatrixElement,
    FieldType,
    LabelShape,
    QRCodeElement,
    TemplateConfig,
    TextElement,
)
from labelable.printers.ptouch_protocol import build_print_job
from labelable.templates.converters import (
    batch_image_to_ptouch_raster,
    image_to_epl2,
    image_to_ptouch_raster,
    image_to_zpl,
)
from labelable.templates.elements import (
    Code128ElementRenderer,
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

    SUPPORTED_TYPES = {"zpl", "epl2", "ptouch"}

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
        self._code128_renderer = Code128ElementRenderer(self._font_manager)

    def render(
        self,
        template: TemplateConfig,
        context: dict[str, Any],
        output_format: str = "zpl",
    ) -> bytes:
        """Render a template to printer commands.

        Auto-detects batch mode when template has batch config and a LIST field.

        Args:
            template: Template configuration with elements.
            context: Dictionary of field values.
            output_format: Output format ("zpl", "epl2", or "ptouch").

        Returns:
            Printer commands as bytes.

        Raises:
            TemplateError: If rendering fails.
        """
        try:
            # Validate context
            validated_context = template.validate_data(context)
            self._apply_font_paths(template)

            # Auto-detect batch mode
            is_batch = self._is_batch_mode(template, validated_context)
            if is_batch:
                image = self._render_batch_image(template, validated_context, mode="1")
            else:
                image = self._render_single_label_image(template, validated_context, mode="1")

            # Convert to output format
            if output_format.lower() == "ptouch":
                tape_width = template.ptouch_tape_width_mm or 24
                if is_batch:
                    # Batch: horizontal strip (width=feed, height=tape).
                    # Use column-by-column rasterizer instead of the
                    # standard rotate+mirror converter.
                    raster_data, line_count = batch_image_to_ptouch_raster(
                        image,
                        tape_width_mm=tape_width,
                        compression=True,
                    )
                else:
                    padding_px = int(template.ptouch_margin_mm * template.dpi / 25.4)
                    cropped = self._crop_to_content(image, padding_px)
                    raster_data, line_count = image_to_ptouch_raster(
                        cropped,
                        tape_width_mm=tape_width,
                        compression=True,
                    )
                return build_print_job(
                    raster_data,
                    line_count,
                    media_width_mm=tape_width,
                    auto_cut=template.ptouch_auto_cut,
                    chain_print=template.ptouch_chain_print,
                    compression=True,
                )
            elif output_format.lower() == "epl2":
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

        Auto-detects batch mode when template has batch config and a LIST field.

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
            self._apply_font_paths(template)

            # Auto-detect batch mode
            if self._is_batch_mode(template, validated_context):
                image = self._render_batch_image(
                    template,
                    validated_context,
                    mode="RGB",
                )
            else:
                image = self._render_single_label_image(template, validated_context, mode="RGB")

                # Apply circular mask if needed
                if template.shape == LabelShape.CIRCLE:
                    image = self._apply_circle_mask(image, template, preview=True)

                # Crop to content for P-Touch previews (shows actual label size)
                if template.ptouch_tape_width_mm is not None:
                    padding_px = int(template.ptouch_margin_mm * template.dpi / 25.4)
                    image = self._crop_to_content(image, padding_px)

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

    def _apply_font_paths(self, template: TemplateConfig) -> None:
        """Apply template-specific font paths if configured."""
        if template.font_paths:
            font_manager = FontManager(template.font_paths)
            self._text_renderer = TextElementRenderer(font_manager)
            self._qrcode_renderer = QRCodeElementRenderer(font_manager)
            self._datamatrix_renderer = DataMatrixElementRenderer(font_manager)
            self._code128_renderer = Code128ElementRenderer(font_manager)

    def _render_single_label_image(
        self,
        template: TemplateConfig,
        validated_context: dict[str, Any],
        mode: str = "1",
    ) -> Image.Image:
        """Render a single label to a PIL Image (no format conversion).

        Args:
            template: Template configuration with elements.
            validated_context: Already-validated context dictionary.
            mode: PIL image mode ("1" for 1-bit, "RGB" for preview).

        Returns:
            PIL Image of the rendered label.
        """
        image = self._create_image(template, mode=mode)
        draw = ImageDraw.Draw(image)

        for element in template.elements:
            self._render_element(draw, image, element, validated_context, template)

        if template.shape == LabelShape.CIRCLE:
            image = self._apply_circle_mask(image, template, preview=(mode != "1"))

        return image

    @staticmethod
    def _is_batch_mode(template: TemplateConfig, context: dict[str, Any]) -> bool:
        """Check if this render should use batch mode."""
        if template.batch is None:
            return False
        for field in template.fields:
            if field.type == FieldType.LIST and field.name in context:
                return True
        return False

    @staticmethod
    def _extract_list_items(template: TemplateConfig, context: dict[str, Any]) -> tuple[str, list[str]]:
        """Find the LIST field and extract individual items.

        Returns:
            Tuple of (field_name, list_of_items).

        Raises:
            TemplateError: If no LIST field is found.
        """
        for field in template.fields:
            if field.type == FieldType.LIST and field.name in context:
                raw = context[field.name]
                items = [item.strip() for item in str(raw).split("\n") if item.strip()]
                return field.name, items
        raise TemplateError("Batch mode requires a LIST field with data")

    def _render_batch_image(
        self,
        template: TemplateConfig,
        validated_context: dict[str, Any],
        mode: str = "1",
    ) -> Image.Image:
        """Render a batch of labels as a single horizontal strip image.

        All labels use the same font size (determined by tape height minus
        margins). The strip is composed horizontally: labels side-by-side
        in the feed direction, with vertical cut-line guides.

        The raster converter handles rotation for printing.

        Args:
            template: Template configuration with batch config.
            validated_context: Already-validated context dictionary.
            mode: PIL image mode ("1" for 1-bit, "RGB" for preview).

        Returns:
            PIL Image of the full batch strip.
        """
        assert template.batch is not None
        batch = template.batch

        list_field_name, items = self._extract_list_items(template, validated_context)
        if not items:
            raise TemplateError("Batch list is empty")

        dpi = template.dpi
        tape_width_px = int(template.dimensions.width_mm * dpi / 25.4)
        margin_px = int(batch.margin_mm * dpi / 25.4)
        padding_px = int(batch.padding_mm * dpi / 25.4)
        min_label_len_px = int(batch.min_label_length_mm * dpi / 25.4)

        # Find the text element referencing the list field
        text_element = None
        for element in template.elements:
            if isinstance(element, TextElement) and element.field == list_field_name:
                text_element = element
                break
        if text_element is None:
            raise TemplateError(f"No text element found for LIST field '{list_field_name}'")

        # Available height for text = tape width minus top/bottom margins
        available_height = tape_width_px - 2 * margin_px

        # Find uniform font size fitting all items within available height
        font_name = text_element.font
        font_size = self._find_batch_font_size(
            font_name,
            text_element.font_size,
            available_height,
            items,
        )
        font = self._font_manager.get_font(font_name, font_size)

        # Measure all items at uniform font size to find label width
        temp_img = Image.new("1", (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        item_widths = []
        for item in items:
            bbox = temp_draw.textbbox((0, 0), item, font=font)
            item_widths.append(bbox[2] - bbox[0])

        max_text_width = max(item_widths)
        uniform_label_width = max(max_text_width, min_label_len_px)

        # Compose horizontal strip
        n = len(items)
        cut_line_px = 1 if batch.cut_lines else 0
        slot_width = uniform_label_width + 2 * padding_px
        total_width = n * slot_width + (n + 1) * cut_line_px
        total_height = tape_width_px

        bg_color: int | str = 1 if mode == "1" else "white"
        line_color: int | str = 0 if mode == "1" else "black"
        text_color: int | str = 0 if mode == "1" else "black"

        strip = Image.new(mode, (total_width, total_height), color=bg_color)
        draw = ImageDraw.Draw(strip)

        for i, item in enumerate(items):
            slot_x = cut_line_px + i * (slot_width + cut_line_px)

            # Measure this item
            bbox = draw.textbbox((0, 0), item, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            text_y_offset = bbox[1]  # baseline offset from textbbox

            # Vertical: center text within margins
            y = margin_px + (available_height - text_h) // 2 - text_y_offset

            # Horizontal: alignment within slot (padding on each side)
            if batch.alignment == BatchAlignment.CENTER:
                x = slot_x + (slot_width - text_w) // 2
            elif batch.alignment == BatchAlignment.RIGHT:
                x = slot_x + slot_width - padding_px - text_w
            else:  # LEFT
                x = slot_x + padding_px

            draw.text((x, y), item, font=font, fill=text_color)

        # Draw N+1 vertical cut lines at all edges
        if batch.cut_lines:
            for i in range(n + 1):
                line_x = i * (slot_width + cut_line_px)
                draw.line(
                    [(line_x, 0), (line_x, total_height - 1)],
                    fill=line_color,
                    width=1,
                )

        return strip

    def _find_batch_font_size(
        self,
        font_name: str,
        max_size: int,
        available_height: int,
        items: list[str],
    ) -> int:
        """Find the largest font size where all items fit the available height.

        Uses binary search, same tolerance as TextElementRenderer.

        Args:
            font_name: Font name (supports Google Fonts via FontManager).
            max_size: Maximum font size to try.
            available_height: Available pixel height for text.
            items: List of text strings to fit.

        Returns:
            Optimal font size.
        """
        temp_img = Image.new("1", (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        min_size = 6

        while max_size - min_size > 2:
            mid_size = (min_size + max_size) // 2
            font = self._font_manager.get_font(font_name, mid_size)

            fits = True
            for item in items:
                bbox = temp_draw.textbbox((0, 0), item, font=font)
                if bbox[3] - bbox[1] > available_height:
                    fits = False
                    break

            if fits:
                min_size = mid_size
            else:
                max_size = mid_size

        return min_size

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
        element: TextElement | QRCodeElement | DataMatrixElement | Code128Element,
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
        elif isinstance(element, Code128Element):
            self._code128_renderer.render(draw, image, element, context, template)

    @staticmethod
    def _crop_to_content(image: Image.Image, padding_px: int = 0) -> Image.Image:
        """Crop image to its content bounding box with optional padding.

        Args:
            image: PIL Image to crop.
            padding_px: Pixels of padding around content.

        Returns:
            Cropped image.
        """
        # Convert to grayscale to find content
        gray = image.convert("L")
        # Invert: getbbox() finds non-zero pixels, white=255 so invert
        from PIL import ImageChops

        inverted = ImageChops.invert(gray)
        bbox = inverted.getbbox()

        if bbox is None:
            # No content found, return as-is
            return image

        left, top, right, bottom = bbox

        # Apply padding
        width, height = image.size
        left = max(0, left - padding_px)
        top = max(0, top - padding_px)
        right = min(width, right + padding_px)
        bottom = min(height, bottom + padding_px)

        return image.crop((left, top, right, bottom))

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
