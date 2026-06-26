import { Editor, Menu, MarkdownFileInfo, MarkdownView, Notice, Plugin, TAbstractFile, TFile } from "obsidian";
import {
	buildLabelPayload,
	getLabelableTemplate,
	getStorageEventType,
	hasProjectId,
	normalizeProperties,
	toFileMetadata,
} from "./label";
import { CloudflareQueueError, pushLabelMessage } from "./queue";
import { DEFAULT_SETTINGS, LabelableSettings, LabelableSettingTab } from "./settings";

export default class LabelablePlugin extends Plugin {
	settings!: LabelableSettings;

	async onload() {
		await this.loadSettings();
		this.addSettingTab(new LabelableSettingTab(this.app, this));

		this.registerEvent(
			this.app.workspace.on("file-menu", (menu: Menu, file: TAbstractFile) => {
				if (!(file instanceof TFile)) return;
				this.addPrintMenuItems(menu, file);
			}),
		);

		this.registerEvent(
			this.app.workspace.on("editor-menu", (menu: Menu, _editor: Editor, info: MarkdownView | MarkdownFileInfo) => {
				const file = info.file;
				if (!(file instanceof TFile)) return;
				this.addPrintMenuItems(menu, file);
			}),
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
	private addPrintMenuItems(menu: Menu, file: TFile): void {
		const frontmatter = this.app.metadataCache.getFileCache(file)?.frontmatter;
		const properties = normalizeProperties(frontmatter);

		const storageEventType = getStorageEventType(properties);
		if (storageEventType) {
			menu.addItem((item) => {
				item
					.setTitle("Print Storage Label")
					.setIcon("printer")
					.onClick(() => this.printLabel(file, storageEventType));
			});
		}

		if (hasProjectId(properties)) {
			menu.addItem((item) => {
				item
					.setTitle("Print Project Label")
					.setIcon("printer")
					.onClick(() => this.printLabel(file, "project-label"));
			});
			menu.addItem((item) => {
				item
					.setTitle("Print Project Component Label")
					.setIcon("tag")
					.onClick(() => this.printLabel(file, "project-component"));
			});
		}

		const template = getLabelableTemplate(properties);
		if (template) {
			menu.addItem((item) => {
				item
					.setTitle("Print label")
					.setIcon("printer")
					.onClick(() => this.printLabel(file, template));
			});
		}
	}

	private async printLabel(file: TFile, eventType: string): Promise<void> {
		const frontmatter = this.app.metadataCache.getFileCache(file)?.frontmatter;
		const payload = buildLabelPayload(frontmatter, toFileMetadata(file), eventType);

		try {
			await pushLabelMessage(this.settings, payload);
			new Notice(`Label queued for "${file.basename}".`);
		} catch (error) {
			const message = error instanceof CloudflareQueueError ? error.message : String(error);
			new Notice(`Labelable: failed to queue label — ${message}`, 8000);
		}
	}

	async loadSettings() {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings() {
		await this.saveData(this.settings);
	}
}