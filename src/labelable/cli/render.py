"""CLI tool for rendering image template previews."""

import argparse
import json
import os
import sys
from pathlib import Path

import yaml


def _setup_macos_library_path() -> None:
    """Set up library path for macOS Homebrew installations.

    pylibdmtx requires libdmtx to be findable. On macOS with Homebrew,
    the library is installed to /opt/homebrew/lib (Apple Silicon) or
    /usr/local/lib (Intel), but these aren't in the default search path.
    """
    if sys.platform != "darwin":
        return

    if os.environ.get("DYLD_LIBRARY_PATH"):
        return  # Already set by user

    # Check common Homebrew library locations
    homebrew_paths = [
        Path("/opt/homebrew/lib"),  # Apple Silicon
        Path("/usr/local/lib"),  # Intel
    ]

    for lib_path in homebrew_paths:
        if (lib_path / "libdmtx.dylib").exists():
            os.environ["DYLD_LIBRARY_PATH"] = str(lib_path)
            return


def main() -> int:
    """Main entry point for labelable-render CLI."""
    # Set up macOS library path for pylibdmtx
    _setup_macos_library_path()

    parser = argparse.ArgumentParser(
        description="Render an image template to a preview PNG file.",
        prog="labelable-render",
    )
    parser.add_argument(
        "template",
        type=Path,
        help="Path to template YAML file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("preview.png"),
        help="Output file path (default: preview.png)",
    )
    parser.add_argument(
        "-d",
        "--data",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Field value (can be specified multiple times)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        dest="json_file",
        help="JSON file with field values",
    )
    parser.add_argument(
        "--format",
        choices=["png", "zpl", "epl2"],
        default="png",
        help="Output format (default: png)",
    )
    parser.add_argument(
        "--font-path",
        action="append",
        default=[],
        dest="font_paths",
        help="Additional font search path (can be specified multiple times)",
    )
    parser.add_argument(
        "--download-fonts",
        action="store_true",
        help="Download missing fonts from Google Fonts",
    )
    parser.add_argument(
        "--fonts-dir",
        type=Path,
        default=None,
        help="Directory for downloaded fonts (default: ./fonts next to template)",
    )

    args = parser.parse_args()

    # Check template exists
    if not args.template.exists():
        print(f"Error: Template file not found: {args.template}", file=sys.stderr)
        return 1

    # Load template
    try:
        with open(args.template) as f:
            template_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error parsing template YAML: {e}", file=sys.stderr)
        return 1

    # Parse field values
    context: dict[str, str] = {}

    # Load from JSON file if provided
    if args.json_file:
        if not args.json_file.exists():
            print(f"Error: JSON file not found: {args.json_file}", file=sys.stderr)
            return 1
        try:
            with open(args.json_file) as f:
                json_data = json.load(f)
            context.update(json_data)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON file: {e}", file=sys.stderr)
            return 1

    # Parse -d key=value arguments
    for item in args.data:
        if "=" not in item:
            print(f"Error: Invalid data format '{item}'. Use KEY=VALUE", file=sys.stderr)
            return 1
        key, value = item.split("=", 1)
        context[key] = value

    # Create template config
    from labelable.models.template import EngineType, TemplateConfig

    # Set template name from filename
    template_data["name"] = args.template.stem

    try:
        template = TemplateConfig(**template_data)
    except Exception as e:
        print(f"Error loading template: {e}", file=sys.stderr)
        return 1

    # Check this is an image template
    if template.engine != EngineType.IMAGE:
        print(
            f"Warning: Template engine is '{template.engine}', not 'image'. "
            "Preview may not be accurate for jinja templates.",
            file=sys.stderr,
        )
        # For jinja templates, we can't render a preview
        if template.engine == EngineType.JINJA:
            print("Error: Cannot preview jinja templates. Use engine: image.", file=sys.stderr)
            return 1

    # Determine fonts directory
    fonts_dir = args.fonts_dir
    if fonts_dir is None:
        fonts_dir = args.template.parent / "fonts"

    # Download missing fonts if requested
    if args.download_fonts:
        from labelable.models.template import TextElement
        from labelable.templates.fonts import FontManager
        from labelable.templates.google_fonts import ensure_google_fonts, get_font_family_from_name

        # Extract fonts from template elements
        fonts_needed: set[str] = set()
        for element in template.elements:
            if isinstance(element, TextElement) and element.font:
                fonts_needed.add(element.font)

        if fonts_needed:
            fonts_dir.mkdir(parents=True, exist_ok=True)
            font_manager = FontManager(custom_paths=[fonts_dir])

            # Find which fonts need downloading
            families_to_download: set[str] = set()
            for font_name in fonts_needed:
                if font_manager._find_font(font_name) is None:
                    family = get_font_family_from_name(font_name)
                    if family:
                        families_to_download.add(family)

            if families_to_download:
                try:
                    downloaded = ensure_google_fonts(list(families_to_download), fonts_dir)
                    if downloaded:
                        print(f"Downloaded fonts: {', '.join(downloaded)}", file=sys.stderr)
                except Exception as e:
                    print(f"Error downloading fonts: {e}", file=sys.stderr)
                    return 1

    # Create image engine with custom font paths
    from labelable.templates.image_engine import ImageTemplateEngine

    font_paths = list(args.font_paths) + list(template.font_paths)
    if fonts_dir.exists():
        font_paths.insert(0, str(fonts_dir))
    engine = ImageTemplateEngine(custom_font_paths=font_paths if font_paths else None)

    # Render
    try:
        if args.format == "png":
            output = engine.render_preview(template, context, format="PNG")
        else:
            output = engine.render(template, context, output_format=args.format)
    except Exception as e:
        print(f"Error rendering template: {e}", file=sys.stderr)
        return 1

    # Write output
    try:
        with open(args.output, "wb") as f:
            f.write(output)
        print(f"Rendered to {args.output}")
    except OSError as e:
        print(f"Error writing output: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
