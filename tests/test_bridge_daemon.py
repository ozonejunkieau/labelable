"""Tests for the BridgeDaemon (cli/bridge.py) with mocked HTTP and USB."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labelable.cli.bridge import (
    BridgeDaemon,
    _get_usb_serial,
)
from labelable.printers.ptouch_protocol import STATUS_RESPONSE_LENGTH


def _make_mock_printer() -> MagicMock:
    """Create a mock PTouchPrinter."""
    printer = MagicMock()
    printer.is_connected = True
    printer.connect = AsyncMock()
    printer.disconnect = AsyncMock()
    printer.print_raw = AsyncMock()
    printer._send = AsyncMock()
    printer._recv = AsyncMock(return_value=b"")
    return printer


def _make_daemon(printer: MagicMock | None = None) -> BridgeDaemon:
    """Create a BridgeDaemon with a mock printer."""
    if printer is None:
        printer = _make_mock_printer()
    return BridgeDaemon(
        printer=printer,
        serial_number="TEST-SN-001",
        labelable_url="http://localhost:7979",
        name="test-bridge",
    )


class TestBridgeDaemonInit:
    def test_init_strips_trailing_slash(self):
        daemon = _make_daemon()
        assert daemon.labelable_url == "http://localhost:7979"

        daemon2 = BridgeDaemon(
            printer=_make_mock_printer(),
            serial_number="X",
            labelable_url="http://example.com:7979/",
            name="t",
        )
        assert daemon2.labelable_url == "http://example.com:7979"

    def test_api_base_uses_registered_name(self):
        daemon = _make_daemon()
        daemon._registered_name = "my-printer"
        assert daemon._api_base == "http://localhost:7979/api/v1/bridge/my-printer"


class TestBridgeDaemonRegister:
    async def test_register_success(self):
        daemon = _make_daemon()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"status": "registered", "printer_name": "test-bridge"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        result = await daemon.register()
        assert result is True
        assert daemon._registered_name == "test-bridge"

    async def test_register_failure_http_error(self):
        daemon = _make_daemon()

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        result = await daemon.register()
        assert result is False
        assert daemon._registered_name is None

    async def test_register_failure_exception(self):
        daemon = _make_daemon()

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=ConnectionError("refused"))
        daemon._session = mock_session

        result = await daemon.register()
        assert result is False

    async def test_register_creates_session_if_none(self):
        daemon = _make_daemon()
        assert daemon._session is None

        with patch("labelable.cli.bridge.aiohttp.ClientSession") as mock_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={"printer_name": "test-bridge"})
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_resp)
            mock_cls.return_value = mock_session

            await daemon.register()
            mock_cls.assert_called_once()


class TestBridgeDaemonReportStatus:
    async def test_report_status_returns_early_if_no_session(self):
        daemon = _make_daemon()
        daemon._session = None
        await daemon.report_status()  # should not raise

    async def test_report_status_returns_early_if_not_registered(self):
        daemon = _make_daemon()
        daemon._session = AsyncMock()
        daemon._registered_name = None
        await daemon.report_status()  # should not raise

    async def test_report_status_posts_status(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        # Mock USB status response - all zeros = errors, but we just need to test the flow
        daemon.printer._recv = AsyncMock(return_value=b"\x00" * STATUS_RESPONSE_LENGTH)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert "/status" in call_args[0][0]

    async def test_report_status_handles_usb_failure(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        daemon.printer._send = AsyncMock(side_effect=ConnectionError("USB gone"))

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()  # should not raise

        # Should still POST status (online=False)
        call_args = mock_session.post.call_args
        json_body = call_args[1]["json"]
        assert json_body["online"] is False


class TestBridgeDaemonPollAndPrint:
    async def test_poll_returns_early_if_no_session(self):
        daemon = _make_daemon()
        daemon._session = None
        await daemon.poll_and_print()  # should not raise

    async def test_poll_no_job_204(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        mock_resp = AsyncMock()
        mock_resp.status = 204
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.poll_and_print()
        daemon.printer.print_raw.assert_not_called()

    async def test_poll_prints_and_reports_success(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        test_data = b"\x1b@\x1bia\x01" + b"\x00" * 50

        # Mock GET /job response with data
        mock_job_resp = AsyncMock()
        mock_job_resp.status = 200
        mock_job_resp.read = AsyncMock(return_value=test_data)
        mock_job_resp.__aenter__ = AsyncMock(return_value=mock_job_resp)
        mock_job_resp.__aexit__ = AsyncMock(return_value=False)

        # Mock POST /result response
        mock_result_resp = AsyncMock()
        mock_result_resp.status = 200
        mock_result_resp.__aenter__ = AsyncMock(return_value=mock_result_resp)
        mock_result_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_job_resp)
        mock_session.post = MagicMock(return_value=mock_result_resp)
        daemon._session = mock_session

        await daemon.poll_and_print()

        daemon.printer.print_raw.assert_called_once_with(test_data)
        # Verify result was reported
        result_call = mock_session.post.call_args
        assert "/result" in result_call[0][0]
        assert result_call[1]["json"]["ok"] is True

    async def test_poll_reports_failure_on_print_error(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        daemon.printer.print_raw = AsyncMock(side_effect=Exception("USB write failed"))

        mock_job_resp = AsyncMock()
        mock_job_resp.status = 200
        mock_job_resp.read = AsyncMock(return_value=b"\x00" * 10)
        mock_job_resp.__aenter__ = AsyncMock(return_value=mock_job_resp)
        mock_job_resp.__aexit__ = AsyncMock(return_value=False)

        mock_result_resp = AsyncMock()
        mock_result_resp.status = 200
        mock_result_resp.__aenter__ = AsyncMock(return_value=mock_result_resp)
        mock_result_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_job_resp)
        mock_session.post = MagicMock(return_value=mock_result_resp)
        daemon._session = mock_session

        await daemon.poll_and_print()

        result_call = mock_session.post.call_args
        assert result_call[1]["json"]["ok"] is False
        assert "USB write failed" in result_call[1]["json"]["error"]

    async def test_poll_reconnects_if_disconnected(self):
        printer = _make_mock_printer()
        printer.is_connected = False
        daemon = _make_daemon(printer)
        daemon._registered_name = "test-bridge"

        mock_job_resp = AsyncMock()
        mock_job_resp.status = 200
        mock_job_resp.read = AsyncMock(return_value=b"\x00" * 10)
        mock_job_resp.__aenter__ = AsyncMock(return_value=mock_job_resp)
        mock_job_resp.__aexit__ = AsyncMock(return_value=False)

        mock_result_resp = AsyncMock()
        mock_result_resp.status = 200
        mock_result_resp.__aenter__ = AsyncMock(return_value=mock_result_resp)
        mock_result_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_job_resp)
        mock_session.post = MagicMock(return_value=mock_result_resp)
        daemon._session = mock_session

        await daemon.poll_and_print()
        printer.connect.assert_called_once()

    async def test_poll_handles_network_error(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=ConnectionError("network down"))
        daemon._session = mock_session

        await daemon.poll_and_print()  # should not raise


class TestBridgeDaemonReportStatusDetailed:
    """Additional report_status tests for coverage of status parsing paths."""

    def _make_valid_status_bytes(self, *, errors: bool = False, width_mm: int = 9) -> bytes:
        """Build a 32-byte P-Touch status response."""
        data = bytearray(32)
        if errors:
            data[8] = 0x01  # NO_MEDIA error
        data[10] = width_mm  # media width
        data[11] = 0x01  # LAMINATED_TAPE
        data[18] = 0x00  # status type
        return bytes(data)

    async def test_report_status_with_valid_usb_response(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        valid_status = self._make_valid_status_bytes(width_mm=12)
        daemon.printer._recv = AsyncMock(return_value=valid_status)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()

        call_args = mock_session.post.call_args
        json_body = call_args[1]["json"]
        assert json_body["online"] is True
        assert json_body["tape_width_mm"] == 12
        assert "P-Touch" in json_body["model_info"]

    async def test_report_status_sends_extended_fields(self):
        """Verify new status fields (media_kind, tape_colour, etc.) are sent."""
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        valid_status = self._make_valid_status_bytes(width_mm=9)
        daemon.printer._recv = AsyncMock(return_value=valid_status)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()

        call_args = mock_session.post.call_args
        json_body = call_args[1]["json"]
        assert "media_kind" in json_body
        assert "tape_colour" in json_body
        assert "text_colour" in json_body
        assert "low_battery" in json_body
        assert "errors" in json_body
        assert json_body["media_kind"] == "Laminated Tape"
        assert json_body["low_battery"] is False
        assert json_body["errors"] == []

    async def test_report_status_with_weak_battery(self):
        """Verify low_battery is True when WEAK_BATTERY error flag is set."""
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        data = bytearray(32)
        data[8] = 0x08  # WEAK_BATTERY
        data[10] = 9
        data[11] = 0x01  # LAMINATED_TAPE
        daemon.printer._recv = AsyncMock(return_value=bytes(data))

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()

        call_args = mock_session.post.call_args
        json_body = call_args[1]["json"]
        assert json_body["low_battery"] is True
        assert "WEAK_BATTERY" in json_body["errors"]

    async def test_report_status_with_errors_in_usb_response(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        error_status = self._make_valid_status_bytes(errors=True)
        daemon.printer._recv = AsyncMock(return_value=error_status)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()

        call_args = mock_session.post.call_args
        json_body = call_args[1]["json"]
        assert json_body["online"] is False

    async def test_report_status_short_usb_response(self):
        """Short USB response (< 32 bytes) should report offline."""
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        daemon.printer._recv = AsyncMock(return_value=b"\x00" * 10)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()

        call_args = mock_session.post.call_args
        json_body = call_args[1]["json"]
        assert json_body["online"] is False

    async def test_report_status_http_failure(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        daemon.printer._recv = AsyncMock(return_value=b"")

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.report_status()  # should not raise

    async def test_report_status_http_exception(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        daemon.printer._recv = AsyncMock(return_value=b"")

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=ConnectionError("down"))
        daemon._session = mock_session

        await daemon.report_status()  # should not raise


class TestBridgeDaemonPollEdgeCases:
    async def test_poll_non_200_non_204_returns(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.poll_and_print()
        daemon.printer.print_raw.assert_not_called()

    async def test_poll_empty_data_returns(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        await daemon.poll_and_print()
        daemon.printer.print_raw.assert_not_called()

    async def test_poll_result_report_http_failure(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        mock_job_resp = AsyncMock()
        mock_job_resp.status = 200
        mock_job_resp.read = AsyncMock(return_value=b"\x00" * 10)
        mock_job_resp.__aenter__ = AsyncMock(return_value=mock_job_resp)
        mock_job_resp.__aexit__ = AsyncMock(return_value=False)

        mock_result_resp = AsyncMock()
        mock_result_resp.status = 500
        mock_result_resp.__aenter__ = AsyncMock(return_value=mock_result_resp)
        mock_result_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_job_resp)
        mock_session.post = MagicMock(return_value=mock_result_resp)
        daemon._session = mock_session

        await daemon.poll_and_print()  # should not raise

    async def test_poll_result_report_exception(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        mock_job_resp = AsyncMock()
        mock_job_resp.status = 200
        mock_job_resp.read = AsyncMock(return_value=b"\x00" * 10)
        mock_job_resp.__aenter__ = AsyncMock(return_value=mock_job_resp)
        mock_job_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_job_resp)
        mock_session.post = MagicMock(side_effect=ConnectionError("down"))
        daemon._session = mock_session

        await daemon.poll_and_print()  # should not raise


class TestBridgeDaemonReregistration:
    async def test_poll_404_sets_reregister_flag(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        assert not daemon._needs_reregister
        await daemon.poll_and_print()
        assert daemon._needs_reregister

    async def test_status_404_sets_reregister_flag(self):
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"
        daemon.printer._recv = AsyncMock(return_value=b"")

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        daemon._session = mock_session

        assert not daemon._needs_reregister
        await daemon.report_status()
        assert daemon._needs_reregister

    async def test_run_loop_reregisters_on_flag(self):
        daemon = _make_daemon()
        daemon._needs_reregister = True
        daemon.register = AsyncMock(return_value=True)
        daemon.report_status = AsyncMock()
        daemon.poll_and_print = AsyncMock()

        call_count = 0

        async def counting_sleep(delay: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()

        with patch("labelable.cli.bridge.asyncio.sleep", side_effect=counting_sleep):
            with pytest.raises(asyncio.CancelledError):
                await daemon.run_loop()

        daemon.register.assert_called_once()
        # After successful re-register, status is reported immediately
        daemon.report_status.assert_called_once()
        assert not daemon._needs_reregister

    async def test_run_loop_skips_poll_on_failed_reregister(self):
        daemon = _make_daemon()
        daemon._needs_reregister = True
        daemon.register = AsyncMock(return_value=False)
        daemon.report_status = AsyncMock()
        daemon.poll_and_print = AsyncMock()

        call_count = 0

        async def counting_sleep(delay: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()

        with patch("labelable.cli.bridge.asyncio.sleep", side_effect=counting_sleep):
            with pytest.raises(asyncio.CancelledError):
                await daemon.run_loop()

        # Re-register attempted but failed; poll_and_print skipped via continue
        daemon.register.assert_called_once()
        daemon.poll_and_print.assert_not_called()


class TestBridgeDaemonReregistrationIntegration:
    """End-to-end: poll gets 404 -> sets flag -> run_loop re-registers -> resumes polling."""

    async def test_full_404_reregister_recovery(self):
        """Trace through the full sequence:
        Iter 1: poll_and_print -> GET /job -> 404, sets _needs_reregister. sleep(1).
        Iter 2: flag detected -> POST /register -> POST /status (re-register path).
                then normal: poll_and_print -> GET /job -> 204. sleep(2).
        Iter 3: normal: poll_and_print -> GET /job -> 204. sleep raises.
        (status_every=7 so periodic status never fires in 3 iterations)
        """
        daemon = _make_daemon()
        daemon._registered_name = "test-bridge"
        daemon.printer._recv = AsyncMock(return_value=b"")

        iteration = 0

        def _make_resp(status: int, **kwargs) -> AsyncMock:
            resp = AsyncMock()
            resp.status = status
            for k, v in kwargs.items():
                setattr(resp, k, v)
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            return resp

        mock_session = AsyncMock()

        get_responses = iter(
            [
                _make_resp(404),  # iter 1: job poll -> 404
                _make_resp(204),  # iter 2: job poll after re-register -> no job
                _make_resp(204),  # iter 3: job poll -> no job
            ]
        )
        post_responses = iter(
            [
                # iter 2 re-register path: register then status
                _make_resp(200, json=AsyncMock(return_value={"printer_name": "test-bridge"})),
                _make_resp(200),
            ]
        )

        mock_session.get = MagicMock(side_effect=lambda *a, **kw: next(get_responses))
        mock_session.post = MagicMock(side_effect=lambda *a, **kw: next(post_responses))
        daemon._session = mock_session

        async def stop_after_three(delay: float) -> None:
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                raise asyncio.CancelledError()

        with patch("labelable.cli.bridge.asyncio.sleep", side_effect=stop_after_three):
            with pytest.raises(asyncio.CancelledError):
                await daemon.run_loop()

        # 3 GETs: poll(404) + poll(204) + poll(204)
        assert mock_session.get.call_count == 3
        # 2 POSTs: register + status-after-reregister
        assert mock_session.post.call_count == 2
        assert daemon._registered_name == "test-bridge"
        assert not daemon._needs_reregister


class TestBridgeDaemonRunLoop:
    async def test_run_loop_calls_status_and_poll(self):
        daemon = _make_daemon()
        daemon.report_status = AsyncMock()
        daemon.poll_and_print = AsyncMock()

        call_count = 0

        async def counting_sleep(delay: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        with patch("labelable.cli.bridge.asyncio.sleep", side_effect=counting_sleep):
            with pytest.raises(asyncio.CancelledError):
                await daemon.run_loop()

        assert daemon.poll_and_print.call_count == 3


class TestGetUsbSerial:
    def test_get_usb_serial_device_not_found(self):
        with patch("usb.core.find", return_value=None):
            with pytest.raises(ConnectionError, match="not found"):
                _get_usb_serial(0x04F9, 0x20AF)

    def test_get_usb_serial_with_serial_number(self):
        mock_dev = MagicMock()
        mock_dev.serial_number = "ABC123"
        with patch("usb.core.find", return_value=mock_dev):
            result = _get_usb_serial(0x04F9, 0x20AF)
            assert result == "ABC123"

    def test_get_usb_serial_without_serial_returns_none(self):
        mock_dev = MagicMock()
        mock_dev.serial_number = ""
        with patch("usb.core.find", return_value=mock_dev):
            result = _get_usb_serial(0x04F9, 0x20AF)
            assert result is None


class TestBridgeDaemonCleanup:
    async def test_cleanup_closes_session_and_disconnects(self):
        daemon = _make_daemon()
        mock_session = AsyncMock()
        daemon._session = mock_session

        await daemon.cleanup()
        mock_session.close.assert_called_once()
        daemon.printer.disconnect.assert_called_once()

    async def test_cleanup_without_session(self):
        daemon = _make_daemon()
        daemon._session = None
        await daemon.cleanup()  # should not raise
        daemon.printer.disconnect.assert_called_once()
