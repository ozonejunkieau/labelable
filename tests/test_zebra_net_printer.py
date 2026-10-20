"""Tests for the ESP32 network-bridge Zebra transport (ZebraNetPrinter)."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from pydantic import ValidationError

from labelable.models.printer import (
    HealthcheckConfig,
    PrinterConfig,
    PrinterType,
    TCPConnection,
    ZebraBridgeConnection,
)
from labelable.models.template import EngineType, LabelDimensions, TemplateConfig
from labelable.printers import create_printer, dpi_mismatch_warning
from labelable.printers.base import PrinterError
from labelable.printers.http_bridge import POST_TIMEOUT, RETRY_ATTEMPTS
from labelable.printers.zebra_net import JOIN_LIMIT_BYTES, TOKEN_ENV_VAR, ZebraNetPrinter

# Captured from a real zebra-bridge with a GX420t attached
IDLE_STATUS = {
    "state": "idle",
    "usb_connected": True,
    "jobs_printed": 0,
    "jobs_failed": 0,
    "last_failure": None,
    "printer": {
        "model": "GX420t-200dpi",
        "firmware": "V56.17.5Z",
        "dots_per_mm": 8,
        "dpi": 203,
        "usb_pid": 133,
    },
    "media": {"thermal_transfer": True, "print_mode": "tear_off", "label_length_dots": 188},
    "formats_in_buffer": 0,
    "labels_remaining": 0,
    "buffer_full": False,
    "under_temperature": False,
    "errors": [],
}

ZPL = b"^XA^FO50,50^A0N,40,40^FDzebra-bridge^FS^XZ"


def _status(**overrides) -> dict:
    body = dict(IDLE_STATUS)
    body.update(overrides)
    return body


def _make_config(**conn_kwargs) -> PrinterConfig:
    conn_kwargs.setdefault("host", "esp32-zebra")
    conn_kwargs.setdefault("token", "tok")
    return PrinterConfig(
        name="zebra",
        type=PrinterType.ZPL,
        connection=ZebraBridgeConnection(**conn_kwargs),
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
        self.post_calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if not self._get_responses:
            raise AssertionError(f"unexpected GET {url}")
        resp = self._get_responses.pop(0)
        if len(self._get_responses) == 0:
            # Last scripted response repeats, so completion polling can settle
            self._get_responses.append(resp)
        return resp

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if not self._post_responses:
            raise AssertionError(f"unexpected POST {url}")
        resp = self._post_responses.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        return resp

    async def close(self) -> None:
        self.closed = True


def _printer(session: FakeSession, **conn_kwargs) -> ZebraNetPrinter:
    p = ZebraNetPrinter(_make_config(**conn_kwargs))
    p._session = session  # type: ignore[assignment]
    p._connected = True
    return p


def _no_sleep():
    return patch("labelable.printers.http_bridge.asyncio.sleep", new=AsyncMock())


# ---------------------------------------------------------------------------
# Config / factory
# ---------------------------------------------------------------------------


class TestConfig:
    def test_create_printer_dispatches_on_connection(self):
        assert isinstance(create_printer(_make_config()), ZebraNetPrinter)

    def test_output_format_is_zpl(self):
        assert create_printer(_make_config()).output_format == "zpl"

    def test_defaults(self):
        conn = ZebraBridgeConnection(host="h")
        assert conn.port == 80
        assert conn.completion_timeout_s == 330.0

    def test_rejects_non_zpl_type(self):
        with pytest.raises(ValidationError, match="only supports type 'zpl'"):
            PrinterConfig(name="z", type=PrinterType.EPL2, connection=ZebraBridgeConnection(host="h"))

    def test_parses_from_dict(self):
        config = PrinterConfig.model_validate(
            {"name": "z", "type": "zpl", "connection": {"type": "zebra_bridge", "host": "10.0.0.5"}}
        )
        assert isinstance(config.connection, ZebraBridgeConnection)

    def test_rejects_wrong_connection_type(self):
        config = PrinterConfig(name="z", type=PrinterType.ZPL, connection=TCPConnection(host="h"))
        with pytest.raises(PrinterError, match="requires ZebraBridgeConnection"):
            ZebraNetPrinter(config)

    def test_token_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv(TOKEN_ENV_VAR, "env-token")
        assert ZebraNetPrinter(_make_config(token=None))._token == "env-token"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_idle_is_online_and_identifies_printer(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status())]))
        assert await printer.is_online() is True
        assert printer.model_info == "GX420t-200dpi V56.17.5Z (203 dpi)"
        assert printer.dpi == 203

    @pytest.mark.asyncio
    async def test_error_state_is_still_online(self):
        """state=error describes the last job; it must not wedge the queue."""
        status = _status(state="error", jobs_failed=1, last_failure="fault: paper_out")
        printer = _printer(FakeSession(get_responses=[_make_response(200, status)]))
        assert await printer.is_online() is True

    @pytest.mark.asyncio
    async def test_offline_while_printing(self):
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(state="printing"))]))
        assert await printer.is_online() is False

    @pytest.mark.asyncio
    async def test_offline_with_fault(self):
        status = _status(errors=["head_open", "paused"])
        printer = _printer(FakeSession(get_responses=[_make_response(200, status)]))
        assert await printer.is_online() is False
        assert printer.errors == ["head_open", "paused"]

    @pytest.mark.asyncio
    async def test_offline_when_not_answering_hs(self):
        """media null while connected = printer silent on ~HS (EPL mode)."""
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(media=None))]))
        assert await printer.is_online() is False

    @pytest.mark.asyncio
    async def test_offline_when_usb_disconnected(self):
        status = _status(usb_connected=False, printer=None, media=None)
        printer = _printer(FakeSession(get_responses=[_make_response(200, status)]))
        assert await printer.is_online() is False
        assert printer.dpi is None
        assert printer.model_info == "Zebra bridge (printer not identified)"

    @pytest.mark.asyncio
    async def test_offline_when_device_unreachable(self):
        printer = _printer(FakeSession(get_responses=[_make_response(500)]))
        assert await printer.is_online() is False
        assert printer.is_connected is False


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


class TestPrint:
    @pytest.mark.asyncio
    async def test_posts_raw_zpl_and_waits_for_counter(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(state="printing")),
                _make_response(200, _status(jobs_printed=1)),
            ],
            post_responses=[_make_response(202, {"result": "accepted"})],
        )
        printer = _printer(session)
        with _no_sleep():
            await printer.print_raw(ZPL)

        url, kwargs = session.post_calls[0]
        assert url == "http://esp32-zebra:80/print"
        assert kwargs["data"] == ZPL
        assert kwargs["headers"]["Content-Type"] == "application/zpl"
        assert kwargs["headers"]["Authorization"] == "Bearer tok"
        assert "json" not in kwargs

    @pytest.mark.asyncio
    async def test_empty_job_rejected(self):
        printer = _printer(FakeSession())
        with pytest.raises(PrinterError, match="empty"):
            await printer.print_raw(b"")

    @pytest.mark.asyncio
    async def test_missing_token_rejected(self, monkeypatch):
        monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
        printer = _printer(FakeSession(), token=None)
        with pytest.raises(PrinterError, match=TOKEN_ENV_VAR):
            await printer.print_raw(ZPL)

    @pytest.mark.asyncio
    async def test_failed_job_reports_device_reason(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(state="error", jobs_failed=1, last_failure="fault: paper_out")),
            ],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session)
        with _no_sleep():
            with pytest.raises(PrinterError, match="failed job: fault: paper_out"):
                await printer.print_raw(ZPL)

    @pytest.mark.asyncio
    async def test_not_ready_fails_fast_with_faults(self):
        session = FakeSession(
            get_responses=[_make_response(200, _status(errors=["paused"]))],
            post_responses=[_make_response(503, {"error": "printer not ready"})],
        )
        printer = _printer(session)
        with _no_sleep():
            with pytest.raises(PrinterError, match="not ready: paused"):
                await printer.print_raw(ZPL)
        assert len(session.post_calls) == 1

    @pytest.mark.asyncio
    async def test_not_ready_without_faults_suggests_zpl_mode(self):
        session = FakeSession(
            get_responses=[_make_response(200, _status(media=None))],
            post_responses=[_make_response(503, {"error": "printer not ready"})],
        )
        printer = _printer(session)
        with _no_sleep():
            with pytest.raises(PrinterError, match="ZPL mode"):
                await printer.print_raw(ZPL)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "reason"),
        [(409, "printer busy"), (503, "printer not connected")],
    )
    async def test_retryable_statuses_are_retried(self, code, reason):
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[_make_response(code, {"error": reason}) for _ in range(RETRY_ATTEMPTS)],
        )
        printer = _printer(session)
        with _no_sleep():
            with pytest.raises(PrinterError, match=f"unavailable after {RETRY_ATTEMPTS} attempts"):
                await printer.print_raw(ZPL)
        assert len(session.post_calls) == RETRY_ATTEMPTS

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [400, 401, 413, 415, 500])
    async def test_other_errors_fail_immediately(self, code):
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[_make_response(code, {"error": "nope"})],
        )
        printer = _printer(session)
        with _no_sleep():
            with pytest.raises(PrinterError, match=f"HTTP {code}"):
                await printer.print_raw(ZPL)
        assert len(session.post_calls) == 1

    @pytest.mark.asyncio
    async def test_timeout_uses_configured_completion_timeout(self):
        session = FakeSession(
            get_responses=[_make_response(200, _status(state="printing"))],
            post_responses=[_make_response(202)],
        )
        printer = _printer(session, completion_timeout_s=5)
        ticks = iter(range(0, 10_000))
        loop = MagicMock()
        loop.time = lambda: float(next(ticks))
        with (
            _no_sleep(),
            patch("labelable.printers.http_bridge.asyncio.get_event_loop", return_value=loop),
        ):
            with pytest.raises(PrinterError, match="within 5s"):
                await printer.print_raw(ZPL)


class TestQuantity:
    def _session(self) -> FakeSession:
        return FakeSession(
            get_responses=[_make_response(200, _status()), _make_response(200, _status(jobs_printed=1))],
            post_responses=[_make_response(202)],
        )

    @pytest.mark.asyncio
    async def test_copies_joined_into_one_job(self):
        session = self._session()
        with _no_sleep():
            await _printer(session).print_with_quantity(ZPL, 3)
        assert len(session.post_calls) == 1
        assert session.post_calls[0][1]["data"] == ZPL * 3

    @pytest.mark.asyncio
    async def test_native_pq_sent_once_unmodified(self):
        session = self._session()
        data = b"^XA^PQ3^FDx^FS^XZ"
        with _no_sleep():
            await _printer(session).print_with_quantity(data, 3)
        assert session.post_calls[0][1]["data"] == data

    @pytest.mark.asyncio
    async def test_too_large_to_join_loops(self):
        big = b"^XA" + b"x" * (JOIN_LIMIT_BYTES // 2) + b"^XZ"
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=2)),
            ],
            post_responses=[_make_response(202), _make_response(202)],
        )
        with _no_sleep():
            await _printer(session).print_with_quantity(big, 2)
        assert len(session.post_calls) == 2
        assert session.post_calls[0][1]["data"] == big


# ---------------------------------------------------------------------------
# DPI mismatch warning
# ---------------------------------------------------------------------------


def _template(dpi: int, engine: EngineType = EngineType.IMAGE) -> TemplateConfig:
    return TemplateConfig(
        name="t",
        engine=engine,
        dpi=dpi,
        dimensions=LabelDimensions(width_mm=50, height_mm=25),
        template="^XA^XZ",
        supported_printers=["zebra"],
    )


class TestDpiWarning:
    async def _printer_at(self, dpi: int) -> ZebraNetPrinter:
        printer_status = {**IDLE_STATUS["printer"], "dpi": dpi}  # type: ignore[dict-item]
        printer = _printer(FakeSession(get_responses=[_make_response(200, _status(printer=printer_status))]))
        await printer.is_online()
        return printer

    @pytest.mark.asyncio
    async def test_warns_on_mismatch(self):
        printer = await self._printer_at(300)
        warning = dpi_mismatch_warning(printer, _template(203))
        assert warning is not None
        assert "68%" in warning

    @pytest.mark.asyncio
    async def test_silent_when_matching(self):
        printer = await self._printer_at(203)
        assert dpi_mismatch_warning(printer, _template(203)) is None

    @pytest.mark.asyncio
    async def test_silent_for_jinja_templates(self):
        printer = await self._printer_at(300)
        assert dpi_mismatch_warning(printer, _template(203, EngineType.JINJA)) is None

    def test_silent_when_dpi_unknown(self):
        printer = ZebraNetPrinter(_make_config())
        assert dpi_mismatch_warning(printer, _template(203)) is None


# ---------------------------------------------------------------------------
# Robustness (audit follow-ups)
# ---------------------------------------------------------------------------


class TestRobustness:
    @pytest.mark.asyncio
    async def test_busy_with_own_job_reads_online_without_polling(self):
        session = FakeSession(get_responses=[_make_response(200, _status(state="printing"))])
        printer = _printer(session)
        printer._printing = True
        assert await printer.is_online() is True
        assert session.get_calls == []

    @pytest.mark.asyncio
    async def test_connection_error_after_admission_is_not_resent(self):
        """A dropped connection after the device took the job must not print it twice."""
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),  # before submit
                _make_response(200, _status(state="printing")),  # admission check
                _make_response(200, _status(jobs_printed=1)),
            ],
            post_responses=[aiohttp.ServerDisconnectedError()],
        )
        with _no_sleep():
            await _printer(session).print_raw(ZPL)
        assert len(session.post_calls) == 1

    @pytest.mark.asyncio
    async def test_connection_error_before_admission_is_retried(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),  # before submit
                _make_response(200, _status()),  # admission check: nothing happened
                _make_response(200, _status(jobs_printed=1)),
            ],
            post_responses=[aiohttp.ClientConnectionError("reset"), _make_response(202)],
        )
        with _no_sleep():
            await _printer(session).print_raw(ZPL)
        assert len(session.post_calls) == 2

    @pytest.mark.asyncio
    async def test_connection_errors_exhaust_retries(self):
        session = FakeSession(
            get_responses=[_make_response(200, _status())],
            post_responses=[TimeoutError() for _ in range(RETRY_ATTEMPTS)],
        )
        with _no_sleep():
            with pytest.raises(PrinterError, match="unreachable after"):
                await _printer(session).print_raw(ZPL)

    @pytest.mark.asyncio
    async def test_post_has_no_total_timeout(self):
        """A large job's upload must not be cut off by the 10 s request timeout."""
        session = FakeSession(
            get_responses=[_make_response(200, _status()), _make_response(200, _status(jobs_printed=1))],
            post_responses=[_make_response(202)],
        )
        with _no_sleep():
            await _printer(session).print_raw(ZPL)
        timeout = session.post_calls[0][1]["timeout"]
        assert timeout is POST_TIMEOUT
        assert timeout.total is None and timeout.sock_read

    @pytest.mark.asyncio
    async def test_fault_after_job_printed_is_success(self):
        """Paper out on the *next* label must not fail a job that printed."""
        session = FakeSession(
            get_responses=[
                _make_response(200, _status()),
                _make_response(200, _status(jobs_printed=1, errors=["paper_out"])),
            ],
            post_responses=[_make_response(202)],
        )
        with _no_sleep():
            await _printer(session).print_raw(ZPL)

    @pytest.mark.asyncio
    async def test_zero_quantity_prints_nothing(self):
        session = FakeSession()
        await _printer(session).print_with_quantity(ZPL, 0)
        assert session.post_calls == []


# ---------------------------------------------------------------------------
# GET /config details
# ---------------------------------------------------------------------------

# Abridged ^HH reply captured from the same GX420t via GET /config
CONFIG_RAW = (
    "  09.0                DARKNESS          \r\n"
    "  2 IPS               PRINT SPEED       \r\n"
    "  +000                TEAR OFF          \r\n"
    "  CUTTER              PRINT MODE        \r\n"
    "  GAP/NOTCH           MEDIA TYPE        \r\n"
    "  WEB                 SENSOR TYPE       \r\n"
    "  THERMAL-TRANS.      PRINT METHOD      \r\n"
    "  581                 PRINT WIDTH       \r\n"
    "  0188                LABEL LENGTH      \r\n"
    "  39.0IN   989MM      MAXIMUM LENGTH    \r\n"
    "  DEFAULT             BACKFEED          \r\n"
    "  +000                LABEL TOP         \r\n"
    "  +0000               LEFT POSITION     \r\n"
    "  LINER/TAG FULL      CUTTER TYPE       \r\n"
    "  18,249 IN           HEAD USAGE        \r\n"
    "  31J10330054         SERIAL NUMBER     \r\n"
    "  2104k............R: RAM               \r\n"
)


class TestConfigDetails:
    def test_parse_config_label(self):
        from labelable.printers.zebra_net import parse_config_label

        lines = parse_config_label(CONFIG_RAW)
        assert lines["PRINT WIDTH"] == "581"
        assert lines["PRINT MODE"] == "CUTTER"
        # A value with its own double space keeps both parts
        assert lines["MAXIMUM LENGTH"] == "39.0IN 989MM"
        # A line without two columns is skipped, not misread
        assert not any("RAM" in label for label in lines)

    @pytest.mark.asyncio
    async def test_refresh_reads_config_with_token(self):
        session = FakeSession(
            get_responses=[_make_response(200, IDLE_STATUS), _make_response(200, {"raw": CONFIG_RAW})]
        )
        p = _printer(session)
        await p.is_online()
        await p.refresh_details()

        assert session.get_calls[-1] == "http://esp32-zebra:80/config"
        details = p.details
        assert details["Print width"] == "581 dots (72.7 mm)"
        assert details["Label length"] == "188 dots (23.5 mm)"
        assert details["Print mode"] == "Cutter"
        assert details["Left position"] == "+0 dots"
        assert details["Serial number"] == "31J10330054"
        assert details["Darkness"] == "9"
        assert details["Head usage"] == "18,249 in"
        assert details["Firmware"] == "V56.17.5Z"
        assert details["Jobs since bridge start"] == "0 printed, 0 failed"

    @pytest.mark.asyncio
    async def test_refresh_is_rate_limited(self):
        session = FakeSession(
            get_responses=[_make_response(200, IDLE_STATUS), _make_response(200, {"raw": CONFIG_RAW})]
        )
        p = _printer(session)
        await p.is_online()
        await p.refresh_details()
        calls = len(session.get_calls)
        await p.refresh_details()
        assert len(session.get_calls) == calls

    @pytest.mark.asyncio
    async def test_refresh_skipped_while_printing(self):
        session = FakeSession(get_responses=[_make_response(200, _status(state="printing"))])
        p = _printer(session)
        await p.is_online()
        await p.refresh_details()
        assert session.get_calls == ["http://esp32-zebra:80/status"]

    @pytest.mark.asyncio
    async def test_refresh_skipped_without_token(self, monkeypatch):
        monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
        session = FakeSession(get_responses=[_make_response(200, IDLE_STATUS)])
        p = _printer(session, token=None)
        await p.is_online()
        await p.refresh_details()
        assert session.get_calls == ["http://esp32-zebra:80/status"]

    @pytest.mark.asyncio
    async def test_old_firmware_404_stops_asking(self):
        session = FakeSession(get_responses=[_make_response(200, IDLE_STATUS), _make_response(404)])
        p = _printer(session)
        await p.is_online()
        await p.refresh_details()
        p._config_read_at = None
        calls = len(session.get_calls)
        await p.refresh_details()
        assert len(session.get_calls) == calls

    @pytest.mark.asyncio
    async def test_busy_reply_retried_next_time(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, IDLE_STATUS),
                _make_response(409, {"error": "printer busy"}),
                _make_response(200, {"raw": CONFIG_RAW}),
            ]
        )
        p = _printer(session)
        await p.is_online()
        await p.refresh_details()
        assert "Print width" not in p.details
        await p.refresh_details()
        assert p.details["Print width"].startswith("581 dots")

    @pytest.mark.asyncio
    async def test_details_from_status_before_config(self):
        session = FakeSession(get_responses=[_make_response(200, IDLE_STATUS)])
        p = _printer(session)
        await p.is_online()
        details = p.details
        assert details["Print mode"] == "Tear off"
        assert details["Label length"] == "188 dots (23.5 mm)"
        assert "Print width" not in details

    @pytest.mark.asyncio
    async def test_print_marks_config_stale(self):
        printed = _status(jobs_printed=1)
        session = FakeSession(
            get_responses=[_make_response(200, IDLE_STATUS), _make_response(200, printed)],
            post_responses=[_make_response(202, {"result": "accepted"})],
        )
        p = _printer(session)
        p._config_read_at = 1.0
        with _no_sleep():
            await p.print_raw(ZPL)
        assert p._config_read_at is None
