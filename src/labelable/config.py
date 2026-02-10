"""Configuration management for Labelable."""

import logging
import os
from pathlib import Path

import aiohttp
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from labelable.models.printer import HAConnection, PrinterConfig, PrinterType
from labelable.models.template import TemplateConfig

logger = logging.getLogger(__name__)


class TemplateLoadResult(BaseModel):
    """Result of loading templates, including any warnings."""

    templates: dict[str, TemplateConfig] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    """Application configuration loaded from config.yaml."""

    queue_timeout_seconds: int = 300
    templates_dir: Path = Path("./templates")
    fonts_dir: Path = Path("./fonts")
    printers: list[PrinterConfig] = Field(default_factory=list)
    # User mapping: HA user ID -> display name
    user_mapping: dict[str, str] = Field(default_factory=dict)
    default_user: str = ""
    # API key for external access (optional, if not set API is open)
    api_key: str | None = None
    # Enable automatic Google Fonts downloading
    download_google_fonts: bool = False


class Settings(BaseSettings):
    """Environment-based settings."""

    model_config = SettingsConfigDict(
        env_prefix="LABELABLE_",
        env_file=".env",
        extra="ignore",
    )

    config_file: Path = Path("config.yaml")
    host: str = "0.0.0.0"
    port: int = 7979
    debug: bool = False
    show_user_debug: bool = True  # Show HA user ID on home page for debugging user_mapping


def load_config(config_path: Path) -> AppConfig:
    """Load application configuration from YAML file."""
    if not config_path.exists():
        return AppConfig()

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    # Handle None values for list/dict fields (YAML returns None for empty keys)
    if data.get("printers") is None:
        data["printers"] = []
    if data.get("user_mapping") is None:
        data["user_mapping"] = {}

    return AppConfig.model_validate(data)


def _extract_fonts_from_template(template: TemplateConfig) -> set[str]:
    """Extract all font names used by a template's elements."""
    from labelable.models.template import TextElement

    fonts: set[str] = set()
    for element in template.elements:
        if isinstance(element, TextElement) and element.font:
            fonts.add(element.font)
    return fonts


def _validate_template_fonts(
    template: TemplateConfig,
    fonts_dir: Path,
) -> list[str]:
    """Validate that all fonts required by a template are available.

    Args:
        template: Template to validate.
        fonts_dir: Directory containing downloaded fonts.

    Returns:
        List of missing font names.
    """
    from labelable.models.template import TextElement
    from labelable.templates.fonts import FontManager

    missing: list[str] = []
    font_manager = FontManager(custom_paths=[fonts_dir] if fonts_dir.exists() else None)

    for element in template.elements:
        if not isinstance(element, TextElement) or not element.font:
            continue

        font_name = element.font
        # Check if font can be found
        font_path = font_manager._find_font(font_name)
        if font_path is None:
            missing.append(font_name)

    return missing


def load_templates(
    templates_dir: Path,
    fonts_dir: Path | None = None,
    download_google_fonts: bool = False,
) -> TemplateLoadResult:
    """Load all template configurations from the templates directory.

    Args:
        templates_dir: Directory containing template YAML files.
        fonts_dir: Directory for storing/loading fonts.
        download_google_fonts: If True, attempt to download missing fonts from Google Fonts.

    Returns:
        TemplateLoadResult with templates dict and any warnings.
    """
    result = TemplateLoadResult()
    pending_templates: list[tuple[Path, TemplateConfig]] = []

    if not templates_dir.exists():
        return result

    # First pass: load all templates and collect font requirements
    all_fonts: set[str] = set()
    for template_file in templates_dir.glob("*.yaml"):
        # Skip example/reference templates (files starting with underscore)
        if template_file.name.startswith("_"):
            continue

        try:
            with open(template_file) as f:
                data = yaml.safe_load(f)
            if data:
                template = TemplateConfig.model_validate(data)
                pending_templates.append((template_file, template))
                all_fonts.update(_extract_fonts_from_template(template))
        except Exception as e:
            # Log but don't fail on individual template errors
            logger.warning(f"Failed to load template {template_file}: {e}")

    # Download missing Google Fonts if enabled
    if download_google_fonts and fonts_dir and all_fonts:
        fonts_dir.mkdir(parents=True, exist_ok=True)
        _download_missing_fonts(all_fonts, fonts_dir)

    # Second pass: validate fonts and add templates
    for _template_file, template in pending_templates:
        # Only validate fonts for image engine templates
        from labelable.models.template import EngineType

        if template.engine == EngineType.IMAGE and fonts_dir:
            missing_fonts = _validate_template_fonts(template, fonts_dir)
            if missing_fonts:
                fonts_str = ", ".join(missing_fonts)
                logger.error(f"Template '{template.name}' requires missing fonts: {fonts_str}. Skipping template.")
                # Add warning for UI if download_google_fonts is not enabled
                if not download_google_fonts:
                    result.warnings.append(
                        f"Template '{template.name}' skipped: missing fonts ({fonts_str}). "
                        f"Enable 'download_google_fonts: true' in config.yaml to auto-download Google Fonts."
                    )
                else:
                    result.warnings.append(
                        f"Template '{template.name}' skipped: fonts not available ({fonts_str}). "
                        f"These may not be valid Google Fonts."
                    )
                continue

        result.templates[template.name] = template

    return result


def _download_missing_fonts(fonts: set[str], fonts_dir: Path) -> None:
    """Download missing fonts from Google Fonts.

    Args:
        fonts: Set of font names to check/download.
        fonts_dir: Directory to store downloaded fonts.
    """
    from labelable.templates.fonts import FontManager
    from labelable.templates.google_fonts import (
        ensure_google_fonts,
        get_font_family_from_name,
    )

    font_manager = FontManager(custom_paths=[fonts_dir])

    # Collect Google Font families to download
    families_to_download: set[str] = set()
    for font_name in fonts:
        # Check if font is already available
        if font_manager._find_font(font_name) is not None:
            continue

        # Try to determine Google Font family name
        family = get_font_family_from_name(font_name)
        if family:
            families_to_download.add(family)

    if not families_to_download:
        return

    # Download missing fonts
    try:
        downloaded = ensure_google_fonts(list(families_to_download), fonts_dir)
        if downloaded:
            logger.info(f"Downloaded Google Font families: {', '.join(downloaded)}")
            # Clear font manager cache so it finds new fonts
            font_manager.clear_cache()
    except Exception as e:
        logger.error(f"Failed to download Google Fonts: {e}")


async def discover_ha_printers() -> list[PrinterConfig]:
    """Discover printers from HA zebra_printer integration.

    This function queries the Home Assistant API to find any printers
    managed by the zebra_printer integration and returns them as
    PrinterConfig objects with HA connections.

    Detection strategy:
    Look for sensor.*_language entities which are unique to zebra_printer.
    The entity name gives us the device name, and the state gives us the protocol.
    """
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        logger.info("SUPERVISOR_TOKEN not set, skipping HA printer discovery")
        return []

    headers = {"Authorization": f"Bearer {supervisor_token}"}
    base_url = "http://supervisor/core/api"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # Get entity states to find zebra_printer sensors
            logger.debug(f"Querying HA states: {base_url}/states")
            async with session.get(f"{base_url}/states") as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"Failed to query HA states: {resp.status} - {text}")
                    return []
                states = await resp.json()
                logger.debug(f"States API returned {len(states)} entities")

        printers = []
        seen_devices = set()

        for state in states:
            entity_id = state.get("entity_id", "")

            # Look for language sensors from zebra_printer integration
            # Format: sensor.{device_name}_language
            if not entity_id.startswith("sensor.") or not entity_id.endswith("_language"):
                continue

            # Extract device name from entity ID
            # sensor.my_printer_language -> my_printer
            device_name = entity_id[7:-9]  # Remove "sensor." prefix and "_language" suffix
            if device_name in seen_devices:
                continue
            seen_devices.add(device_name)

            # Get printer type from language sensor value
            language = (state.get("state") or "").upper()
            if language == "EPL2":
                printer_type = PrinterType.EPL2
            elif language == "ZPL":
                printer_type = PrinterType.ZPL
            else:
                logger.warning(f"Unknown printer language '{language}' for {device_name}, defaulting to ZPL")
                printer_type = PrinterType.ZPL

            # Use the device name as the device_id for service calls
            # The HA service will need to look up the actual device from this
            printers.append(
                PrinterConfig(
                    name=f"ha-{device_name}",
                    type=printer_type,
                    connection=HAConnection(device_id=device_name),
                )
            )
            logger.info(f"Discovered HA printer: {device_name} (type={printer_type})")

        if not printers:
            logger.info("No zebra_printer devices found in HA states")
        else:
            logger.info(f"Discovered {len(printers)} printer(s) from HA integration")

        return printers

    except Exception as e:
        logger.warning(f"Error during HA printer discovery: {e}", exc_info=True)
        return []


async def load_config_async(config_path: Path) -> AppConfig:
    """Load application configuration with HA auto-discovery.

    This is an async version of load_config that also attempts to
    discover printers from the Home Assistant zebra_printer integration
    if no printers are configured.
    """
    config = load_config(config_path)

    # If no printers configured, try HA auto-discovery
    if not config.printers:
        config.printers = await discover_ha_printers()

    return config


# Global settings instance
settings = Settings()
