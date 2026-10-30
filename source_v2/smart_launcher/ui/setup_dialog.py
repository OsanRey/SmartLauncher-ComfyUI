from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from smart_launcher.application.existing_installation import ExistingInstallationProfile
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.ui.theme import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD
from smart_launcher.ui.window_position import center_over_parent

ConnectCallback = Callable[[Path, TorchExpectation], ExistingInstallationProfile]


class SetupDialog(ctk.CTkToplevel):
    """Explicit first-run choice; this dialog never guesses or scans for ComfyUI."""

    def __init__(
        self,
        master,
        *,
        on_new_install: Callable[[], None],
        on_connect: ConnectCallback,
        on_connected: Callable[[ExistingInstallationProfile], None],
    ) -> None:
        super().__init__(master, fg_color=COLORS["window"])
        self.title("Настройка Smart Launcher 2.0")
        self.geometry("560x365")
        self.minsize(520, 350)
        self.transient(master)
        self.grab_set()
        self._on_new_install = on_new_install
        self._on_connect = on_connect
        self._on_connected = on_connected
        self._busy = False
        self._build()
        center_over_parent(self, master)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self,
            text="Настройка Smart Launcher 2.0",
            text_color=COLORS["text"],
            font=("Segoe UI Semibold", 19),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 4))
        ctk.CTkLabel(
            self,
            text="Новая независимая установка. Путь выбираете вы.",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 18))

        new_frame = self._section("Новая установка", "Создать отдельное ядро и окружение ComfyUI.")
        new_frame.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 10))
        ctk.CTkButton(
            new_frame,
            text="Выбрать параметры",
            width=150,
            height=34,
            corner_radius=5,
            font=FONT_UI_BOLD,
            fg_color="#237A42",
            hover_color="#2A9150",
            command=self._new_install,
        ).grid(row=0, column=1, rowspan=2, padx=14, pady=14)

        existing = self._section(
            "Подключить существующую",
            "Только выбранную вручную установку с исправным Git и venv.",
        )
        existing.grid(row=3, column=0, sticky="ew", padx=24)
        self.connect_button = ctk.CTkButton(
            existing,
            text="Выбрать папку",
            width=150,
            height=34,
            corner_radius=5,
            font=FONT_UI_BOLD,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._choose_existing,
        )
        self.connect_button.grid(row=0, column=1, rowspan=2, padx=14, pady=14)

        profile_row = ctk.CTkFrame(self, fg_color="transparent")
        profile_row.grid(row=4, column=0, sticky="ew", padx=24, pady=(12, 0))
        profile_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            profile_row,
            text="Профиль подключаемой установки",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
        ).grid(row=0, column=0, sticky="w")
        self.profile = ctk.CTkSegmentedButton(
            profile_row,
            values=["NVIDIA CUDA 13.0", "CPU"],
            selected_color=COLORS["blue"],
            selected_hover_color=COLORS["blue_hover"],
            unselected_color=COLORS["surface_raised"],
            unselected_hover_color=COLORS["surface_hover"],
            font=FONT_UI,
        )
        self.profile.grid(row=0, column=1, sticky="e")
        self.profile.set("NVIDIA CUDA 13.0")
        self.message = ctk.CTkLabel(
            self,
            text="",
            text_color=COLORS["red"],
            font=FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=505,
        )
        self.message.grid(row=5, column=0, sticky="ew", padx=24, pady=(10, 14))

    def _section(self, title: str, detail: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(
            self,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
        )
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame, text=title, text_color=COLORS["text"], font=FONT_UI_BOLD, anchor="w"
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 1))
        ctk.CTkLabel(
            frame, text=detail, text_color=COLORS["muted"], font=FONT_SMALL, anchor="w"
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(1, 12))
        return frame

    def _new_install(self) -> None:
        if self._busy:
            return
        self.grab_release()
        self.destroy()
        self._on_new_install()

    def _choose_existing(self) -> None:
        if self._busy:
            return
        value = filedialog.askdirectory(parent=self, title="Папка существующего ComfyUI")
        if not value:
            return
        expectation = (
            TorchExpectation(require_cuda=False, cuda_major=None)
            if self.profile.get() == "CPU"
            else TorchExpectation(require_cuda=True, cuda_major=13)
        )
        self._busy = True
        self.connect_button.configure(state="disabled", text="Проверка...")
        self.message.configure(text="Проверяю ядро, venv и установленные пакеты...", text_color=COLORS["yellow"])
        threading.Thread(
            target=self._connect_worker,
            args=(Path(value), expectation),
            name="existing-installation-check",
            daemon=True,
        ).start()

    def _connect_worker(self, root: Path, expectation: TorchExpectation) -> None:
        try:
            profile = self._on_connect(root, expectation)
        except Exception as exc:
            self._finish_error(str(exc))
            return
        try:
            self.after(0, lambda: self._finish_success(profile))
        except Exception:
            pass

    def _finish_error(self, detail: str) -> None:
        try:
            self.after(0, lambda: self._show_error(detail))
        except Exception:
            pass

    def _show_error(self, detail: str) -> None:
        if not self.winfo_exists():
            return
        self._busy = False
        self.connect_button.configure(state="normal", text="Выбрать папку")
        self.message.configure(text=detail, text_color=COLORS["red"])

    def _finish_success(self, profile: ExistingInstallationProfile) -> None:
        if not self.winfo_exists():
            return
        self.grab_release()
        self.destroy()
        self._on_connected(profile)
