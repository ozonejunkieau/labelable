"""Jinja2 template engine for ZPL/EPL2 printers."""

import hashlib
from typing import Any

from jinja2 import BaseLoader, Environment, TemplateSyntaxError, UndefinedError

from labelable.models.template import TemplateConfig
from labelable.templates.engine import BaseTemplateEngine, TemplateError


def _md5_filter(value: str) -> str:
    """Return MD5 hash of a string (hex digest)."""
    return hashlib.md5(value.encode("utf-8")).hexdigest()


class StringLoader(BaseLoader):
    """Jinja2 loader that loads templates from strings."""

    def get_source(self, environment, template):
        # Not used for string templates
        raise NotImplementedError()


class JinjaTemplateEngine(BaseTemplateEngine):
    """Jinja2-based template engine for text-based printer commands.

    Suitable for ZPL and EPL2 printers that accept text-based commands.
    """

    SUPPORTED_TYPES = {"zpl", "epl2"}

    def __init__(self) -> None:
        self._env = Environment(
            loader=StringLoader(),
            autoescape=False,  # No HTML escaping for printer commands
            keep_trailing_newline=True,
        )
        # Add custom filters
        self._env.filters["md5"] = _md5_filter

    def render(self, template: TemplateConfig, context: dict[str, Any]) -> bytes:
        """Render a Jinja2 template with the given context.

        Args:
            template: The template configuration containing the Jinja2 template string.
            context: Dictionary of field values to render.

        Returns:
            Rendered printer commands as bytes.

        Raises:
            TemplateError: If rendering fails.
        """
        try:
            # Validate context against template fields
            validated_context = template.validate_data(context)

            # Compile and render the template
            jinja_template = self._env.from_string(template.template)
            rendered = jinja_template.render(**validated_context)

            return rendered.encode("utf-8")

        except TemplateSyntaxError as e:
            raise TemplateError(f"Template syntax error: {e}") from e
        except UndefinedError as e:
            raise TemplateError(f"Undefined variable in template: {e}") from e
        except ValueError as e:
            raise TemplateError(f"Invalid template data: {e}") from e
        except Exception as e:
            raise TemplateError(f"Failed to render template: {e}") from e

    def supports_printer_type(self, printer_type: str) -> bool:
        """Check if this engine supports the given printer type."""
        return printer_type.lower() in self.SUPPORTED_TYPES
