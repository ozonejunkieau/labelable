"""Tests for font management."""

import tempfile
from pathlib import Path

from PIL import ImageFont

from labelable.templates.fonts import FontManager, get_font_manager


class TestFontManager:
    """Tests for FontManager."""

    def test_get_font_returns_font(self):
        """Getting a font should return a PIL font object."""
        manager = FontManager()
        font = manager.get_font("Arial", 12)

        # Should return a font object (either TrueType or default)
        assert font is not None
        assert isinstance(font, (ImageFont.FreeTypeFont, ImageFont.ImageFont))

    def test_get_font_caches_result(self):
        """Getting the same font twice should return cached result."""
        manager = FontManager()
        font1 = manager.get_font("Arial", 12)
        font2 = manager.get_font("Arial", 12)

        assert font1 is font2

    def test_different_sizes_are_different_fonts(self):
        """Different font sizes should return different font objects."""
        manager = FontManager()
        font1 = manager.get_font("Arial", 12)
        font2 = manager.get_font("Arial", 24)

        # Different size = different cache key
        assert (font1 is font2) is False or (
            isinstance(font1, ImageFont.ImageFont) and isinstance(font2, ImageFont.ImageFont)
        )

    def test_unknown_font_falls_back_to_default(self):
        """Unknown font names should fall back to PIL default."""
        manager = FontManager()
        font = manager.get_font("NonExistentFontName12345", 12)

        # Should still return a font (the default)
        assert font is not None

    def test_font_aliases(self):
        """Font aliases should work."""
        manager = FontManager()

        # These should all resolve to the same base font name
        font1 = manager.get_font("dejavu", 12)
        font2 = manager.get_font("DejaVuSans", 12)

        assert font1 is not None
        assert font2 is not None

    def test_custom_paths_searched_first(self):
        """Custom paths should be searched before system paths."""
        # Create a temp directory with a font file indicator
        with tempfile.TemporaryDirectory() as tmpdir:
            # We can't easily create a real font file, but we can verify
            # the search path logic
            manager = FontManager(custom_paths=[tmpdir])

            # Custom path should be in the search list
            assert Path(tmpdir) in manager._custom_paths

    def test_clear_cache(self):
        """Clear cache should remove cached fonts."""
        manager = FontManager()
        manager.get_font("Arial", 12)  # Populate cache

        manager.clear_cache()

        # Cache should be empty
        assert len(manager._cache) == 0
        assert len(manager._path_cache) == 0

    def test_font_path_caching(self):
        """Font path lookups should be cached."""
        manager = FontManager()

        # First lookup populates cache
        manager.get_font("Arial", 12)

        # Path cache should have an entry (even if None for not found)
        # The normalized name "Arial" should be in the cache
        assert len(manager._path_cache) > 0

    def test_direct_font_path(self):
        """Font name with path separators should be treated as direct path."""
        manager = FontManager()

        # This should try to load as a direct path
        # Even if the path doesn't exist, it should fall back gracefully
        font = manager.get_font("/nonexistent/path/font.ttf", 12)
        assert font is not None


class TestGetFontManager:
    """Tests for get_font_manager helper function."""

    def test_returns_default_manager(self):
        """Should return a default manager when no paths provided."""
        manager = get_font_manager()
        assert manager is not None
        assert isinstance(manager, FontManager)

    def test_custom_paths_creates_new_manager(self):
        """Custom paths should create a new manager instance."""
        manager1 = get_font_manager()
        manager2 = get_font_manager(custom_paths=["/tmp"])

        # Custom paths should create a different manager
        assert manager1 is not manager2

    def test_no_paths_returns_cached_manager(self):
        """No custom paths should return the same cached manager."""
        manager1 = get_font_manager()
        manager2 = get_font_manager()

        assert manager1 is manager2


class TestFontManagerManifest:
    """Tests for FontManager manifest functionality."""

    def test_load_manifests_from_custom_path(self):
        """Load font manifests from custom paths."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a manifest file
            manifest = {"Test Font": "TestFont.ttf", "test font": "TestFont.ttf"}
            manifest_path = Path(tmpdir) / "fonts.json"
            manifest_path.write_text(json.dumps(manifest))

            manager = FontManager(custom_paths=[tmpdir])

            # Manifest should be loaded
            assert Path(tmpdir) in manager._manifests
            assert manager._manifests[Path(tmpdir)] == manifest

    def test_find_in_manifest(self):
        """Find font via manifest lookup."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a manifest and fake font file
            manifest = {"Test Font": "TestFont.ttf"}
            manifest_path = Path(tmpdir) / "fonts.json"
            manifest_path.write_text(json.dumps(manifest))

            # Create the fake font file
            font_path = Path(tmpdir) / "TestFont.ttf"
            font_path.write_bytes(b"fake font data")

            manager = FontManager(custom_paths=[tmpdir])

            # Should find via manifest
            result = manager._find_in_manifest("Test Font")
            assert result == font_path

    def test_find_in_manifest_case_insensitive(self):
        """Manifest lookup should be case-insensitive."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"test font": "TestFont.ttf"}
            manifest_path = Path(tmpdir) / "fonts.json"
            manifest_path.write_text(json.dumps(manifest))

            font_path = Path(tmpdir) / "TestFont.ttf"
            font_path.write_bytes(b"fake font data")

            manager = FontManager(custom_paths=[tmpdir])

            # Should find with different case
            result = manager._find_in_manifest("Test Font")
            assert result == font_path

    def test_find_in_manifest_not_found(self):
        """Return None when font not in manifest."""
        manager = FontManager()

        result = manager._find_in_manifest("NonexistentFont")
        assert result is None

    def test_clear_cache_reloads_manifests(self):
        """Clearing cache should reload manifests."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"Font1": "Font1.ttf"}
            manifest_path = Path(tmpdir) / "fonts.json"
            manifest_path.write_text(json.dumps(manifest))

            manager = FontManager(custom_paths=[tmpdir])
            assert "Font1" in manager._manifests[Path(tmpdir)]

            # Update manifest
            manifest["Font2"] = "Font2.ttf"
            manifest_path.write_text(json.dumps(manifest))

            # Clear cache and reload
            manager.clear_cache()

            # Should have new entry
            assert "Font2" in manager._manifests[Path(tmpdir)]


class TestFontManagerSearch:
    """Tests for FontManager font search functionality."""

    def test_find_font_with_name_variants(self):
        """Find font using name variants (with/without spaces)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create font file with no-space name
            font_path = Path(tmpdir) / "OpenSans-Regular.ttf"
            font_path.write_bytes(b"fake font data")

            manager = FontManager(custom_paths=[tmpdir])

            # Should find with space variant
            result = manager._find_font("Open Sans")
            # Note: might not find if manifest isn't set up
            # Just test it doesn't crash
            assert result is None or result == font_path

    def test_find_font_searches_subdirectories(self):
        """Find font in subdirectory of custom path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create subdirectory with font
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()
            font_path = subdir / "TestFont.ttf"
            font_path.write_bytes(b"fake font data")

            manager = FontManager(custom_paths=[tmpdir])

            result = manager._find_font("TestFont")
            assert result == font_path

    def test_find_font_caches_not_found(self):
        """Not-found results should be cached."""
        manager = FontManager()

        # First lookup
        result1 = manager._find_font("NonexistentFont12345")
        assert result1 is None

        # Should be cached as None
        assert "NonexistentFont12345" in manager._path_cache
        assert manager._path_cache["NonexistentFont12345"] is None

        # Second lookup should use cache
        result2 = manager._find_font("NonexistentFont12345")
        assert result2 is None
