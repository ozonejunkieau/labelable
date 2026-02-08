"""Font management for image template engine."""

import logging
from collections.abc import Sequence
from pathlib import Path

from PIL import ImageFont

logger = logging.getLogger(__name__)

# Common system font directories by platform
SYSTEM_FONT_DIRS = [
    # Linux
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
    # macOS
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
    Path.home() / "Library/Fonts",
    # Windows
    Path("C:/Windows/Fonts"),
]

# Font name aliases for common fonts
FONT_ALIASES = {
    "dejavu": "DejaVuSans",
    "dejavu-sans": "DejaVuSans",
    "dejavusans": "DejaVuSans",
    "dejavu-bold": "DejaVuSans-Bold",
    "dejavusans-bold": "DejaVuSans-Bold",
    "arial": "Arial",
    "helvetica": "Helvetica",
    "times": "Times New Roman",
    "courier": "Courier New",
}


class FontManager:
    """Manages font loading with caching and fallback search.

    Search order:
    1. Font manifest (maps names to files based on font metadata)
    2. User-specified custom paths (from template/app config)
    3. System fonts
    4. PIL default font (fallback)
    """

    def __init__(self, custom_paths: Sequence[str | Path] | None = None) -> None:
        """Initialize font manager.

        Args:
            custom_paths: Additional paths to search for fonts (directories or files).
        """
        self._custom_paths = [Path(p) for p in (custom_paths or [])]
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._path_cache: dict[str, Path | None] = {}
        self._manifests: dict[Path, dict[str, str]] = {}  # dir -> manifest
        self._load_manifests()

    def _load_manifests(self) -> None:
        """Load font manifests from custom paths."""
        from labelable.templates.font_manifest import load_manifest

        for custom_path in self._custom_paths:
            if custom_path.is_dir():
                manifest = load_manifest(custom_path)
                if manifest:
                    self._manifests[custom_path] = manifest
                    logger.debug(f"Loaded font manifest from {custom_path} with {len(manifest)} entries")

    def _find_in_manifest(self, name: str) -> Path | None:
        """Look up font name in manifests.

        Args:
            name: Font name to look up.

        Returns:
            Path to font file, or None if not found.
        """
        for fonts_dir, manifest in self._manifests.items():
            # Try exact match first
            if name in manifest:
                font_path = fonts_dir / manifest[name]
                if font_path.exists():
                    return font_path

            # Try case-insensitive match
            name_lower = name.lower()
            if name_lower in manifest:
                font_path = fonts_dir / manifest[name_lower]
                if font_path.exists():
                    return font_path

        return None

    def get_font(self, name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Get a font by name and size.

        Args:
            name: Font name (e.g., "DejaVuSans", "Arial") or path to font file
            size: Font size in points

        Returns:
            PIL font object
        """
        # Check if name is a direct path to a font file
        if "/" in name or "\\" in name:
            font_path = Path(name)
            cache_key = (str(font_path), size)
            if cache_key in self._cache:
                return self._cache[cache_key]

            if font_path.exists():
                try:
                    self._cache[cache_key] = ImageFont.truetype(str(font_path), size)
                    return self._cache[cache_key]
                except OSError as e:
                    logger.warning(f"Failed to load font {font_path}: {e}")

            # Path doesn't exist or failed to load - use default
            logger.warning(f"Font path '{name}' not found, using PIL default")
            self._cache[cache_key] = ImageFont.load_default(size)
            return self._cache[cache_key]

        # Normalize font name
        normalized_name = FONT_ALIASES.get(name.lower(), name)

        cache_key = (normalized_name, size)
        if cache_key in self._cache:
            return self._cache[cache_key]

        font_path = self._find_font(normalized_name)

        if font_path:
            try:
                font = ImageFont.truetype(str(font_path), size)
                self._cache[cache_key] = font
                return font
            except OSError as e:
                logger.warning(f"Failed to load font {font_path}: {e}")

        # Fallback to PIL default
        logger.warning(f"Font '{name}' not found, using PIL default")
        font = ImageFont.load_default(size)
        self._cache[cache_key] = font
        return font

    def _find_font(self, name: str) -> Path | None:
        """Find font file path by name.

        Args:
            name: Font name (without extension)

        Returns:
            Path to font file, or None if not found
        """
        if name in self._path_cache:
            return self._path_cache[name]

        # Check manifests first (most reliable)
        manifest_result = self._find_in_manifest(name)
        if manifest_result:
            self._path_cache[name] = manifest_result
            logger.debug(f"Found font '{name}' via manifest: {manifest_result}")
            return manifest_result

        # Common font file extensions
        extensions = [".ttf", ".otf", ".TTF", ".OTF"]

        # Build list of name variants to try for file-based search
        # e.g., "Metal Mania" -> ["Metal Mania", "MetalMania", "MetalMania-Regular"]
        name_variants = [name]
        if " " in name:
            no_space = name.replace(" ", "")
            name_variants.append(no_space)
            name_variants.append(f"{no_space}-Regular")

        # Search in custom paths first
        for custom_path in self._custom_paths:
            if custom_path.is_dir():
                for variant in name_variants:
                    for ext in extensions:
                        font_file = custom_path / f"{variant}{ext}"
                        if font_file.exists():
                            self._path_cache[name] = font_file
                            logger.debug(f"Found font '{name}' at {font_file}")
                            return font_file
                # Also search subdirectories (one level)
                for subdir in custom_path.iterdir():
                    if subdir.is_dir():
                        for variant in name_variants:
                            for ext in extensions:
                                font_file = subdir / f"{variant}{ext}"
                                if font_file.exists():
                                    self._path_cache[name] = font_file
                                    logger.debug(f"Found font '{name}' at {font_file}")
                                    return font_file
            elif custom_path.is_file() and custom_path.stem in name_variants:
                self._path_cache[name] = custom_path
                return custom_path

        # Search in system font directories
        for sys_dir in SYSTEM_FONT_DIRS:
            if not sys_dir.exists():
                continue
            # Search recursively in system dirs (limit depth for performance)
            for variant in name_variants:
                for ext in extensions:
                    # Try direct match
                    font_file = sys_dir / f"{variant}{ext}"
                    if font_file.exists():
                        self._path_cache[name] = font_file
                        logger.debug(f"Found font '{name}' at {font_file}")
                        return font_file

            # Try recursive search with limited depth
            for variant in name_variants:
                try:
                    for font_file in sys_dir.rglob(f"{variant}.*"):
                        if font_file.suffix.lower() in [".ttf", ".otf"]:
                            self._path_cache[name] = font_file
                            logger.debug(f"Found font '{name}' at {font_file}")
                            return font_file
                except PermissionError:
                    continue

        # Not found
        self._path_cache[name] = None
        return None

    def clear_cache(self) -> None:
        """Clear the font cache and reload manifests."""
        self._cache.clear()
        self._path_cache.clear()
        self._manifests.clear()
        self._load_manifests()


# Default font manager instance
_default_manager: FontManager | None = None


def get_font_manager(custom_paths: Sequence[str | Path] | None = None) -> FontManager:
    """Get or create font manager.

    Args:
        custom_paths: Additional paths to search for fonts.

    Returns:
        FontManager instance
    """
    global _default_manager

    if custom_paths:
        # Create new manager with custom paths
        return FontManager(custom_paths)

    if _default_manager is None:
        _default_manager = FontManager()

    return _default_manager
