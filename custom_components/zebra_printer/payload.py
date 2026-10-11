"""Turn print_raw service data into the exact bytes sent to the printer.

Kept free of Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

import base64
import binascii


def payload_bytes(data: str | None, data_b64: str | None) -> bytes:
    """Return the printer payload from a print_raw service call.

    ``data_b64`` carries arbitrary bytes, which is what an EPL2 ``GW`` bitmap
    or a ZPL graphic needs: service data is JSON, so a binary job cannot
    survive as text. ``data`` is for human-written commands and is sent as
    UTF-8, which suits ZPL with ``^CI28``.

    Raises:
        ValueError: If neither field is given, or ``data_b64`` is not valid base64.
    """
    if data_b64 is not None:
        try:
            return base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError) as err:
            raise ValueError(f"data_b64 is not valid base64: {err}") from err
    if data is not None:
        return data.encode("utf-8")
    raise ValueError("print_raw needs either data or data_b64")
