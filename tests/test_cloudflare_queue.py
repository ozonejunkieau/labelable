"""Tests for the Cloudflare Queue consumer."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labelable.config import AppConfig, CloudflareQueueConfig
from labelable.integrations.cloudflare_queue import (
    _normalize_event_type,
    _process_message,
    run_cloudflare_queue_consumer,
)
from labelable.models.job import PrintJob
from labelable.models.template import EngineType, LabelDimensions, TemplateConfig
from labelable.queue import PrintQueue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_printer(name: str = "zpl-printer", online: bool = True) -> MagicMock:
    printer = MagicMock()
    printer.name = name
    printer.is_online = AsyncMock(return_value=online)
    printer.config = MagicMock()
    printer.config.type.value = "zpl"
    return printer


def _make_template(
    name: str = "print_label",
    supported: list[str] | None = None,
    engine: EngineType = EngineType.JINJA,
) -> TemplateConfig:
    return TemplateConfig(
        name=name,
        description="Test",
        dimensions=LabelDimensions(width_mm=50, height_mm=25),
        supported_printers=supported or ["zpl-printer"],
        template="^XA^FD{{ properties.name }}^FS^XZ",
        engine=engine,
    )


def _make_message(body: dict[str, Any], lease_id: str = "lease-1") -> dict[str, Any]:
    return {"lease_id": lease_id, "body": body}


def _make_queue() -> MagicMock:
    q = MagicMock(spec=PrintQueue)
    q.submit = AsyncMock()
    return q


# ---------------------------------------------------------------------------
# _normalize_event_type
# ---------------------------------------------------------------------------


class TestNormalizeEventType:
    def test_lowercase(self):
        assert _normalize_event_type("PrintLabel") == "printlabel"

    def test_spaces_become_underscores(self):
        assert _normalize_event_type("Print Label") == "print_label"

    def test_hyphens_become_underscores(self):
        assert _normalize_event_type("print-label") == "print_label"

    def test_mixed_spaces_and_hyphens(self):
        # Consecutive spaces/hyphens collapse into a single underscore
        assert _normalize_event_type("Print - Label") == "print_label"

    def test_already_normalised(self):
        assert _normalize_event_type("print_label") == "print_label"

    def test_strips_leading_trailing_whitespace(self):
        assert _normalize_event_type("  print label  ") == "print_label"

    def test_uppercase_with_spaces(self):
        assert _normalize_event_type("BUILD PROJECT") == "build_project"


# ---------------------------------------------------------------------------
# _process_message
# ---------------------------------------------------------------------------


class TestProcessMessage:
    """Tests for the per-message processing logic."""

    @pytest.fixture
    def jinja_engine(self):
        engine = MagicMock()
        engine.render = MagicMock(return_value=b"^XA^XZ")
        return engine

    @pytest.fixture
    def image_engine(self):
        engine = MagicMock()
        engine.render = MagicMock(return_value=b"\x00\x00")
        return engine

    @pytest.mark.asyncio
    async def test_acks_missing_event_type(self, jinja_engine, image_engine):
        msg = _make_message({"timestamp": "2026-01-01"})  # no event_type
        result = await _process_message(msg, {}, {}, _make_queue(), jinja_engine, image_engine)
        assert result is True  # ack / discard

    @pytest.mark.asyncio
    async def test_acks_unknown_template(self, jinja_engine, image_engine):
        msg = _make_message({"event_type": "unknown_event"})
        result = await _process_message(msg, {}, {}, _make_queue(), jinja_engine, image_engine)
        assert result is True

    @pytest.mark.asyncio
    async def test_nacks_when_no_printer_available(self, jinja_engine, image_engine):
        template = _make_template(supported=["other-printer"])
        templates = {"print_label": template}
        printers = {}  # no printers configured
        msg = _make_message({"event_type": "print_label"})
        result = await _process_message(msg, templates, printers, _make_queue(), jinja_engine, image_engine)
        assert result is False  # nack / retry

    @pytest.mark.asyncio
    async def test_nacks_when_named_printer_missing(self, jinja_engine, image_engine):
        template = _make_template()
        templates = {"print_label": template}
        printers = {}  # "zpl-printer" not in dict
        msg = _make_message({"event_type": "print_label", "printer": "zpl-printer"})
        result = await _process_message(msg, templates, printers, _make_queue(), jinja_engine, image_engine)
        assert result is False

    @pytest.mark.asyncio
    async def test_nacks_when_printer_offline(self, jinja_engine, image_engine):
        printer = _make_printer(online=False)
        printers = {"zpl-printer": printer}
        templates = {"print_label": _make_template()}
        msg = _make_message({"event_type": "print_label"})
        result = await _process_message(msg, templates, printers, _make_queue(), jinja_engine, image_engine)
        assert result is False

    @pytest.mark.asyncio
    async def test_acks_on_render_error(self, image_engine):
        """Render failure should ack (discard) to avoid poison-pill loops."""
        printer = _make_printer()
        printers = {"zpl-printer": printer}
        templates = {"print_label": _make_template()}

        bad_engine = MagicMock()
        bad_engine.render = MagicMock(side_effect=ValueError("bad template"))

        msg = _make_message({"event_type": "print_label"})
        result = await _process_message(msg, templates, printers, _make_queue(), bad_engine, image_engine)
        assert result is True

    @pytest.mark.asyncio
    async def test_successful_jinja_job_submitted(self, jinja_engine, image_engine):
        printer = _make_printer()
        printers = {"zpl-printer": printer}
        templates = {"print_label": _make_template()}
        queue = _make_queue()

        body = {
            "event_type": "print_label",
            "timestamp": "2026-06-25T03:00:00Z",
            "properties": {"name": "Build Thingy"},
        }
        msg = _make_message(body)
        result = await _process_message(msg, templates, printers, queue, jinja_engine, image_engine)

        assert result is True
        queue.submit.assert_awaited_once()
        submitted_job: PrintJob = queue.submit.call_args[0][0]
        assert submitted_job.template_name == "print_label"
        assert submitted_job.printer_name == "zpl-printer"
        assert submitted_job.rendered_content == b"^XA^XZ"
        # Full body must be passed as context
        jinja_engine.render.assert_called_once()
        _, call_context = jinja_engine.render.call_args[0]
        assert call_context["properties"]["name"] == "Build Thingy"
        assert call_context["timestamp"] == "2026-06-25T03:00:00Z"

    @pytest.mark.asyncio
    async def test_event_type_normalised_for_lookup(self, jinja_engine, image_engine):
        """'Print Label' with a space should find the 'print_label' template."""
        printer = _make_printer()
        printers = {"zpl-printer": printer}
        templates = {"print_label": _make_template()}
        queue = _make_queue()

        msg = _make_message({"event_type": "Print Label"})
        result = await _process_message(msg, templates, printers, queue, jinja_engine, image_engine)

        assert result is True
        queue.submit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_explicit_printer_in_message(self, jinja_engine, image_engine):
        """If body.printer is set it should be used instead of the fallback."""
        printer_a = _make_printer("printer-a")
        printer_b = _make_printer("printer-b")
        printers = {"printer-a": printer_a, "printer-b": printer_b}
        template = _make_template(supported=["printer-a", "printer-b"])
        templates = {"print_label": template}
        queue = _make_queue()

        msg = _make_message({"event_type": "print_label", "printer": "printer-b"})
        result = await _process_message(msg, templates, printers, queue, jinja_engine, image_engine)

        assert result is True
        submitted_job: PrintJob = queue.submit.call_args[0][0]
        assert submitted_job.printer_name == "printer-b"

    @pytest.mark.asyncio
    async def test_string_body_is_parsed(self, jinja_engine, image_engine):
        """Messages with a JSON string body (text-content-type) should be parsed."""
        printer = _make_printer()
        printers = {"zpl-printer": printer}
        templates = {"print_label": _make_template()}
        queue = _make_queue()

        raw_body = json.dumps({"event_type": "print_label"})
        msg = {"lease_id": "l1", "body": raw_body}
        result = await _process_message(msg, templates, printers, queue, jinja_engine, image_engine)

        assert result is True
        queue.submit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_json_string_body_discarded(self, jinja_engine, image_engine):
        msg = {"lease_id": "l1", "body": "not-json!!!"}
        result = await _process_message(msg, {}, {}, _make_queue(), jinja_engine, image_engine)
        assert result is True  # ack / discard

    @pytest.mark.asyncio
    async def test_image_engine_used_for_image_templates(self, jinja_engine, image_engine):
        printer = _make_printer()
        printers = {"zpl-printer": printer}
        template = _make_template(engine=EngineType.IMAGE)
        templates = {"print_label": template}
        queue = _make_queue()

        msg = _make_message({"event_type": "print_label"})
        result = await _process_message(msg, templates, printers, queue, jinja_engine, image_engine)

        assert result is True
        image_engine.render.assert_called_once()
        jinja_engine.render.assert_not_called()

    @pytest.mark.asyncio
    async def test_nacks_when_image_engine_is_none(self, jinja_engine):
        """IMAGE template with image_engine=None should nack (retry) not crash."""
        printer = _make_printer()
        printers = {"zpl-printer": printer}
        template = _make_template(engine=EngineType.IMAGE)
        templates = {"print_label": template}

        msg = _make_message({"event_type": "print_label"})
        result = await _process_message(msg, templates, printers, _make_queue(), jinja_engine, None)

        assert result is False


# ---------------------------------------------------------------------------
# CloudflareQueueConfig
# ---------------------------------------------------------------------------


class TestCloudflareQueueConfig:
    def test_disabled_by_default(self):
        config = AppConfig()
        assert config.cloudflare_queue.enabled is False

    def test_defaults(self):
        cfg = CloudflareQueueConfig()
        assert cfg.poll_interval_seconds == 5
        assert cfg.batch_size == 10
        assert cfg.visibility_timeout_ms == 30_000

    def test_loaded_from_app_config(self):
        config = AppConfig(
            cloudflare_queue=CloudflareQueueConfig(
                enabled=True,
                account_id="acct123",
                queue_id="q456",
            )
        )
        assert config.cloudflare_queue.enabled is True
        assert config.cloudflare_queue.account_id == "acct123"
        assert config.cloudflare_queue.queue_id == "q456"


# ---------------------------------------------------------------------------
# run_cloudflare_queue_consumer — start-up guard tests
# These only test the early-exit paths; the poll loop itself is integration
# territory and requires mocking aiohttp.ClientSession.
# ---------------------------------------------------------------------------


class TestConsumerStartupGuards:
    def _make_config(self, **kwargs) -> AppConfig:
        return AppConfig(
            cloudflare_queue=CloudflareQueueConfig(
                enabled=True,
                account_id="acct",
                queue_id="q1",
                api_token="tok",
                **kwargs,
            )
        )

    @pytest.mark.asyncio
    async def test_exits_when_no_api_token(self):
        config = AppConfig(
            cloudflare_queue=CloudflareQueueConfig(enabled=True, account_id="acct", queue_id="q1", api_token="")
        )
        # Should return without error and without making any HTTP calls
        with patch.dict("os.environ", {}, clear=True):
            with patch("labelable.integrations.cloudflare_queue.aiohttp.ClientSession") as mock_session:
                await run_cloudflare_queue_consumer(config, {}, {}, _make_queue(), MagicMock(), MagicMock())
                mock_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_token_read_from_env(self, monkeypatch):
        """Token absent from config but present in env var should proceed past guard."""
        monkeypatch.setenv("LABELABLE_CF_API_TOKEN", "env-token")
        config = AppConfig(
            cloudflare_queue=CloudflareQueueConfig(enabled=True, account_id="acct", queue_id="q1", api_token="")
        )

        # Make the session raise immediately so the loop doesn't run
        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(side_effect=Exception("stop"))
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=False)
        mock_session_instance.post = MagicMock(return_value=mock_resp)

        with patch("labelable.integrations.cloudflare_queue.aiohttp.ClientSession", return_value=mock_session_instance):
            with pytest.raises(Exception, match="stop"):
                await run_cloudflare_queue_consumer(config, {}, {}, _make_queue(), MagicMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_exits_when_account_id_missing(self):
        config = AppConfig(
            cloudflare_queue=CloudflareQueueConfig(enabled=True, account_id="", queue_id="q1", api_token="tok")
        )
        with patch("labelable.integrations.cloudflare_queue.aiohttp.ClientSession") as mock_session:
            await run_cloudflare_queue_consumer(config, {}, {}, _make_queue(), MagicMock(), MagicMock())
            mock_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_exits_when_queue_id_missing(self):
        config = AppConfig(
            cloudflare_queue=CloudflareQueueConfig(enabled=True, account_id="acct", queue_id="", api_token="tok")
        )
        with patch("labelable.integrations.cloudflare_queue.aiohttp.ClientSession") as mock_session:
            await run_cloudflare_queue_consumer(config, {}, {}, _make_queue(), MagicMock(), MagicMock())
            mock_session.assert_not_called()


# ---------------------------------------------------------------------------
# Poll loop tests
#
# Strategy: patch asyncio.sleep to raise CancelledError on the first call
# so the infinite loop exits after exactly one iteration, then catch it.
#
# Key mock structure: patch aiohttp.ClientSession with return_value=session so
# that ClientSession(headers=...) returns our session mock (not session.return_value).
# ---------------------------------------------------------------------------


def _make_valid_config() -> AppConfig:
    return AppConfig(
        cloudflare_queue=CloudflareQueueConfig(
            enabled=True,
            account_id="acct",
            queue_id="q1",
            api_token="tok",
            poll_interval_seconds=5,
        )
    )


def _make_http_response(status: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    resp.status = status
    resp.json = AsyncMock(return_value=json_body or {})
    resp.text = AsyncMock(return_value=text)
    return resp


def _make_session(*responses: MagicMock) -> MagicMock:
    """Build a session mock whose .post() returns responses in order."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(side_effect=list(responses))
    return session


def _patch_session(session: MagicMock):
    """Return a patch context that makes ClientSession(...) yield session."""
    return patch("labelable.integrations.cloudflare_queue.aiohttp.ClientSession", return_value=session)


def _patch_sleep():
    """Return a patch context that stops the loop on the first sleep call."""
    return patch(
        "labelable.integrations.cloudflare_queue.asyncio.sleep",
        side_effect=asyncio.CancelledError,
    )


async def _run_loop(config, templates, printers, queue, jinja_engine, image_engine, session):
    """Run the consumer for one iteration; absorb CancelledError from sleep patch."""
    with _patch_session(session), _patch_sleep():
        try:
            await run_cloudflare_queue_consumer(config, templates, printers, queue, jinja_engine, image_engine)
        except asyncio.CancelledError:
            pass


class TestPollLoop:
    """Tests for the run_cloudflare_queue_consumer poll loop."""

    @pytest.mark.asyncio
    async def test_empty_queue_sleeps_poll_interval(self):
        """Empty pull response causes a sleep at poll_interval_seconds."""
        pull_resp = _make_http_response(200, json_body={"result": {"messages": []}})
        session = _make_session(pull_resp)

        with _patch_session(session):
            with patch(
                "labelable.integrations.cloudflare_queue.asyncio.sleep",
                side_effect=asyncio.CancelledError,
            ) as mock_sleep:
                try:
                    await run_cloudflare_queue_consumer(
                        _make_valid_config(), {}, {}, _make_queue(), MagicMock(), MagicMock()
                    )
                except asyncio.CancelledError:
                    pass

        mock_sleep.assert_called_once_with(5)  # poll_interval_seconds

    @pytest.mark.asyncio
    async def test_http_error_on_pull_backs_off(self):
        """Non-200 pull response sleeps with initial back-off value."""
        pull_resp = _make_http_response(500, text="Internal Server Error")
        session = _make_session(pull_resp)

        with _patch_session(session):
            with patch(
                "labelable.integrations.cloudflare_queue.asyncio.sleep",
                side_effect=asyncio.CancelledError,
            ) as mock_sleep:
                try:
                    await run_cloudflare_queue_consumer(
                        _make_valid_config(), {}, {}, _make_queue(), MagicMock(), MagicMock()
                    )
                except asyncio.CancelledError:
                    pass

        mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_network_error_on_pull_backs_off(self):
        """aiohttp.ClientError on pull triggers back-off sleep."""
        import aiohttp as _aiohttp

        err_resp = MagicMock()
        err_resp.__aenter__ = AsyncMock(side_effect=_aiohttp.ClientError("refused"))
        err_resp.__aexit__ = AsyncMock(return_value=False)
        session = _make_session(err_resp)

        with _patch_session(session):
            with patch(
                "labelable.integrations.cloudflare_queue.asyncio.sleep",
                side_effect=asyncio.CancelledError,
            ) as mock_sleep:
                try:
                    await run_cloudflare_queue_consumer(
                        _make_valid_config(), {}, {}, _make_queue(), MagicMock(), MagicMock()
                    )
                except asyncio.CancelledError:
                    pass

        mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_messages_are_processed_and_acked(self):
        """Messages in pull response are processed and lease IDs acked."""
        messages = [
            {"lease_id": "lease-1", "body": {"event_type": "print_label"}},
            {"lease_id": "lease-2", "body": {"event_type": "print_label"}},
        ]
        pull_resp = _make_http_response(200, json_body={"result": {"messages": messages}})
        ack_resp = _make_http_response(200, json_body={"result": {}})
        # Third call: empty pull → triggers sleep → CancelledError stops the loop
        empty_pull = _make_http_response(200, json_body={"result": {"messages": []}})
        session = _make_session(pull_resp, ack_resp, empty_pull)

        jinja_engine = MagicMock()
        jinja_engine.render = MagicMock(return_value=b"^XA^XZ")
        printer = _make_printer()
        queue = _make_queue()

        await _run_loop(
            _make_valid_config(),
            {"print_label": _make_template()},
            {"zpl-printer": printer},
            queue,
            jinja_engine,
            MagicMock(),
            session,
        )

        assert queue.submit.await_count == 2
        ack_body = session.post.call_args_list[1][1]["json"]
        acked_ids = [a["lease_id"] for a in ack_body["acks"]]
        assert "lease-1" in acked_ids
        assert "lease-2" in acked_ids

    @pytest.mark.asyncio
    async def test_offline_printer_messages_retried(self):
        """Messages that nack (printer offline) appear in retries, not acks."""
        messages = [{"lease_id": "lease-1", "body": {"event_type": "print_label"}}]
        pull_resp = _make_http_response(200, json_body={"result": {"messages": messages}})
        ack_resp = _make_http_response(200, json_body={"result": {}})
        empty_pull = _make_http_response(200, json_body={"result": {"messages": []}})
        session = _make_session(pull_resp, ack_resp, empty_pull)

        await _run_loop(
            _make_valid_config(),
            {"print_label": _make_template()},
            {"zpl-printer": _make_printer(online=False)},
            _make_queue(),
            MagicMock(),
            MagicMock(),
            session,
        )

        ack_body = session.post.call_args_list[1][1]["json"]
        assert "acks" not in ack_body
        assert ack_body["retries"] == [{"lease_id": "lease-1"}]

    @pytest.mark.asyncio
    async def test_ack_http_error_is_logged_not_raised(self):
        """A non-200 ack response is logged but does not crash the consumer."""
        messages = [{"lease_id": "l1", "body": {"event_type": "noop"}}]
        pull_resp = _make_http_response(200, json_body={"result": {"messages": messages}})
        ack_resp = _make_http_response(500, text="server error")
        empty_pull = _make_http_response(200, json_body={"result": {"messages": []}})
        session = _make_session(pull_resp, ack_resp, empty_pull)

        # Should complete without raising (ack error is swallowed, then sleep cancels)
        await _run_loop(_make_valid_config(), {}, {}, _make_queue(), MagicMock(), MagicMock(), session)

    @pytest.mark.asyncio
    async def test_unexpected_process_error_discards_message(self):
        """If _process_message raises unexpectedly the message is acked (discarded)."""
        messages = [{"lease_id": "l1", "body": {"event_type": "print_label"}}]
        pull_resp = _make_http_response(200, json_body={"result": {"messages": messages}})
        ack_resp = _make_http_response(200, json_body={"result": {}})
        empty_pull = _make_http_response(200, json_body={"result": {"messages": []}})
        session = _make_session(pull_resp, ack_resp, empty_pull)

        printer = _make_printer()
        printer.is_online = AsyncMock(side_effect=RuntimeError("unexpected"))

        await _run_loop(
            _make_valid_config(),
            {"print_label": _make_template()},
            {"zpl-printer": printer},
            _make_queue(),
            MagicMock(),
            MagicMock(),
            session,
        )

        ack_body = session.post.call_args_list[1][1]["json"]
        assert ack_body["acks"] == [{"lease_id": "l1"}]
        assert "retries" not in ack_body
