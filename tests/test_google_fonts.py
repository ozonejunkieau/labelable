"""Tests for Google Fonts downloader."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from labelable.templates.google_fonts import (
    _is_family_in_manifest,
    download_google_font,
    ensure_google_fonts,
    get_font_family_from_name,
)


class TestIsFamilyInManifest:
    """Tests for manifest-based family detection."""

    def test_empty_manifest_returns_false(self):
        """Empty manifest returns False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _is_family_in_manifest(Path(tmpdir), "Roboto")
            assert result is False

    def test_family_in_manifest_returns_true(self):
        """Family in manifest returns True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a manifest with the family
            import json

            manifest = {"Roboto": "Roboto-Regular.ttf", "roboto": "Roboto-Regular.ttf"}
            (Path(tmpdir) / "fonts.json").write_text(json.dumps(manifest))

            result = _is_family_in_manifest(Path(tmpdir), "Roboto")
            assert result is True

    def test_family_with_spaces_found(self):
        """Family with spaces is found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import json

            manifest = {"Open Sans": "OpenSans-Regular.ttf", "OpenSans": "OpenSans-Regular.ttf"}
            (Path(tmpdir) / "fonts.json").write_text(json.dumps(manifest))

            result = _is_family_in_manifest(Path(tmpdir), "Open Sans")
            assert result is True

    def test_case_insensitive_lookup(self):
        """Family lookup is case-insensitive."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import json

            manifest = {"roboto": "Roboto-Regular.ttf"}
            (Path(tmpdir) / "fonts.json").write_text(json.dumps(manifest))

            result = _is_family_in_manifest(Path(tmpdir), "Roboto")
            assert result is True


class TestGetFontFamilyFromName:
    """Tests for font family name extraction."""

    def test_simple_font_name(self):
        """Simple font name returns as-is."""
        assert get_font_family_from_name("Roboto") == "Roboto"

    def test_font_with_weight_suffix(self):
        """Font with weight suffix extracts base name."""
        assert get_font_family_from_name("Roboto-Regular") == "Roboto"
        assert get_font_family_from_name("Roboto-Bold") == "Roboto"
        assert get_font_family_from_name("Roboto-Light") == "Roboto"

    def test_camel_case_converts_to_spaces(self):
        """CamelCase font names convert to spaces."""
        assert get_font_family_from_name("OpenSans") == "Open Sans"
        assert get_font_family_from_name("FiraCode") == "Fira Code"
        assert get_font_family_from_name("SourceCodePro") == "Source Code Pro"

    def test_camel_case_with_suffix(self):
        """CamelCase with suffix extracts and converts."""
        assert get_font_family_from_name("OpenSans-Regular") == "Open Sans"
        assert get_font_family_from_name("FiraCode-Bold") == "Fira Code"

    def test_system_fonts_return_none(self):
        """System fonts return None."""
        assert get_font_family_from_name("Arial") is None
        assert get_font_family_from_name("Helvetica") is None
        assert get_font_family_from_name("Times New Roman") is None
        assert get_font_family_from_name("DejaVuSans") is None
        assert get_font_family_from_name("DejaVuSans-Bold") is None
        assert get_font_family_from_name("Courier") is None

    def test_case_insensitive_system_font_check(self):
        """System font check is case-insensitive."""
        assert get_font_family_from_name("arial") is None
        assert get_font_family_from_name("ARIAL") is None
        assert get_font_family_from_name("Arial-Bold") is None


class TestDownloadGoogleFont:
    """Tests for Google Font downloading."""

    @patch("labelable.templates.google_fonts.httpx.get")
    def test_download_creates_font_files(self, mock_get):
        """Downloading a font creates properly named TTF files."""
        # Create fake CSS response
        css_content = """
        @font-face {
          font-family: 'Roboto';
          font-style: normal;
          font-weight: 400;
          src: url(https://fonts.gstatic.com/s/roboto/v50/regular.ttf) format('truetype');
        }
        @font-face {
          font-family: 'Roboto';
          font-style: normal;
          font-weight: 700;
          src: url(https://fonts.gstatic.com/s/roboto/v50/bold.ttf) format('truetype');
        }
        """

        css_response = MagicMock()
        css_response.text = css_content
        css_response.raise_for_status = MagicMock()

        font_response = MagicMock()
        font_response.content = b"fake ttf data"
        font_response.raise_for_status = MagicMock()

        mock_get.side_effect = [css_response, font_response, font_response]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = download_google_font("Roboto", Path(tmpdir))

            assert len(result) == 2
            assert (Path(tmpdir) / "Roboto-Regular.ttf").exists()
            assert (Path(tmpdir) / "Roboto-Bold.ttf").exists()

    @patch("labelable.templates.google_fonts.httpx.get")
    def test_download_handles_spaces_in_family(self, mock_get):
        """Font families with spaces are handled correctly."""
        css_content = """
        @font-face {
          font-family: 'Open Sans';
          font-style: normal;
          font-weight: 400;
          src: url(https://fonts.gstatic.com/s/opensans/v50/regular.ttf) format('truetype');
        }
        """

        css_response = MagicMock()
        css_response.text = css_content
        css_response.raise_for_status = MagicMock()

        font_response = MagicMock()
        font_response.content = b"fake ttf data"
        font_response.raise_for_status = MagicMock()

        mock_get.side_effect = [css_response, font_response]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = download_google_font("Open Sans", Path(tmpdir))

            assert len(result) == 1
            # Spaces should be removed from filename
            assert (Path(tmpdir) / "OpenSans-Regular.ttf").exists()


class TestEnsureGoogleFonts:
    """Tests for ensure_google_fonts function."""

    @patch("labelable.templates.google_fonts.download_google_font")
    def test_skips_already_downloaded(self, mock_download):
        """Fonts already in manifest are skipped."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create manifest with Roboto
            manifest = {"Roboto": "Roboto-Regular.ttf"}
            (Path(tmpdir) / "fonts.json").write_text(json.dumps(manifest))

            result = ensure_google_fonts(["Roboto", "Open Sans"], Path(tmpdir))

            # Should only download Open Sans
            mock_download.assert_called_once()
            assert mock_download.call_args[0][0] == "Open Sans"
            assert "Open Sans" in result
            assert "Roboto" not in result

    @patch("labelable.templates.google_fonts.download_google_font")
    def test_returns_downloaded_families(self, mock_download):
        """Returns list of newly downloaded families."""
        mock_download.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            result = ensure_google_fonts(["Roboto", "Open Sans"], Path(tmpdir))

            assert set(result) == {"Roboto", "Open Sans"}

    @patch("labelable.templates.google_fonts.download_google_font")
    def test_creates_dest_directory(self, mock_download):
        """Creates destination directory if it doesn't exist."""
        mock_download.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            fonts_dir = Path(tmpdir) / "fonts" / "google"
            ensure_google_fonts(["Roboto"], fonts_dir)

            assert fonts_dir.exists()
