"""Shared template CRUD logic for REST API and MCP server."""

import os
import re
from pathlib import Path

import yaml

from labelable.models.template import TemplateConfig

# Template name must start with alphanumeric, then alphanumeric, hyphens, or underscores
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class TemplateCRUDError(Exception):
    """Error during template CRUD operation, with HTTP-style status code."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def validate_template_name(name: str) -> None:
    """Validate a template name is safe for use as a filename.

    Raises TemplateCRUDError if the name is invalid.
    """
    if not name:
        raise TemplateCRUDError("Template name cannot be empty", 400)
    if not _NAME_PATTERN.match(name):
        raise TemplateCRUDError(
            f"Invalid template name '{name}': must start with alphanumeric and contain only "
            "alphanumeric characters, hyphens, and underscores",
            400,
        )


def resolve_template_path(name: str, templates_path: Path) -> Path:
    """Resolve a template name to a file path, guarding against path traversal.

    Returns the resolved path. Raises TemplateCRUDError if the path escapes templates_path.
    """
    validate_template_name(name)
    target = (templates_path / f"{name}.yaml").resolve()
    templates_resolved = templates_path.resolve()
    if not str(target).startswith(str(templates_resolved) + "/") and target.parent != templates_resolved:
        raise TemplateCRUDError("Path traversal detected", 400)
    return target


def template_to_yaml(template: TemplateConfig) -> str:
    """Serialize a TemplateConfig to YAML string."""
    data = template.model_dump(exclude_none=True, mode="json")
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


# Owner read/write only — no execute, no group/other write
_FILE_MODE = 0o644


def _write_file(path: Path, content: str) -> None:
    """Write content to a file with restrictive permissions (0o644).

    Uses os.open with explicit mode for new files, and os.fchmod to enforce
    permissions on existing files (O_CREAT mode is ignored when file exists).
    """
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, _FILE_MODE)
    try:
        os.fchmod(fd, _FILE_MODE)
        os.write(fd, content.encode())
    finally:
        os.close(fd)


def _check_fonts(template: TemplateConfig, fonts_dir: Path | None, download_google_fonts: bool) -> None:
    from labelable.config import missing_template_fonts

    missing = missing_template_fonts(template, fonts_dir, download_google_fonts)
    if missing:
        hint = "" if download_google_fonts else " Enable 'download_google_fonts: true' to fetch Google Fonts."
        raise TemplateCRUDError(f"Template '{template.name}' needs missing fonts: {', '.join(missing)}.{hint}", 400)


def create_template_on_disk(
    template: TemplateConfig,
    templates_path: Path,
    templates_dict: dict[str, TemplateConfig],
    fonts_dir: Path | None = None,
    download_google_fonts: bool = False,
) -> None:
    """Write a new template to disk and add it to the in-memory dict.

    Raises TemplateCRUDError(409) if a template with that name already exists.
    """
    if template.name in templates_dict:
        raise TemplateCRUDError(f"Template '{template.name}' already exists", 409)

    target = resolve_template_path(template.name, templates_path)
    if target.exists():
        # The file is there but the template isn't loaded: it was skipped at
        # startup (bad YAML, missing fonts). Overwriting it would silently
        # destroy the user's file.
        raise TemplateCRUDError(
            f"A file for template '{template.name}' already exists but did not load; "
            "check the template warnings, fix or remove the file, then retry",
            409,
        )
    _check_fonts(template, fonts_dir, download_google_fonts)
    templates_path.mkdir(parents=True, exist_ok=True)
    _write_file(target, template_to_yaml(template))
    templates_dict[template.name] = template


def update_template_on_disk(
    name: str,
    template: TemplateConfig,
    templates_path: Path,
    templates_dict: dict[str, TemplateConfig],
    fonts_dir: Path | None = None,
    download_google_fonts: bool = False,
) -> None:
    """Overwrite an existing template on disk and update the in-memory dict.

    Raises TemplateCRUDError(404) if the template does not exist.
    """
    if name not in templates_dict:
        raise TemplateCRUDError(f"Template '{name}' not found", 404)
    if template.name != name:
        # Keyed by the URL name but saved with the body's name, the template
        # vanished from the UI and could overwrite another one on reload
        raise TemplateCRUDError(
            f"Template body name '{template.name}' does not match '{name}'; renaming is not supported",
            400,
        )
    _check_fonts(template, fonts_dir, download_google_fonts)

    target = _existing_template_file(name, templates_path) or resolve_template_path(name, templates_path)
    _write_file(target, template_to_yaml(template))
    templates_dict[name] = template


def _existing_template_file(name: str, templates_path: Path) -> Path | None:
    """The file a loaded template came from.

    Templates are keyed by the ``name`` inside the file, not the filename, so
    ``x.yaml`` may hold template ``y``. Writing ``y.yaml`` on update would
    leave two files defining the same template.
    """
    validate_template_name(name)
    conventional = templates_path / f"{name}.yaml"
    if conventional.exists():
        return conventional.resolve()
    for candidate in sorted(templates_path.glob("*.yaml")):
        try:
            data = yaml.safe_load(candidate.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("name") == name:
            return candidate.resolve()
    return None
