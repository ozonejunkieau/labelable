import { App, PluginSettingTab, Setting } from "obsidian";
import type LabelablePlugin from "./main";

export interface LabelableSettings {
	/** Cloudflare account ID (visible in dashboard URL). */
	accountId: string;
	/** Cloudflare Queue ID. */
	queueId: string;
	/** API token with Queues Write permission. */
	apiToken: string;
}

export const DEFAULT_SETTINGS: LabelableSettings = {
	accountId: "",
	queueId: "",
	apiToken: "",
};

export class LabelableSettingTab extends PluginSettingTab {
	plugin: LabelablePlugin;

	constructor(app: App, plugin: LabelablePlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();
		containerEl.createEl("h2", { text: "Labelable" });

		new Setting(containerEl)
			.setName("Cloudflare account ID")
			.setDesc("Found in the Cloudflare dashboard URL: /accounts/<id>/")
			.addText((text) =>
				text
					.setPlaceholder("abc123def456...")
					.setValue(this.plugin.settings.accountId)
					.onChange(async (value) => {
						this.plugin.settings.accountId = value.trim();
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("Queue ID")
			.setDesc("The Cloudflare Queue to publish label jobs to.")
			.addText((text) =>
				text
					.setPlaceholder("queue-id-here")
					.setValue(this.plugin.settings.queueId)
					.onChange(async (value) => {
						this.plugin.settings.queueId = value.trim();
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("API token")
			.setDesc("Token with Cloudflare Queues Write permission. Stored in Obsidian local data (not synced).")
			.addText((text) => {
				text.inputEl.type = "password";
				text
					.setPlaceholder("••••••••")
					.setValue(this.plugin.settings.apiToken)
					.onChange(async (value) => {
						this.plugin.settings.apiToken = value;
						await this.plugin.saveSettings();
					});
			});
	}
}
