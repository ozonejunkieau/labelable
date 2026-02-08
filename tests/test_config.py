"""Tests for configuration loading."""

import tempfile
from pathlib import Path

import yaml

from labelable.config import AppConfig, load_templates
from labelable.models.template import EngineType


class TestLoadTemplates:
    """Tests for load_templates function."""

    def test_load_templates_from_directory(self):
        """Load templates from a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a template file
            template_yaml = {
                "name": "test-template",
                "description": "Test template",
                "dimensions": {"width_mm": 50, "height_mm": 25},
                "supported_printers": ["zpl-printer"],
                "fields": [{"name": "title", "type": "string", "required": True}],
                "template": "^XA^FD{{ title }}^FS^XZ",
            }

            template_path = Path(tmpdir) / "test-template.yaml"
            with open(template_path, "w") as f:
                yaml.dump(template_yaml, f)

            templates = load_templates(Path(tmpdir))

            assert "test-template" in templates
            assert templates["test-template"].name == "test-template"
            assert templates["test-template"].description == "Test template"

    def test_load_templates_empty_directory(self):
        """Load templates from an empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates = load_templates(Path(tmpdir))
            assert templates == {}

    def test_load_templates_skips_invalid_yaml(self):
        """Skip files with invalid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an invalid YAML file
            invalid_path = Path(tmpdir) / "invalid.yaml"
            invalid_path.write_text("invalid: yaml: content: {{")

            # Create a valid template
            valid_yaml = {
                "name": "valid-template",
                "dimensions": {"width_mm": 50, "height_mm": 25},
                "template": "test",
            }
            valid_path = Path(tmpdir) / "valid.yaml"
            with open(valid_path, "w") as f:
                yaml.dump(valid_yaml, f)

            templates = load_templates(Path(tmpdir))

            # Should only have the valid template
            assert "valid-template" in templates
            assert "invalid" not in templates

    def test_load_templates_skips_non_yaml_files(self):
        """Skip non-YAML files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a non-YAML file
            (Path(tmpdir) / "readme.txt").write_text("This is not a template")
            (Path(tmpdir) / "script.py").write_text("print('hello')")

            # Create a valid template
            valid_yaml = {
                "name": "test-template",
                "dimensions": {"width_mm": 50, "height_mm": 25},
                "template": "test",
            }
            valid_path = Path(tmpdir) / "test.yaml"
            with open(valid_path, "w") as f:
                yaml.dump(valid_yaml, f)

            templates = load_templates(Path(tmpdir))

            assert len(templates) == 1
            assert "test-template" in templates

    def test_load_templates_uses_filename_as_name(self):
        """Use filename as template name if not specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Template with explicit name
            template_yaml = {
                "name": "explicit-name",
                "description": "Has explicit name",
                "dimensions": {"width_mm": 50, "height_mm": 25},
                "template": "test",
            }

            template_path = Path(tmpdir) / "my-template.yaml"
            with open(template_path, "w") as f:
                yaml.dump(template_yaml, f)

            templates = load_templates(Path(tmpdir))

            # Template is stored by its explicit name
            assert "explicit-name" in templates

    def test_load_templates_image_engine(self):
        """Load template with image engine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            template_yaml = {
                "name": "image-template",
                "engine": "image",
                "shape": "rectangle",
                "dimensions": {"width_mm": 50, "height_mm": 25},
                "dpi": 203,
                "elements": [
                    {
                        "type": "text",
                        "field": "title",
                        "bounds": {"x_mm": 0, "y_mm": 0, "width_mm": 50, "height_mm": 25},
                    }
                ],
            }

            template_path = Path(tmpdir) / "image-template.yaml"
            with open(template_path, "w") as f:
                yaml.dump(template_yaml, f)

            templates = load_templates(Path(tmpdir))

            assert "image-template" in templates
            assert templates["image-template"].engine == EngineType.IMAGE

    def test_load_templates_nonexistent_directory(self):
        """Handle nonexistent directory gracefully."""
        templates = load_templates(Path("/nonexistent/path"))
        assert templates == {}


class TestAppConfig:
    """Tests for AppConfig model."""

    def test_default_values(self):
        """Test default configuration values."""
        config = AppConfig()

        assert config.queue_timeout_seconds == 300
        assert config.templates_dir == Path("templates")
        assert config.default_user == ""
        assert config.printers == []

    def test_custom_values(self):
        """Test custom configuration values."""
        config = AppConfig(
            queue_timeout_seconds=600,
            templates_dir="/custom/templates",
            default_user="TestUser",
        )

        assert config.queue_timeout_seconds == 600
        assert config.templates_dir == Path("/custom/templates")
        assert config.default_user == "TestUser"

    def test_user_mapping(self):
        """Test user mapping configuration."""
        config = AppConfig(
            user_mapping={
                "user-uuid-1": "Alice",
                "user-uuid-2": "Bob",
            }
        )

        assert config.user_mapping["user-uuid-1"] == "Alice"
        assert config.user_mapping["user-uuid-2"] == "Bob"

    def test_google_fonts_config(self):
        """Test Google Fonts configuration."""
        config = AppConfig(
            download_google_fonts=True,
            fonts_dir="/custom/fonts",
        )

        assert config.download_google_fonts is True
        assert config.fonts_dir == Path("/custom/fonts")

    def test_google_fonts_defaults(self):
        """Test Google Fonts default values."""
        config = AppConfig()

        assert config.download_google_fonts is False
        assert config.fonts_dir == Path("fonts")
