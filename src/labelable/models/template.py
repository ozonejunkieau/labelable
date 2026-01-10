"""Label template configuration models."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# Default datetime format
DEFAULT_DATETIME_FORMAT = "%Y-%m-%d %H:%M"


class FieldType(StrEnum):
    """Supported field types for template fields."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    USER = "user"
    SELECT = "select"  # Enum/select with predefined options


class LabelDimensions(BaseModel):
    """Label dimensions in millimeters."""

    width_mm: float
    height_mm: float


class TemplateField(BaseModel):
    """Definition of a field in a label template."""

    name: str
    type: FieldType = FieldType.STRING
    required: bool = True
    default: Any = None
    description: str = ""
    format: str = ""  # Format string for datetime fields (strftime format)
    options: list[str] = []  # Options for select fields


class TemplateConfig(BaseModel):
    """Configuration for a label template."""

    name: str
    description: str = ""
    dimensions: LabelDimensions
    supported_printers: list[str] = Field(default_factory=list)  # Printer names from config
    fields: list[TemplateField] = Field(default_factory=list)
    template: str  # Jinja2 template content

    def get_field(self, name: str) -> TemplateField | None:
        """Get a field by name."""
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def validate_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply defaults to template data.

        Returns the validated data with defaults applied.
        Raises ValueError if required fields are missing or types don't match.
        """
        result = {}

        for field in self.fields:
            if field.type == FieldType.DATETIME:
                # Datetime fields auto-populate with current time
                # Use field's format or default format
                fmt = field.format or DEFAULT_DATETIME_FORMAT
                result[field.name] = datetime.now().strftime(fmt)
            elif field.type == FieldType.USER:
                # User fields are populated externally from request context
                # Use value from data if provided, otherwise empty string
                result[field.name] = data.get(field.name, "")
            elif field.name in data:
                value = data[field.name]
                # Type coercion/validation
                if field.type == FieldType.INTEGER:
                    result[field.name] = int(value)
                elif field.type == FieldType.FLOAT:
                    result[field.name] = float(value)
                elif field.type == FieldType.BOOLEAN:
                    if isinstance(value, bool):
                        result[field.name] = value
                    elif isinstance(value, str):
                        # "on" is what HTML checkboxes send when checked
                        result[field.name] = value.lower() in ("true", "1", "yes", "on")
                    else:
                        result[field.name] = bool(value)
                else:
                    result[field.name] = str(value)
            elif field.default is not None:
                result[field.name] = field.default
            elif field.required:
                raise ValueError(f"Missing required field: {field.name}")

        return result
