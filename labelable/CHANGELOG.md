# Changelog

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
