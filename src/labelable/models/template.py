"""Label template configuration models."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

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


class EngineType(StrEnum):
    """Template engine types."""

    JINJA = "jinja"
    IMAGE = "image"


class LabelShape(StrEnum):
    """Label shape types."""

    RECTANGLE = "rectangle"
    CIRCLE = "circle"


class HorizontalAlignment(StrEnum):
    """Horizontal text alignment."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class VerticalAlignment(StrEnum):
    """Vertical text alignment."""

    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class ErrorCorrectionLevel(StrEnum):
    """QR code error correction levels."""

    L = "L"  # ~7% correction
    M = "M"  # ~15% correction
    Q = "Q"  # ~25% correction
    H = "H"  # ~30% correction


class BoundingBox(BaseModel):
    """Bounding box for element positioning in millimeters."""

    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float


class TextElement(BaseModel):
    """Text element for image templates."""

    type: Literal["text"] = "text"
    field: str | None = None  # Field name to render (mutually exclusive with static_text)
    static_text: str | None = None  # Static text to render
    bounds: BoundingBox
    font: str = "DejaVuSans"
    font_size: int = 14
    alignment: HorizontalAlignment = HorizontalAlignment.LEFT
    vertical_align: VerticalAlignment = VerticalAlignment.TOP
    wrap: bool = False
    auto_scale: bool = False
    circle_aware: bool = False
    line_spacing: float = 1.0  # Multiplier for line height (1.0 = normal, 1.5 = 150%)


class QRCodeElement(BaseModel):
    """QR code element for image templates.

    Position is specified by center coordinates (x_mm, y_mm) and size_mm.
    The QR code will be centered at (x_mm, y_mm).
    Use prefix/suffix to build URLs: content = prefix + field_value + suffix
    """

    type: Literal["qrcode"] = "qrcode"
    field: str
    x_mm: float  # Center X position
    y_mm: float  # Center Y position
    size_mm: float  # Size (QR codes are always square)
    error_correction: ErrorCorrectionLevel = ErrorCorrectionLevel.M
    prefix: str = ""  # Prepended to field value (e.g., "https://example.com/")
    suffix: str = ""  # Appended to field value


class DataMatrixElement(BaseModel):
    """DataMatrix element for image templates.

    Position is specified by center coordinates (x_mm, y_mm) and size_mm.
    The DataMatrix will be centered at (x_mm, y_mm).
    Use prefix/suffix to build URLs: content = prefix + field_value + suffix
    """

    type: Literal["datamatrix"] = "datamatrix"
    field: str
    x_mm: float  # Center X position
    y_mm: float  # Center Y position
    size_mm: float  # Size (DataMatrix codes are always square)
    prefix: str = ""  # Prepended to field value
    suffix: str = ""  # Appended to field value


class Code128Element(BaseModel):
    """Code 128 linear barcode element for image templates.

    Position is specified by center coordinates (x_mm, y_mm).
    Width is determined by content and module_width_mm.
    """

    type: Literal["code128"] = "code128"
    field: str
    x_mm: float  # Center X position
    y_mm: float  # Center Y position
    height_mm: float  # Barcode height
    module_width_mm: float = 0.3  # Width of narrowest bar (default ~0.3mm for 203dpi)
    prefix: str = ""  # Prepended to field value
    suffix: str = ""  # Appended to field value


# Union type with discriminator for element parsing
LabelElement = Annotated[
    TextElement | QRCodeElement | DataMatrixElement | Code128Element,
    Field(discriminator="type"),
]


class LabelDimensions(BaseModel):
    """Label dimensions in millimeters."""

    width_mm: float = 0.0
    height_mm: float = 0.0
    diameter_mm: float | None = None  # For circular labels


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
    template: str | None = None  # Jinja2 template content (required for jinja engine)
    quantity: int | None = None  # Fixed quantity - if set, user cannot change it

    # Image engine fields
    engine: EngineType = EngineType.JINJA
    shape: LabelShape = LabelShape.RECTANGLE
    dpi: int = 203  # Default DPI for thermal printers
    elements: list[LabelElement] = Field(default_factory=list)
    font_paths: list[str] = Field(default_factory=list)  # Additional font search paths

    # Label positioning (for centering on media)
    label_offset_x_mm: float = 0.0  # Horizontal offset in mm
    label_offset_y_mm: float = 0.0  # Vertical offset in mm

    # Print settings
    darkness: int | None = None  # Print darkness 0-30 (ZPL ~SD command)

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

        Built-in variables (like 'quantity') are passed through unchanged.
        """
        # Start with built-in variables that aren't template fields
        field_names = {f.name for f in self.fields}
        result = {k: v for k, v in data.items() if k not in field_names}

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
