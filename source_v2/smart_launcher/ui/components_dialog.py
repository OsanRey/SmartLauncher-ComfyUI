from __future__ import annotations

import customtkinter as ctk

from smart_launcher.application.component_catalog import ComponentCatalog, ComponentInfo
from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.ui.theme import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD
from smart_launcher.ui.window_position import center_over_parent


class ComponentsDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        settings: LauncherSettings,
        *,
        catalog: ComponentCatalog | None = None,
    ) -> None:
        super().__init__(master, fg_color=COLORS["window"])
        self.title("Компоненты Smart Launcher")
        self.geometry("720x650")
        self.minsize(640, 540)
        self.transient(master)
        self.grab_set()
        self._settings = settings
        self._catalog = catalog or ComponentCatalog()
        self._build()
        center_over_parent(self, master)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Компоненты системы",
            text_color=COLORS["text"],
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            header,
            text="Обновить сведения",
            width=145,
            height=32,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            font=FONT_UI,
            command=self._refresh,
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            header,
            text="Лаунчер показывает требования и состояние, но не устанавливает системные компоненты автоматически.",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        self.list = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
        )
        self.list.grid(row=1, column=0, sticky="nsew", padx=24)
        self.list.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 20))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            text="Глубокая проверка Python, пакетов и CUDA запускается из настроек отдельно.",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer,
            text="Закрыть",
            width=100,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            font=FONT_UI_BOLD,
            command=self.destroy,
        ).grid(row=0, column=1, sticky="e")
        self._refresh()

    def _refresh(self) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        for row, component in enumerate(self._catalog.inspect(self._settings)):
            self._add_component(row, component)

    def _add_component(self, row: int, component: ComponentInfo) -> None:
        item = ctk.CTkFrame(self.list, fg_color="transparent", corner_radius=0)
        item.grid(row=row, column=0, sticky="ew", padx=8, pady=(7, 0))
        item.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            item,
            text=component.name,
            text_color=COLORS["text"],
            font=FONT_UI_BOLD,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            item,
            text=component.requirement,
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(12, 10))
        ctk.CTkLabel(
            item,
            text=component.status,
            text_color=self._tone_color(component.tone),
            font=FONT_UI_BOLD,
            anchor="e",
            width=142,
        ).grid(row=0, column=2, sticky="e")
        ctk.CTkLabel(
            item,
            text=component.detail,
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=610,
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(3, 7))
        ctk.CTkFrame(item, height=1, fg_color=COLORS["border"], corner_radius=0).grid(
            row=2, column=0, columnspan=3, sticky="ew"
        )

    @staticmethod
    def _tone_color(tone: str) -> str:
        return {
            "good": COLORS["green"],
            "bad": COLORS["red"],
            "neutral": COLORS["muted"],
        }.get(tone, COLORS["muted"])
