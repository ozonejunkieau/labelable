"""Tests for template CRUD logic."""

import os
import stat
from pathlib import Path

import pytest
import yaml

from labelable.api.template_crud import (
    TemplateCRUDError,
    create_template_on_disk,
    resolve_template_path,
    template_to_yaml,
    update_template_on_disk,
    validate_template_name,
)
from labelable.models.template import LabelDimensions, TemplateConfig


def _make_template(name: str = "test-label") -> TemplateConfig:
    return TemplateConfig(
        name=name,
        description="A test template",
        dimensions=LabelDimensions(width_mm=40, height_mm=28),
        supported_printers=["zpl"],
    )


class TestValidateTemplateName:
    def test_valid_names(self):
        for name in ["label", "my-label", "my_label", "Label1", "a", "a-b-c", "A123_test"]:
            validate_template_name(name)  # should not raise

    def test_empty_name(self):
        with pytest.raises(TemplateCRUDError, match="cannot be empty"):
            validate_template_name("")

    def test_starts_with_underscore(self):
        with pytest.raises(TemplateCRUDError, match="must start with alphanumeric"):
            validate_template_name("_hidden")

    def test_starts_with_hyphen(self):
        with pytest.raises(TemplateCRUDError, match="must start with alphanumeric"):
            validate_template_name("-bad")

    def test_contains_slash(self):
        with pytest.raises(TemplateCRUDError, match="must start with alphanumeric"):
            validate_template_name("path/traversal")

    def test_contains_dot(self):
        with pytest.raises(TemplateCRUDError, match="must start with alphanumeric"):
            validate_template_name("file.yaml")

    def test_dotdot(self):
        with pytest.raises(TemplateCRUDError, match="must start with alphanumeric"):
            validate_template_name("../etc/passwd")


class TestResolveTemplatePath:
    def test_resolves_inside_directory(self, tmp_path: Path):
        result = resolve_template_path("my-label", tmp_path)
        assert result == (tmp_path / "my-label.yaml").resolve()

    def test_rejects_invalid_name(self, tmp_path: Path):
        with pytest.raises(TemplateCRUDError):
            resolve_template_path("../evil", tmp_path)


class TestTemplateToYaml:
    def test_roundtrip(self):
        template = _make_template()
        yaml_str = template_to_yaml(template)
        data = yaml.safe_load(yaml_str)
        roundtripped = TemplateConfig.model_validate(data)
        assert roundtripped.name == template.name
        assert roundtripped.description == template.description
        assert roundtripped.dimensions.width_mm == template.dimensions.width_mm

    def test_excludes_none(self):
        template = _make_template()
        yaml_str = template_to_yaml(template)
        data = yaml.safe_load(yaml_str)
        assert "template" not in data  # Jinja template field is None


class TestCreateTemplateOnDisk:
    def test_creates_file(self, tmp_path: Path):
        template = _make_template()
        templates_dict: dict[str, TemplateConfig] = {}

        create_template_on_disk(template, tmp_path, templates_dict)

        assert (tmp_path / "test-label.yaml").exists()
        assert "test-label" in templates_dict

        # Verify file content is valid YAML that round-trips
        with open(tmp_path / "test-label.yaml") as f:
            data = yaml.safe_load(f)
        assert data["name"] == "test-label"

    def test_creates_directory_if_missing(self, tmp_path: Path):
        subdir = tmp_path / "templates"
        template = _make_template()
        templates_dict: dict[str, TemplateConfig] = {}

        create_template_on_disk(template, subdir, templates_dict)
        assert (subdir / "test-label.yaml").exists()

    def test_file_not_executable(self, tmp_path: Path):
        """Created template files must never have execute permission."""
        template = _make_template()
        templates_dict: dict[str, TemplateConfig] = {}

        # Set a permissive umask to prove we override it
        old_umask = os.umask(0o000)
        try:
            create_template_on_disk(template, tmp_path, templates_dict)
        finally:
            os.umask(old_umask)

        mode = (tmp_path / "test-label.yaml").stat().st_mode
        assert not (mode & stat.S_IXUSR), "Owner execute bit must not be set"
        assert not (mode & stat.S_IXGRP), "Group execute bit must not be set"
        assert not (mode & stat.S_IXOTH), "Other execute bit must not be set"
        # Verify it's exactly 0o644
        assert stat.S_IMODE(mode) == 0o644

    def test_conflict_raises_409(self, tmp_path: Path):
        template = _make_template()
        templates_dict = {"test-label": template}

        with pytest.raises(TemplateCRUDError) as exc_info:
            create_template_on_disk(template, tmp_path, templates_dict)
        assert exc_info.value.status_code == 409


class TestUpdateTemplateOnDisk:
    def test_updates_file(self, tmp_path: Path):
        template = _make_template()
        templates_dict: dict[str, TemplateConfig] = {}
        create_template_on_disk(template, tmp_path, templates_dict)

        updated = _make_template()
        updated.description = "Updated description"
        update_template_on_disk("test-label", updated, tmp_path, templates_dict)

        assert templates_dict["test-label"].description == "Updated description"
        with open(tmp_path / "test-label.yaml") as f:
            data = yaml.safe_load(f)
        assert data["description"] == "Updated description"

    def test_updated_file_not_executable(self, tmp_path: Path):
        """Updated template files must never have execute permission."""
        template = _make_template()
        templates_dict: dict[str, TemplateConfig] = {}
        create_template_on_disk(template, tmp_path, templates_dict)

        # Make the file executable to simulate a bad state
        target = tmp_path / "test-label.yaml"
        target.chmod(0o755)

        updated = _make_template()
        updated.description = "Updated"
        old_umask = os.umask(0o000)
        try:
            update_template_on_disk("test-label", updated, tmp_path, templates_dict)
        finally:
            os.umask(old_umask)

        mode = target.stat().st_mode
        assert not (mode & stat.S_IXUSR), "Owner execute bit must not be set after update"
        assert stat.S_IMODE(mode) == 0o644

    def test_not_found_raises_404(self, tmp_path: Path):
        template = _make_template()
        templates_dict: dict[str, TemplateConfig] = {}

        with pytest.raises(TemplateCRUDError) as exc_info:
            update_template_on_disk("nonexistent", template, tmp_path, templates_dict)
        assert exc_info.value.status_code == 404


class TestCrudNaming:
    def test_update_rejects_body_name_mismatch(self, tmp_path: Path):
        templates = {"foo": _make_template("foo")}
        (tmp_path / "foo.yaml").write_text(template_to_yaml(templates["foo"]))
        with pytest.raises(TemplateCRUDError) as exc:
            update_template_on_disk("foo", _make_template("bar"), tmp_path, templates)
        assert exc.value.status_code == 400
        assert not (tmp_path / "bar.yaml").exists()

    def test_update_writes_back_to_the_file_it_came_from(self, tmp_path: Path):
        """x.yaml holding template 'y' is updated in place, not duplicated as y.yaml."""
        original = _make_template("y")
        (tmp_path / "x.yaml").write_text(template_to_yaml(original))
        updated = original.model_copy(update={"description": "changed"})
        update_template_on_disk("y", updated, tmp_path, {"y": original})
        assert not (tmp_path / "y.yaml").exists()
        assert yaml.safe_load((tmp_path / "x.yaml").read_text())["description"] == "changed"

    def test_create_does_not_overwrite_a_file_that_failed_to_load(self, tmp_path: Path):
        (tmp_path / "broken.yaml").write_text("name: broken\nthis: [is not loaded\n")
        with pytest.raises(TemplateCRUDError) as exc:
            create_template_on_disk(_make_template("broken"), tmp_path, {})
        assert exc.value.status_code == 409
        assert "did not load" in exc.value.message
        assert "is not loaded" in (tmp_path / "broken.yaml").read_text()


class TestCrudFontValidation:
    """API-created image templates get the same font check as startup."""

    def _image_template(self, font: str) -> TemplateConfig:
        from labelable.models.template import BoundingBox, EngineType, TextElement

        return TemplateConfig(
            name="img",
            engine=EngineType.IMAGE,
            dimensions=LabelDimensions(width_mm=40, height_mm=28),
            supported_printers=["zpl"],
            elements=[
                TextElement(
                    type="text",
                    static_text="x",
                    bounds=BoundingBox(x_mm=0, y_mm=0, width_mm=10, height_mm=5),
                    font=font,
                )
            ],
        )

    def test_create_rejects_missing_font(self, tmp_path: Path):
        fonts = tmp_path / "fonts"
        fonts.mkdir()
        with pytest.raises(TemplateCRUDError) as exc:
            create_template_on_disk(self._image_template("NoSuchFont-Regular"), tmp_path, {}, fonts_dir=fonts)
        assert exc.value.status_code == 400
        assert "NoSuchFont-Regular" in exc.value.message
        assert not (tmp_path / "img.yaml").exists()

    def test_create_accepts_available_font(self, tmp_path: Path):
        # templates/ is gitignored, so its fonts exist only on a developer's
        # machine; the check matches by file name, so an empty file will do
        fonts = tmp_path / "fonts"
        fonts.mkdir()
        (fonts / "BarlowSemiCondensed-Bold.ttf").touch()
        templates: dict = {}
        create_template_on_disk(self._image_template("BarlowSemiCondensed-Bold"), tmp_path, templates, fonts_dir=fonts)
        assert "img" in templates

    def test_no_fonts_dir_skips_check(self, tmp_path: Path):
        templates: dict = {}
        create_template_on_disk(self._image_template("NoSuchFont-Regular"), tmp_path, templates)
        assert "img" in templates
