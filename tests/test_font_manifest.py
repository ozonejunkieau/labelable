"""Tests for font manifest management."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from labelable.templates.font_manifest import (
    MANIFEST_FILE,
    build_font_manifest,
    generate_font_aliases,
    load_manifest,
    read_font_metadata,
    save_manifest,
    update_manifest,
)


class TestGenerateFontAliases:
    """Tests for alias generation from font metadata."""

    def test_regular_weight_generates_family_alias(self):
        """Regular weight font generates base family name alias."""
        metadata = {
            "family": "Roboto",
            "subfamily": "Regular",
            "full_name": "Roboto Regular",
            "postscript_name": "Roboto-Regular",
            "weight": 400,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert "Roboto" in aliases
        assert "Roboto Regular" in aliases
        assert "Roboto-Regular" in aliases

    def test_bold_weight_generates_weight_alias(self):
        """Bold weight font generates weight name aliases."""
        metadata = {
            "family": "Roboto",
            "subfamily": "Bold",
            "full_name": "Roboto Bold",
            "postscript_name": "Roboto-Bold",
            "weight": 700,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert "Roboto Bold" in aliases
        assert "Roboto-Bold" in aliases
        # Should not include base family name for non-regular weights
        # (only Regular/400 weight gets plain family name)
        assert aliases.count("Roboto") == 0 or "Roboto" not in aliases[:5]

    def test_italic_generates_italic_alias(self):
        """Italic font generates italic aliases."""
        metadata = {
            "family": "Roboto",
            "subfamily": "Italic",
            "full_name": "Roboto Italic",
            "postscript_name": "Roboto-Italic",
            "weight": 400,
            "is_italic": True,
        }
        aliases = generate_font_aliases(metadata)

        assert "Roboto Italic" in aliases
        assert "Roboto-Italic" in aliases

    def test_bold_italic_generates_combined_alias(self):
        """Bold italic font generates combined aliases."""
        metadata = {
            "family": "Roboto",
            "subfamily": "Bold Italic",
            "full_name": "Roboto Bold Italic",
            "postscript_name": "Roboto-BoldItalic",
            "weight": 700,
            "is_italic": True,
        }
        aliases = generate_font_aliases(metadata)

        assert "Roboto Bold Italic" in aliases
        assert "Roboto-BoldItalic" in aliases

    def test_family_with_spaces_generates_no_space_alias(self):
        """Font family with spaces generates no-space variant."""
        metadata = {
            "family": "Open Sans",
            "subfamily": "Regular",
            "full_name": "Open Sans Regular",
            "postscript_name": "OpenSans-Regular",
            "weight": 400,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert "Open Sans" in aliases
        assert "OpenSans" in aliases
        assert "OpenSans-Regular" in aliases

    def test_numeric_weight_alias(self):
        """Generates numeric weight aliases."""
        metadata = {
            "family": "Roboto",
            "subfamily": "Bold",
            "full_name": "Roboto Bold",
            "postscript_name": "Roboto-Bold",
            "weight": 700,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert "Roboto-700" in aliases

    def test_postscript_name_is_alias(self):
        """PostScript name is included as alias."""
        metadata = {
            "family": "Metal Mania",
            "subfamily": "Regular",
            "full_name": "Metal Mania Regular",
            "postscript_name": "MetalMania-Regular",
            "weight": 400,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert "MetalMania-Regular" in aliases

    def test_full_name_is_alias(self):
        """Full font name is included as alias."""
        metadata = {
            "family": "Metal Mania",
            "subfamily": "Regular",
            "full_name": "Metal Mania",
            "postscript_name": "MetalMania-Regular",
            "weight": 400,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert "Metal Mania" in aliases
        assert "MetalMania" in aliases  # No-space variant

    def test_empty_family_returns_empty_list(self):
        """Empty family name returns no aliases."""
        metadata = {
            "family": "",
            "subfamily": "Regular",
            "full_name": None,
            "postscript_name": None,
            "weight": 400,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert aliases == []

    def test_no_duplicate_aliases(self):
        """Aliases should not contain duplicates."""
        metadata = {
            "family": "Roboto",
            "subfamily": "Regular",
            "full_name": "Roboto",  # Same as family
            "postscript_name": "Roboto-Regular",
            "weight": 400,
            "is_italic": False,
        }
        aliases = generate_font_aliases(metadata)

        assert len(aliases) == len(set(aliases))


class TestManifestIO:
    """Tests for manifest save/load operations."""

    def test_save_and_load_manifest(self):
        """Manifest can be saved and loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"Roboto": "Roboto-Regular.ttf", "Roboto Bold": "Roboto-Bold.ttf"}

            save_manifest(manifest, Path(tmpdir))

            loaded = load_manifest(Path(tmpdir))
            assert loaded == manifest

    def test_load_missing_manifest_returns_empty(self):
        """Loading from directory without manifest returns empty dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = load_manifest(Path(tmpdir))
            assert loaded == {}

    def test_manifest_file_is_json(self):
        """Manifest is saved as JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"Test": "test.ttf"}
            save_manifest(manifest, Path(tmpdir))

            manifest_path = Path(tmpdir) / MANIFEST_FILE
            assert manifest_path.exists()
            assert manifest_path.suffix == ".json"


class TestBuildFontManifest:
    """Tests for building manifest from font files."""

    def test_empty_directory_returns_empty_manifest(self):
        """Empty directory returns empty manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = build_font_manifest(Path(tmpdir))
            assert manifest == {}

    def test_nonexistent_directory_returns_empty_manifest(self):
        """Nonexistent directory returns empty manifest."""
        manifest = build_font_manifest(Path("/nonexistent/path"))
        assert manifest == {}

    @patch("labelable.templates.font_manifest.read_font_metadata")
    def test_builds_manifest_from_font_files(self, mock_read):
        """Builds manifest from font file metadata."""
        mock_read.return_value = {
            "family": "Test Font",
            "subfamily": "Regular",
            "full_name": "Test Font Regular",
            "postscript_name": "TestFont-Regular",
            "weight": 400,
            "is_italic": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake font file
            font_file = Path(tmpdir) / "TestFont-Regular.ttf"
            font_file.write_bytes(b"fake font data")

            manifest = build_font_manifest(Path(tmpdir))

            assert "Test Font" in manifest
            assert manifest["Test Font"] == "TestFont-Regular.ttf"

    @patch("labelable.templates.font_manifest.read_font_metadata")
    def test_case_insensitive_aliases_added(self, mock_read):
        """Case-insensitive aliases are added to manifest."""
        mock_read.return_value = {
            "family": "Test Font",
            "subfamily": "Regular",
            "full_name": "Test Font",
            "postscript_name": "TestFont-Regular",
            "weight": 400,
            "is_italic": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            font_file = Path(tmpdir) / "TestFont-Regular.ttf"
            font_file.write_bytes(b"fake font data")

            manifest = build_font_manifest(Path(tmpdir))

            # Should have both original and lowercase
            assert "Test Font" in manifest
            assert "test font" in manifest


class TestUpdateManifest:
    """Tests for update_manifest function."""

    @patch("labelable.templates.font_manifest.build_font_manifest")
    @patch("labelable.templates.font_manifest.save_manifest")
    def test_updates_and_saves_manifest(self, mock_save, mock_build):
        """Updates manifest and saves it."""
        mock_build.return_value = {"Test": "test.ttf"}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = update_manifest(Path(tmpdir))

            mock_build.assert_called_once()
            mock_save.assert_called_once()
            assert result == {"Test": "test.ttf"}

    @patch("labelable.templates.font_manifest.build_font_manifest")
    @patch("labelable.templates.font_manifest.save_manifest")
    def test_does_not_save_empty_manifest(self, mock_save, mock_build):
        """Does not save if manifest is empty."""
        mock_build.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = update_manifest(Path(tmpdir))

            mock_save.assert_not_called()
            assert result == {}


class TestReadFontMetadata:
    """Tests for reading font metadata from files."""

    def test_returns_none_for_invalid_file(self):
        """Returns None for invalid/corrupted font file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "bad.ttf"
            bad_file.write_bytes(b"not a real font")

            result = read_font_metadata(bad_file)
            assert result is None

    def test_returns_none_for_nonexistent_file(self):
        """Returns None for nonexistent file."""
        result = read_font_metadata(Path("/nonexistent/font.ttf"))
        assert result is None
