"""Configuration management for Labelable."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from labelable.models.printer import PrinterConfig
from labelable.models.template import TemplateConfig


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


# Global settings instance
settings = Settings()
