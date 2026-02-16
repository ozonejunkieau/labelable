"""CLI diagnostic tool for Brother P-Touch printers."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from labelable.models.printer import HealthcheckConfig, PrinterConfig, PrinterType, USBConnection
from labelable.printers.ptouch import PTouchPrinter
from labelable.printers.ptouch_protocol import CMD_STATUS_REQUEST, STATUS_RESPONSE_LENGTH, parse_status


async def _status(connection: USBConnection) -> int:
    """Connect to a P-Touch printer and display its status."""
    config = PrinterConfig(
        name="ptouch-cli",
        type=PrinterType.PTOUCH,
        connection=connection,
        healthcheck=HealthcheckConfig(),
    )
    printer = PTouchPrinter(config)

    try:
        print(f"Connecting to USB {connection.vendor_id:04x}:{connection.product_id:04x}...")
        await printer.connect()

        print("Requesting status...")
        await printer._send(CMD_STATUS_REQUEST)
        response = await printer._recv(size=STATUS_RESPONSE_LENGTH, timeout=5.0)

        if len(response) != STATUS_RESPONSE_LENGTH:
            print(f"Error: expected {STATUS_RESPONSE_LENGTH} bytes, got {len(response)}", file=sys.stderr)
            if response:
                print(f"Raw: {response.hex(' ')}", file=sys.stderr)
            return 1

        status = parse_status(response)

        print()
        print(f"  Status type:  {status.status_type.name}")
        print(f"  Media width:  {status.media_width_mm} mm")
        print(f"  Media kind:   {status.media_kind.name.replace('_', ' ').title()}")
        print(f"  Tape colour:  {status.tape_colour}")
        print(f"  Text colour:  {status.text_colour}")

        if status.has_errors:
            print(f"  Errors:       {', '.join(status.error_descriptions)}")
        else:
            print("  Errors:       None")

        print()
        print(f"  Raw ({len(response)} bytes): {response.hex(' ')}")
        return 0

    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        await printer.disconnect()


async def _print(
    template_path: Path,
    context: dict[str, str],
    connection: USBConnection,
    preview_path: Path | None = None,
    dump_path: Path | None = None,
) -> int:
    """Render a template and print or save output."""
    from labelable.models.template import EngineType, TemplateConfig
    from labelable.templates.image_engine import ImageTemplateEngine

    # Load template YAML
    try:
        with open(template_path) as f:
            template_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error parsing template YAML: {e}", file=sys.stderr)
        return 1

    template_data["name"] = template_path.stem

    try:
        template = TemplateConfig(**template_data)
    except Exception as e:
        print(f"Error loading template: {e}", file=sys.stderr)
        return 1

    if template.engine != EngineType.IMAGE:
        print("Error: P-Touch printing requires engine: image", file=sys.stderr)
        return 1

    # Build font paths
    font_paths = list(template.font_paths)
    fonts_dir = template_path.parent / "fonts"
    if fonts_dir.exists():
        font_paths.insert(0, str(fonts_dir))

    engine = ImageTemplateEngine(custom_font_paths=font_paths if font_paths else None)

    try:
        if preview_path is not None:
            output = engine.render_preview(template, context, format="PNG")
            with open(preview_path, "wb") as f:
                f.write(output)
            print(f"Preview saved to {preview_path}")
            return 0

        # Render to ptouch raster format
        output = engine.render(template, context, output_format="ptouch")

        if dump_path is not None:
            with open(dump_path, "wb") as f:
                f.write(output)
            print(f"Raw data saved to {dump_path} ({len(output)} bytes)")
            return 0

        # Send to printer
        config = PrinterConfig(
            name="ptouch-cli",
            type=PrinterType.PTOUCH,
            connection=connection,
            healthcheck=HealthcheckConfig(),
        )
        printer = PTouchPrinter(config)

        print(f"Connecting to USB {connection.vendor_id:04x}:{connection.product_id:04x}...")
        await printer.connect()

        try:
            print(f"Sending {len(output)} bytes...")
            await printer.print_raw(output)
            print("Print job sent.")
            return 0
        finally:
            await printer.disconnect()

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic tool for Brother P-Touch printers.",
        prog="labelable-ptouch",
    )
    sub = parser.add_subparsers(dest="command")

    # status subcommand
    status_parser = sub.add_parser("status", help="Query printer status via USB")
    status_parser.add_argument(
        "--vid", type=lambda x: int(x, 0), default=0x04F9, help="USB vendor ID (default: 0x04f9)"
    )
    status_parser.add_argument(
        "--pid", type=lambda x: int(x, 0), default=0x20AF, help="USB product ID (default: 0x20af)"
    )

    # print subcommand
    print_parser = sub.add_parser("print", help="Render and print a template")
    print_parser.add_argument("template", type=Path, help="Path to template YAML file")
    print_parser.add_argument(
        "-d",
        "--data",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Field value (can be specified multiple times)",
    )
    print_parser.add_argument(
        "--json",
        type=Path,
        dest="json_file",
        help="JSON file with field values",
    )
    print_parser.add_argument(
        "--preview",
        type=Path,
        metavar="FILE",
        help="Save cropped PNG preview instead of printing",
    )
    print_parser.add_argument(
        "--dump",
        type=Path,
        metavar="FILE",
        help="Save raw raster bytes to file for debugging",
    )
    print_parser.add_argument(
        "--vid",
        type=lambda x: int(x, 0),
        default=0x04F9,
        help="USB vendor ID (default: 0x04f9)",
    )
    print_parser.add_argument(
        "--pid",
        type=lambda x: int(x, 0),
        default=0x20AF,
        help="USB product ID (default: 0x20af)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "status":
        connection = USBConnection(vendor_id=args.vid, product_id=args.pid)
        return asyncio.run(_status(connection))

    if args.command == "print":
        if not args.template.exists():
            print(f"Error: Template not found: {args.template}", file=sys.stderr)
            return 1

        # Parse field values
        context: dict[str, str] = {}
        if args.json_file:
            if not args.json_file.exists():
                print(f"Error: JSON file not found: {args.json_file}", file=sys.stderr)
                return 1
            with open(args.json_file) as f:
                context.update(json.load(f))

        for item in args.data:
            if "=" not in item:
                print(f"Error: Invalid data format '{item}'. Use KEY=VALUE", file=sys.stderr)
                return 1
            key, value = item.split("=", 1)
            context[key] = value

        connection = USBConnection(vendor_id=args.vid, product_id=args.pid)
        return asyncio.run(
            _print(
                args.template,
                context,
                connection,
                preview_path=args.preview,
                dump_path=args.dump,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
