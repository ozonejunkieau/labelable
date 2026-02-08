"""Base class for element renderers."""

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from PIL import Image, ImageDraw

from labelable.models.template import BoundingBox, LabelElement, TemplateConfig
from labelable.templates.fonts import FontManager

# TypeVar for element types - allows subclasses to use specific types
ElementT = TypeVar("ElementT", bound=LabelElement)


class BaseElementRenderer(ABC):
    """Abstract base class for element renderers."""

    def __init__(self, font_manager: FontManager) -> None:
        """Initialize renderer with font manager.

        Args:
            font_manager: FontManager instance for loading fonts.
        """
        self.font_manager = font_manager

    @abstractmethod
    def render(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        element: Any,  # Specific element type in subclasses
        context: dict[str, Any],
        template: TemplateConfig,
    ) -> None:
        """Render an element onto the image.

        Args:
            draw: PIL ImageDraw object for drawing.
            image: PIL Image to render onto.
            element: Element configuration.
            context: Template context with field values.
            template: Template configuration.
        """
        pass

    def mm_to_px(self, mm: float, dpi: int) -> int:
        """Convert millimeters to pixels.

        Args:
            mm: Distance in millimeters.
            dpi: Dots per inch.

        Returns:
            Distance in pixels.
        """
        return int(mm * dpi / 25.4)

    def get_bounds_px(self, bounds: BoundingBox, dpi: int) -> tuple[int, int, int, int]:
        """Get bounding box in pixels.

        Args:
            bounds: Bounding box in millimeters.
            dpi: Dots per inch.

        Returns:
            Tuple of (x, y, width, height) in pixels.
        """
        return (
            self.mm_to_px(bounds.x_mm, dpi),
            self.mm_to_px(bounds.y_mm, dpi),
            self.mm_to_px(bounds.width_mm, dpi),
            self.mm_to_px(bounds.height_mm, dpi),
        )
