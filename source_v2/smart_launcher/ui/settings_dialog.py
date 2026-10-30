from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import customtkinter as ctk

from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.ui.components_dialog import ComponentsDialog
from smart_launcher.ui.theme import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD
from smart_launcher.ui.window_position import center_over_parent


class SettingsDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        settings: LauncherSettings,
        *,
        on_save: Callable[[LauncherSettings], None],
        on_repair_environment: Callable[[], bool] | None = None,
        on_open_logs: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color=COLORS["window"])
        self.title("Настройки Smart Launcher 2.0")
        self.geometry("610x585")
        self.minsize(570, 550)
        self.transient(master)
        self.grab_set()
        self._settings = settings
        self._on_save = on_save
        self._on_repair_environment = on_repair_environment
        self._on_open_logs = on_open_logs
        self._build()
        center_over_parent(self, master)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self, text="Настройки запуска", text_color=COLORS["text"], font=("Segoe UI Semibold", 18), anchor="w").grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 10))
        panel = ctk.CTkFrame(self, fg_color=COLORS["surface"], border_width=1, border_color=COLORS["border"], corner_radius=5)
        panel.grid(row=1, column=0, sticky="nsew", padx=24)
        panel.grid_columnconfigure(1, weight=1)

        self._label(panel, "ComfyUI", 0)
        root = self._entry(panel)
        root.grid(row=0, column=1, sticky="ew", padx=(8, 14), pady=(14, 7))
        root.insert(0, str(self._settings.comfy_root))
        root.configure(state="disabled")
        self._label(panel, "Порт", 1)
        self.port = self._entry(panel)
        self.port.grid(row=1, column=1, sticky="ew", padx=(8, 14), pady=7)
        self.port.insert(0, str(self._settings.port))
        self._label(panel, "Канал обновлений", 2)
        self.channel = ctk.CTkSegmentedButton(panel, values=["Stable", "Development"], selected_color=COLORS["blue"], selected_hover_color=COLORS["blue_hover"], unselected_color=COLORS["surface_raised"], unselected_hover_color=COLORS["surface_hover"], font=FONT_UI)
        self.channel.grid(row=2, column=1, sticky="ew", padx=(8, 14), pady=7)
        self.channel.set("Stable" if self._settings.update_channel is UpdateChannel.STABLE else "Development")

        toggles = ctk.CTkFrame(panel, fg_color="transparent")
        toggles.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 7))
        toggles.grid_columnconfigure((0, 1, 2), weight=1)
        self.lan = self._checkbox(toggles, "Доступ по LAN", self._settings.lan_enabled)
        self.manager = self._checkbox(toggles, "Manager", self._settings.manager_enabled)
        self.browser = self._checkbox(toggles, "Открывать браузер", self._settings.auto_open_browser)
        self.lan.grid(row=0, column=0, sticky="w")
        self.manager.grid(row=0, column=1, sticky="w")
        self.browser.grid(row=0, column=2, sticky="w")

        ctk.CTkLabel(panel, text="Дополнительные аргументы, один на строку", text_color=COLORS["muted"], font=FONT_SMALL, anchor="w").grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 4))
        self.arguments = ctk.CTkTextbox(panel, height=105, fg_color=COLORS["surface_raised"], border_width=1, border_color=COLORS["border"], corner_radius=5, text_color=COLORS["text"], font=("Cascadia Mono", 11))
        self.arguments.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=14, pady=(0, 8))
        self.arguments.insert("1.0", "\n".join(self._settings.extra_arguments))

        tokens = ctk.CTkFrame(panel, fg_color="transparent")
        tokens.grid(row=6, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))
        tokens.grid_columnconfigure((0, 1), weight=1)
        self.civitai_token = self._entry(tokens)
        self.huggingface_token = self._entry(tokens)
        self.civitai_token.configure(placeholder_text="Civitai token", show="•")
        self.huggingface_token.configure(placeholder_text="Hugging Face token", show="•")
        self.civitai_token.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.huggingface_token.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        if self._settings.civitai_token:
            self.civitai_token.insert(0, self._settings.civitai_token)
        if self._settings.huggingface_token:
            self.huggingface_token.insert(0, self._settings.huggingface_token)

        environment = ctk.CTkFrame(panel, fg_color="transparent")
        environment.grid(row=7, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))
        environment.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            environment,
            text="Изолированное окружение Python",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            environment,
            text="Компоненты",
            width=125,
            height=32,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            font=FONT_UI,
            command=self._open_components,
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            environment,
            text="Проверить окружение",
            width=164,
            height=32,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            font=FONT_UI,
            command=self._repair_environment,
            state="normal" if self._on_repair_environment else "disabled",
        ).grid(row=1, column=1, padx=(8, 0), pady=(7, 0))
        ctk.CTkButton(
            environment,
            text="Открыть папку журналов",
            width=178,
            height=32,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            font=FONT_UI,
            command=self._on_open_logs,
            state="normal" if self._on_open_logs else "disabled",
        ).grid(row=1, column=2, padx=(8, 0), pady=(7, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 20))
        footer.grid_columnconfigure(0, weight=1)
        self.error = ctk.CTkLabel(footer, text="", text_color=COLORS["red"], font=FONT_SMALL, anchor="w")
        self.error.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(footer, text="Отмена", width=90, fg_color=COLORS["surface_raised"], hover_color=COLORS["surface_hover"], command=self.destroy).grid(row=0, column=1, padx=(8, 6))
        ctk.CTkButton(footer, text="Сохранить", width=105, fg_color=COLORS["blue"], hover_color=COLORS["blue_hover"], font=FONT_UI_BOLD, command=self._save).grid(row=0, column=2)

    def _save(self) -> None:
        try:
            arguments = tuple(line.strip() for line in self.arguments.get("1.0", "end").splitlines() if line.strip())
            updated = replace(
                self._settings,
                port=int(self.port.get().strip()),
                lan_enabled=bool(self.lan.get()),
                manager_enabled=bool(self.manager.get()),
                auto_open_browser=bool(self.browser.get()),
                update_channel=UpdateChannel.STABLE if self.channel.get() == "Stable" else UpdateChannel.DEVELOPMENT,
                extra_arguments=arguments,
                civitai_token=self.civitai_token.get().strip() or None,
                huggingface_token=self.huggingface_token.get().strip() or None,
            )
            self._on_save(updated)
        except Exception as exc:
            self.error.configure(text=str(exc))
            return
        self.grab_release()
        self.destroy()

    def _repair_environment(self) -> None:
        if self._on_repair_environment is None:
            return
        if self._on_repair_environment():
            self.grab_release()
            self.destroy()

    def _open_components(self) -> None:
        dialog = ComponentsDialog(self, self._settings)
        self.wait_window(dialog)
        if self.winfo_exists():
            self.grab_set()

    @staticmethod
    def _label(master, text: str, row: int) -> None:
        ctk.CTkLabel(master, text=text, text_color=COLORS["muted"], font=FONT_SMALL, anchor="w").grid(row=row, column=0, sticky="w", padx=(14, 0), pady=7)

    @staticmethod
    def _entry(master) -> ctk.CTkEntry:
        return ctk.CTkEntry(master, height=34, corner_radius=5, border_color=COLORS["border"], fg_color=COLORS["surface_raised"], font=FONT_UI)

    @staticmethod
    def _checkbox(master, text: str, selected: bool) -> ctk.CTkCheckBox:
        checkbox = ctk.CTkCheckBox(master, text=text, checkbox_width=18, checkbox_height=18, corner_radius=4, fg_color=COLORS["blue"], hover_color=COLORS["blue_hover"], font=FONT_SMALL)
        if selected:
            checkbox.select()
        return checkbox
