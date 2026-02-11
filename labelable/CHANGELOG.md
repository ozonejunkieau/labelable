# Changelog

## 0.2.1-dev3

- Improve preview page UX
  - Clean, focused layout with centered image
  - Back and Print buttons side by side
  - Hide form fields, show only Print button
  - Only show quantity when > 1
- Fix footer dark mode styling (proper background and text colors)

## 0.2.1-dev2

- Fix FastUI form serialization error on preview page
  - Remove invalid json_schema_extra that broke FastUI's JSON schema parsing

## 0.2.1-dev1

- Add image preview for labels using the image template engine
  - Preview page shows rendered PNG before printing
  - "Back to Form" and "Print" buttons on preview page
  - Auto-populated fields (datetime, user) handled correctly

## 0.2.0

### Image Template Engine
- Add visual element-based label rendering with PIL
- Support for rectangle and circular label shapes
- Text elements with word wrapping, auto-scaling, alignment, and circle-aware layout
- `line_spacing` property for controlling gap between wrapped text lines
- QR code generation via `qrcode` library
- DataMatrix barcode generation via `pylibdmtx`
- Label offset and darkness settings for printer calibration

### Google Fonts Support
- Auto-download fonts from Google Fonts when `download_google_fonts: true` in config
- Font manifest for fast lookup of downloaded fonts
- UI warning when templates are skipped due to missing fonts

### CLI Tools
- `labelable-render` command for previewing templates as PNG/ZPL/EPL2

### Zebra Printer HA Integration
- DPI sensor for printer resolution
- Calibrate Media and Feed Label button entities
- HACS discovery support via hacs.json
- Improved EPL2 protocol support and sensor availability

### Fixes
- Fix HA-connected printers showing offline
- Fix fonttools dependency for Alpine base images
- Fix reload_templates crash after refactoring

## 0.1.1-dev20

- Fix reload_templates crash (AttributeError on template.dimensions)

## 0.1.1-dev19

- Add UI warning when templates are skipped due to missing fonts
- Fix httpx dependency (move to main dependencies for Google Fonts download)

## 0.1.1-dev18

- Add line_spacing property for text elements (multiplier for line height in wrapped text)

## 0.1.1-dev17

- Fix fonttools dependency at runtime (configure index-strategy in pyproject.toml)
- Add CI job to build HA add-on image (catches dependency issues earlier)

## 0.1.1-dev16

- Fix fonttools dependency for HA Alpine base image (use PyPI fallback)
- Add hacs.json for HACS integration discovery

## 0.1.1-dev15

- Add image template engine for visual label rendering:
  - Text elements with word wrapping, auto-scaling, and alignment
  - QR code generation via `qrcode` library
  - DataMatrix barcode generation via `pylibdmtx`
  - Circle and rectangle label shapes
  - Circle-aware text wrapping for round labels
- Add Google Fonts auto-downloading for custom fonts
- Add `labelable-render` CLI for previewing templates as PNG
- Add label offset and darkness settings for printer calibration
- Add calibration templates for circular labels
- Add basedpyright type checking

## 0.1.1-dev14

- Add DPI (resolution) sensor for Zebra printers:
  - ZPL: Extracts from model string (e.g., "ZD420-300dpi") or queries via `! U1 getvar "head.resolution.in_dpi"`
  - EPL2: Always 203 DPI (hardware limitation)
- Add button entities for printer actions:
  - "Calibrate Media" - Triggers media sensor calibration
  - "Feed Label" - Feeds one label through the printer

## 0.1.1-dev13

- Fix HA-connected printers showing offline and not printing
  - Add `_is_online_ha()` method to query HA API for printer ready state
  - Falls back to checking language sensor exists if ready sensor not found
  - Fetches model info from HA sensor

## 0.1.1-dev12

- Improve language detection with proper if/elif/else and warning for unknown values
- Update auto-discovery documentation with detailed explanation

## 0.1.1-dev11

- Fix addon permissions for HA API access (enables auto-discovery)

## 0.1.1-dev10

- Add auto-discovery of zebra_printer HA integration devices:
  - Queries HA device registry for zebra_printer devices
  - Automatically configures printers when no printers section in config
  - Discovers all devices regardless of online status
  - Improved logging for debugging discovery issues
- Switch HA component versions to semver format (0.1.1-dev10 instead of 0.1.1.dev10)
- Pre-commit hook now validates both PEP 440 and semver version formats

## 0.1.1.dev9

- Fix sensor availability handling:
  - Static sensors (model, firmware, language) stay available always
  - Polled sensors (speed, darkness, dimensions, status) become unavailable when unreachable
  - Binary sensors (ready, head_open, etc.) become unavailable when unreachable
- Clean up dead code in EPL2 protocol parser

## 0.1.1.dev8

- Fix EPL2 response timing:
  - Add 0.5s delay after sending command before reading response
  - Read response in chunks to capture full multi-line UQ output
  - Fixes EPL2 sensors showing as Unknown or Unavailable

## 0.1.1.dev7

- Fix EPL2 sensor availability:
  - Enable print_speed, darkness, label_length, print_width sensors for EPL2
  - Enable ribbon_out binary sensor for EPL2 (uses rY/rN from I line)
  - Mark head_open, paper_out, paused as ZPL-only (EPL2 UQ doesn't provide these)

## 0.1.1.dev6

- Zebra Printer HA integration improvements:
  - Replace 'online' sensor with 'ready' sensor (shows error state, unavailable when unreachable)
  - Add 'language' sensor to report ZPL or EPL2 protocol type
  - Fix EPL2 status parsing for multi-line UQ response format
  - Add thermal transfer capability config option (auto-detect from model or manual checkbox)
  - Print method select only shown for printers configured with thermal transfer capability
- Unify version numbering across addon and custom component
- Pre-commit hook now validates manifest.json version consistency

## 0.1.1.dev5

- Add `last_checked` timestamp to printers API and UI
- Add `md5` Jinja filter for template hashing (e.g., `{{ name | md5 }}`)
- Add fixed quantity support in templates (`quantity: N` in YAML skips quantity input)
- Improve footer contrast with blue links instead of gray
- Enhanced reload templates page shows `supported_printers` for each template
- Add HA REST sensor documentation for monitoring printer status
- Add version consistency check to pre-commit hook

## 0.1.1.dev4

- Fix HA user detection to use correct headers (X-Remote-User-Id/Name/Display-Name)
- User field now falls back to HA display name if not in user_mapping
- Add pre-commit hook for lint and test enforcement

## 0.1.1.dev3

- Fix quantity doubling bug with smart detection in printer subclasses
  - ZPLPrinter detects ^PQ command and skips looping when present
  - EPL2Printer detects P command with quantity > 1 and skips looping when present
  - BasePrinter provides default loop behavior for templates without native quantity commands
- Add unit tests for printer quantity handling
- Add HA user ID debug display on home page (controlled by LABELABLE_SHOW_USER_DEBUG env var)

## 0.1.1.dev2

- Fix footer link contrast by overriding FastUI's .text-muted class

## 0.1.1.dev1

- Fix template validation to pass through built-in variables like `quantity`
- Add "Reload Templates" button to home page for reloading templates from disk
- Improve footer styling with better contrast and version link to GitHub

## 0.1.0

- Initial release
- FastAPI-based label printing API with FastUI web interface
- Support for Zebra ZPL and EPL2 thermal printers via TCP or serial connections
- Jinja2 template engine for dynamic label content
- YAML-based template and printer configuration
- In-memory print queue with offline queuing when printers unavailable
- Home Assistant add-on with ingress support for seamless sidebar integration
- User field auto-population from Home Assistant user context
- Dark mode support matching Home Assistant theme
- API key authentication for external API access
