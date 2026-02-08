# Labelable - Developer Guide

## Project Overview

Labelable is a label printing API and web UI designed for home use, primarily as a Home Assistant add-on. It supports Zebra ZPL and EPL2 thermal printers via TCP or serial connections.

## Development Setup

This project uses [UV](https://github.com/astral-sh/uv) for dependency management and [just](https://github.com/casey/just) as a command runner.

```bash
# Install dependencies
just install     # or: uv sync --all-extras

# Run development server
just run         # starts on http://localhost:7979

# Run tests
just test        # or: uv run pytest

# Format and lint
just fmt
just lint

# Install pre-commit hook (runs lint + tests before each commit)
ln -sf ../../scripts/pre-commit .git/hooks/pre-commit
```

## Architecture

```
src/labelable/
├── app.py              # FastAPI application factory
├── config.py           # Pydantic settings, loads config.yaml, HA auto-discovery
├── queue.py            # In-memory print queue with expiry
├── __main__.py         # Entry point (uv run labelable)
├── models/             # Pydantic data models
│   ├── printer.py      # Printer configuration (TCP, serial, HA connections)
│   ├── template.py     # Label template definitions
│   └── job.py          # Print job representation
├── printers/           # Printer implementations
│   ├── base.py         # Abstract Printer interface with cached status
│   ├── zpl.py          # Zebra ZPL (TCP/serial/HA)
│   ├── epl2.py         # Zebra EPL2 (TCP/serial/HA)
│   └── ptouch.py       # Brother P-Touch (stubbed)
├── templates/          # Template engines
│   ├── engine.py       # Abstract TemplateEngine
│   ├── jinja_engine.py # Jinja2 implementation (raw ZPL/EPL2)
│   ├── image_engine.py # PIL-based image rendering
│   ├── elements/       # Element renderers for image engine
│   │   ├── base.py     # BaseElementRenderer ABC
│   │   ├── text.py     # Text with wrap/scale/circle-aware
│   │   ├── qrcode.py   # QR code via qrcode library
│   │   └── datamatrix.py # DataMatrix via pylibdmtx
│   ├── converters/     # Bitmap format converters
│   │   ├── zpl.py      # Image → ZPL ^GFA command
│   │   └── epl2.py     # Image → EPL2 GW command
│   ├── fonts/          # Font management
│   │   └── __init__.py # FontManager class
│   ├── font_manifest.py # Font metadata extraction
│   └── google_fonts.py # Google Fonts downloader
├── cli/                # CLI tools
│   └── render.py       # labelable-render preview tool
└── api/
    ├── routes.py       # REST API endpoints
    └── ui.py           # FastUI web interface

custom_components/zebra_printer/   # HA Custom Integration
├── __init__.py         # Integration setup, platform loading
├── manifest.json       # HACS metadata, DHCP discovery config
├── config_flow.py      # Manual IP + DHCP discovery flows
├── const.py            # Constants (OUI prefixes, commands)
├── coordinator.py      # DataUpdateCoordinator for polling
├── entity.py           # Base entity with device info
├── binary_sensor.py    # Online, head_open, paper_out, etc.
├── sensor.py           # Model, firmware, labels_printed, etc.
├── services.py         # print_raw, calibrate, feed handlers
├── services.yaml       # Service definitions for UI
├── strings.json        # Config flow UI strings
├── translations/       # Localization
└── protocol/           # Printer protocol implementations
    ├── base.py         # Abstract protocol + PrinterStatus dataclass
    ├── zpl.py          # ZPL parsing (~HS, ~HI, ~HQOD)
    └── epl2.py         # EPL2 parsing (UQ)
```

## Configuration

### Main config (config.yaml)

```yaml
queue_timeout_seconds: 300
default_user: ""
user_mapping:
  "ha-user-uuid-here": "Display Name"

printers:
  - name: zpl-printer
    type: zpl
    connection:
      type: tcp
      host: 192.168.1.100
      port: 9100
    healthcheck:
      interval: 60
      command: "~HS"

  - name: epl2-printer
    type: epl2
    connection:
      type: tcp
      host: 192.168.1.101
      port: 1883
    healthcheck:
      interval: 60
      command: "UQ"

  # HA Integration connection (uses zebra_printer integration as transport)
  - name: ha-printer
    type: zpl
    connection:
      type: ha
      device_id: warehouse_zebra  # Device ID from HA integration
      # ha_url: http://supervisor/core  # Default, override if needed
      # ha_token: null  # Uses SUPERVISOR_TOKEN when running as add-on

templates_dir: ./templates
```

### HA Connection Auto-Discovery

When running as a Home Assistant add-on with no printers configured (or missing
printers section), Labelable automatically discovers printers from the `zebra_printer`
HA custom integration.

```yaml
# Empty or missing printers list triggers auto-discovery
printers: []
```

**How it works:**
1. Queries the HA REST API (`/api/states`) for all entity states
2. Finds `sensor.*_language` entities (unique to zebra_printer integration)
3. Extracts device name from entity ID (e.g., `sensor.warehouse_printer_language` → `warehouse_printer`)
4. Determines printer type from the language sensor value (ZPL, EPL2, or defaults to ZPL with warning)
5. Creates `HAConnection` configurations using the device name as `device_id`

**Requirements:**
- The `zebra_printer` custom integration must be installed and configured in HA
- The Labelable add-on must have `homeassistant_api: true` permission (already set in config.yaml)
- The `SUPERVISOR_TOKEN` environment variable is automatically provided when running as an add-on

**Assumptions:**
- Device names are derived from entity IDs, not the friendly name
- Service calls use name-based lookup (supports both device UUID and entity name patterns)
- Printers are discovered regardless of online/offline status
- Unknown language values default to ZPL with a warning logged

**Discovered printer naming:**
- Printers are named with `ha-` prefix: `ha-{device_name}` (e.g., `ha-warehouse_printer`)

### Template Files (templates/*.yaml)

Templates are YAML files supporting two engines:
- `jinja` (default): Raw ZPL/EPL2 commands with Jinja2 templating
- `image`: Visual element-based rendering with PIL (supports QR codes, DataMatrix, text wrapping)

**Supported field types:**
- `string` - Text input
- `integer` - Numeric input (integers only)
- `float` - Numeric input (decimals allowed)
- `boolean` - Checkbox (true/false)
- `select` - Radio buttons (predefined options list)
- `datetime` - Auto-populated with current timestamp (uses `format` for strftime)
- `user` - Auto-populated from Home Assistant user (via `X-Remote-User-Display-Name` header)

**Jinja Engine Example:**
```yaml
name: leftovers
description: Food container label
engine: jinja  # Optional, this is the default
dimensions:
  width_mm: 40
  height_mm: 28
supported_printers:
  - zpl
fields:
  - name: name
    type: string
    required: true
  - name: gluten_free
    type: boolean
    default: false
template: |
  ^XA
  ^FO50,50^FD{{ name }}^FS
  {% if gluten_free %}^FD!!! GF !!!^FS{% endif %}
  ^XZ
```

**Image Engine Example:**
```yaml
name: jar-label
description: Circular label for mason jars
engine: image
shape: circle  # or rectangle (default)
dimensions:
  diameter_mm: 50  # For circles; use width_mm/height_mm for rectangles
dpi: 203  # Printer DPI (default 203)
label_offset_x_mm: 2.3  # Horizontal offset for label alignment
label_offset_y_mm: 0.0  # Vertical offset
darkness: 15  # Print darkness 0-30 (ZPL ~SD command)
supported_printers:
  - zpl
fields:
  - name: title
    type: string
    required: true
  - name: code
    type: string
    required: true
elements:
  - type: text
    field: title
    bounds:
      x_mm: 5
      y_mm: 5
      width_mm: 40
      height_mm: 12
    font: DejaVuSans-Bold
    font_size: 24
    alignment: center
    vertical_align: middle
    auto_scale: true
    circle_aware: true  # Adjusts wrapping for circular labels

  - type: qrcode
    field: code
    x_mm: 25  # Center position
    y_mm: 35
    size_mm: 16
    error_correction: M  # L, M, Q, or H

  - type: datamatrix
    field: code
    x_mm: 25
    y_mm: 35
    size_mm: 12
```

## Key Design Patterns

### Async Architecture
- All printer operations are async using `asyncio`
- Each printer runs its own background task for queue processing
- TCP connections use `asyncio.open_connection`
- Serial connections use `pyserial` with executor

### Cached Printer Status
- Printers cache their online status (30s TTL)
- Background worker refreshes status every `healthcheck.interval` seconds
- Model info is parsed from healthcheck responses (~HI for ZPL, UQ for EPL2)
- Avoids blocking page renders while checking printer status

### Print Queue
- In-memory `asyncio.Queue` per printer
- Jobs expire after configurable timeout (default 5 min)
- Queue is lost on restart (intentional - no persistence)

### Home Assistant Integration
- HA Ingress provides user headers:
  - `X-Remote-User-Id` - User's UUID
  - `X-Remote-User-Name` - Username
  - `X-Remote-User-Display-Name` - Display name
- User ID can be mapped via `user_mapping` in config
- Falls back to display name, then username, then `default_user`

### HA Printer Status Sensors
To monitor printer status in Home Assistant dashboards or automations, use REST sensors.
Add to your HA `configuration.yaml`:

```yaml
rest:
  - resource: http://localhost:7979/api/v1/printers
    scan_interval: 60
    sensor:
      - name: "Label Printer Status"
        value_template: "{{ value_json[0].online }}"
        json_attributes_path: "$[0]"
        json_attributes:
          - name
          - type
          - queue_size
          - last_checked

# Or for a specific printer:
sensor:
  - platform: rest
    name: "Warehouse Printer"
    resource: http://localhost:7979/api/v1/printers/warehouse-zpl
    value_template: "{{ 'Online' if value_json.online else 'Offline' }}"
    json_attributes:
      - queue_size
      - last_checked
    scan_interval: 60
```

Note: Replace `localhost:7979` with the add-on's internal hostname if accessing from HA Core.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/printers` | List printers with status |
| GET | `/api/v1/templates` | List available templates |
| GET | `/api/v1/templates/{name}` | Get template details |
| POST | `/api/v1/print/{template}` | Submit print job |

## Printer Commands Reference

### ZPL (Zebra Programming Language)
- `~HS` - Host status query (healthcheck)
- `~HI` - Host identification (model info)
- `^XA` / `^XZ` - Label start/end
- `^FO` - Field origin (position)
- `^FD` / `^FS` - Field data start/end
- `^CF` - Change font
- `^A0` - Scalable font

### EPL2 (Eltron Programming Language)
- `UQ` - Status query (healthcheck, returns model info)
- `N` - Clear image buffer
- `A` - ASCII text
- `B` - Barcode
- `P` - Print label

## CLI Tools

### labelable-render

Preview image templates without a printer:

```bash
# Basic usage
uv run labelable-render templates/my-template.yaml -o preview.png

# With field values
uv run labelable-render templates/jar-label.yaml \
  -d title="Honey" \
  -d code="HONEY-001" \
  -o preview.png

# With JSON data file
uv run labelable-render templates/jar-label.yaml \
  --json data.json \
  -o preview.png

# Output as ZPL/EPL2 instead of PNG
uv run labelable-render templates/jar-label.yaml \
  --format zpl \
  -d title="Test" \
  -o output.zpl

# Download missing Google Fonts automatically
uv run labelable-render templates/my-template.yaml \
  --download-fonts \
  -o preview.png
```

## Font Management

The image engine searches for fonts in this order:
1. Font manifest (maps font names to files based on TTF metadata)
2. Custom paths (from template `font_paths` or app config)
3. System fonts
4. PIL default font (fallback)

### Google Fonts

Enable automatic downloading in config:
```yaml
download_google_fonts: true
fonts_dir: ./fonts  # Where to save downloaded fonts
```

Or use the CLI flag `--download-fonts`.

Fonts are downloaded on-demand when a template references a font not found locally. A manifest file (`fonts/.font-manifest.json`) caches font metadata for fast lookup.

## Type Checking

This project uses [basedpyright](https://github.com/DetachHead/basedpyright) for static type checking:

```bash
uv run basedpyright src/
```

Configuration is in `pyproject.toml` under `[tool.basedpyright]`.

## Adding a New Printer Type

1. Create `src/labelable/printers/newtype.py`
2. Inherit from `BasePrinter`
3. Implement all abstract methods (connect, disconnect, is_online, print_raw)
4. Add to `PrinterType` enum in `models/printer.py`
5. Register in printer factory in `printers/__init__.py`

## Adding a New Template Engine

1. Create `src/labelable/templates/newengine.py`
2. Inherit from `BaseTemplateEngine`
3. Implement `render(template, context) -> bytes`
4. Register in engine factory

## Release Process

Before creating a tagged release, complete these steps:

### 0. Verify Lint and Tests Pass
**CRITICAL**: Always run lint and tests before committing:
```bash
just lint        # or: uv run ruff check src tests
just test        # or: uv run pytest
```
Do NOT commit if either fails. Fix issues first.

### 1. Update Version Numbers
Update version in both files (they must match):
- `pyproject.toml` - `version = "X.Y.Z"`
- `labelable/config.yaml` - `version: "X.Y.Z"`

### 2. Update Changelog
Edit `labelable/CHANGELOG.md` (Home Assistant add-on changelog):
- Add new version heading: `## X.Y.Z`
- List changes as bullet points with `-` prefix
- Place new version at the top (reverse chronological order)
- Keep entries concise and user-focused

Example:
```markdown
## 0.2.0

- Add support for Brother P-Touch printers
- Fix template validation error messages
- Improve printer status caching

## 0.1.0

- Initial release
...
```

### 3. Commit and Tag
```bash
git add -A
git commit -m "Release vX.Y.Z"
git tag vX.Y.Z
git push && git push --tags
```

### 4. Verify Build
GitHub Actions will automatically build container images. Verify the build succeeds:
```bash
gh run list --limit 1
```

## Home Assistant Ingress

The UI must work both directly and via HA Ingress proxy. Key considerations:

### URL Handling
- HA Ingress sets `X-Ingress-Path` header with the proxy prefix
- `IngressPathMiddleware` in `app.py` sets ASGI `root_path` from this header
- FastUI needs two meta tags for correct URL handling:
  - `fastui:APIRootUrl` - Base URL for API calls (e.g., `/api/hassio_ingress/<token>/api`)
  - `fastui:APIPathStrip` - Prefix to strip from browser path before appending to APIRootUrl
- Form `submit_url` must include full path since FastUI's `useRequest()` doesn't transform URLs

### Testing Ingress Changes
1. Update version, commit, tag, and push
2. Wait for GitHub Actions build to complete
3. Update add-on in Home Assistant
4. Test both navigation and form submission via HA sidebar
