# labelable-obsidian

Obsidian plugin that adds label-printing context menu items to notes. Print jobs are published to a Cloudflare Queue; the [Labelable](../README.md) server running on Home Assistant consumes the queue and sends the job to the printer.

## How it works

Right-clicking a note (or using the editor context menu) shows label options based on the note's frontmatter properties:

| Property present | Menu item | Template |
|---|---|---|
| `Storage ID: STT*` | Print Storage Label | `storage-tray` |
| `Storage ID: STC*` | Print Storage Label | `storage-crate` |
| `Project ID: PRJnnnn` | Print Project Label | `project-label` |
| `Project ID: PRJnnnn` | Print Project Component Label | `project-component` |
| `Labelable Template: foo` | Print label | `foo` (explicit override) |

Multiple items can appear simultaneously. A note with both `Project ID` and `Storage ID` will show both.

The full payload sent to the queue includes the resolved `event_type`, a timestamp, file metadata (`path`, `name`, `created`, `modified`), and all frontmatter properties normalised to snake_case. Templates access these as `{{ properties.name }}`, `{{ file.path }}`, etc.

## Plugin files

```
labelable-obsidian/
├── main.ts          # Plugin entry point, menu registration
├── label.ts         # Payload building, property helpers, event_type resolution
├── queue.ts         # Cloudflare Queue HTTP push (uses Obsidian requestUrl)
├── settings.ts      # Settings interface and settings tab UI
├── manifest.json    # Obsidian plugin metadata
├── esbuild.config.mjs
├── package.json
└── tsconfig.json
```

## Build

```bash
cd obsidian/labelable-obsidian
npm install
npm run build      # production build → main.js
npm run dev        # development build with inline sourcemaps
```

The plugin is symlinked into the vault at install time — a rebuild is live immediately without re-copying.

## Install

```bash
ln -sf /path/to/labelable/obsidian/labelable-obsidian \
  ~/path/to/vault/.obsidian/plugins/labelable
```

Then in Obsidian: **Settings → Community plugins → Labelable → Enable**.

## Settings

Configure via **Settings → Labelable**:

| Field | Description |
|---|---|
| Cloudflare account ID | Found in the dashboard URL: `/accounts/<id>/` |
| Queue ID | The queue to publish to |
| API token | Token with **Queues Write** permission |

Settings are stored in `data.json` inside the plugin directory. This file contains credentials and is gitignored — never commit it.

## Event type resolution

`resolveEventType` in `label.ts` determines the template name from frontmatter properties, in priority order:

1. `Labelable Template` property (explicit override, used as-is)
2. `Storage ID` starting `STT` → `storage-tray`
3. `Storage ID` starting `STC` → `storage-crate`
4. `Project ID` matching `PRJnnnn` → `project-label`
5. Default → `print_label`

Context menu items bypass the resolver and specify the event_type directly, so notes with multiple matching properties correctly produce distinct items with the right template each.
