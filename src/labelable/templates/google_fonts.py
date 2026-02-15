"""Google Fonts downloader for image template engine."""

import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# User agent that requests TTF format (some user agents get woff2)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _is_family_in_manifest(dest: Path, family: str) -> bool:
    """Check if a font family is already in the manifest.

    Args:
        dest: Directory containing fonts and manifest.
        family: Font family name to check.

    Returns:
        True if the family is found in the manifest.
    """
    from labelable.templates.font_manifest import load_manifest

    manifest = load_manifest(dest)
    if not manifest:
        return False

    # Check if family name (with or without spaces) is in manifest
    family_no_space = family.replace(" ", "")
    return family in manifest or family_no_space in manifest or family.lower() in manifest


WEIGHT_NAMES = {
    "100": "Thin",
    "200": "ExtraLight",
    "300": "Light",
    "400": "Regular",
    "500": "Medium",
    "600": "SemiBold",
    "700": "Bold",
    "800": "ExtraBold",
    "900": "Black",
}


def download_google_font(family: str, dest: Path) -> list[Path]:
    """Download a Google Font family to dest.

    Uses the Google Fonts CSS API to get font file URLs, then downloads
    the actual font files with proper naming (e.g., Roboto-Regular.ttf).

    Args:
        family: Google Font family name, e.g. "Roboto".
        dest: Directory to store font files.

    Returns:
        List of paths to downloaded font files.

    Raises:
        httpx.HTTPStatusError: If the download fails.
        ValueError: If no font files are found.
    """
    dest.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading Google Font: {family}")

    # URL-encode family name (spaces become +)
    family_param = family.replace(" ", "+")

    # Request common weights
    css_url = f"https://fonts.googleapis.com/css2?family={family_param}:wght@100;200;300;400;500;600;700;800;900"

    # Use a user agent that gets TTF format
    headers = {"User-Agent": USER_AGENT}

    resp = httpx.get(css_url, headers=headers, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()

    css_content = resp.text

    # Parse CSS to extract weight and URL pairs
    # Pattern matches @font-face blocks with font-weight and url
    block_pattern = re.compile(
        r"@font-face\s*\{[^}]*font-weight:\s*(\d+)[^}]*url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)[^}]*\}",
        re.DOTALL,
    )
    matches = block_pattern.findall(css_content)

    if not matches:
        raise ValueError(f"No TTF font files found for '{family}'")

    # Download each font file with proper naming
    downloaded_files: list[Path] = []
    seen_weights: set[str] = set()

    # Normalize family name for filename (remove spaces)
    family_filename = family.replace(" ", "")

    for weight, font_url in matches:
        if weight in seen_weights:
            continue
        seen_weights.add(weight)

        # Get weight name
        weight_name = WEIGHT_NAMES.get(weight, f"W{weight}")

        # Create proper filename
        filename = f"{family_filename}-{weight_name}.ttf"

        # Download the font file
        font_resp = httpx.get(font_url, headers=headers, timeout=30.0)
        font_resp.raise_for_status()

        font_path = dest / filename
        font_path.write_bytes(font_resp.content)
        downloaded_files.append(font_path)
        logger.debug(f"Downloaded: {font_path.name}")

    logger.info(f"Downloaded {len(downloaded_files)} font files for {family}")

    # Update font manifest with metadata from downloaded files
    from labelable.templates.font_manifest import update_manifest

    update_manifest(dest)

    return downloaded_files


def ensure_google_fonts(families: list[str], dest: Path) -> list[str]:
    """Download Google Fonts to dest if not already present.

    Uses the font manifest to check if fonts are already downloaded.

    Args:
        families: Google Font family names, e.g. ["Roboto", "Fira Code"].
        dest: Directory to store font files.

    Returns:
        List of font families that were newly downloaded.

    Raises:
        httpx.HTTPStatusError: If a download fails.
    """
    dest.mkdir(parents=True, exist_ok=True)

    downloaded: list[str] = []
    for family in families:
        if _is_family_in_manifest(dest, family):
            logger.debug(f"Font family already downloaded: {family}")
            continue

        download_google_font(family, dest)
        downloaded.append(family)

    return downloaded


def get_font_family_from_name(font_name: str) -> str | None:
    """Extract the likely Google Font family name from a font name.

    Google Fonts downloads include variants like "Roboto-Regular.ttf",
    "Roboto-Bold.ttf", etc. This function extracts "Roboto" from those.

    Common patterns:
    - "Roboto-Regular" -> "Roboto"
    - "Roboto-Bold" -> "Roboto"
    - "OpenSans-Regular" -> "Open Sans" (with space)
    - "FiraCode-Regular" -> "Fira Code" (with space)

    Args:
        font_name: Font name from template, e.g. "Roboto-Bold"

    Returns:
        Likely Google Font family name, or None if it looks like a system font.
    """
    # Skip obvious system/default fonts
    system_fonts = {
        "arial",
        "helvetica",
        "times",
        "times new roman",
        "courier",
        "courier new",
        "verdana",
        "georgia",
        "tahoma",
        "trebuchet",
        "impact",
        "comic sans",
        "dejavusans",
        "dejavu sans",
        "liberation",
    }

    name_lower = font_name.lower()
    for sys_font in system_fonts:
        if sys_font in name_lower:
            return None

    # Remove weight/style suffix
    base_name = font_name.split("-")[0]

    # Convert CamelCase to spaces for Google Fonts API
    # e.g., "OpenSans" -> "Open Sans", "FiraCode" -> "Fira Code"
    # Handle acronyms: "PTSans" -> "PT Sans" (space before uppercase followed by lowercase)
    spaced_name = ""
    for i, char in enumerate(base_name):
        if i > 0 and char.isupper():
            prev_lower = base_name[i - 1].islower()
            # Insert space if: prev is lowercase OR (prev is upper AND next is lowercase)
            next_lower = i + 1 < len(base_name) and base_name[i + 1].islower()
            if prev_lower or (base_name[i - 1].isupper() and next_lower):
                spaced_name += " "
        spaced_name += char

    return spaced_name if spaced_name else None
