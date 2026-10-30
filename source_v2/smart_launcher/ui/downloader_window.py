from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from smart_launcher.application.download_controller import DownloadController
from smart_launcher.application.custom_node_catalog import CustomNodeCatalog
from smart_launcher.application.custom_node_controller import CustomNodeController
from smart_launcher.domain.custom_nodes import CustomNodeRequest, InstalledCustomNode
from smart_launcher.domain.downloads import (
    DownloadCandidate,
    DownloadRequest,
    ModelKind,
    QueueEntryState,
)
from smart_launcher.domain.events import ProgressChannel
from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.downloads.resolver import ModelLinkResolver
from smart_launcher.infrastructure.stores import ProgressStore
from smart_launcher.ui.theme import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD
from smart_launcher.ui.window_position import center_over_parent


class DownloaderWindow(ctk.CTkToplevel):
    KINDS = {
        "Checkpoint": ModelKind.CHECKPOINT,
        "Diffusion Model": ModelKind.DIFFUSION_MODEL,
        "LoRA": ModelKind.LORA,
        "VAE": ModelKind.VAE,
        "ControlNet": ModelKind.CONTROLNET,
        "Text Encoder": ModelKind.TEXT_ENCODER,
        "CLIP Vision": ModelKind.CLIP_VISION,
        "Upscale Model": ModelKind.UPSCALE_MODEL,
        "Embedding": ModelKind.EMBEDDING,
        "Другая папка": ModelKind.OTHER,
    }

    def __init__(
        self,
        master,
        settings: LauncherSettings,
        controller: DownloadController,
        resolver: ModelLinkResolver,
        progress: ProgressStore,
        custom_nodes: CustomNodeController,
        custom_node_catalog: CustomNodeCatalog,
        *,
        on_tokens_changed: Callable[[str | None, str | None], None],
        on_custom_node_start: Callable[[CustomNodeRequest], bool],
        on_custom_node_cancel: Callable[[], bool],
    ) -> None:
        super().__init__(master, fg_color=COLORS["window"])
        self.title("Загрузчик")
        self.geometry("720x650")
        self.minsize(660, 590)
        self.transient(master)
        self._settings = settings
        self._controller = controller
        self._resolver = resolver
        self._progress = progress
        self._custom_nodes = custom_nodes
        self._custom_node_catalog = custom_node_catalog
        self._on_tokens_changed = on_tokens_changed
        self._on_custom_node_start = on_custom_node_start
        self._on_custom_node_cancel = on_custom_node_cancel
        self._candidates: dict[str, DownloadCandidate] = {}
        self._custom_destination: Path | None = None
        self._resolving = False
        self._queue_signature = None
        self._closed = False
        self._indeterminate_running = False
        self._after_id: str | None = None
        self._token_save_after_id: str | None = None
        self._node_signature = None
        self._node_catalog_entries: tuple[InstalledCustomNode, ...] = ()
        self._node_catalog_loading = False
        self._node_catalog_signature = None
        self._build()
        center_over_parent(self, master)
        self.protocol("WM_DELETE_WINDOW", self._close_window)
        self.after(50, self._load_node_catalog)
        self._after_id = self.after(150, self._refresh)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(
            self,
            fg_color="transparent",
            segmented_button_fg_color=COLORS["surface_raised"],
            segmented_button_selected_color=COLORS["blue"],
            segmented_button_selected_hover_color=COLORS["blue_hover"],
            segmented_button_unselected_color=COLORS["surface_raised"],
            segmented_button_unselected_hover_color=COLORS["surface_hover"],
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(8, 4))
        models = self.tabs.add("Модели")
        nodes = self.tabs.add("Custom Nodes")
        self._build_models(models)
        self._build_custom_nodes(nodes)

    def _build_models(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Загрузчик моделей",
            text_color=COLORS["text"],
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.status = ctk.CTkLabel(
            header, text="Вставьте ссылку и проверьте её", text_color=COLORS["muted"], font=FONT_SMALL
        )
        self.status.grid(row=0, column=1, sticky="e")

        form = ctk.CTkFrame(
            parent,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
        )
        form.grid(row=1, column=0, sticky="ew", padx=22)
        form.grid_columnconfigure(1, weight=1)
        self._label(form, "Ссылка", 0)
        self.url = self._entry(form)
        self.url.grid(row=0, column=1, sticky="ew", padx=(8, 7), pady=(14, 6))
        self.verify_button = ctk.CTkButton(
            form,
            text="Проверить",
            width=95,
            height=34,
            corner_radius=5,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._resolve_link,
        )
        self.verify_button.grid(row=0, column=2, padx=(0, 14), pady=(14, 6))

        self._label(form, "Токены", 1)
        token_row = ctk.CTkFrame(form, fg_color="transparent")
        token_row.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 14), pady=6)
        token_row.grid_columnconfigure((0, 1), weight=1)
        self.civitai = self._entry(token_row, "Civitai")
        self.huggingface = self._entry(token_row, "Hugging Face")
        self.civitai.configure(show="•")
        self.huggingface.configure(show="•")
        self.civitai.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.huggingface.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        if self._settings.civitai_token:
            self.civitai.insert(0, self._settings.civitai_token)
        if self._settings.huggingface_token:
            self.huggingface.insert(0, self._settings.huggingface_token)
        for entry in (self.civitai, self.huggingface):
            entry.bind("<KeyRelease>", self._schedule_token_save)
            entry.bind("<FocusOut>", self._persist_tokens)

        self._label(form, "Файл", 2)
        self.candidate = ctk.CTkOptionMenu(
            form,
            values=["Сначала проверьте ссылку"],
            fg_color=COLORS["surface_raised"],
            button_color=COLORS["blue"],
            button_hover_color=COLORS["blue_hover"],
            dropdown_fg_color=COLORS["surface_raised"],
            font=FONT_UI,
        )
        self.candidate.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 14), pady=6)

        self._label(form, "Тип", 3)
        self.kind = ctk.CTkOptionMenu(
            form,
            values=list(self.KINDS),
            command=lambda _value: self._destination_changed(),
            fg_color=COLORS["surface_raised"],
            button_color=COLORS["blue"],
            button_hover_color=COLORS["blue_hover"],
            dropdown_fg_color=COLORS["surface_raised"],
            font=FONT_UI,
        )
        self.kind.grid(row=3, column=1, sticky="ew", padx=(8, 7), pady=6)
        self.kind.set("Diffusion Model")
        self.browse = ctk.CTkButton(
            form,
            text="Папка...",
            width=95,
            height=34,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            command=self._browse_destination,
        )
        self.browse.grid(row=3, column=2, padx=(0, 14), pady=6)
        self.destination = ctk.CTkLabel(
            form, text="", text_color=COLORS["muted"], font=FONT_SMALL, anchor="w"
        )
        self.destination.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(8, 14), pady=(0, 8))

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=14, pady=(5, 14))
        actions.grid_columnconfigure(0, weight=1)
        self.download_button = ctk.CTkButton(
            actions,
            text="Скачать",
            fg_color="#237A42",
            hover_color="#2A9150",
            command=self._enqueue,
        )
        self.download_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(actions, text="Отменить текущую", width=135, fg_color="#A8323B", hover_color="#C13C47", command=self._cancel).grid(row=0, column=1, padx=(4, 0))

        queue_panel = ctk.CTkFrame(parent, fg_color="transparent")
        queue_panel.grid(row=2, column=0, sticky="nsew", padx=22, pady=(10, 0))
        queue_panel.grid_columnconfigure(0, weight=1)
        queue_panel.grid_rowconfigure(1, weight=1)
        queue_header = ctk.CTkFrame(queue_panel, fg_color="transparent")
        queue_header.grid(row=0, column=0, sticky="ew")
        queue_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(queue_header, text="Очередь загрузки", text_color=COLORS["text"], font=FONT_UI_BOLD).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(queue_header, text="Очистить ожидающие", width=130, height=28, fg_color=COLORS["surface_raised"], hover_color=COLORS["surface_hover"], command=self._clear_queue).grid(row=0, column=1, sticky="e")
        self.queue = ctk.CTkScrollableFrame(
            queue_panel,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
        )
        self.queue.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.queue.grid_columnconfigure(0, weight=1)

        progress = ctk.CTkFrame(parent, fg_color="transparent", height=52)
        progress.grid(row=3, column=0, sticky="ew", padx=22, pady=(8, 14))
        progress.grid_columnconfigure(0, weight=1)
        self.progress_label = ctk.CTkLabel(progress, text="Нет активной загрузки", text_color=COLORS["muted"], font=FONT_SMALL, anchor="w")
        self.progress_label.grid(row=0, column=0, sticky="ew")
        self.progress_bar = ctk.CTkProgressBar(progress, height=6, corner_radius=3, progress_color=COLORS["cyan"])
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.progress_bar.set(0)
        self._destination_changed()

    def _build_custom_nodes(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))
        ctk.CTkLabel(
            header,
            text="Установка Custom Nodes",
            text_color=COLORS["text"],
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="ComfyUI должен быть остановлен. Локальные изменения существующей ноды сохраняются и блокируют обновление.",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(4, 0))

        form = ctk.CTkFrame(
            parent,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
        )
        form.grid(row=1, column=0, sticky="ew", padx=22)
        form.grid_columnconfigure(1, weight=1)
        self._label(form, "GitHub", 0)
        self.node_url = self._entry(form, "https://github.com/author/repository")
        self.node_url.grid(row=0, column=1, sticky="ew", padx=(8, 14), pady=(14, 7))
        self._label(form, "Папка", 1)
        ctk.CTkLabel(
            form,
            text=str(self._settings.comfy_root / "custom_nodes"),
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).grid(row=1, column=1, sticky="ew", padx=(8, 14), pady=7)
        self.node_requirements = ctk.CTkCheckBox(
            form,
            text="Установить requirements.txt в окружение ComfyUI",
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            font=FONT_UI,
        )
        self.node_requirements.select()
        self.node_requirements.grid(
            row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(8, 12)
        )

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 14))
        actions.grid_columnconfigure(0, weight=1)
        self.node_button = ctk.CTkButton(
            actions,
            text="Установить или обновить",
            fg_color="#237A42",
            hover_color="#2A9150",
            font=FONT_UI_BOLD,
            command=self._custom_node_action,
        )
        self.node_button.grid(row=0, column=0, sticky="ew")

        catalog_panel = ctk.CTkFrame(parent, fg_color="transparent")
        catalog_panel.grid(row=2, column=0, sticky="nsew", padx=22, pady=(10, 0))
        catalog_panel.grid_columnconfigure(0, weight=1)
        catalog_panel.grid_rowconfigure(1, weight=1)
        catalog_header = ctk.CTkFrame(catalog_panel, fg_color="transparent")
        catalog_header.grid(row=0, column=0, sticky="ew")
        catalog_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            catalog_header,
            text="Установленные ноды",
            text_color=COLORS["text"],
            font=FONT_UI_BOLD,
        ).grid(row=0, column=0, sticky="w")
        self.node_catalog_refresh = ctk.CTkButton(
            catalog_header,
            text="Обновить список",
            width=115,
            height=28,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            command=self._load_node_catalog,
        )
        self.node_catalog_refresh.grid(row=0, column=1, padx=(6, 0))
        self.node_catalog_check = ctk.CTkButton(
            catalog_header,
            text="Проверить обновления",
            width=145,
            height=28,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=lambda: self._load_node_catalog(check_remote=True),
        )
        self.node_catalog_check.grid(row=0, column=2, padx=(6, 0))
        self.node_dependencies = ctk.CTkButton(
            catalog_header,
            text="Исправить зависимости",
            width=150,
            height=28,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            command=self._repair_node_dependencies,
        )
        self.node_dependencies.grid(row=0, column=3, padx=(6, 0))
        self.node_catalog = ctk.CTkScrollableFrame(
            catalog_panel,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
        )
        self.node_catalog.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.node_catalog.grid_columnconfigure(0, weight=1)

        result_panel = ctk.CTkFrame(
            parent,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
        )
        result_panel.grid(row=3, column=0, sticky="ew", padx=22, pady=(8, 14))
        self.node_status = ctk.CTkLabel(
            result_panel,
            text="Готово к установке",
            text_color=COLORS["muted"],
            font=FONT_UI,
            anchor="nw",
            justify="left",
            wraplength=610,
        )
        self.node_status.pack(fill="x", padx=14, pady=14)
        self._render_node_catalog()

    def _custom_node_action(self) -> None:
        if self._custom_nodes.running:
            self._on_custom_node_cancel()
            return
        request = CustomNodeRequest(
            self.node_url.get(),
            self._settings.comfy_root,
            self._settings.venv_path,
            bool(self.node_requirements.get()),
        )
        if self._on_custom_node_start(request):
            self.node_status.configure(
                text="Операция запущена. Закрытие загрузчика её не отменяет.",
                text_color=COLORS["yellow"],
            )
        else:
            self.node_status.configure(
                text="Операция недоступна: остановите ComfyUI или дождитесь завершения обслуживания.",
                text_color=COLORS["yellow"],
            )

    def _load_node_catalog(
        self, check_remote: bool = False, *, announce: bool = True
    ) -> None:
        if self._closed or self._node_catalog_loading:
            return
        self._node_catalog_loading = True
        self.node_catalog_refresh.configure(state="disabled")
        self.node_catalog_check.configure(state="disabled")
        self.node_dependencies.configure(state="disabled")
        self._render_node_catalog()
        if announce:
            self.node_status.configure(
                text=(
                    "Проверяю обновления установленных нод..."
                    if check_remote
                    else "Читаю установленные ноды..."
                ),
                text_color=COLORS["yellow"],
            )

        def worker() -> None:
            try:
                entries = self._custom_node_catalog.scan(self._settings.comfy_root)
                if check_remote:
                    entries = self._custom_node_catalog.check_updates(
                        entries, comfy_root=self._settings.comfy_root
                    )
            except Exception as exc:
                self._ui(lambda: self._node_catalog_failed(str(exc), announce))
                return
            self._ui(
                lambda: self._node_catalog_ready(entries, check_remote, announce)
            )

        threading.Thread(
            target=worker,
            name="custom-node-catalog",
            daemon=True,
        ).start()

    def _node_catalog_ready(
        self,
        entries: tuple[InstalledCustomNode, ...],
        checked_remote: bool,
        announce: bool,
    ) -> None:
        self._node_catalog_loading = False
        self._node_catalog_entries = entries
        self.node_catalog_refresh.configure(state="normal")
        self.node_catalog_check.configure(state="normal")
        self.node_dependencies.configure(state="normal")
        if announce:
            self.node_status.configure(
                text=(
                    f"Проверено нод: {len(entries)}"
                    if checked_remote
                    else f"Установлено нод: {len(entries)}"
                ),
                text_color=COLORS["green"],
            )
        self._render_node_catalog()

    def _node_catalog_failed(self, detail: str, announce: bool) -> None:
        self._node_catalog_loading = False
        self.node_catalog_refresh.configure(state="normal")
        self.node_catalog_check.configure(state="normal")
        self.node_dependencies.configure(state="normal")
        if announce:
            self.node_status.configure(text=detail, text_color=COLORS["red"])
        self._render_node_catalog()

    def _render_node_catalog(self) -> None:
        signature = (
            self._node_catalog_loading,
            self._custom_nodes.running,
            tuple(
                (
                    entry.name,
                    entry.repository_url,
                    entry.local_commit,
                    entry.remote_commit,
                    entry.dirty,
                    entry.error,
                    entry.registry_id,
                    entry.installed_version,
                    entry.latest_version,
                )
                for entry in self._node_catalog_entries
            )
        )
        if signature == self._node_catalog_signature:
            return
        self._node_catalog_signature = signature
        for child in self.node_catalog.winfo_children():
            child.destroy()
        if not self._node_catalog_entries:
            ctk.CTkLabel(
                self.node_catalog,
                text="Установленные ноды не найдены",
                text_color=COLORS["muted"],
                font=FONT_SMALL,
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return
        for row, entry in enumerate(self._node_catalog_entries):
            item = ctk.CTkFrame(
                self.node_catalog,
                fg_color=COLORS["surface_raised"],
                corner_radius=5,
            )
            item.grid(row=row, column=0, sticky="ew", padx=2, pady=3)
            item.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                item,
                text=entry.name,
                text_color=COLORS["text"],
                font=FONT_UI,
                anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=7)
            state, color = self._node_catalog_state(entry)
            ctk.CTkLabel(
                item,
                text=state,
                text_color=color,
                font=FONT_SMALL,
            ).grid(row=0, column=1, padx=6)
            button = ctk.CTkButton(
                item,
                text="Обновить",
                width=82,
                height=26,
                fg_color=COLORS["blue"],
                hover_color=COLORS["blue_hover"],
                command=lambda node=entry: self._update_installed_node(node),
            )
            button.grid(row=0, column=2, padx=(6, 8))
            remove_button = ctk.CTkButton(
                item,
                text="Удалить",
                width=72,
                height=26,
                fg_color="#A8323B",
                hover_color="#C13C47",
                command=lambda node=entry: self._remove_installed_node(node),
            )
            remove_button.grid(row=0, column=3, padx=(0, 8))
            if (
                self._node_catalog_loading
                or self._custom_nodes.running
                or not entry.managed
                or entry.dirty
            ):
                button.configure(state="disabled")
            if self._node_catalog_loading or self._custom_nodes.running:
                remove_button.configure(state="disabled")

    @staticmethod
    def _node_catalog_state(entry: InstalledCustomNode) -> tuple[str, str]:
        if entry.error:
            return entry.error, COLORS["red"]
        if not entry.managed:
            return "Не управляется", COLORS["muted"]
        if entry.dirty:
            return "Локальные изменения", COLORS["yellow"]
        if entry.update_available is True:
            suffix = f" {entry.latest_version}" if entry.latest_version else ""
            return f"Доступно{suffix}", COLORS["green"]
        if entry.update_available is False:
            suffix = f" {entry.installed_version}" if entry.is_registry else ""
            return f"Актуальна{suffix}", COLORS["green"]
        if entry.is_registry:
            return f"Registry {entry.installed_version}", COLORS["muted"]
        return "Не проверено", COLORS["muted"]

    def _update_installed_node(self, entry: InstalledCustomNode) -> None:
        if not entry.repository_url and not entry.registry_id:
            return
        if entry.registry_id:
            request = CustomNodeRequest(
                entry.repository_url or "",
                self._settings.comfy_root,
                self._settings.venv_path,
                bool(self.node_requirements.get()),
                registry_id=entry.registry_id,
            )
            started = self._on_custom_node_start(request)
            self.node_status.configure(
                text=(
                    "Обновление через ComfyUI Manager запущено."
                    if started
                    else "Операция недоступна: остановите ComfyUI или дождитесь завершения обслуживания."
                ),
                text_color=COLORS["yellow"],
            )
            return
        self.node_url.delete(0, "end")
        self.node_url.insert(0, entry.repository_url or "")
        self._custom_node_action()

    def _remove_installed_node(self, entry: InstalledCustomNode) -> None:
        if not messagebox.askyesno(
            "Удаление Custom Node",
            f"Удалить {entry.name}?\n\nПапка ноды и её файлы будут удалены.",
            parent=self,
        ):
            return
        request = CustomNodeRequest(
            entry.repository_url or "",
            self._settings.comfy_root,
            self._settings.venv_path,
            bool(self.node_requirements.get()),
            registry_id=entry.registry_id,
            remove_target=entry.path,
        )
        if self._on_custom_node_start(request):
            self.node_status.configure(
                text=f"Удаляю {entry.name}...",
                text_color=COLORS["yellow"],
            )
        else:
            self.node_status.configure(
                text="Операция недоступна: остановите ComfyUI или дождитесь завершения обслуживания.",
                text_color=COLORS["yellow"],
            )

    def _repair_node_dependencies(self) -> None:
        if not messagebox.askyesno(
            "Восстановление зависимостей",
            "ComfyUI Manager проверит зависимости всех установленных нод и может изменить "
            "версии общих Python-пакетов. Продолжить?",
            parent=self,
        ):
            return
        request = CustomNodeRequest(
            "",
            self._settings.comfy_root,
            self._settings.venv_path,
            repair_dependencies=True,
        )
        if self._on_custom_node_start(request):
            self.node_status.configure(
                text="Восстанавливаю зависимости через ComfyUI Manager...",
                text_color=COLORS["yellow"],
            )
        else:
            self.node_status.configure(
                text="Операция недоступна: сначала остановите ComfyUI.",
                text_color=COLORS["yellow"],
            )

    def _resolve_link(self) -> None:
        if self._resolving:
            return
        self._resolving = True
        self.verify_button.configure(state="disabled", text="Проверка...")
        self.status.configure(text="Получаю список файлов...", text_color=COLORS["yellow"])
        url = self.url.get()
        civitai, huggingface = self._tokens()
        self._persist_tokens()

        def worker() -> None:
            try:
                candidates = self._resolver.resolve(
                    url, civitai_token=civitai, huggingface_token=huggingface
                )
            except Exception as exc:
                self._ui(lambda: self._resolution_failed(str(exc)))
                return
            self._ui(lambda: self._resolution_ready(candidates))

        threading.Thread(target=worker, name="model-link-resolver", daemon=True).start()

    def _resolution_ready(self, candidates: tuple[DownloadCandidate, ...]) -> None:
        self._resolving = False
        self.verify_button.configure(state="normal", text="Проверить")
        labels = []
        self._candidates.clear()
        for index, item in enumerate(candidates, 1):
            size = f" ({item.size_bytes / 1024**2:.1f} MB)" if item.size_bytes else ""
            prefix = f"{item.version_name} / " if item.version_name else ""
            label = f"{prefix}{item.name}{size}"
            if label in self._candidates:
                label = f"{label} [{index}]"
            labels.append(label)
            self._candidates[label] = item
        self.candidate.configure(values=labels)
        self.candidate.set(labels[0])
        self.status.configure(text=f"Найдено файлов: {len(labels)}", text_color=COLORS["green"])

    def _resolution_failed(self, detail: str) -> None:
        self._resolving = False
        self.verify_button.configure(state="normal", text="Проверить")
        self.status.configure(text=detail, text_color=COLORS["red"])

    def _enqueue(self) -> None:
        candidate = self._candidates.get(self.candidate.get())
        if candidate is None:
            self.status.configure(text="Сначала проверьте ссылку", text_color=COLORS["yellow"])
            return
        destination = self._current_destination()
        if destination is None:
            self.status.configure(text="Выберите папку назначения", text_color=COLORS["yellow"])
            return
        civitai, huggingface = self._tokens()
        self._persist_tokens()
        self._controller.enqueue(
            DownloadRequest(
                candidate,
                destination,
                civitai,
                huggingface,
            )
        )
        self.status.configure(text="Задание добавлено", text_color=COLORS["green"])

    def _current_destination(self) -> Path | None:
        kind = self.KINDS[self.kind.get()]
        if kind is ModelKind.OTHER:
            return self._custom_destination
        relative = kind.relative_folder
        return self._settings.comfy_root / relative if relative else None

    def _destination_changed(self) -> None:
        destination = self._current_destination()
        self.destination.configure(
            text=str(destination) if destination else "Выберите другую папку"
        )

    def _browse_destination(self) -> None:
        value = filedialog.askdirectory(parent=self, title="Папка для модели")
        if value:
            self._custom_destination = Path(value)
            self.kind.set("Другая папка")
            self._destination_changed()

    def _cancel(self) -> None:
        if self._controller.cancel_active():
            queued = any(
                entry.state is QueueEntryState.QUEUED
                for entry in self._controller.snapshot()
            )
            self.status.configure(
                text=(
                    "Текущая загрузка отменяется. Очередь продолжится."
                    if queued
                    else "Текущая загрузка отменяется."
                ),
                text_color=COLORS["yellow"],
            )
        else:
            self.status.configure(text="Нет активной загрузки", text_color=COLORS["muted"])

    def _clear_queue(self) -> None:
        count = self._controller.clear_queue()
        self.status.configure(text=f"Удалено из очереди: {count}", text_color=COLORS["muted"])

    def _refresh(self) -> None:
        if self._closed:
            return
        entries = self._controller.snapshot()
        self.download_button.configure(
            text="Добавить в очередь" if entries else "Скачать"
        )
        signature = tuple((entry.operation_id, entry.state) for entry in entries)
        if signature != self._queue_signature:
            self._queue_signature = signature
            for child in self.queue.winfo_children():
                child.destroy()
            if not entries:
                ctk.CTkLabel(self.queue, text="Очередь пуста", text_color=COLORS["muted"], font=FONT_SMALL).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            for row, entry in enumerate(entries):
                state = "Скачивается" if entry.state is QueueEntryState.ACTIVE else "Ожидает"
                color = COLORS["cyan"] if entry.state is QueueEntryState.ACTIVE else COLORS["muted"]
                item = ctk.CTkFrame(self.queue, fg_color=COLORS["surface_raised"], corner_radius=5)
                item.grid(row=row, column=0, sticky="ew", padx=2, pady=3)
                item.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(item, text=entry.request.candidate.name, text_color=COLORS["text"], font=FONT_UI, anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=7)
                ctk.CTkLabel(item, text=state, text_color=color, font=FONT_SMALL).grid(row=0, column=1, padx=10)
        current = self._progress.get(ProgressChannel.DOWNLOAD)
        if current is None:
            self.progress_label.configure(text="Нет активной загрузки", text_color=COLORS["muted"])
            if self._indeterminate_running:
                self.progress_bar.stop()
                self._indeterminate_running = False
            self.progress_bar.set(0)
        else:
            self.progress_label.configure(text=current.label, text_color=COLORS["cyan"])
            if current.fraction is None:
                if not self._indeterminate_running:
                    self.progress_bar.start()
                    self._indeterminate_running = True
            else:
                if self._indeterminate_running:
                    self.progress_bar.stop()
                    self._indeterminate_running = False
                self.progress_bar.set(current.fraction)
        self._refresh_custom_node()
        self._after_id = self.after(150, self._refresh)

    def _refresh_custom_node(self) -> None:
        running = self._custom_nodes.running
        result = self._custom_nodes.last_result
        phase = self._progress.get(ProgressChannel.MAINTENANCE) if running else None
        signature = (
            running,
            phase.label if phase else None,
            result.succeeded if result else None,
            result.cancelled if result else None,
            result.detail if result else None,
        )
        if signature == self._node_signature:
            return
        self._node_signature = signature
        self._render_node_catalog()
        if running:
            self.node_button.configure(
                text="Отменить операцию", fg_color="#A8323B", hover_color="#C13C47"
            )
            self.node_status.configure(
                text=phase.label if phase else "Custom Node обрабатывается...",
                text_color=COLORS["yellow"],
            )
            return
        self.node_button.configure(
            text="Установить или обновить", fg_color="#237A42", hover_color="#2A9150"
        )
        if result is not None:
            color = COLORS["green"] if result.succeeded else (
                COLORS["yellow"] if result.cancelled else COLORS["red"]
            )
            self.node_status.configure(text=result.detail, text_color=color)
            self._load_node_catalog(announce=False)

    def _ui(self, callback) -> None:
        try:
            self.after(0, callback)
        except Exception:
            pass

    def _tokens(self) -> tuple[str | None, str | None]:
        return (
            self.civitai.get().strip() or None,
            self.huggingface.get().strip() or None,
        )

    def _schedule_token_save(self, _event=None) -> None:
        if self._token_save_after_id is not None:
            try:
                self.after_cancel(self._token_save_after_id)
            except Exception:
                pass
        self._token_save_after_id = self.after(450, self._persist_tokens)

    def _persist_tokens(self, _event=None) -> None:
        pending = self._token_save_after_id
        self._token_save_after_id = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except Exception:
                pass
        self._on_tokens_changed(*self._tokens())

    def _close_window(self) -> None:
        self._persist_tokens()
        self.destroy()

    def destroy(self) -> None:
        self._closed = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._token_save_after_id is not None:
            try:
                self.after_cancel(self._token_save_after_id)
            except Exception:
                pass
            self._token_save_after_id = None
        super().destroy()

    @staticmethod
    def _label(master, text: str, row: int) -> None:
        ctk.CTkLabel(master, text=text, text_color=COLORS["muted"], font=FONT_SMALL, anchor="w").grid(row=row, column=0, sticky="w", padx=(14, 0), pady=6)

    @staticmethod
    def _entry(master, placeholder: str = "") -> ctk.CTkEntry:
        return ctk.CTkEntry(master, height=34, corner_radius=5, border_color=COLORS["border"], fg_color=COLORS["surface_raised"], placeholder_text=placeholder, font=FONT_UI)
