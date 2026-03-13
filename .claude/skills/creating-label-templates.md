---
description: Use when creating, editing, debugging, or printing label templates. Covers template YAML structure, image engine elements (text, QR, barcodes), field types, Jinja engine, printer testing (ZPL, EPL2, P-Touch), direct TCP debugging, font configuration, batch labels, and the labelable-render CLI.
globs:
  - "templates/**/*.yaml"
  - "src/labelable/templates/**"
  - "src/labelable/printers/**"
---

# Creating Label Templates for Labelable

Label templates are YAML files in the `templates/` directory. They define the layout, fields, and rendering of labels for thermal printers (Zebra ZPL/EPL2) and Brother P-Touch label printers.

## Template Engines

There are two engines:

- **`image`** (recommended) — Visual element-based rendering with PIL. Supports text, QR codes, DataMatrix, Code 128 barcodes. Works with all printer types.
- **`jinja`** (default) — Raw ZPL/EPL2 commands with Jinja2 templating. Only for Zebra printers. Requires knowledge of the printer command language.

Always use `engine: image` unless the user specifically needs raw ZPL/EPL2 control.

## Quick Reference: Minimal Templates

### Rectangular label (Zebra ZPL printer)

```yaml
name: my-label
description: Simple rectangular label
engine: image
dimensions:
  width_mm: 73
  height_mm: 20
dpi: 203
supported_printers:
  - zpl-printer
fields:
  - name: title
    type: string
    required: true
elements:
  - type: text
    field: title
    bounds: { x_mm: 1, y_mm: 1, width_mm: 71, height_mm: 18 }
    font_size: 48
    alignment: center
    vertical_align: middle
    auto_scale: true
```

### Circular label (Zebra ZPL printer)

```yaml
name: jar-label
description: Circular jar label
engine: image
shape: circle
dimensions:
  diameter_mm: 50
dpi: 203
supported_printers:
  - zpl-printer
fields:
  - name: title
    type: string
    required: true
elements:
  - type: text
    field: title
    bounds: { x_mm: 3, y_mm: 4, width_mm: 44, height_mm: 42 }
    font_size: 96
    alignment: center
    vertical_align: middle
    auto_scale: true
    circle_aware: true
    wrap: true
```

### Rectangular label (Zebra EPL2 printer)

```yaml
name: my-epl2-label
description: Simple EPL2 label
engine: image
dimensions:
  width_mm: 40
  height_mm: 15
dpi: 203
supported_printers:
  - epl2-printer
fields:
  - name: title
    type: string
    required: true
  - name: code
    type: string
    required: false
elements:
  - type: text
    field: title
    bounds: { x_mm: 1, y_mm: 0.5, width_mm: 25, height_mm: 13 }
    font_size: 48
    alignment: left
    vertical_align: middle
    auto_scale: true
  - type: qrcode
    field: code
    x_mm: 34
    y_mm: 7.5
    size_mm: 11
    error_correction: M
```

### P-Touch label (Brother continuous tape)

```yaml
name: ptouch-label
description: Simple P-Touch tape label
engine: image
dimensions:
  width_mm: 9        # Tape width (fixed by physical tape)
  height_mm: 50      # Max length (cropped to content)
dpi: 180             # P-Touch uses 180 DPI
supported_printers:
  - ptouch
ptouch_tape_width_mm: 9
ptouch_auto_cut: true
ptouch_margin_mm: 3.0
fields:
  - name: title
    type: string
    required: true
elements:
  - type: text
    field: title
    bounds: { x_mm: 0, y_mm: 2, width_mm: 9, height_mm: 7 }
    font_size: 72
    alignment: center
    vertical_align: middle
    auto_scale: true
```

### Batch labels (P-Touch strip of multiple labels)

```yaml
name: wire-labels
description: Multiple labels printed as one strip
engine: image
dimensions:
  width_mm: 9
  height_mm: 50
dpi: 180
supported_printers:
  - ptouch
ptouch_tape_width_mm: 9
ptouch_auto_cut: true
ptouch_margin_mm: 1.0
batch:
  alignment: center
  cut_lines: true
  padding_mm: 1.5
fields:
  - name: items
    type: list
    required: true
    description: "One item per line"
elements:
  - type: text
    field: items
    bounds: { x_mm: 0, y_mm: 0, width_mm: 9, height_mm: 50 }
    font: MartianMono-Medium
    font_size: 72
    alignment: center
    auto_scale: true
```

## Template Structure Reference

### Top-level properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `name` | string | (required) | Template identifier |
| `description` | string | | Human-readable description |
| `engine` | `image` or `jinja` | `jinja` | Rendering engine |
| `shape` | `rectangle` or `circle` | `rectangle` | Label shape (image engine) |
| `dimensions` | object | | Label size (see below) |
| `dpi` | int | `203` | Printer DPI. Zebra = 203 or 300. P-Touch = 180 |
| `supported_printers` | list[str] | | Printer names this template works with |
| `fields` | list | | User input fields |
| `elements` | list | | Visual elements (image engine only) |
| `template` | string | | Jinja2 template string (jinja engine only) |
| `darkness` | int | | Print darkness 0-30 (ZPL only) |
| `label_offset_x_mm` | float | `0.0` | Horizontal print offset |
| `label_offset_y_mm` | float | `0.0` | Vertical print offset |
| `quantity` | int | | Fixed print quantity (hides quantity input in UI) |

### Dimensions

For rectangles:
```yaml
dimensions:
  width_mm: 73
  height_mm: 20
```

For circles:
```yaml
dimensions:
  diameter_mm: 50
```

For P-Touch continuous tape, `width_mm` is the tape width (fixed) and `height_mm` is the max label length (content is auto-cropped).

### P-Touch properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `ptouch_tape_width_mm` | int | | Tape width: 6, 9, 12, 18, or 24 |
| `ptouch_auto_cut` | bool | `true` | Auto-cut after printing |
| `ptouch_chain_print` | bool | `false` | Hold label in printer (no feed) |
| `ptouch_margin_mm` | float | `1.0` | Content padding before cropping |

### Batch config

Only used with P-Touch continuous tape and a `list` field.

```yaml
batch:
  alignment: center     # left, center, or right
  cut_lines: true       # Draw cut guides between labels
  padding_mm: 1.5       # Padding around each label
  min_label_length_mm: 0  # Minimum label width (0 = auto)
```

## Field Types

| Type | UI Widget | Description |
|------|-----------|-------------|
| `string` | Text input | Free text |
| `integer` | Number input | Whole numbers |
| `float` | Number input | Decimal numbers |
| `boolean` | Checkbox | True/false |
| `select` | Radio buttons | Predefined choices (requires `options` list) |
| `datetime` | Auto-filled | Current timestamp (uses `format` for strftime) |
| `user` | Auto-filled | HA user display name |
| `list` | Textarea | Newline-separated values (for batch mode) |

Field properties:
```yaml
fields:
  - name: title           # Field name (used in elements/template)
    type: string           # Field type
    required: true         # Whether field must be filled
    default: ""            # Default value
    description: "Help text shown in UI"
    format: "%Y-%m-%d"    # strftime format (datetime only)
    options:               # Choices (select only)
      - Option A
      - Option B
```

## Element Types (Image Engine)

### Text

```yaml
- type: text
  field: title              # Field name to render (or use static_text)
  # static_text: "Fixed"   # Alternative: static text, no field needed
  bounds:
    x_mm: 1
    y_mm: 1
    width_mm: 50
    height_mm: 10
  font: DejaVuSans          # Font name (default: DejaVuSans)
  font_size: 24             # Font size in points (default: 14)
  alignment: center          # left, center, right (default: left)
  vertical_align: middle     # top, middle, bottom (default: top)
  wrap: true                 # Word wrap within bounds (default: false)
  auto_scale: true           # Shrink font to fit bounds (default: false)
  circle_aware: true         # Adjust wrapping for circular labels (default: false)
  line_spacing: 1.3          # Line height multiplier (default: 1.0)
```

- `auto_scale` does a binary search from `font_size` down to 6pt to find the largest size that fits
- `circle_aware` calculates available width per line based on chord geometry
- `wrap` and `circle_aware` are independent: circle_aware without wrap will still try to fit on one line
- Use `static_text` instead of `field` for labels that don't change (e.g., a fixed heading)

### QR Code

```yaml
- type: qrcode
  field: code               # Field containing QR data
  x_mm: 60                  # Center X position
  y_mm: 10                  # Center Y position
  size_mm: 18               # QR code size (square)
  error_correction: M        # L, M, Q, or H (default: M)
  prefix: "https://example.com/"  # Prepended to field value
  suffix: ""                 # Appended to field value
```

The encoded content is `prefix + field_value + suffix`.

### DataMatrix

```yaml
- type: datamatrix
  field: code
  x_mm: 25
  y_mm: 35
  size_mm: 12
  prefix: ""
  suffix: ""
```

Same positioning model as QR code. Requires `pylibdmtx`.

### Code 128 Barcode

```yaml
- type: code128
  field: code
  x_mm: 32                  # Center X position
  y_mm: 17                  # Center Y position
  height_mm: 4              # Barcode height
  module_width_mm: 0.3      # Narrowest bar width (default: 0.3)
  prefix: ""
  suffix: ""
```

Width is determined automatically by content length and module width.

## Coordinate System

- Origin (0, 0) is **top-left** of the label
- All positions are in **millimeters**
- Text elements use a **bounding box** (`bounds`) — text is positioned within this box according to alignment
- Barcode/QR elements use **center coordinates** (`x_mm`, `y_mm`) — the element is centered at this point
- For circular labels, the circle is inscribed in a square of `diameter_mm` size

## Fonts

- Default font: `DejaVuSans` (falls back to PIL default if not installed)
- System fonts like `Arial`, `Arial Bold`, `Helvetica`, `Courier New` work on macOS
- Google Fonts are auto-downloaded when `download_google_fonts: true` in config
- Specify font by name (e.g., `MartianMono-Medium`, `Delius`, `PTSans-Bold`)
- For monospace labels (wire markers, codes), use `MartianMono-Medium`
- For decorative labels (pantry, jars), use a display font like `Delius` or `PlaywriteNZ-Regular`
- Use `labelable-render` to verify fonts resolve — warnings are printed for missing fonts

## Previewing Templates

```bash
# Render a preview PNG
uv run labelable-render templates/my-label.yaml -d title="Test" -o preview.png

# Preview P-Touch label
uv run labelable-ptouch print templates/ptouch-test.yaml -d title="Test" --preview preview.png

# Preview batch labels
uv run labelable-ptouch print templates/wire-labels.yaml -d items="GND\nVCC\nSDA" --preview preview.png
```

Always preview labels with `labelable-render` or `--preview` before printing.

## API Usage

```bash
# Print via API
curl -X POST http://localhost:7979/api/v1/print/my-label \
  -H "Content-Type: application/json" \
  -d '{"printer": "zpl-printer", "data": {"title": "Hello"}}'

# List available templates
curl http://localhost:7979/api/v1/templates

# List printers
curl http://localhost:7979/api/v1/printers
```

## Direct Printer Testing (TCP)

For EPL2 printers accessible via TCP, you can send commands directly for testing:

```bash
# Check printer status (needs CRLF + delay for response)
(printf 'UQ\r\n'; sleep 2) | nc -w 5 <ip> <port> | cat -v

# Send raw EPL2 commands
(printf 'N\r\nA10,5,0,4,1,1,N,"Test"\r\nP1\r\n'; sleep 2) | nc -w 5 <ip> <port>

# Send image-rendered EPL2 binary
uv run labelable-render templates/my-label.yaml --format epl2 -d title="Test" -o /tmp/label.bin
(cat /tmp/label.bin; sleep 2) | nc -w 5 <ip> <port>
```

Key UQ response fields: `rY`/`rN` = ready yes/no, `q<dots>` = label width, `Q<dots>,<gap>` = label length.
"ERROR!! PRESS EXIT" means the printer needs a physical button press or power cycle to clear an error.

## Design Tips

- **Always use `auto_scale: true`** for text that varies in length — it prevents overflow
- **Set `font_size` to the maximum desired size** when using auto_scale — it scales down from there
- **P-Touch labels are rotated 90 degrees** internally: `width_mm` = tape width (narrow direction), `height_mm` = label length (feed direction). Content is auto-cropped to fit
- **For circular labels**, set `circle_aware: true` and `wrap: true` on text elements to get proper text flow within the circle boundary
- **Batch mode** auto-activates when a template has both a `batch` config and a `list` field — all labels are rendered as one continuous strip with uniform sizing
- **`supported_printers`** should list printer names from config, not printer types. E.g., `zpl-printer` not `zpl`
- **Keep bounds within label dimensions** — elements outside the label area will be clipped
