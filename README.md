# Labelable

A general purpose label printing API and web UI for home use.

## Features

- **Multiple Printer Support**: Zebra ZPL, Zebra EPL2, and Brother P-Touch (USB), plus ESP32-P4 network bridges for P-Touch and Zebra USB printers
- **Two Template Engines**:
  - **Jinja**: Raw ZPL/EPL2 commands with Jinja2 templating
  - **Image**: Visual element-based rendering with QR codes, DataMatrix, text wrapping
- **Label Shapes**: Rectangle and circle support with circle-aware text layout
- **Label Preview**: CLI tool to preview templates as PNG before printing
- **Google Fonts**: Automatic font downloading for image templates
- **REST API**: Print labels programmatically from Home Assistant or other systems
- **Web UI**: Simple browser-based interface for manual label printing
- **Print Queue**: Automatic queuing when printers are offline
- **Home Assistant Integration**: Ingress support, user mapping, API authentication
- **HA Custom Integration**: Native Zebra printer integration with DHCP discovery and sensor entities

## Installation

### Home Assistant Add-on (Recommended)

1. Add this repository to Home Assistant:
   - Go to **Settings → Add-ons → Add-on Store**
   - Click **⋮** (menu) → **Repositories**
   - Add: `https://github.com/ozonejunkieau/labelable`

2. Install the **Labelable** add-on

3. Configure your printers in `/config/labelable/config.yaml`

4. Access via the Home Assistant sidebar

### Docker

```bash
docker build -t labelable .
docker run -p 7979:7979 \
  -v ./config.yaml:/app/config.yaml \
  -v ./templates:/app/templates \
  labelable
```

### Python Package

The base install is the template engine only - enough to render labels and
e-paper images from Python or with `labelable-render`. Extras add the rest:

| Install | Provides |
|---------|----------|
| `labelable` | Templates, rendering, `labelable-render` |
| `labelable[bridge]` | `labelable-bridge` and `labelable-ptouch` (P-Touch over USB) |
| `labelable[server]` | The API and web UI, with every printer transport |

```bash
uv add "labelable @ git+https://github.com/ozonejunkieau/labelable"                 # library
uv tool install "labelable[server] @ git+https://github.com/ozonejunkieau/labelable" # server
```

The add-on and Docker image already include `server`.

### Local Development

```bash
uv sync      # the dev group pulls in labelable[server] for the tests
just run
```

## Configuration

Copy `config.example.yaml` to `config.yaml`:

```yaml
queue_timeout_seconds: 300
templates_dir: ./templates

# API key for external access (optional)
# If set, requests must authenticate (see API Authentication below)
api_key: your-secret-key-here

# Home Assistant user mapping
user_mapping:
  "abc123-user-uuid": "Test User"
default_user: ""

printers:
  - name: warehouse-zpl
    type: zpl
    connection:
      type: tcp
      host: 192.168.1.100
      port: 9100
    healthcheck:
      interval: 60
      command: "~HS"

# Google Fonts auto-download for image templates
download_google_fonts: true
fonts_dir: ./fonts
```

## Templates

Create label templates in `templates/`. Files starting with `_` are ignored (use for examples).

### Jinja Engine (Default)

For raw ZPL/EPL2 commands with Jinja2 templating:

```yaml
name: shipping-label
description: Basic shipping address label
engine: jinja  # Optional, this is the default
dimensions:
  width_mm: 100
  height_mm: 50
supported_printers:
  - warehouse-zpl
fields:
  - name: name
    type: string
    required: true
  - name: printed_at
    type: datetime
    format: "%Y-%m-%d %H:%M"
template: |
  ^XA
  ^FO50,50^A0N,30,30^FD{{ name }}^FS
  ^FO50,100^A0N,20,20^FD{{ printed_at }}^FS
  ^PQ{{ quantity }}
  ^XZ
```

### Image Engine

For visual element-based labels with QR codes, DataMatrix, and advanced text features:

```yaml
name: jar-label
description: Circular label for mason jars
engine: image
shape: circle
dimensions:
  diameter_mm: 50
dpi: 203
label_offset_x_mm: 2.3  # Fine-tune label positioning
print_width_mm: 104     # Optional ZPL ^PW, if the printer clips the right edge
supported_printers:
  - zpl-printer
fields:
  - name: title
    type: string
    required: true
  - name: code
    type: string
elements:
  - type: text
    field: title
    bounds: { x_mm: 5, y_mm: 8, width_mm: 40, height_mm: 15 }
    font: DejaVuSans-Bold
    font_size: 28
    alignment: center
    auto_scale: true
    circle_aware: true
    wrap: true
    line_spacing: 1.3  # Line height multiplier for wrapped text
    vertical_align_to_ink: true  # Recommended for new templates, see below

  - type: qrcode
    field: code
    x_mm: 25
    y_mm: 32
    size_mm: 16
    error_correction: M
```

### Colour Displays (e-paper)

Image templates can target 2, 3, 4 and 6 colour displays instead of a
printer. Give the template a `palette` and they render to an indexed PNG
whose palette indices follow the palette order:

```yaml
name: shelf-tag
engine: image
dimensions: { width_mm: 40, height_mm: 30 }   # 400 x 300 px
dpi: 254                                      # 1 px = 0.1 mm
palette:                                      # or a preset: bw, bwr, bwy, bwry, spectra6
  - black
  - white
  - yellow
  - { name: red, rgb: "#ff0000", measured_rgb: "#9c2b25" }
background: white
fields:
  - { name: title }
  - { name: photo, type: image }
elements:
  - type: text
    field: title
    color: red
    bounds: { x_mm: 1, y_mm: 1, width_mm: 38, height_mm: 7 }
    auto_scale: true
  - type: image
    field: photo
    fit: cover                 # contain (default), cover, stretch
    dither: floyd_steinberg    # or none
    bounds: { x_mm: 1, y_mm: 9, width_mm: 25, height_mm: 20 }
```

For header bars and boxes, `rect` elements take a `fill` and/or an
`outline` colour (plus `outline_width_mm` and `radius_mm`). Elements draw in
order, so list a rect before the text that sits on it:

```yaml
  - type: rect
    bounds: { x_mm: 0, y_mm: 0, width_mm: 40, height_mm: 8 }
    fill: red              # or null for an outline-only box
    radius_mm: 1
  - type: text
    field: title
    color: white
    bounds: { x_mm: 1, y_mm: 0, width_mm: 38, height_mm: 8 }
    vertical_align: middle
    vertical_align_to_ink: true   # centre the glyphs themselves in the bar
```

`rect` works on thermal templates too, in black or white.

Text and barcodes take a `color` and are drawn crisp, in exact palette
colours. Only `image` elements are dithered. `measured_rgb` is optional
calibration: how that ink actually looks on the panel. Dithering matches
against it, so photos come out truer, while the PNG keeps the nominal `rgb`.

Image elements also work on thermal labels (dithered to black and white).
An image `source` (instead of `field`) is read from the templates directory.

To render many labels from Python, use Labelable as a library:

```python
import yaml
from labelable.models.template import TemplateConfig
from labelable.templates.image_engine import ImageTemplateEngine

engine = ImageTemplateEngine(custom_font_paths=["./fonts"], image_paths=["./images"])
template = TemplateConfig.model_validate(yaml.safe_load(open("shelf-tag.yaml")))
for item in items:
    image = engine.render_image(template, {"title": item.name, "photo": item.photo})
    image.save(f"{item.id}.png")   # mode "P", palette in template order
```

Or from the CLI: `labelable-render shelf-tag.yaml --format device-png -o out.png`.

A complete example, with rendered output, is in [`examples/epaper`](examples/epaper).

Text elements are positioned from the font's origin by default, which
leaves text sitting a little low and lets middle- or bottom-aligned text
spill past its bounds. Set `vertical_align_to_ink: true` on a text element to
place it by its visible glyphs instead. It is off by default so existing
labels print exactly as before.

### Preview Templates

Use the CLI to preview image templates:

```bash
uv run labelable-render templates/jar-label.yaml \
  -d title="Honey" -d code="H001" \
  --download-fonts \
  -o preview.png
```

### Field Types

| Type | Description |
|------|-------------|
| `string` | Text input |
| `integer` | Whole number |
| `float` | Decimal number |
| `boolean` | True/false checkbox |
| `select` | Radio buttons from predefined `options` list |
| `datetime` | Auto-populated with current time (uses `format` for strftime) |
| `user` | Auto-populated from Home Assistant user or `default_user` |
| `list` | Newline-separated list (textarea in UI, used with batch config) |
| `image` | Image for an `image` element: base64 / data URI over the API; PIL Image or bytes from Python |

### Built-in Template Variables

| Variable | Description |
|----------|-------------|
| `quantity` | Number of labels being printed (for `^PQ` and `^SN` commands) |

## API Usage

See [docs/PYTHON_CLIENT.md](docs/PYTHON_CLIENT.md) for complete API documentation with examples.

### Quick Example

```python
import requests

response = requests.post(
    "http://localhost:7979/api/v1/print/shipping-label",
    headers={"X-API-Key": "your-secret-key"},  # omit if no api_key configured
    json={"data": {"name": "John Doe"}, "printer": "warehouse-zpl", "quantity": 1}
)
```

### From Home Assistant Automations

When running as an add-on, use the internal Docker hostname:

```yaml
# configuration.yaml
rest_command:
  print_label:
    url: "http://local-labelable:7979/api/v1/print/{{ template }}"
    method: POST
    content_type: "application/json"
    payload: '{"data": {{ data | tojson }}, "printer": "{{ printer }}"}'
```

```yaml
# automation
action:
  - service: rest_command.print_label
    data:
      template: shipping-label
      printer: warehouse-zpl
      data:
        name: "Package for {{ trigger.event.data.recipient }}"
```

### API Authentication

If `api_key` is set in config, requests must authenticate via one of:

| Method | Example |
|--------|---------|
| `X-API-Key` header | `X-API-Key: your-secret-key` |
| `Authorization` header | `Authorization: Bearer your-secret-key` |
| Query parameter | `?api_key=your-secret-key` |

**Note:** Requests via Home Assistant Ingress (sidebar UI) are automatically authenticated and don't need the API key.

> [!WARNING]
> **The API key is not a complete access control.** It is checked on the REST API
> (`/api/v1/...`) only, and can be bypassed. Treat any network that can reach
> ports 7979/7980 as able to print, create templates and reload them. See
> [Security](#security).

### Security

Known limitations of the API key (`api_key`), as of 0.5.1:

| Gap | Effect |
|-----|--------|
| **`X-Ingress-Path` is trusted from any client.** A request carrying this header skips the key check (`api/routes.py`, `verify_api_key`) and, in dual HTTP+HTTPS mode, the block on direct HTTP access (`app.py`, `IngressPathMiddleware`). Only HA Ingress should send it, but nothing verifies that it did. | Anyone who can reach port 7979 or 7980 can call the whole REST API without the key. |
| **The web UI's action routes are not covered by the key.** `POST /api/print/{template}/submit`, `POST /api/print/{template}/preview` and `/api/reload-templates` are on the UI router, which has no key dependency. | Anyone who can reach the port can print any template and reload templates. |
| **The MCP endpoint (`/mcp`) does not check the key** when `mcp_enabled: true`. | Anyone who can reach the port can print and create or update templates through MCP. |

What this means in practice:

- **Don't expose ports 7979/7980 beyond a network you trust.** HA Ingress (the
  sidebar) does not need either port mapped; leave them unmapped in the add-on's
  Network settings unless something outside HA must reach Labelable.
- Template creation is part of what is exposed. Jinja templates are rendered in
  a sandbox (since 0.5.2), so a template cannot run arbitrary code, but it can
  still send any command a printer accepts.
- Leave `mcp_enabled` off unless the port is only reachable by trusted clients.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/printers` | List printers with status |
| GET | `/api/v1/templates` | List available templates |
| GET | `/api/v1/templates/{name}` | Get template details |
| POST | `/api/v1/print/{template}` | Submit print job |

**Response Codes:**
- `200` - Job accepted and queued; the printer was online. It has not necessarily printed yet: poll `GET /api/v1/jobs/{job_id}`
- `202` - Job queued; the printer is offline and the job prints when it comes back
- `400` - Invalid request
- `401` - Invalid or missing API key
- `404` - Template or printer not found

## Development

```bash
just install    # Install dependencies
just run        # Run dev server (auto-reload)
just test       # Run tests
just fmt        # Format code
just lint       # Lint code
```

### Creating a Release

1. Go to **Actions** → **Create Release**
2. Click **Run workflow**
3. Enter the version (e.g., `0.2.0`)
4. Click **Run workflow**

This automatically:
- Updates version in `pyproject.toml` and `ha-addon/config.yaml`
- Creates a git tag
- Creates a GitHub release
- Triggers the build workflow to push Docker images to GHCR

## Tested Hardware

| Printer | Connection | Protocol |
|---------|------------|----------|
| Zebra GK420d | Ethernet (TCP 9100) | ZPL |
| Zebra LP2844 | RS232-WiFi adapter | EPL2 |
| Brother PT-P710BT | USB | P-Touch PTCBP |

## Zebra Printer HA Integration

This repository includes a native Home Assistant custom integration for Zebra printers (`custom_components/zebra_printer/`).

### Features

- **DHCP Discovery**: Automatically detects Zebra printers on your network by MAC address
- **Protocol Auto-Detection**: Probes printers to determine ZPL or EPL2 protocol
- **Rich Sensors**: Model, firmware, labels printed, head distance, print speed, and more
- **Binary Sensors**: Online status, paper out, head open, ribbon out, paused, buffer full
- **Services**: `print_raw`, `calibrate`, `feed` for automations

### Installation via HACS

1. Add this repository to HACS as a custom repository
2. Install "Zebra Printer" integration
3. Go to **Settings → Devices & Services → Add Integration → Zebra Printer**
4. Enter your printer's IP address (or let DHCP discovery find it)

### Labelable + HA Integration

When running Labelable as an HA add-on, you can configure printers to use the HA integration as a transport layer:

```yaml
printers:
  - name: warehouse-printer
    type: zpl
    connection:
      type: ha
      device_id: warehouse_zebra  # HA device ID from the integration
```

This allows Labelable to print via the HA integration's `zebra_printer.print_raw` service.

## Roadmap

Planned features for future releases:

- **Print History**: Track recently printed labels with reprint functionality

## About

This project was developed primarily using [Claude Code](https://claude.ai/code), Anthropic's AI coding assistant. Human oversight and direction provided by [@ozonejunkieau](https://github.com/ozonejunkieau).

## License

MIT
