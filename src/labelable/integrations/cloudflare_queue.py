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
from labelable.models.job import PrintJob
from labelable.models.template import EngineType

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

    printer = printers[printer_name]

    # Nack if printer is offline so the message is retried later
    if not await printer.is_online():
        logger.debug("CF queue: printer %r offline, will retry job", printer_name)
        return False

    # Build render context: flatten body["properties"] into the top level so
    # image engine elements can reference fields by simple name (e.g. field: name)
    # without needing dot-notation. Top-level body keys (event_type, timestamp,
    # file, properties) are still accessible at their original paths.
    properties = body.get("properties")
    context: dict[str, object] = {**(properties if isinstance(properties, dict) else {}), **body}

    # Render
    try:
        if template.engine == EngineType.IMAGE:
            if image_engine is None:
                logger.error("CF queue: image engine not initialized, will retry")
                return False
            output_format = printer.config.type.value  # "zpl", "epl2", or "ptouch"
            rendered = image_engine.render(template, context, output_format=output_format)
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
    backoff = 1.0

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
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
                        await asyncio.sleep(min(backoff, 60.0))
                        backoff = min(backoff * 2, 60.0)
                        continue

                    payload = await resp.json()
                    messages: list[dict] = (payload.get("result") or {}).get("messages") or []
                    backoff = 1.0

            except (aiohttp.ClientError, TimeoutError) as exc:
                logger.warning("CF queue network error: %s — retrying in %.0fs", exc, backoff)
                await asyncio.sleep(min(backoff, 60.0))
                backoff = min(backoff * 2, 60.0)
                continue

            if not messages:
                await asyncio.sleep(cf.poll_interval_seconds)
                continue

            # --- Process messages and collect ack/retry sets ---
            ack_ids: list[dict] = []
            retry_ids: list[dict] = []

            for msg in messages:
                lease_id = msg.get("lease_id", "")
                try:
                    should_ack = await _process_message(msg, templates, printers, queue, jinja_engine, image_engine)
                except Exception:
                    logger.exception("CF queue: unexpected error processing message, discarding")
                    should_ack = True

                (ack_ids if should_ack else retry_ids).append({"lease_id": lease_id})

            # --- Acknowledge in a single request ---
            ack_body: dict[str, Any] = {}
            if ack_ids:
                ack_body["acks"] = ack_ids
            if retry_ids:
                ack_body["retries"] = retry_ids

            if ack_body:
                try:
                    async with session.post(ack_url, json=ack_body) as ack_resp:
                        if ack_resp.status != 200:
                            text = await ack_resp.text()
                            logger.warning("CF queue ack failed: HTTP %d - %s", ack_resp.status, text)
                except aiohttp.ClientError as exc:
                    logger.warning("CF queue ack network error: %s", exc)
