"""Label template configuration models."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

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
    LIST = "list"  # Newline-separated list of values (textarea in UI)
    IMAGE = "image"  # PIL Image, encoded image bytes, or base64 / data URI string


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


def _parse_rgb(value: Any) -> Any:
    """Accept "#RRGGBB" / "RRGGBB" as well as an [r, g, b] list."""
    if isinstance(value, str):
        hex_digits = value.strip().removeprefix("#")
        if len(hex_digits) != 6:
            raise ValueError(f"Expected a #RRGGBB colour, got '{value}'")
        return tuple(int(hex_digits[i : i + 2], 16) for i in (0, 2, 4))
    return value


_Channel = Annotated[int, Field(ge=0, le=255)]
RGB = Annotated[tuple[_Channel, _Channel, _Channel], BeforeValidator(_parse_rgb)]

# Nominal colours for palette entries given by name only
NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "orange": (255, 128, 0),
}

# Common e-paper panels. Order is the palette index order in the output PNG.
PALETTE_PRESETS: dict[str, list[str]] = {
    "bw": ["black", "white"],
    "bwr": ["black", "white", "red"],
    "bwy": ["black", "white", "yellow"],
    "bwry": ["black", "white", "yellow", "red"],
    "spectra6": ["black", "white", "yellow", "red", "blue", "green"],
}

# Colours a template without a palette (thermal, 1-bit) can use
MONO_COLORS = ("black", "white")


class PaletteColor(BaseModel):
    """One ink of a colour display.

    ``rgb`` is the colour written to the output PNG's palette. ``measured_rgb``
    is how the ink actually looks on the panel (e-paper red is closer to
    brick than #FF0000). Dithering matches source pixels against the measured
    colours and diffuses the error relative to them, so a calibrated palette
    gives truer photos; the output still carries the nominal ``rgb``.
    """

    name: str
    rgb: RGB
    measured_rgb: RGB | None = None

    @property
    def dither_rgb(self) -> tuple[int, int, int]:
        """Colour used for dithering: the measured one when known."""
        return self.measured_rgb or self.rgb


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
    color: str = "black"  # Palette colour name
    # Place each line vertically by its drawn glyphs. Off (legacy) draws at the
    # font's origin, so text sits low by the gap above the glyphs and middle or
    # bottom aligned text can spill out of its bounds. Off by default so
    # existing labels keep their layout.
    vertical_align_to_ink: bool = False


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
    color: str = "black"  # Palette colour of the dark modules


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
    color: str = "black"  # Palette colour of the dark modules


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
    color: str = "black"  # Palette colour of the bars


class ImageFit(StrEnum):
    """How an image is scaled into its bounds."""

    CONTAIN = "contain"  # Keep aspect ratio, fit inside bounds
    COVER = "cover"  # Keep aspect ratio, fill bounds and crop the overflow
    STRETCH = "stretch"  # Fill bounds exactly, ignoring aspect ratio


class DitherMode(StrEnum):
    """How an image is reduced to the template palette."""

    FLOYD_STEINBERG = "floyd_steinberg"
    NONE = "none"  # Nearest palette colour per pixel


class ImageElement(BaseModel):
    """Raster image element, reduced to the template palette.

    The image comes from either ``field`` (a context value: a PIL Image,
    encoded image bytes, or a base64 / data URI string) or ``source`` (a file
    relative to one of the engine's ``image_paths``). Field values are never
    treated as file paths.
    """

    type: Literal["image"] = "image"
    field: str | None = None
    source: str | None = None
    bounds: BoundingBox
    fit: ImageFit = ImageFit.CONTAIN
    alignment: HorizontalAlignment = HorizontalAlignment.CENTER
    vertical_align: VerticalAlignment = VerticalAlignment.MIDDLE
    dither: DitherMode = DitherMode.FLOYD_STEINBERG

    @model_validator(mode="after")
    def _one_source(self) -> "ImageElement":
        if (self.field is None) == (self.source is None):
            raise ValueError("image element needs exactly one of 'field' or 'source'")
        return self


class RectElement(BaseModel):
    """Filled and/or outlined rectangle, e.g. a header bar behind white text.

    Elements draw in list order, so put the rectangle before anything that
    should sit on top of it.
    """

    type: Literal["rect"] = "rect"
    bounds: BoundingBox
    fill: str | None = "black"  # Palette colour name, or null for outline only
    outline: str | None = None  # Palette colour name
    outline_width_mm: float = Field(default=0.5, gt=0)
    radius_mm: float = Field(default=0.0, ge=0)  # Corner radius

    @model_validator(mode="after")
    def _visible(self) -> "RectElement":
        if self.fill is None and self.outline is None:
            raise ValueError("rect element needs a 'fill', an 'outline', or both")
        return self


# Union type with discriminator for element parsing
LabelElement = Annotated[
    TextElement | QRCodeElement | DataMatrixElement | Code128Element | ImageElement | RectElement,
    Field(discriminator="type"),
]


class BatchAlignment(StrEnum):
    """Alignment of text along the label length in batch mode."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class BatchConfig(BaseModel):
    """Configuration for batch label printing (multiple labels from a list)."""

    alignment: BatchAlignment = BatchAlignment.CENTER
    cut_lines: bool = True
    padding_mm: float = 1.0
    min_label_length_mm: float = 0
    margin_mm: float = 1.0  # Top/bottom margin within tape width


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

    # Colour displays (e-paper). Unset means a 1-bit thermal label in black
    # and white. Set, the template renders only to an indexed PNG whose
    # palette indices follow this list. Accepts a preset name (see
    # PALETTE_PRESETS), a list of colour names, or a list of PaletteColor.
    palette: list[PaletteColor] | None = None
    background: str = "white"  # Palette colour name for the canvas

    # Label positioning (for centering on media)
    label_offset_x_mm: float = 0.0  # Horizontal offset in mm
    label_offset_y_mm: float = 0.0  # Vertical offset in mm

    # Print settings
    darkness: int | None = None  # Print darkness 0-30 (ZPL ~SD command)
    # Print width in mm (ZPL ^PW command). Unset leaves the printer's stored
    # width, which clips anything printed past it.
    print_width_mm: float | None = Field(default=None, gt=0)

    # P-Touch settings (for continuous tape printers)
    ptouch_tape_width_mm: int | None = None  # 6, 9, 12, 18, or 24
    ptouch_auto_cut: bool = True
    ptouch_chain_print: bool = False  # Hold label in printer (don't feed after print)
    ptouch_margin_mm: float = 1.0  # Padding around content before cropping

    # Batch printing settings (for printing multiple labels from a list field)
    batch: BatchConfig | None = None

    @field_validator("palette", mode="before")
    @classmethod
    def _expand_palette(cls, value: Any) -> Any:
        if isinstance(value, str):
            if value not in PALETTE_PRESETS:
                raise ValueError(f"Unknown palette preset '{value}' (known: {', '.join(PALETTE_PRESETS)})")
            value = PALETTE_PRESETS[value]
        if isinstance(value, list):
            expanded = []
            for entry in value:
                if isinstance(entry, str):
                    if entry not in NAMED_COLORS:
                        raise ValueError(f"Unknown colour '{entry}' - give it as {{name, rgb}}")
                    entry = {"name": entry, "rgb": NAMED_COLORS[entry]}
                expanded.append(entry)
            value = expanded
        return value

    @model_validator(mode="after")
    def _check_colors(self) -> "TemplateConfig":
        if self.palette is not None:
            names = [c.name for c in self.palette]
            if len(names) < 2:
                raise ValueError("palette needs at least two colours")
            if len(set(names)) != len(names):
                raise ValueError(f"palette colour names must be unique: {names}")
        allowed = self.color_names()
        if self.background not in allowed:
            raise ValueError(f"background '{self.background}' is not in the palette {list(allowed)}")
        for element in self.elements:
            for attr in ("color", "fill", "outline"):
                color = getattr(element, attr, None)
                if color is not None and color not in allowed:
                    raise ValueError(f"{element.type} element {attr} '{color}' is not in the palette {list(allowed)}")
        return self

    def palette_colors(self) -> list[PaletteColor]:
        """Colours to render with: the template palette, or black/white."""
        if self.palette is not None:
            return self.palette
        return [PaletteColor(name=name, rgb=NAMED_COLORS[name]) for name in MONO_COLORS]

    def color_names(self) -> tuple[str, ...]:
        """Names usable for element ``color`` and ``background``."""
        return tuple(c.name for c in self.palette_colors())

    def color_rgb(self, name: str) -> tuple[int, int, int]:
        """Nominal RGB of a palette colour."""
        for color in self.palette_colors():
            if color.name == name:
                return color.rgb
        raise ValueError(f"Colour '{name}' is not in the palette {list(self.color_names())}")

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
                elif field.type == FieldType.LIST:
                    result[field.name] = str(value)
                elif field.type == FieldType.IMAGE:
                    # Decoded by the image element renderer
                    result[field.name] = value
                else:
                    result[field.name] = str(value)
            elif field.default is not None:
                result[field.name] = field.default
            elif field.required:
                raise ValueError(f"Missing required field: {field.name}")

        return result
