import { requestUrl } from "obsidian";
import type { LabelPayload } from "./label";
import type { LabelableSettings } from "./settings";

export class CloudflareQueueError extends Error {
	constructor(
		message: string,
		readonly status?: number,
	) {
		super(message);
		this.name = "CloudflareQueueError";
	}
}

/**
 * Publishes a label payload to the configured Cloudflare Queue.
 *
 * Uses Obsidian's requestUrl (not fetch) to bypass CORS restrictions imposed
 * on requests originating from app://obsidian.md.
 *
 * API: POST /accounts/{accountId}/queues/{queueId}/messages
 * Body: { "body": <payload object> }
 */
export async function pushLabelMessage(settings: LabelableSettings, payload: LabelPayload): Promise<void> {
	const { accountId, queueId, apiToken } = settings;

	if (!accountId || !queueId || !apiToken) {
		throw new CloudflareQueueError(
			"Cloudflare Queue not configured — set Account ID, Queue ID, and API token in plugin settings.",
		);
	}

	const url = `https://api.cloudflare.com/client/v4/accounts/${accountId}/queues/${queueId}/messages`;
	const body = JSON.stringify({ body: payload });

	console.log("[Labelable] Pushing to queue:", url);
	console.log("[Labelable] Payload:", body);

	let response;
	try {
		response = await requestUrl({
			url,
			method: "POST",
			headers: {
				Authorization: `Bearer ${apiToken}`,
				"Content-Type": "application/json",
			},
			body,
			throw: false,
		});
	} catch (err) {
		console.error("[Labelable] requestUrl threw:", err);
		throw new CloudflareQueueError(`Network error: ${err instanceof Error ? err.message : String(err)}`);
	}

	console.log("[Labelable] Response status:", response.status);
	console.log("[Labelable] Response body:", response.text);

	if (response.status < 200 || response.status >= 300) {
		throw new CloudflareQueueError(
			`Queue push failed (HTTP ${response.status}): ${response.text}`,
			response.status,
		);
	}

	const result = response.json as { success: boolean; errors?: { message: string }[] };
	if (!result.success) {
		const errors = (result.errors ?? []).map((e) => e.message).join(", ");
		throw new CloudflareQueueError(`Queue push rejected: ${errors || "unknown error"}`);
	}
}
