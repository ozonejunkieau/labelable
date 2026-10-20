"""Cloudflare Queue consumer for Labelable.

Polls the Cloudflare HTTP Pull Consumer API and submits print jobs based on
incoming queue messages. The event_type field maps to a template name; the
entire message body is passed as the Jinja/image template context.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

import aiohttp

from labelable.config import AppConfig
from labelable.models.job import PrintJob, resolve_quantity
from labelable.models.template import EngineType
from labelable.printers import dpi_mismatch_warning

logger = logging.getLogger(__name__)

_CF_API_BASE = "https://api.cloudflare.com/client/v4"


def _normalize_event_type(event_type: str) -> str:
    """Map an event_type string to a template name.

    Lowercases, strips leading/trailing whitespace, and collapses internal
    whitespace runs to underscores.  Hyphens are preserved so that slug-style
    names like "project-label" match the template name directly without
    requiring callers to use underscores.
    """
    return re.sub(r"\s+", "_", event_type.strip().lower())


async def _process_message(
    message: dict[str, Any],
    templates: dict,
    printers: dict,
    queue: Any,
    jinja_engine: Any,
    image_engine: Any,
) -> bool:
    """Process one queue message.

    Returns True to acknowledge (consume), False to nack (retry later).
    """
    raw_body = message.get("body", {})

    # Cloudflare delivers text messages as strings; parse if needed
    if isinstance(raw_body, str):
        try:
            body: dict[str, Any] = json.loads(raw_body)
        except json.JSONDecodeError:
            logger.warning("CF queue message body is not valid JSON, discarding")
            return True
    else:
        body = dict(raw_body)

    event_type = body.get("event_type", "")
    logger.info(
        "CF queue: processing message %s (attempt %s, event_type=%r)",
        message.get("id", "?"),
        message.get("attempts", "?"),
        event_type,
    )
    if not event_type:
        logger.warning("CF queue message missing event_type, discarding")
        return True

    template_name = _normalize_event_type(event_type)
    template = templates.get(template_name)
    if template is None:
        logger.warning(
            "CF queue: no template for event_type=%r (resolved to %r), discarding",
            event_type,
            template_name,
        )
        return True

    # Printer resolution: explicit field in message takes priority, then
    # fall back to the first configured printer whose name is in the
    # template's supported_printers list.
    printer_name: str | None = body.get("printer") or None
    if not printer_name:
        for name in printers:
            if name in template.supported_printers:
                printer_name = name
                break

    if not printer_name:
        logger.error(
            "CF queue: no compatible printer for template %r, will retry",
            template_name,
        )
        return False

    if printer_name not in printers:
        logger.error(
            "CF queue: printer %r (from message) not found, will retry",
            printer_name,
        )
        return False

    if printer_name not in template.supported_printers:
        # Retrying cannot fix the message, and printing would send the wrong
        # language to the printer. REST and MCP refuse this too.
        logger.error(
            "CF queue: printer %r does not support template %r, discarding",
            printer_name,
            template_name,
        )
        return True

    printer = printers[printer_name]

    # Nack if printer is offline so the message is retried later
    if not await printer.online_status():
        logger.debug("CF queue: printer %r offline, will retry job", printer_name)
        return False

    # Build render context: flatten body["properties"] into the top level so
    # image engine elements can reference fields by simple name (e.g. field: name)
    # without needing dot-notation. Top-level body keys (event_type, timestamp,
    # file, properties) are still accessible at their original paths.
    properties = body.get("properties")
    context: dict[str, object] = {**(properties if isinstance(properties, dict) else {}), **body}

    # Quantity may be given at the top level or in properties; a template's
    # fixed quantity wins, as for every other job source
    try:
        quantity = resolve_quantity(context.get("quantity"), template.quantity)
    except ValueError as exc:
        logger.error("CF queue: %s, discarding message", exc)
        return True
    context["quantity"] = quantity

    # Render
    try:
        if template.engine == EngineType.IMAGE:
            if image_engine is None:
                logger.error("CF queue: image engine not initialized, will retry")
                return False
            output_format = printer.output_format  # "zpl", "epl2", "ptouch", "ptouch_raw"
            rendered = image_engine.render(template, context, output_format=output_format)
            if dpi_warning := dpi_mismatch_warning(printer, template):
                logger.warning(dpi_warning)
        else:
            rendered = jinja_engine.render(template, context)
    except Exception:
        # Ack (discard) on render failure to avoid a poison-pill loop
        logger.exception(
            "CF queue: render failed for template %r, discarding message",
            template_name,
        )
        return True

    job = PrintJob(
        template_name=template_name,
        printer_name=printer_name,
        data=body,
        quantity=quantity,
        rendered_content=rendered,
    )
    await queue.submit(job)
    logger.info(
        "CF queue: submitted job %s to printer %r (event_type=%r)",
        job.id,
        printer_name,
        event_type,
    )
    return True


async def run_cloudflare_queue_consumer(
    config: AppConfig,
    templates: dict,
    printers: dict,
    queue: Any,
    jinja_engine: Any,
    image_engine: Any,
) -> None:
    """Background task: poll Cloudflare Queue and dispatch print jobs.

    Designed to run for the lifetime of the application. Reconnects
    automatically on network errors with exponential back-off (cap 60s).
    """
    cf = config.cloudflare_queue
    api_token = cf.api_token or os.environ.get("LABELABLE_CF_API_TOKEN", "")

    if not api_token:
        logger.error(
            "Cloudflare Queue enabled but no API token set. "
            "Set cloudflare_queue.api_token in config.yaml or the "
            "LABELABLE_CF_API_TOKEN environment variable."
        )
        return

    if not cf.account_id or not cf.queue_id:
        logger.error("Cloudflare Queue enabled but account_id or queue_id is not configured")
        return

    pull_url = f"{_CF_API_BASE}/accounts/{cf.account_id}/queues/{cf.queue_id}/messages/pull"
    ack_url = f"{_CF_API_BASE}/accounts/{cf.account_id}/queues/{cf.queue_id}/messages/ack"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    logger.info("Cloudflare Queue consumer started (queue_id=%s)", cf.queue_id)
    backoff = _Backoff()

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                delay = await _poll_once(
                    session, pull_url, ack_url, cf, templates, printers, queue, jinja_engine, image_engine, backoff
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Nothing awaits this task, so an escaping exception would stop
                # the consumer silently until the add-on restarted.
                delay = backoff.next()
                logger.exception("CF queue: unexpected error in poll loop, retrying in %.0fs", delay)
            if delay:
                await asyncio.sleep(delay)


class _Backoff:
    """Exponential back-off for pull failures: 1, 2, 4 ... capped at 60 s."""

    def __init__(self) -> None:
        self._delay = 1.0

    def next(self) -> float:
        delay = self._delay
        self._delay = min(self._delay * 2, 60.0)
        return delay

    def reset(self) -> None:
        self._delay = 1.0


async def _poll_once(
    session: aiohttp.ClientSession,
    pull_url: str,
    ack_url: str,
    cf: Any,
    templates: dict,
    printers: dict,
    queue: Any,
    jinja_engine: Any,
    image_engine: Any,
    backoff: "_Backoff",
) -> float | None:
    """Pull one batch, process it and ack it.

    Returns how long to sleep before the next pull, or None to pull again
    straight away.
    """
    # --- Pull a batch ---
    try:
        async with session.post(
            pull_url,
            json={
                "batch_size": cf.batch_size,
                "visibility_timeout_ms": cf.visibility_timeout_ms,
            },
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("CF queue pull failed: HTTP %d - %s", resp.status, text)
                return backoff.next()
            payload = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        delay = backoff.next()
        logger.warning("CF queue network error: %s — retrying in %.0fs", exc, delay)
        return delay

    backoff.reset()
    result = payload.get("result") if isinstance(payload, dict) else None
    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list) or not messages:
        return cf.poll_interval_seconds

    # --- Process messages and collect ack/retry sets ---
    ack_ids: list[dict] = []
    retry_ids: list[dict] = []

    for msg in messages:
        lease_id = msg.get("lease_id", "") if isinstance(msg, dict) else ""
        try:
            should_ack = await _process_message(msg, templates, printers, queue, jinja_engine, image_engine)
        except Exception:
            logger.exception("CF queue: unexpected error processing message, discarding")
            should_ack = True

        if should_ack:
            ack_ids.append({"lease_id": lease_id})
        else:
            retry_ids.append({"lease_id": lease_id, "delay_seconds": cf.retry_delay_seconds})

    # --- Acknowledge in a single request ---
    ack_body: dict[str, Any] = {}
    if ack_ids:
        ack_body["acks"] = ack_ids
    if retry_ids:
        ack_body["retries"] = retry_ids

    try:
        async with session.post(ack_url, json=ack_body) as ack_resp:
            if ack_resp.status != 200:
                text = await ack_resp.text()
                logger.warning("CF queue ack failed: HTTP %d - %s", ack_resp.status, text)
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.warning("CF queue ack network error: %s", exc)

    # Nothing in this batch could be printed (e.g. printer offline): wait
    # before pulling again rather than spinning on the rest of the queue
    if not ack_ids:
        return cf.poll_interval_seconds
    return None
