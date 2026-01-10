"""Abstract base class for template engines."""

from abc import ABC, abstractmethod
from typing import Any

from labelable.models.template import TemplateConfig


class BaseTemplateEngine(ABC):
    """Abstract base class for template engines."""

    @abstractmethod
    def render(self, template: TemplateConfig, context: dict[str, Any]) -> bytes:
        """Render a template with the given context.

        Args:
            template: The template configuration.
            context: Dictionary of field values to render.

        Returns:
            Rendered output as bytes (printer commands or bitmap data).

        Raises:
            TemplateError: If rendering fails.
        """
        pass

    @abstractmethod
    def supports_printer_type(self, printer_type: str) -> bool:
        """Check if this engine supports the given printer type.

        Args:
            printer_type: The printer type (e.g., 'zpl', 'epl2', 'ptouch').

        Returns:
            True if this engine can render for the printer type.
        """
        pass


class TemplateError(Exception):
    """Exception raised for template rendering errors."""

    pass
