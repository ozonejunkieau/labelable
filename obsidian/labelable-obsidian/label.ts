/**
 * Normalizes a frontmatter property key into a queue-friendly snake_case key.
 *
 * "Project ID" -> "project_id", "crate id" -> "crate_id".
 */
function normalizeKey(key: string): string {
	return key
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "_")
		.replace(/^_+|_+$/g, "");
}

/** Normalizes all frontmatter keys to snake_case, dropping the Obsidian-internal "position" key. */
export function normalizeProperties(frontmatter: Record<string, unknown> | undefined): Record<string, unknown> {
	const properties: Record<string, unknown> = {};
	if (frontmatter) {
		for (const [key, value] of Object.entries(frontmatter)) {
			if (key === "position") continue;
			properties[normalizeKey(key)] = value;
		}
	}
	return properties;
}

/**
 * Returns the storage event_type if `storage_id` matches STT… or STC…,
 * or null if not applicable.
 */
export function getStorageEventType(properties: Record<string, unknown>): string | null {
	const id = typeof properties["storage_id"] === "string" ? properties["storage_id"] : "";
	if (/^STT/i.test(id)) return "storage-tray";
	if (/^STC/i.test(id)) return "storage-crate";
	return null;
}

/** Returns true if `project_id` matches the PRJnnnn pattern. */
export function hasProjectId(properties: Record<string, unknown>): boolean {
	const id = typeof properties["project_id"] === "string" ? properties["project_id"] : "";
	return /^PRJ\d+$/i.test(id);
}

/**
 * Returns the explicit labelable_template value if present and non-empty,
 * or null otherwise.
 */
export function getLabelableTemplate(properties: Record<string, unknown>): string | null {
	const t = properties["labelable_template"];
	return typeof t === "string" && t.trim() ? t.trim() : null;
}

export interface FileMetadata {
	path: string;
	name: string;
	extension: string;
	/** Creation time, ISO-8601 (converted from Obsidian's epoch-millis stat). */
	created: string;
	/** Last modification time, ISO-8601 (converted from Obsidian's epoch-millis stat). */
	modified: string;
}

/**
 * Minimal shape of Obsidian's TFile needed to build FileMetadata, so this
 * module stays free of an `obsidian` import and is easy to unit test.
 */
export interface TFileLike {
	path: string;
	basename: string;
	extension: string;
	stat: { ctime: number; mtime: number };
}

/** Converts a TFile(-like) object into the FileMetadata block of the payload. */
export function toFileMetadata(file: TFileLike): FileMetadata {
	return {
		path: file.path,
		name: file.basename,
		extension: file.extension,
		created: new Date(file.stat.ctime).toISOString(),
		modified: new Date(file.stat.mtime).toISOString(),
	};
}

export interface LabelPayload {
	event_type: string;
	timestamp: string;
	file: FileMetadata;
	properties: Record<string, unknown>;
}

/**
 * Resolves the event_type from normalized frontmatter properties.
 *
 * Priority:
 *   1. Explicit `labelable_template` property — user override, used as-is.
 *   2. `storage_id` starting with "STT" → "storage-tray"
 *   3. `storage_id` starting with "STC" → "storage-crate"
 *   4. `project_id` matching PRJnnnn → "project-label"
 *   5. Default → "print_label"
 */
export function resolveEventType(properties: Record<string, unknown>): string {
	const labelableTemplate = properties["labelable_template"];
	if (typeof labelableTemplate === "string" && labelableTemplate.trim()) {
		return labelableTemplate.trim();
	}

	const storageId = typeof properties["storage_id"] === "string" ? properties["storage_id"] : "";
	if (/^STT/i.test(storageId)) return "storage-tray";
	if (/^STC/i.test(storageId)) return "storage-crate";

	const projectId = typeof properties["project_id"] === "string" ? properties["project_id"] : "";
	if (/^PRJ\d+$/i.test(projectId)) return "project-label";

	return "print_label";
}

/**
 * Builds the JSON payload for the label-print queue message: file metadata
 * and frontmatter properties (normalized to snake_case keys) live under
 * their own namespaced fields, alongside a fixed event_type marker and an
 * ISO-8601 timestamp. Namespacing avoids key collisions, e.g. a frontmatter
 * "Name" property vs. the file's own name.
 *
 * Pass `eventTypeOverride` to force a specific event_type (e.g. from a
 * property-specific menu item) rather than running the resolver.
 */
export function buildLabelPayload(
	frontmatter: Record<string, unknown> | undefined,
	file: FileMetadata,
	eventTypeOverride?: string,
): LabelPayload {
	const properties = normalizeProperties(frontmatter);
	return {
		event_type: eventTypeOverride ?? resolveEventType(properties),
		timestamp: new Date().toISOString(),
		file,
		properties,
	};
}
