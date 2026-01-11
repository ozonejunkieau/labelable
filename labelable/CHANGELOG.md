# Changelog

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
