"""Text element renderer with wrapping and auto-scaling."""

import math
from typing import Any

from PIL import Image, ImageDraw

from labelable.models.template import (
    HorizontalAlignment,
    LabelShape,
    TemplateConfig,
    TextElement,
    VerticalAlignment,
)
from labelable.templates.elements.base import BaseElementRenderer
from labelable.templates.fonts import FontManager


class TextElementRenderer(BaseElementRenderer):
    """Renders text elements with support for wrapping, scaling, and circle-aware layout."""

    MIN_FONT_SIZE = 6
    SCALE_TOLERANCE = 2  # Binary search stops when within this many points

    def __init__(self, font_manager: FontManager) -> None:
        super().__init__(font_manager)

    def render(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: TextElement,
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render text element onto image."""
        # Get text content
        if element.field:
            text = str(context.get(element.field, ""))
        elif element.static_text:
            text = element.static_text
        else:
            return  # Nothing to render

        if not text:
            return

        dpi = template.dpi
        x, y, width, height = self.get_bounds_px(element.bounds, dpi)

        # Determine font size (auto-scale if enabled)
        font_size = element.font_size
        if element.auto_scale:
            font_size = self._find_optimal_font_size(text, element, width, height, template)

        font = self.font_manager.get_font(element.font, font_size)

        # Get circle parameters if circle-aware
        circle_center = None
        circle_radius = None
        if element.circle_aware and template.shape == LabelShape.CIRCLE:
            if template.dimensions.diameter_mm:
                radius_px = self.mm_to_px(template.dimensions.diameter_mm / 2, dpi)
                center_x = radius_px
                center_y = radius_px
                circle_center = (center_x, center_y)
                circle_radius = radius_px

        # Wrap text if enabled
        if element.wrap:
            lines = self._wrap_text(text, font, width, draw, circle_center, circle_radius, y, height)
        else:
            lines = [text]

        # Calculate total text height with line spacing
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_heights.append(bbox[3] - bbox[1])

        # Apply line spacing multiplier
        spacing_multiplier = element.line_spacing
        base_line_height = max(line_heights) if line_heights else font_size
        spaced_line_height = int(base_line_height * spacing_multiplier)

        # Total height accounts for spacing between lines
        if len(line_heights) > 1:
            spacing_extra = int((len(line_heights) - 1) * base_line_height * (spacing_multiplier - 1))
            total_height = sum(line_heights) + spacing_extra
        else:
            total_height = sum(line_heights)

        # Calculate vertical starting position
        if element.vertical_align == VerticalAlignment.MIDDLE:
            current_y = y + (height - total_height) // 2
        elif element.vertical_align == VerticalAlignment.BOTTOM:
            current_y = y + height - total_height
        else:  # TOP
            current_y = y

        # Draw each line
        for i, line in enumerate(lines):
            if not line.strip():
                current_y += spaced_line_height
                continue

            # Calculate available width for this line (circle-aware)
            available_width = width
            if circle_center and circle_radius:
                available_width = self._get_chord_width(
                    current_y + line_heights[i] // 2,
                    circle_center,
                    circle_radius,
                    x,
                    width,
                )

            # Get line width
            bbox = draw.textbbox((0, 0), line, font=font)
            line_width = bbox[2] - bbox[0]

            # Calculate horizontal position
            if element.alignment == HorizontalAlignment.CENTER:
                line_x = x + (available_width - line_width) // 2
                if circle_center and circle_radius:
                    # Adjust for circle - center relative to chord
                    chord_start = self._get_chord_start(
                        current_y + line_heights[i] // 2,
                        circle_center,
                        circle_radius,
                        x,
                    )
                    line_x = chord_start + (available_width - line_width) // 2
            elif element.alignment == HorizontalAlignment.RIGHT:
                line_x = x + available_width - line_width
                if circle_center and circle_radius:
                    chord_start = self._get_chord_start(
                        current_y + line_heights[i] // 2,
                        circle_center,
                        circle_radius,
                        x,
                    )
                    line_x = chord_start + available_width - line_width
            else:  # LEFT
                line_x = x
                if circle_center and circle_radius:
                    line_x = self._get_chord_start(
                        current_y + line_heights[i] // 2,
                        circle_center,
                        circle_radius,
                        x,
                    )

            draw.text((line_x, current_y), line, font=font, fill="black")
            # Apply line spacing for next line
            current_y += int(line_heights[i] * spacing_multiplier)

    def _find_optimal_font_size(
        self,
        text: str,
        element: TextElement,
        width: int,
        height: int,
        template: TemplateConfig,
    ) -> int:
        """Binary search for the largest font size that fits the bounds.

        Args:
            text: Text to render.
            element: Text element configuration.
            width: Available width in pixels.
            height: Available height in pixels.
            template: Template configuration.

        Returns:
            Optimal font size in points.
        """
        min_size = self.MIN_FONT_SIZE
        max_size = element.font_size

        # Create a temporary image for measurements
        temp_image = Image.new("1", (width, height), color=1)
        temp_draw = ImageDraw.Draw(temp_image)

        while max_size - min_size > self.SCALE_TOLERANCE:
            mid_size = (min_size + max_size) // 2
            if self._text_fits(text, element, width, height, mid_size, temp_draw, template):
                min_size = mid_size
            else:
                max_size = mid_size

        return min_size

    def _text_fits(
        self,
        text: str,
        element: TextElement,
        width: int,
        height: int,
        font_size: int,
        draw: ImageDraw.ImageDraw,
        template: TemplateConfig,
    ) -> bool:
        """Check if text fits within bounds at given font size.

        Args:
            text: Text to check.
            element: Text element configuration.
            width: Available width in pixels.
            height: Available height in pixels.
            font_size: Font size to test.
            draw: ImageDraw for text measurements.
            template: Template configuration.

        Returns:
            True if text fits.
        """
        font = self.font_manager.get_font(element.font, font_size)

        if element.wrap:
            lines = self._wrap_text(text, font, width, draw)
        else:
            lines = [text]

        # Check total height
        total_height = 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            total_height += bbox[3] - bbox[1]

        if total_height > height:
            return False

        # Check widths
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            if bbox[2] - bbox[0] > width:
                return False

        return True

    def _wrap_text(
        self,
        text: str,
        font,
        max_width: int,
        draw: ImageDraw.ImageDraw,
        circle_center: tuple[int, int] | None = None,
        circle_radius: int | None = None,
        start_y: int = 0,
        total_height: int = 0,
    ) -> list[str]:
        """Wrap text to fit within width, optionally circle-aware.

        Args:
            text: Text to wrap.
            font: PIL font object.
            max_width: Maximum width in pixels.
            draw: ImageDraw for text measurements.
            circle_center: Circle center (x, y) for circle-aware wrapping.
            circle_radius: Circle radius for circle-aware wrapping.
            start_y: Starting Y position for circle calculations.
            total_height: Total available height for circle calculations.

        Returns:
            List of wrapped lines.
        """
        words = text.split()
        if not words:
            return []

        lines = []
        current_line = []
        current_y = start_y

        # Get line height for Y calculations
        bbox = draw.textbbox((0, 0), "Ay", font=font)
        line_height = bbox[3] - bbox[1]

        for word in words:
            # Calculate available width at current Y position
            available_width = max_width
            if circle_center and circle_radius:
                available_width = self._get_chord_width(
                    int(current_y + line_height // 2),
                    circle_center,
                    circle_radius,
                    0,
                    max_width,
                )

            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            test_width = bbox[2] - bbox[0]

            if test_width <= available_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                    current_y += line_height
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))

        return lines

    def _get_chord_width(
        self,
        y: int,
        center: tuple[int, int],
        radius: int,
        box_x: int,
        box_width: int,
    ) -> int:
        """Calculate chord width at given Y position within circle.

        Args:
            y: Y position to calculate chord at.
            center: Circle center (x, y).
            radius: Circle radius.
            box_x: Bounding box X position.
            box_width: Bounding box width.

        Returns:
            Available width (chord width constrained by bounding box).
        """
        y_offset = abs(y - center[1])

        if y_offset >= radius:
            return 0

        # Chord width: 2 * sqrt(r^2 - y_offset^2)
        chord_half = math.sqrt(radius * radius - y_offset * y_offset)
        chord_width = int(2 * chord_half)

        # Constrain to bounding box
        return min(chord_width, box_width)

    def _get_chord_start(
        self,
        y: int,
        center: tuple[int, int],
        radius: int,
        box_x: int,
    ) -> int:
        """Calculate chord start X position at given Y.

        Args:
            y: Y position.
            center: Circle center (x, y).
            radius: Circle radius.
            box_x: Bounding box X position.

        Returns:
            Chord start X position.
        """
        y_offset = abs(y - center[1])

        if y_offset >= radius:
            return box_x

        chord_half = math.sqrt(radius * radius - y_offset * y_offset)
        chord_start = center[0] - chord_half

        return max(int(chord_start), box_x)
