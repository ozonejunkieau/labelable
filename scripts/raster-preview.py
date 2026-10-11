"""Decode the exact bytes sent to a P-Touch printer back into a PNG.

This is ground truth for "what did the printer actually receive": it parses
the wire format rather than re-rendering the template, so it exercises the
converter, the Z/G/g framing and the PackBits encoder on the way through.

Usage:

    # From a template + field values, for either transport
    uv run python scripts/raster-preview.py templates/foo.yaml \\
        --json data.json --format ptouch_raw -o out.png

    # From bytes captured off the wire
    uv run python scripts/raster-preview.py --raw dump.bin -o out.png
    uv run python scripts/raster-preview.py --raw job.bin --format ptouch -o out.png

`ptouch_raw` is the ESP32 bridge format (bare 16-byte rows). `ptouch` is the
full PTCBP command stream used by USB and the bridge daemon.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from labelable.templates.converters.ptouch import (  # noqa: E402
    BYTES_PER_LINE,
    PRINT_HEAD_PIXELS,
    TAPE_SPECS,
)

CMD_COMPRESSION_TIFF = b"\x4d\x02"
CMD_COMPRESSION_NONE = b"\x4d\x00"
CMD_PRINT_AND_FEED = b"\x1a"


def packbits_decode(data: bytes) -> bytes:
    """Decode TIFF PackBits, the inverse of the converter's encoder."""
    out = bytearray()
    i = 0
    while i < len(data):
        n = data[i]
        i += 1
        if n < 128:  # literal run of n + 1 bytes
            out += data[i : i + n + 1]
            i += n + 1
        elif n > 128:  # next byte repeated 257 - n times
            out += bytes([data[i]]) * (257 - n)
            i += 1
        # n == 128 is a no-op
    return bytes(out)


def rows_from_raw(data: bytes) -> list[bytes]:
    """Split bare concatenated 16-byte rows (the ptouch_raw / bridge format)."""
    if len(data) % BYTES_PER_LINE:
        raise SystemExit(f"not 16-byte aligned: {len(data)} bytes ({len(data) % BYTES_PER_LINE} left over)")
    return [data[i : i + BYTES_PER_LINE] for i in range(0, len(data), BYTES_PER_LINE)]


def rows_from_ptouch(job: bytes) -> list[bytes]:
    """Parse a full PTCBP command stream, honouring Z / G / g line commands."""
    compressed = True
    if CMD_COMPRESSION_TIFF in job:
        body = job.split(CMD_COMPRESSION_TIFF, 1)[1]
    elif CMD_COMPRESSION_NONE in job:
        body = job.split(CMD_COMPRESSION_NONE, 1)[1]
        compressed = False
    else:
        raise SystemExit("no compression-mode command (M 0x02 / M 0x00) found - is this a ptouch job?")
    body = body.rsplit(CMD_PRINT_AND_FEED, 1)[0]

    rows: list[bytes] = []
    i = 0
    while i < len(body):
        cmd = body[i : i + 1]
        if cmd == b"Z":  # blank raster line
            rows.append(bytes(BYTES_PER_LINE))
            i += 1
        elif cmd in (b"G", b"g"):
            n = struct.unpack("<H", body[i + 1 : i + 3])[0]
            payload = body[i + 3 : i + 3 + n]
            line = packbits_decode(payload) if (cmd == b"G" and compressed) else payload
            rows.append(line.ljust(BYTES_PER_LINE, b"\x00")[:BYTES_PER_LINE])
            i += 3 + n
        else:
            raise SystemExit(f"unexpected byte {cmd!r} at offset {i} in raster body")
    return rows


def render(rows: list[bytes], tape_mm: int | None, scale: int, full_head: bool) -> Image.Image:
    """Draw rows as they land on tape: x = feed direction, y = print head pins."""
    w = max(len(rows), 1)
    im = Image.new("RGB", (w, PRINT_HEAD_PIXELS), "white")
    px = im.load()
    assert px is not None
    for x, r in enumerate(rows):
        for n in range(PRINT_HEAD_PIXELS):
            if r[n // 8] >> (7 - (n % 8)) & 1:
                px[x, n] = (0, 0, 0)

    top, height = 0, PRINT_HEAD_PIXELS
    if tape_mm is not None:
        printable, left, _ = TAPE_SPECS[tape_mm]
        # Shade pins outside the tape's printable window - anything drawn
        # there is silently discarded by the hardware.
        for x in range(w):
            for n in list(range(left)) + list(range(left + printable, PRINT_HEAD_PIXELS)):
                if px[x, n] == (255, 255, 255):
                    px[x, n] = (255, 225, 225)
        if not full_head:
            top, height = left, printable

    im = im.crop((0, top, w, top + height))
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
    return im


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("template", nargs="?", type=Path, help="Template YAML to render")
    src.add_argument("--raw", type=Path, help="Read printer bytes from a file instead")
    ap.add_argument("-d", "--data", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--json", type=Path, help="JSON file of field values")
    ap.add_argument("--format", choices=["ptouch_raw", "ptouch"], default="ptouch_raw")
    ap.add_argument("-o", "--output", type=Path, default=Path("raster.png"))
    ap.add_argument("--scale", type=int, default=3, help="Nearest-neighbour zoom (default 3)")
    ap.add_argument("--full-head", action="store_true", help="Show all 128 pins, not just the tape window")
    ap.add_argument("--font-path", action="append", dest="font_paths", default=[])
    args = ap.parse_args()

    tape_mm: int | None = None

    if args.raw:
        data = args.raw.read_bytes()
        rows = rows_from_ptouch(data) if args.format == "ptouch" else rows_from_raw(data)
    else:
        from labelable.models.template import TemplateConfig
        from labelable.templates.image_engine import ImageTemplateEngine

        template = TemplateConfig(**yaml.safe_load(args.template.read_text()))
        tape_mm = template.ptouch_tape_width_mm

        ctx: dict = json.loads(args.json.read_text()) if args.json else {}
        for pair in args.data:
            k, _, v = pair.partition("=")
            ctx[k] = v.replace("\\n", "\n")

        paths = args.font_paths or [str(args.template.parent / "fonts")]
        engine = ImageTemplateEngine(custom_font_paths=paths)
        data = engine.render(template, ctx, output_format=args.format)
        rows = rows_from_ptouch(data) if args.format == "ptouch" else rows_from_raw(data)

    if tape_mm is None:
        tape_mm = 9 if not args.full_head else None

    img = render(rows, tape_mm, args.scale, args.full_head)
    img.save(args.output)

    blank = sum(1 for r in rows if not any(r))
    print(f"format        : {args.format}")
    print(f"bytes on wire : {len(data)}")
    print(f"raster rows   : {len(rows)}  ({len(rows) / 180 * 25.4:.1f} mm of tape at 180 dpi)")
    print(f"blank rows    : {blank} ({blank / max(len(rows), 1):.0%})")
    if tape_mm:
        printable, left, _ = TAPE_SPECS[tape_mm]
        used = {n for r in rows for n in range(PRINT_HEAD_PIXELS) if r[n // 8] >> (7 - (n % 8)) & 1}
        span = f"{min(used)}..{max(used)}" if used else "(no ink)"
        print(f"tape window   : pins {left}..{left + printable - 1}   ink at pins {span}")
    print(f"wrote         : {args.output}  ({img.width}x{img.height} px at {args.scale}x)")


if __name__ == "__main__":
    main()
