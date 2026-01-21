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


class AppConfig(BaseModel):
    """Application configuration loaded from config.yaml."""

    queue_timeout_seconds: int = 300
    templates_dir: Path = Path("./templates")
    printers: list[PrinterConfig] = Field(default_factory=list)
    # User mapping: HA user ID -> display name
    user_mapping: dict[str, str] = Field(default_factory=dict)
    default_user: str = ""
    # API key for external access (optional, if not set API is open)
    api_key: str | None = None


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


def load_templates(templates_dir: Path) -> dict[str, TemplateConfig]:
    """Load all template configurations from the templates directory."""
    templates: dict[str, TemplateConfig] = {}

    if not templates_dir.exists():
        return templates

    for template_file in templates_dir.glob("*.yaml"):
        # Skip example/reference templates (files starting with underscore)
        if template_file.name.startswith("_"):
            continue

        try:
            with open(template_file) as f:
                data = yaml.safe_load(f)
            if data:
                template = TemplateConfig.model_validate(data)
                templates[template.name] = template
        except Exception as e:
            # Log but don't fail on individual template errors
            print(f"Warning: Failed to load template {template_file}: {e}")

    return templates


async def discover_ha_printers() -> list[PrinterConfig]:
    """Discover printers from HA zebra_printer integration.

    This function queries the Home Assistant API to find any printers
    managed by the zebra_printer integration and returns them as
    PrinterConfig objects with HA connections.
    """
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        logger.debug("SUPERVISOR_TOKEN not set, skipping HA printer discovery")
        return []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://supervisor/core/api/states",
                headers={"Authorization": f"Bearer {supervisor_token}"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to query HA states: {resp.status}")
                    return []
                states = await resp.json()

        printers = []
        seen_devices = set()

        for state in states:
            entity_id = state.get("entity_id", "")

            # Look for online binary sensors from zebra_printer integration
            if not entity_id.startswith("binary_sensor."):
                continue
            if "_online" not in entity_id:
                continue

            attrs = state.get("attributes", {})
            # Verify it's from our integration by checking device class or attributes
            device_class = attrs.get("device_class")
            if device_class != "connectivity":
                continue

            # Extract device ID from entity ID
            # Format: binary_sensor.{device_name}_online
            device_name = entity_id.split(".")[1].replace("_online", "")
            if device_name in seen_devices:
                continue
            seen_devices.add(device_name)

            # Determine printer type from model info if available
            model = (attrs.get("model") or "").lower()
            if "epl" in model:
                printer_type = PrinterType.EPL2
            else:
                printer_type = PrinterType.ZPL

            printers.append(
                PrinterConfig(
                    name=f"ha-{device_name}",
                    type=printer_type,
                    connection=HAConnection(device_id=device_name),
                )
            )

        if printers:
            logger.info(f"Discovered {len(printers)} printer(s) from HA integration")

        return printers

    except Exception as e:
        logger.warning(f"Error during HA printer discovery: {e}")
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
