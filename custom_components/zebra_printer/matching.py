"""Resolve a print_raw device_id given as a name to one printer.

Kept free of Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

from collections.abc import Mapping


def normalize_name(name: str) -> str:
    """Compare names the way entity IDs are built: lowercase, _ for space/-."""
    return name.lower().replace(" ", "_").replace("-", "_")


def match_by_title[K](titles: Mapping[K, str], device_id: str) -> K | None:
    """Pick the entry whose title matches ``device_id``.

    An exact (normalised) match wins. Otherwise a partial match is accepted
    only if exactly one title contains ``device_id``: picking the first of
    several sent jobs to the wrong printer (``"printer"`` matched both
    "Office Printer" and "Garage Printer").

    Raises:
        ValueError: If several titles partially match and none exactly.
    """
    wanted = normalize_name(device_id)
    normalized = {key: normalize_name(title) for key, title in titles.items()}

    exact = [key for key, title in normalized.items() if title == wanted]
    if exact:
        return exact[0]

    partial = [key for key, title in normalized.items() if wanted in title]
    if len(partial) > 1:
        names = ", ".join(sorted(titles[key] for key in partial))
        raise ValueError(f"'{device_id}' matches more than one Zebra printer ({names}); use the full name")
    return partial[0] if partial else None
