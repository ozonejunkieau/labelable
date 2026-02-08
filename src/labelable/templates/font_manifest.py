"""Font manifest management for mapping font names to files.

Reads font metadata from TTF/OTF files and creates a manifest that maps
various name patterns to actual font files.
"""

import json
import logging
from pathlib import Path

from fontTools.ttLib import TTFont

logger = logging.getLogger(__name__)

MANIFEST_FILE = "fonts.json"

# OpenType name table IDs
NAME_ID_FAMILY = 1  # Font Family name
NAME_ID_SUBFAMILY = 2  # Font Subfamily name (e.g., "Bold Italic")
NAME_ID_FULL_NAME = 4  # Full font name
NAME_ID_POSTSCRIPT = 6  # PostScript name
NAME_ID_TYPOGRAPHIC_FAMILY = 16  # Typographic Family name (preferred)
NAME_ID_TYPOGRAPHIC_SUBFAMILY = 17  # Typographic Subfamily name (preferred)

# Weight name mappings (for generating aliases)
WEIGHT_NAMES = {
    100: ["Thin", "Hairline"],
    200: ["ExtraLight", "UltraLight"],
    250: ["ExtraLight", "UltraLight"],
    300: ["Light"],
    350: ["Light"],
    400: ["Regular", "Normal", "Book"],
    500: ["Medium"],
    600: ["SemiBold", "DemiBold"],
    700: ["Bold"],
    800: ["ExtraBold", "UltraBold"],
    900: ["Black", "Heavy"],
}

# Style variations
ITALIC_NAMES = ["Italic", "Oblique", "Ital"]


def read_font_metadata(font_path: Path) -> dict | None:
    """Read metadata from a font file.

    Args:
        font_path: Path to TTF/OTF file.

    Returns:
        Dictionary with font metadata, or None if reading fails.
    """
    try:
        font = TTFont(font_path)
        name_table = font["name"]

        def get_name(name_id: int) -> str | None:
            """Get name from name table, preferring English."""
            record = name_table.getName(name_id, 3, 1, 0x409)  # Windows, Unicode, English
            if record:
                return str(record)
            record = name_table.getName(name_id, 1, 0, 0)  # Mac, Roman, English
            if record:
                return str(record)
            # Try any platform
            for record in name_table.names:
                if record.nameID == name_id:
                    try:
                        return str(record)
                    except Exception:
                        continue
            return None

        # Get family name (prefer typographic family if available)
        family = get_name(NAME_ID_TYPOGRAPHIC_FAMILY) or get_name(NAME_ID_FAMILY)
        subfamily = get_name(NAME_ID_TYPOGRAPHIC_SUBFAMILY) or get_name(NAME_ID_SUBFAMILY)
        full_name = get_name(NAME_ID_FULL_NAME)
        postscript_name = get_name(NAME_ID_POSTSCRIPT)

        # Get weight from OS/2 table
        weight = 400  # Default to regular
        if "OS/2" in font:
            os2_table = font["OS/2"]
            weight = getattr(os2_table, "usWeightClass", 400)

        # Determine if italic from various sources
        is_italic = False
        if subfamily:
            is_italic = any(name.lower() in subfamily.lower() for name in ITALIC_NAMES)
        if "OS/2" in font:
            os2_table = font["OS/2"]
            # fsSelection bit 0 = italic
            fs_selection = getattr(os2_table, "fsSelection", 0)
            is_italic = is_italic or bool(fs_selection & 1)

        font.close()

        return {
            "family": family,
            "subfamily": subfamily,
            "full_name": full_name,
            "postscript_name": postscript_name,
            "weight": weight,
            "is_italic": is_italic,
            "file": font_path.name,
        }

    except Exception as e:
        logger.warning(f"Failed to read font metadata from {font_path}: {e}")
        return None


def generate_font_aliases(metadata: dict) -> list[str]:
    """Generate all possible name aliases for a font.

    Args:
        metadata: Font metadata from read_font_metadata().

    Returns:
        List of name aliases that should map to this font.
    """
    aliases = []
    family = metadata.get("family", "")
    subfamily = metadata.get("subfamily", "")
    full_name = metadata.get("full_name", "")
    postscript_name = metadata.get("postscript_name", "")
    weight = metadata.get("weight", 400)
    is_italic = metadata.get("is_italic", False)

    if not family:
        return aliases

    # Normalize family name variants
    family_no_space = family.replace(" ", "")

    # Get weight names
    weight_names = WEIGHT_NAMES.get(weight, [])
    # Find closest weight if exact not found
    if not weight_names:
        closest = min(WEIGHT_NAMES.keys(), key=lambda w: abs(w - weight))
        weight_names = WEIGHT_NAMES[closest]

    # Build style suffix
    italic_suffix = "Italic" if is_italic else ""

    # Add aliases in priority order

    # 1. Full name as-is
    if full_name:
        aliases.append(full_name)
        aliases.append(full_name.replace(" ", ""))

    # 2. PostScript name
    if postscript_name:
        aliases.append(postscript_name)

    # 3. Family + Subfamily
    if subfamily:
        aliases.append(f"{family} {subfamily}")
        aliases.append(f"{family_no_space}-{subfamily.replace(' ', '')}")

    # 4. Family + weight name + italic
    for weight_name in weight_names:
        if is_italic:
            aliases.append(f"{family} {weight_name} {italic_suffix}")
            aliases.append(f"{family_no_space}-{weight_name}{italic_suffix}")
            aliases.append(f"{family_no_space}-{weight_name}-{italic_suffix}")
        else:
            aliases.append(f"{family} {weight_name}")
            aliases.append(f"{family_no_space}-{weight_name}")

    # 5. Family + numeric weight
    if is_italic:
        aliases.append(f"{family_no_space}-{weight}italic")
        aliases.append(f"{family_no_space}-{weight}-Italic")
    else:
        aliases.append(f"{family_no_space}-{weight}")

    # 6. Just family name (only for Regular weight, non-italic)
    if weight in [400, 350] and not is_italic:
        aliases.append(family)
        aliases.append(family_no_space)

    # 7. Family + Italic (for italic variants of regular weight)
    if weight in [400, 350] and is_italic:
        aliases.append(f"{family} Italic")
        aliases.append(f"{family_no_space}-Italic")
        aliases.append(f"{family}Italic")

    # Remove duplicates while preserving order
    seen = set()
    unique_aliases = []
    for alias in aliases:
        if alias and alias not in seen:
            seen.add(alias)
            unique_aliases.append(alias)

    return unique_aliases


def build_font_manifest(fonts_dir: Path) -> dict[str, str]:
    """Build a font manifest from all fonts in a directory.

    Args:
        fonts_dir: Directory containing font files.

    Returns:
        Dictionary mapping font name aliases to filenames.
    """
    manifest: dict[str, str] = {}

    if not fonts_dir.exists():
        return manifest

    # Process all font files
    for font_file in fonts_dir.glob("*.[ot]tf"):
        metadata = read_font_metadata(font_file)
        if not metadata:
            continue

        aliases = generate_font_aliases(metadata)
        for alias in aliases:
            # Don't overwrite existing mappings (first file wins for each alias)
            # This ensures more specific matches take priority
            if alias not in manifest:
                manifest[alias] = font_file.name
                logger.debug(f"Mapped '{alias}' -> {font_file.name}")

    # Also add case-insensitive variants
    case_insensitive: dict[str, str] = {}
    for alias, filename in manifest.items():
        lower_alias = alias.lower()
        if lower_alias not in manifest and lower_alias not in case_insensitive:
            case_insensitive[lower_alias] = filename

    manifest.update(case_insensitive)

    return manifest


def save_manifest(manifest: dict[str, str], fonts_dir: Path) -> None:
    """Save font manifest to JSON file.

    Args:
        manifest: Font name to filename mapping.
        fonts_dir: Directory to save manifest in.
    """
    manifest_path = fonts_dir / MANIFEST_FILE
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    logger.info(f"Saved font manifest with {len(manifest)} entries to {manifest_path}")


def load_manifest(fonts_dir: Path) -> dict[str, str]:
    """Load font manifest from JSON file.

    Args:
        fonts_dir: Directory containing manifest.

    Returns:
        Font name to filename mapping, or empty dict if not found.
    """
    manifest_path = fonts_dir / MANIFEST_FILE
    if not manifest_path.exists():
        return {}

    try:
        with open(manifest_path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load font manifest: {e}")
        return {}


def update_manifest(fonts_dir: Path) -> dict[str, str]:
    """Rebuild and save font manifest for a directory.

    Args:
        fonts_dir: Directory containing font files.

    Returns:
        The updated manifest.
    """
    manifest = build_font_manifest(fonts_dir)
    if manifest:
        save_manifest(manifest, fonts_dir)
    return manifest
