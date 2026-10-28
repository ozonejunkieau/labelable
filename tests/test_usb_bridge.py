"""Tests for the labelable-usb bridge connection and its EPL2 transport."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from labelable.models import printer as printer_models
from labelable.models.printer import PrinterConfig, UsbBridgeConnection
from labelable.printers import create_printer
from labelable.printers.base import PrinterError
from labelable.printers.epl2_net import EPL2NetPrinter
from labelable.printers.http_bridge import RETRY_ATTEMPTS, TOKEN_ENV_VAR
from labelable.printers.ptouch_net import PTouchNetPrinter
from labelable.printers.zebra_net import JOIN_LIMIT_BYTES, ZebraNetPrinter

EPL_STATUS = {
    "id": "epl-UKQ1935HLU",
    "kind": "epl",
    "serial": "UKQ1935HLU",
    "usb_connected": True,
    "usb": {"vid": 2655, "pid": 9, "port": "1.2"},
    "state": "idle",
    "jobs_printed": 0,
    "jobs_failed": 0,
    "last_failure": None,
    "errors": [],
    "status_age_s": 3,
    "printer": {"firmware": "V4.42", "dpi": 203},
    "media": {"label_width_dots": 320, "label_length_dots": 120, "gap_dots": 24, "direct_thermal": True},
}

EPL_CONFIG = {
    "printer": "epl-UKQ1935HLU",
    "raw": "UKQ1935HLU      V4.42\r\n",
    "parsed": {
        "serial": "UKQ1935HLU",
        "firmware": "V4.42",
        "label_width_dots": 320,
        "label_length_dots": 120,
        "gap_dots": 24,
        "darkness": 9,
        "speed": 3,
        "print_direction": "top",
        "direct_thermal": True,
    },
}

EPL = b'N\nA10,10,0,1,1,1,N,"hi"\nP1\n'


def _conn(**kwargs) -> dict:
    return {"type": "usb_bridge", "host": "labelable-usb", "token": "tok", **kwargs}


def _config(printer_type: str = "epl2", **conn_kwargs) -> PrinterConfig:
    return PrinterConfig.model_validate({"name": "p", "type": printer_type, "connection": _conn(**conn_kwargs)})


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
        if not self._get_responses:
            self._get_responses.append(resp)
        return resp

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if not self._post_responses:
            raise AssertionError(f"unexpected POST {url}")
        return self._post_responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def _epl(session: FakeSession, **conn_kwargs) -> EPL2NetPrinter:
    p = EPL2NetPrinter(_config("epl2", **conn_kwargs))
    p._session = session  # type: ignore[assignment]
    p._connected = True
    return p


def _no_sleep():
    return patch("labelable.printers.http_bridge.asyncio.sleep", new=AsyncMock())


def _status(**overrides) -> dict:
    return {**EPL_STATUS, **overrides}


# ---------------------------------------------------------------------------
# Config validation, aliases, token order
# ---------------------------------------------------------------------------


class TestConnectionConfig:
    def test_defaults(self):
        conn = UsbBridgeConnection(host="h")
        assert conn.port == 80
        assert conn.printer is None
        assert conn.completion_timeout_s is None

    @pytest.mark.parametrize("printer_type", ["ptouch", "zpl", "epl2"])
    def test_allowed_for_every_printer_type(self, printer_type):
        assert isinstance(_config(printer_type).connection, UsbBridgeConnection)

    def test_factory_picks_class_by_type(self):
        assert isinstance(create_printer(_config("ptouch")), PTouchNetPrinter)
        assert isinstance(create_printer(_config("zpl")), ZebraNetPrinter)
        assert isinstance(create_printer(_config("epl2")), EPL2NetPrinter)

    def test_output_formats(self):
        assert create_printer(_config("ptouch")).output_format == "ptouch_raw"
        assert create_printer(_config("zpl")).output_format == "zpl"
        assert create_printer(_config("epl2")).output_format == "epl2"

    @pytest.mark.parametrize(
        ("alias", "printer_type", "legacy_env"),
        [
            ("ptouch_bridge", "ptouch", "LABELABLE_PTOUCH_BRIDGE_TOKEN"),
            ("zebra_bridge", "zpl", "LABELABLE_ZEBRA_BRIDGE_TOKEN"),
        ],
    )
    def test_alias_normalises_into_usb_bridge(self, alias, printer_type, legacy_env):
        config = PrinterConfig.model_validate(
            {"name": "p", "type": printer_type, "connection": {"type": alias, "host": "h", "port": 8080}}
        )
        conn = config.connection
        assert isinstance(conn, UsbBridgeConnection)
        assert (conn.host, conn.port, conn.legacy_token_env) == ("h", 8080, legacy_env)
        # Never serialised back out
        assert "legacy_token_env" not in conn.model_dump()

    def test_alias_keeps_old_options(self):
        config = PrinterConfig.model_validate(
            {
                "name": "p",
                "type": "zpl",
                "connection": {"type": "zebra_bridge", "host": "h", "completion_timeout_s": 400},
            }
        )
        assert create_printer(config).completion_timeout == 400  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("alias", "printer_type"),
        [("ptouch_bridge", "zpl"), ("zebra_bridge", "epl2"), ("zebra_bridge", "ptouch")],
    )
    def test_alias_still_rejects_other_printer_types(self, alias, printer_type):
        with pytest.raises(ValidationError, match="only supports type"):
            PrinterConfig.model_validate(
                {"name": "p", "type": printer_type, "connection": {"type": alias, "host": "h"}}
            )

    def test_alias_warns_once(self, caplog):
        printer_models._warned_aliases.clear()
        raw = {"name": "p", "type": "ptouch", "connection": {"type": "ptouch_bridge", "host": "h"}}
        with caplog.at_level(logging.WARNING, logger=printer_models.__name__):
            PrinterConfig.model_validate(raw)
            PrinterConfig.model_validate(raw)
        warnings = [r for r in caplog.records if "deprecated" in r.getMessage()]
        assert len(warnings) == 1
        assert "usb_bridge" in warnings[0].getMessage()

    def test_usb_bridge_does_not_warn(self, caplog):
        with caplog.at_level(logging.WARNING, logger=printer_models.__name__):
            _config("zpl")
        assert not caplog.records


class TestTokenOrder:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in (TOKEN_ENV_VAR, "LABELABLE_ZEBRA_BRIDGE_TOKEN", "LABELABLE_PTOUCH_BRIDGE_TOKEN"):
            monkeypatch.delenv(name, raising=False)

    def _alias_printer(self, alias="zebra_bridge", **conn_kwargs):
        printer_type = "zpl" if alias == "zebra_bridge" else "ptouch"
        config = PrinterConfig.model_validate(
            {"name": "p", "type": printer_type, "connection": {"type": alias, "host": "h", **conn_kwargs}}
        )
        return create_printer(config)

    def test_config_token_wins(self, monkeypatch):
        monkeypatch.setenv(TOKEN_ENV_VAR, "new-env")
        monkeypatch.setenv("LABELABLE_ZEBRA_BRIDGE_TOKEN", "old-env")
        assert self._alias_printer(token="cfg")._token == "cfg"  # type: ignore[attr-defined]

    def test_new_env_beats_legacy_env(self, monkeypatch):
        monkeypatch.setenv(TOKEN_ENV_VAR, "new-env")
        monkeypatch.setenv("LABELABLE_ZEBRA_BRIDGE_TOKEN", "old-env")
        assert self._alias_printer()._token == "new-env"  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("alias", "var"),
        [("zebra_bridge", "LABELABLE_ZEBRA_BRIDGE_TOKEN"), ("ptouch_bridge", "LABELABLE_PTOUCH_BRIDGE_TOKEN")],
    )
    def test_legacy_env_is_the_last_resort(self, monkeypatch, alias, var):
        monkeypatch.setenv(var, "old-env")
        assert self._alias_printer(alias)._token == "old-env"  # type: ignore[attr-defined]

    def test_legacy_env_ignored_for_plain_usb_bridge(self, monkeypatch):
        monkeypatch.setenv("LABELABLE_ZEBRA_BRIDGE_TOKEN", "old-env")
        assert EPL2NetPrinter(_config(token=None))._token is None

    def test_other_alias_env_is_not_used(self, monkeypatch):
        monkeypatch.setenv("LABELABLE_PTOUCH_BRIDGE_TOKEN", "old-env")
        assert self._alias_printer("zebra_bridge")._token is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ?printer= in URLs
# ---------------------------------------------------------------------------


class TestPrinterParameter:
    @pytest.mark.asyncio
    async def test_status_print_and_config_carry_printer(self):
        session = FakeSession(
            get_responses=[
                _make_response(200, EPL_STATUS),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, EPL_CONFIG),
            ],
            post_responses=[_make_response(202, {"result": "accepted"})],
        )
        p = _epl(session, printer="epl-UKQ1935HLU")
        with _no_sleep():
            await p.print_raw(EPL)
        await p.refresh_details()

        assert session.post_calls[0][0] == "http://labelable-usb:80/print?printer=epl-UKQ1935HLU"
        assert session.get_calls[0] == "http://labelable-usb:80/status?printer=epl-UKQ1935HLU"
        assert session.get_calls[-1] == "http://labelable-usb:80/config?printer=epl-UKQ1935HLU"

    @pytest.mark.asyncio
    async def test_printer_id_is_url_encoded(self):
        session = FakeSession(get_responses=[_make_response(200, EPL_STATUS)])
        p = _epl(session, printer="epl-a b&c")
        await p.is_online()
        assert session.get_calls == ["http://labelable-usb:80/status?printer=epl-a%20b%26c"]

    @pytest.mark.asyncio
    async def test_no_printer_leaves_urls_bare(self):
        session = FakeSession(
            get_responses=[_make_response(200, {k: v for k, v in EPL_STATUS.items() if k != "id"})],
            post_responses=[_make_response(202)],
        )
        p = _epl(session)
        await p.is_online()
        assert session.get_calls == ["http://labelable-usb:80/status"]

    @pytest.mark.asyncio
    async def test_board_summary_resolves_the_only_printer_of_our_kind(self):
        zpl = {**EPL_STATUS, "id": "zpl-1", "kind": "zpl"}
        summary = {"device": {"hostname": "x"}, "printers": [zpl, EPL_STATUS]}
        p = _epl(FakeSession(get_responses=[_make_response(200, summary)]))
        assert await p.is_online() is True
        assert p._last_status is not None and p._last_status["id"] == "epl-UKQ1935HLU"

    @pytest.mark.asyncio
    async def test_board_summary_with_several_candidates_is_offline(self):
        other = {**EPL_STATUS, "id": "epl-2"}
        summary = {"device": {}, "printers": [EPL_STATUS, other]}
        p = _epl(FakeSession(get_responses=[_make_response(200, summary)]))
        assert await p.is_online() is False

    @pytest.mark.asyncio
    async def test_wrong_kind_is_offline(self):
        p = _epl(FakeSession(get_responses=[_make_response(200, _status(kind="zpl"))]), printer="zpl-1")
        assert await p.is_online() is False

    @pytest.mark.asyncio
    async def test_wrong_kind_skips_config_read(self):
        session = FakeSession(get_responses=[_make_response(200, _status(kind="zpl"))])
        p = _epl(session, printer="zpl-1")
        await p.is_online()
        await p.refresh_details()
        assert not any("/config" in url for url in session.get_calls)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    async def _post_error(self, code, reason, **conn_kwargs):
        session = FakeSession(
            get_responses=[_make_response(200, EPL_STATUS)],
            post_responses=[_make_response(code, {"error": reason}) for _ in range(RETRY_ATTEMPTS)],
        )
        p = _epl(session, **conn_kwargs)
        with _no_sleep():
            with pytest.raises(PrinterError) as excinfo:
                await p.print_raw(EPL)
        return excinfo.value, session

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "reason"),
        [(409, "printer busy"), (503, "printer not connected")],
    )
    async def test_retryable(self, code, reason):
        err, session = await self._post_error(code, reason)
        assert f"unavailable after {RETRY_ATTEMPTS} attempts" in str(err)
        assert len(session.post_calls) == RETRY_ATTEMPTS

    @pytest.mark.asyncio
    async def test_not_ready_fails_immediately(self):
        err, session = await self._post_error(503, "printer not ready")
        assert "EPL2 printer not ready" in str(err)
        assert len(session.post_calls) == 1

    @pytest.mark.asyncio
    async def test_unknown_printer_says_to_set_printer(self):
        err, session = await self._post_error(404, "unknown printer", printer="epl-gone")
        assert "epl-gone" in str(err)
        assert "connection.printer" in str(err)
        assert len(session.post_calls) == 1

    @pytest.mark.asyncio
    async def test_printer_required_says_to_set_printer(self):
        err, session = await self._post_error(400, "printer parameter required")
        assert "set `printer:`" in str(err)
        assert len(session.post_calls) == 1

    @pytest.mark.asyncio
    async def test_other_400_is_generic(self):
        err, _ = await self._post_error(400, "invalid JSON")
        assert "HTTP 400" in str(err)


# ---------------------------------------------------------------------------
# EPL2 transport
# ---------------------------------------------------------------------------


class TestEpl2Status:
    @pytest.mark.asyncio
    async def test_idle_identifies_printer(self):
        p = _epl(FakeSession(get_responses=[_make_response(200, EPL_STATUS)]))
        assert await p.is_online() is True
        assert p.model_info == "Zebra EPL2 V4.42 (203 dpi)"
        assert p.dpi == 203

    @pytest.mark.asyncio
    async def test_error_state_is_still_online(self):
        p = _epl(FakeSession(get_responses=[_make_response(200, _status(state="error", last_failure="x"))]))
        assert await p.is_online() is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides",
        [
            {"state": "printing"},
            {"errors": ["not_responding"]},
            {"media": None},
            {"usb_connected": False},
        ],
    )
    async def test_not_ready(self, overrides):
        p = _epl(FakeSession(get_responses=[_make_response(200, _status(**overrides))]))
        assert await p.is_online() is False

    @pytest.mark.asyncio
    async def test_null_printer_fields_are_tolerated(self):
        p = _epl(FakeSession(get_responses=[_make_response(200, _status(printer=None))]))
        await p.is_online()
        assert p.dpi is None
        assert "not identified" in (p.model_info or "")


class TestEpl2Print:
    @pytest.mark.asyncio
    async def test_posts_raw_epl_and_waits_for_counter(self):
        session = FakeSession(
            get_responses=[_make_response(200, EPL_STATUS), _make_response(200, _status(jobs_printed=1))],
            post_responses=[_make_response(202)],
        )
        p = _epl(session)
        with _no_sleep():
            await p.print_raw(EPL)
        url, kwargs = session.post_calls[0]
        assert url == "http://labelable-usb:80/print"
        assert kwargs["data"] == EPL
        assert kwargs["headers"]["Content-Type"] == "application/epl"
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_empty_job_rejected(self):
        with pytest.raises(PrinterError, match="empty"):
            await _epl(FakeSession()).print_raw(b"")

    @pytest.mark.asyncio
    async def test_failed_job_reports_device_reason(self):
        failed = _status(jobs_failed=1, last_failure="printer stopped answering UQ", state="error")
        session = FakeSession(
            get_responses=[_make_response(200, EPL_STATUS), _make_response(200, failed)],
            post_responses=[_make_response(202)],
        )
        with _no_sleep():
            with pytest.raises(PrinterError, match="stopped answering UQ"):
                await _epl(session).print_raw(EPL)

    @pytest.mark.asyncio
    async def test_default_timeout_exceeds_firmware_job_timeout(self):
        assert _epl(FakeSession()).completion_timeout > 120
        assert _epl(FakeSession(), completion_timeout_s=200).completion_timeout == 200


class TestEpl2Quantity:
    def _session(self) -> FakeSession:
        return FakeSession(
            get_responses=[_make_response(200, EPL_STATUS), _make_response(200, _status(jobs_printed=1))],
            post_responses=[_make_response(202)],
        )

    @pytest.mark.asyncio
    async def test_copies_joined_into_one_job(self):
        session = self._session()
        with _no_sleep():
            await _epl(session).print_with_quantity(EPL, 3)
        assert len(session.post_calls) == 1
        assert session.post_calls[0][1]["data"] == EPL * 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("native", [b"N\nP3\n", b"N\r\nP2,1\r\n"])
    async def test_native_p_quantity_sent_once(self, native):
        session = self._session()
        with _no_sleep():
            await _epl(session).print_with_quantity(native, 5)
        assert session.post_calls[0][1]["data"] == native

    @pytest.mark.asyncio
    async def test_label_text_resembling_p_command_does_not_count(self):
        data = b'N\nA0,0,0,1,1,1,N,"Part P20"\nP1\n'
        session = self._session()
        with _no_sleep():
            await _epl(session).print_with_quantity(data, 2)
        assert session.post_calls[0][1]["data"] == data * 2

    @pytest.mark.asyncio
    async def test_too_large_to_join_loops(self):
        data = b"N\n" + b"x" * (JOIN_LIMIT_BYTES // 2 + 1) + b"\nP1\n"
        session = FakeSession(
            get_responses=[
                _make_response(200, EPL_STATUS),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=2)),
            ],
            post_responses=[_make_response(202), _make_response(202)],
        )
        with _no_sleep():
            await _epl(session).print_with_quantity(data, 2)
        assert [c[1]["data"] for c in session.post_calls] == [data, data]

    @pytest.mark.asyncio
    async def test_job_without_leading_n_is_not_joined(self):
        # Without N the second copy would be drawn over the first one's
        # image buffer, so each copy must be its own job.
        data = b'A0,0,0,1,1,1,N,"x"\nP1\n'
        session = FakeSession(
            get_responses=[
                _make_response(200, EPL_STATUS),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=1)),
                _make_response(200, _status(jobs_printed=2)),
            ],
            post_responses=[_make_response(202), _make_response(202)],
        )
        with _no_sleep():
            await _epl(session).print_with_quantity(data, 2)
        assert [c[1]["data"] for c in session.post_calls] == [data, data]

    @pytest.mark.asyncio
    async def test_zero_quantity_prints_nothing(self):
        session = FakeSession()
        await _epl(session).print_with_quantity(EPL, 0)
        assert session.post_calls == []


class TestEpl2ConfigDetails:
    @pytest.mark.asyncio
    async def test_refresh_reads_parsed_config(self):
        session = FakeSession(get_responses=[_make_response(200, EPL_STATUS), _make_response(200, EPL_CONFIG)])
        p = _epl(session)
        await p.is_online()
        await p.refresh_details()
        assert session.get_calls[-1] == "http://labelable-usb:80/config?printer=epl-UKQ1935HLU"
        details = p.details
        assert details["Label width"] == "320 dots (40.0 mm)"
        assert details["Darkness"] == "9"
        assert details["Print direction"] == "Top"
        assert details["Print method"] == "Direct thermal"
        assert details["Firmware"] == "V4.42"
        assert details["Jobs since bridge start"] == "0 printed, 0 failed"

    @pytest.mark.asyncio
    async def test_details_from_status_before_config(self):
        p = _epl(FakeSession(get_responses=[_make_response(200, EPL_STATUS)]))
        await p.is_online()
        assert p.details["Label length"] == "120 dots (15.0 mm)"
        assert "Darkness" not in p.details

    @pytest.mark.asyncio
    async def test_refresh_is_rate_limited(self):
        session = FakeSession(get_responses=[_make_response(200, EPL_STATUS), _make_response(200, EPL_CONFIG)])
        p = _epl(session)
        await p.is_online()
        await p.refresh_details()
        calls = len(session.get_calls)
        await p.refresh_details()
        assert len(session.get_calls) == calls

    @pytest.mark.asyncio
    async def test_refresh_skipped_while_printing(self):
        session = FakeSession(get_responses=[_make_response(200, _status(state="printing"))])
        p = _epl(session)
        await p.is_online()
        await p.refresh_details()
        assert len(session.get_calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [404, 400])
    async def test_unsupported_stops_asking(self, code):
        session = FakeSession(get_responses=[_make_response(200, EPL_STATUS), _make_response(code)])
        p = _epl(session)
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
                _make_response(200, EPL_STATUS),
                _make_response(409, {"error": "printer busy"}),
                _make_response(200, EPL_CONFIG),
            ]
        )
        p = _epl(session)
        await p.is_online()
        await p.refresh_details()
        assert "Darkness" not in p.details
        await p.refresh_details()
        assert p.details["Darkness"] == "9"

    @pytest.mark.asyncio
    async def test_print_marks_config_stale(self):
        session = FakeSession(
            get_responses=[_make_response(200, EPL_STATUS), _make_response(200, _status(jobs_printed=1))],
            post_responses=[_make_response(202)],
        )
        p = _epl(session)
        p._config_read_at = 1.0
        with _no_sleep():
            await p.print_raw(EPL)
        assert p._config_read_at is None


class TestPtouchNullFields:
    @pytest.mark.asyncio
    async def test_null_media_fields_before_first_poll(self):
        status = {
            "id": "ptouch-X",
            "kind": "ptouch",
            "usb_connected": True,
            "state": "idle",
            "jobs_printed": 0,
            "jobs_failed": 0,
            "errors": [],
            "media_width_mm": None,
            "media_type": None,
            "tape_color_id": None,
            "text_color_id": None,
        }
        p = PTouchNetPrinter(_config("ptouch", printer="ptouch-X"))
        p._session = FakeSession(get_responses=[_make_response(200, status)])  # type: ignore[assignment]
        assert await p.is_online() is True
        assert p.media_kind is None
        assert await p.get_media_size() is None
