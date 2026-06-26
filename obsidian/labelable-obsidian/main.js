"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// main.ts
var main_exports = {};
__export(main_exports, {
  default: () => LabelablePlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian3 = require("obsidian");

// label.ts
function normalizeKey(key) {
  return key.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}
function normalizeProperties(frontmatter) {
  const properties = {};
  if (frontmatter) {
    for (const [key, value] of Object.entries(frontmatter)) {
      if (key === "position") continue;
      properties[normalizeKey(key)] = value;
    }
  }
  return properties;
}
function getStorageEventType(properties) {
  const id = typeof properties["storage_id"] === "string" ? properties["storage_id"] : "";
  if (/^STT/i.test(id)) return "storage-tray";
  if (/^STC/i.test(id)) return "storage-crate";
  return null;
}
function hasProjectId(properties) {
  const id = typeof properties["project_id"] === "string" ? properties["project_id"] : "";
  return /^PRJ\d+$/i.test(id);
}
function getLabelableTemplate(properties) {
  const t = properties["labelable_template"];
  return typeof t === "string" && t.trim() ? t.trim() : null;
}
function toFileMetadata(file) {
  return {
    path: file.path,
    name: file.basename,
    extension: file.extension,
    created: new Date(file.stat.ctime).toISOString(),
    modified: new Date(file.stat.mtime).toISOString()
  };
}
function resolveEventType(properties) {
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
function buildLabelPayload(frontmatter, file, eventTypeOverride) {
  const properties = normalizeProperties(frontmatter);
  return {
    event_type: eventTypeOverride != null ? eventTypeOverride : resolveEventType(properties),
    timestamp: (/* @__PURE__ */ new Date()).toISOString(),
    file,
    properties
  };
}

// queue.ts
var import_obsidian = require("obsidian");
var CloudflareQueueError = class extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
    this.name = "CloudflareQueueError";
  }
};
async function pushLabelMessage(settings, payload) {
  var _a;
  const { accountId, queueId, apiToken } = settings;
  if (!accountId || !queueId || !apiToken) {
    throw new CloudflareQueueError(
      "Cloudflare Queue not configured \u2014 set Account ID, Queue ID, and API token in plugin settings."
    );
  }
  const url = `https://api.cloudflare.com/client/v4/accounts/${accountId}/queues/${queueId}/messages`;
  const body = JSON.stringify({ body: payload });
  console.log("[Labelable] Pushing to queue:", url);
  console.log("[Labelable] Payload:", body);
  let response;
  try {
    response = await (0, import_obsidian.requestUrl)({
      url,
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiToken}`,
        "Content-Type": "application/json"
      },
      body,
      throw: false
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
      response.status
    );
  }
  const result = response.json;
  if (!result.success) {
    const errors = ((_a = result.errors) != null ? _a : []).map((e) => e.message).join(", ");
    throw new CloudflareQueueError(`Queue push rejected: ${errors || "unknown error"}`);
  }
}

// settings.ts
var import_obsidian2 = require("obsidian");
var DEFAULT_SETTINGS = {
  accountId: "",
  queueId: "",
  apiToken: ""
};
var LabelableSettingTab = class extends import_obsidian2.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Labelable" });
    new import_obsidian2.Setting(containerEl).setName("Cloudflare account ID").setDesc("Found in the Cloudflare dashboard URL: /accounts/<id>/").addText(
      (text) => text.setPlaceholder("abc123def456...").setValue(this.plugin.settings.accountId).onChange(async (value) => {
        this.plugin.settings.accountId = value.trim();
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian2.Setting(containerEl).setName("Queue ID").setDesc("The Cloudflare Queue to publish label jobs to.").addText(
      (text) => text.setPlaceholder("queue-id-here").setValue(this.plugin.settings.queueId).onChange(async (value) => {
        this.plugin.settings.queueId = value.trim();
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian2.Setting(containerEl).setName("API token").setDesc("Token with Cloudflare Queues Write permission. Stored in Obsidian local data (not synced).").addText((text) => {
      text.inputEl.type = "password";
      text.setPlaceholder("\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022").setValue(this.plugin.settings.apiToken).onChange(async (value) => {
        this.plugin.settings.apiToken = value;
        await this.plugin.saveSettings();
      });
    });
  }
};

// main.ts
var LabelablePlugin = class extends import_obsidian3.Plugin {
  async onload() {
    await this.loadSettings();
    this.addSettingTab(new LabelableSettingTab(this.app, this));
    this.registerEvent(
      this.app.workspace.on("file-menu", (menu, file) => {
        if (!(file instanceof import_obsidian3.TFile)) return;
        this.addPrintMenuItems(menu, file);
      })
    );
    this.registerEvent(
      this.app.workspace.on("editor-menu", (menu, _editor, info) => {
        const file = info.file;
        if (!(file instanceof import_obsidian3.TFile)) return;
        this.addPrintMenuItems(menu, file);
      })
    );
  }
  /**
   * Adds zero or more property-specific print menu items:
   * - "Print Storage Label" when storage_id matches STT… or STC…
   * - "Print Project Label" when project_id matches PRJnnnn
   * - "Print label" when an explicit Labelable Template property is set
   *
   * Multiple items can appear simultaneously if the note has multiple matching
   * properties (e.g. a project note that also has a storage location).
   */
  addPrintMenuItems(menu, file) {
    var _a;
    const frontmatter = (_a = this.app.metadataCache.getFileCache(file)) == null ? void 0 : _a.frontmatter;
    const properties = normalizeProperties(frontmatter);
    const storageEventType = getStorageEventType(properties);
    if (storageEventType) {
      menu.addItem((item) => {
        item.setTitle("Print Storage Label").setIcon("printer").onClick(() => this.printLabel(file, storageEventType));
      });
    }
    if (hasProjectId(properties)) {
      menu.addItem((item) => {
        item.setTitle("Print Project Label").setIcon("printer").onClick(() => this.printLabel(file, "project-label"));
      });
      menu.addItem((item) => {
        item.setTitle("Print Project Component Label").setIcon("tag").onClick(() => this.printLabel(file, "project-component"));
      });
    }
    const template = getLabelableTemplate(properties);
    if (template) {
      menu.addItem((item) => {
        item.setTitle("Print label").setIcon("printer").onClick(() => this.printLabel(file, template));
      });
    }
  }
  async printLabel(file, eventType) {
    var _a;
    const frontmatter = (_a = this.app.metadataCache.getFileCache(file)) == null ? void 0 : _a.frontmatter;
    const payload = buildLabelPayload(frontmatter, toFileMetadata(file), eventType);
    try {
      await pushLabelMessage(this.settings, payload);
      new import_obsidian3.Notice(`Label queued for "${file.basename}".`);
    } catch (error) {
      const message = error instanceof CloudflareQueueError ? error.message : String(error);
      new import_obsidian3.Notice(`Labelable: failed to queue label \u2014 ${message}`, 8e3);
    }
  }
  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }
  async saveSettings() {
    await this.saveData(this.settings);
  }
};
