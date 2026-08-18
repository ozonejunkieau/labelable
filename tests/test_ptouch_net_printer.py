"""Tests for the ESP32 network-bridge P-Touch transport (PTouchNetPrinter)."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labelable.models.printer import (
    HealthcheckConfig,
    PrinterConfig,
    PrinterType,
    PTouchBridgeConnection,
    USBConnection,
)
from labelable.printers import create_printer
from labelable.printers.base import PrinterError
from labelable.printers.ptouch_net import (
    RETRY_ATTEMPTS,
    TOKEN_ENV_VAR,
    PTouchNetPrinter,
)

IDLE_STATUS = {
    "state": "idle",
    "usb_connected": True,
    "jobs_printed": 0,
    "jobs_failed": 0,
    "media_width_mm": 9,
    "media_type": 3,
    "tape_color_id": 1,
    "errors": [],
}


def _status(**overrides) -> dict:
    body = dict(IDLE_STATUS)
    body.update(overrides)
    return body


def _make_config(**conn_kwargs) -> PrinterConfig:
    conn_kwargs.setdefault("host", "esp32-ptouch")
    conn_kwargs.setdefault("token", "tok")
    return PrinterConfig(
        name="netbridge",
        type=PrinterType.PTOUCH,
        connection=PTouchBridgeConnection(**conn_kwargs),
        healthcheck=HealthcheckConfig(),
    )


def _make_response(status: int, json_body=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    resp.status = status
    resp.json = AsyncMock(return_value=json_body if json_body is not None else {})
    resp.text = AsyncMock(return_value=text)
    return resp


class FakeSession:
    """Minimal aiohttp.ClientSession stand-in with scripted responses."""

    def __init__(self, get_responses=None, post_responses=None) -> None:
        self.closed = False
        self._get_responses = list(get_responses or [])
        self._post_responses = list(post_responses or [])
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict, dict]] = []

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if not self._get_responses:
            raise AssertionError(f"unexpected GET {url}")
        resp = self._get_responses.pop(0)
        if len(self._get_responses) == 0:
            # Last scripted response repeats, so completion polling can settle
            self._get_responses.append(resp)
        return resp

    def post(self, url, json=None, headers=None, **kwargs):
        self.post_calls.append((url, json or {}, headers or {}))
        if not self._post_responses:
            raise AssertionError(f"unexpected POST {url}")
        return self._post_responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def _printer(session: FakeSession, **conn_kwargs) -> PTouchNetPrinter:
    p = PTouchNetPrinter(_make_config(**conn_kwargs))
    p._session = session  # type: ignore[assignment]
    p._connected = True
    return p


# ---------------------------------------------------------------------------
# Factory / wiring
# ---------------------------------------------------------------------------


class TestFactoryAndFormat:
    def test_create_printer_dispatches_on_connection(self):
        printer = create_printer(_make_config())
        assert isinstance(printer, PTouchNetPrinter)

    def test_output_format_is_ptouch_raw(self):
        assert create_printer(_make_config()).output_format == "ptouch_raw"

    def test_usb_ptouch_still_uses_usb_transport(self):
        """The new connection type must not disturb the existing USB path."""
        from labelable.printers.ptouch import PTouchPrinter

        config = PrinterConfig(
            name="usb",
            type=PrinterType.PTOUCH,
            connection=USBConnection(),
            healthcheck=HealthcheckConfig(),
        )
        printer = create_printer(config)
        assert isinstance(printer, PTouchPrinter)
        assert printer.output_format == "ptouch"

    def test_rejects_wrong_connection_type(self):
        config = PrinterConfig(
            name="usb",
            type=PrinterType.PTOUCH,
            connection=USBConnection(),
            healthcheck=HealthcheckConfig(),
        )
        with pytest.raises(PrinterError, match="requires PTouchBridgeConnection"):
            PTouchNetPrinter(config)

    def test_base_url_uses_port(self):
        printer = PTouchNetPrinter(_make_config(host="1.2.3.4", port=8080))
        assert printer.base_url == "http://1.2.3.4:8080"

    def test_default_port_is_80(self):
        assert PTouchBridgeConnection(host="h").port == 80

    def test_token_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv(TOKEN_ENV_VAR, "env-token")
        printer = PTouchNetPrinter(_make_config(token=None))
        assert printer._token == "env-token"

    def test_config_token_wins_over_env(self, monkeypatch):
        monkeypatch.setenv(TOKEN_ENV_VAR, "env-token")
        printer = PTouchNetPrinter(_make_config(token="cfg"))
        assert printer._token == "cfg"


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_session_and_polls_status(self):
        session = FakeSession(get_responses=[_make_response(200, _status())])
        printer = PTouchNetPrinter(_make_config())
        with patch("labelable.printers.ptouch_net.aiohttp.ClientSession", return_value=session):
            await printer.connect()
        assert printer.is_connected
        assert session.get_calls == ["http://esp32-ptouch:80/status"]

    @pytest.mark.asyncio
    async def test_connect_raises_when_device_unreachable(self):
        session = FakeSession(get_responses=[_make_response(500, text="boom")])
        printer = PTouchNetPrinter(_make_config())
        with patch("labelable.printers.ptouch_net.aiohttp.ClientSession", return_value=session):
            with pytest.raises(ConnectionError, match="did not respond"):
                await printer.connect()
        assert not printer.is_connected
        assert session.closed

    @pytest.mark.asyncio
    async def test_disconnect_closes_session(self):
        session = FakeSession()
        printer = _printer(session)
        await printer.disconnect()
        assert session.closed
        assert not printer.is_connected


# ---------------------------------------------------------------------------
# Status shape variance and readiness
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_ready_status_is_online(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status())]))
        assert await printer.is_online() is True
        assert printer.errors == []
        assert printer.tape_colour == "White"
        assert printer.media_kind == "Non-laminated"
        assert printer.model_info == "P-Touch bridge (9mm Non-laminated)"

    @pytest.mark.asyncio
    async def test_offline_when_usb_disconnected(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(usb_connected=False))]))
        assert await printer.is_online() is False

    @pytest.mark.asyncio
    async def test_offline_while_printing(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(state="printing"))]))
        assert await printer.is_online() is False

    @pytest.mark.asyncio
    async def test_offline_with_errors(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(errors=["no_media"]))]))
        assert await printer.is_online() is False
        assert printer.errors == ["no_media"]

    @pytest.mark.asyncio
    async def test_offline_when_status_request_fails(self):
        printer = _printer(FakeSession(get_responses=[_make_response(503, text="nope")]))
        assert await printer.is_online() is False
        assert not printer.is_connected

    @pytest.mark.asyncio
    async def test_null_media_width_and_absent_keys(self):
        """Never-polled device: null width, media_type/tape_color_id absent."""
        body = {
            "state": "idle",
            "usb_connected": True,
            "jobs_printed": 0,
            "jobs_failed": 0,
            "media_width_mm": None,
            "errors": [],
        }
        printer = _printer(FakeSession(get_responses=[_make_response(200, body)]))
        assert await printer.is_online() is True
        assert printer.media_kind is None
        assert printer.tape_colour is None
        assert printer.model_info == "P-Touch bridge (no media read)"
        assert await printer.get_media_size() is None

    @pytest.mark.asyncio
    async def test_get_media_size(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(media_width_mm=12))]))
        await printer.is_online()
        assert await printer.get_media_size() == (12.0, 0.0)

    @pytest.mark.asyncio
    async def test_unknown_media_type_is_labelled(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(media_type=99))]))
        await printer.is_online()
        assert printer.media_kind == "Unknown (99)"

    @pytest.mark.asyncio
    async def test_non_dict_body_is_a_failure(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, ["not", "a", "dict"])]))
        assert await printer.is_online() is False


class TestCheckMediaWidth:
    @pytest.mark.asyncio
    async def test_matching_width_passes(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(media_width_mm=9))]))
        await printer.check_media_width(9)

    @pytest.mark.asyncio
    async def test_mismatch_raises_with_ptouch_wording(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(media_width_mm=12))]))
        with pytest.raises(PrinterError, match=r"Media width mismatch: printer has 12mm tape loaded"):
            await printer.check_media_width(9)

    @pytest.mark.asyncio
    async def test_errors_raise(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(errors=["cover_open"]))]))
        with pytest.raises(PrinterError, match="Printer has errors: cover_open"):
            await printer.check_media_width(9)

    @pytest.mark.asyncio
    async def test_unreadable_status_raises(self):
        printer = _printer(FakeSession(get_responses=[_make_response(500)]))
        with pytest.raises(PrinterError, match="Failed to read printer status"):
            await printer.check_media_width(9)

    @pytest.mark.asyncio
    async def test_null_width_raises(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(media_width_mm=None))]))
        with pytest.raises(PrinterError, match="no media width reported"):
            await printer.check_media_width(9)


# ---------------------------------------------------------------------------
# print_raw
# ---------------------------------------------------------------------------

ROWS = b"\x00" * 16 + b"\xff" * 16  # two 16-byte rows


class TestPrintGuards:
    @pytest.mark.asyncio
    async def test_misaligned_payload_rejected(self):
        printer = _printer(FakeSession())
        with pytest.raises(PrinterError, match="not 16-byte aligned"):
            await printer.print_raw(b"\x00" * 17)

    @pytest.mark.asyncio
    async def test_empty_payload_rejected(self):
        printer = _printer(FakeSession())
        with pytest.raises(PrinterError, match="empty raster job"):
            await printer.print_raw(b"")

    @pytest.mark.asyncio
    async def test_missing_token_rejected(self, monkeypatch):
        monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
        printer = _printer(FakeSession(), token=None)
        with pytest.raises(PrinterError, match="No bearer token"):
            await printer.print_raw(ROWS)

    @pytest.mark.asyncio
    async def test_unknown_media_width_rejected(self):
        session = FakeSession(get_responses=[_make_response(200, _status(media_width_mm=None))])
        printer = _printer(session)
        with pytest.raises(PrinterError, match="has not read the loaded tape"):
            await printer.print_raw(ROWS)
        assert session.post_calls == []


class TestPrintPayload:
    @pytest.mark.asyncio
    async def test_payload_shape_and_auth(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(jobs_printed=1)),
            ],
            post_responses=[_make_response(202, {"result": "accepted"})],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            await printer.print_raw(ROWS)

        url, payload, headers = session.post_calls[0]
        assert url == "http://esp32-ptouch:80/print"
        assert headers["Authorization"] == "Bearer tok"
        assert payload["media_width_mm"] == 9
        assert payload["media_type"] == 3
        assert payload["raster_row_count"] == 2
        assert payload["force"] is False
        assert base64.b64decode(payload["raster_rows_b64"]) == ROWS

    @pytest.mark.asyncio
    async def test_live_status_media_beats_config(self):
        """A stale config media_type must not cause a self-inflicted 422."""
        session = FakeSession(
            get_responses=[
                _make_response(200, _status(media_width_mm=12, media_type=1)),
                _make_response(200, _status(media_width_mm=12, media_type=1, jobs_printed=1)),
            ],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session, media_type=3, force=True)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            await printer.print_raw(ROWS)

        _url, payload, _headers = session.post_calls[0]
        assert payload["media_width_mm"] == 12
        assert payload["media_type"] == 1
        assert payload["force"] is True

    @pytest.mark.asyncio
    async def test_config_media_type_used_when_absent_from_status(self):
        body = _status()
        del body["media_type"]
        session = FakeSession(
            get_responses=[
                _make_response(200, body),
                _make_response(200, _status(jobs_printed=1)),
            ],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session, media_type=17)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            await printer.print_raw(ROWS)
        assert session.post_calls[0][1]["media_type"] == 17


class TestPrintErrorMapping:
    @pytest.mark.parametrize(
        "code,reason,match",
        [
            (400, "missing required fields", "HTTP 400"),
            (401, "missing or invalid bearer token", "HTTP 401"),
            (413, "job exceeds configured buffer limit", "HTTP 413"),
            (500, "out of memory", "HTTP 500"),
        ],
    )
    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self, code, reason, match):
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[_make_response(code, {"error": reason})],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PrinterError, match=match):
                await printer.print_raw(ROWS)
        # Exactly one attempt - no retry
        assert len(session.post_calls) == 1

    @pytest.mark.asyncio
    async def test_422_is_a_tape_mismatch(self):
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[_make_response(422, {"error": "declared media_width_mm does not match loaded tape"})],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PrinterError, match="Media width mismatch"):
                await printer.print_raw(ROWS)
        assert len(session.post_calls) == 1

    @pytest.mark.parametrize("code", [409, 503])
    @pytest.mark.asyncio
    async def test_retryable_gives_up_after_bounded_attempts(self, code):
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[_make_response(code, {"error": "printer busy"}) for _ in range(RETRY_ATTEMPTS)],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PrinterError, match=f"unavailable after {RETRY_ATTEMPTS} attempts"):
                await printer.print_raw(ROWS)
        assert len(session.post_calls) == RETRY_ATTEMPTS

    @pytest.mark.asyncio
    async def test_409_then_success(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(jobs_printed=1)),
            ],
            post_responses=[
                _make_response(409, {"error": "printer busy"}),
                _make_response(409, {"error": "printer busy"}),
                _make_response(202, {"result": "accepted"}),
            ],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            await printer.print_raw(ROWS)
        assert len(session.post_calls) == 3

    @pytest.mark.asyncio
    async def test_error_reason_falls_back_to_text(self):
        resp = _make_response(400, text="plain text failure")
        resp.json = AsyncMock(side_effect=ValueError("not json"))
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[resp],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PrinterError, match="plain text failure"):
                await printer.print_raw(ROWS)


class TestCompletionPolling:
    @pytest.mark.asyncio
    async def test_waits_for_jobs_printed_to_increment(self):
        """202 is admission only; we must not return before the job lands."""
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),  # pre-submit snapshot
                _make_response(200, _status(state="printing")),
                _make_response(200, _status(state="printing")),
                _make_response(200, _status(state="idle", jobs_printed=1)),
            ],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            await printer.print_raw(ROWS)
        # 1 pre-submit + 3 completion polls
        assert len(session.get_calls) == 4

    @pytest.mark.asyncio
    async def test_jobs_failed_increment_is_a_failure(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(state="error", jobs_failed=1, errors=["cutter_jam"])),
            ],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PrinterError, match="failed job: cutter_jam"):
                await printer.print_raw(ROWS)

    @pytest.mark.asyncio
    async def test_errors_without_counter_change_is_a_failure(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(state="error", errors=["no_media"])),
            ],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PrinterError, match="reported errors: no_media"):
                await printer.print_raw(ROWS)

    @pytest.mark.asyncio
    async def test_timeout_when_counter_never_moves(self):
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session)
        # asyncio.sleep is patched out but the loop deadline uses the real
        # clock, so drive it with a monotonically advancing fake time.
        ticks = iter(range(0, 10_000))
        loop = MagicMock()
        loop.time = lambda: float(next(ticks))
        with (
            patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()),
            patch("labelable.printers.ptouch_net.asyncio.get_event_loop", return_value=loop),
        ):
            with pytest.raises(PrinterError, match="did not complete the job"):
                await printer.print_raw(ROWS)

    @pytest.mark.asyncio
    async def test_second_copy_uses_fresh_counter_snapshot(self):
        """print_with_quantity loops print_raw; each copy must wait its turn."""
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=2)),
            ],
            post_responses=[_make_response(202), _make_response(202)],
        )
        printer = _printer(session)
        with patch("labelable.printers.ptouch_net.asyncio.sleep", new=AsyncMock()):
            await printer.print_with_quantity(ROWS, 2)
        assert len(session.post_calls) == 2
