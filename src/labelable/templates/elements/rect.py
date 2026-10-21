"""Rectangle element renderer."""

from typing import Any

from PIL import Image, ImageDraw

from labelable.models.template import RectElement, TemplateConfig
from labelable.templates.elements.base import BaseElementRenderer


class RectElementRenderer(BaseElementRenderer):
    """Renders filled and/or outlined rectangles, optionally with rounded corners."""

    def render(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: RectElement,
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render rectangle element onto image."""
        x, y, width, height = self.get_bounds_px(element.bounds, template.dpi)
        if width <= 0 or height <= 0:
            return

        # PIL rectangle corners are inclusive
        box = (x, y, x + width - 1, y + height - 1)
        fill = None if element.fill is None else self.ink(image, template, element.fill)
        outline = None if element.outline is None else self.ink(image, template, element.outline)
        # A thin outline must not vanish when it rounds below a pixel
        outline_width = max(1, self.mm_to_px(element.outline_width_mm, template.dpi)) if outline is not None else 0
        radius = self.mm_to_px(element.radius_mm, template.dpi)

        if radius > 0:
            draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=outline_width)
        else:
            draw.rectangle(box, fill=fill, outline=outline, width=outline_width)
