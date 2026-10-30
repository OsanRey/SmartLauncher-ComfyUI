from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from smart_launcher.application.installation_controller import InstallationJob
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.installation import InstallationRequest
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.ui.theme import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD
from smart_launcher.ui.window_position import center_over_parent

InstallCallback = Callable[[InstallationJob], bool]


class InstallWizard(ctk.CTkToplevel):
    NVIDIA_PROFILE = "NVIDIA CUDA 13.0"
    CPU_PROFILE = "CPU"

    def __init__(
        self,
        master,
        *,
        initial_target: Path | None,
        initial_python: Path | None,
        initial_channel: UpdateChannel,
        on_install: InstallCallback,
    ) -> None:
        super().__init__(master, fg_color=COLORS["window"])
        self.title("Первая установка ComfyUI")
        self.geometry("620x470")
        self.minsize(580, 440)
        self.transient(master)
        self.grab_set()
        self._on_install = on_install
        self._page = 0
        self._build(initial_target, initial_python, initial_channel)
        center_over_parent(self, master)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(
        self,
        initial_target: Path | None,
        initial_python: Path | None,
        initial_channel: UpdateChannel,
    ) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Новая установка ComfyUI",
            text_color=COLORS["text"],
            font=("Segoe UI Semibold", 18),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.step_label = ctk.CTkLabel(
            header, text="1 / 2", text_color=COLORS["muted"], font=FONT_SMALL
        )
        self.step_label.grid(row=0, column=1, sticky="e")

        self.pages = ctk.CTkFrame(self, fg_color="transparent")
        self.pages.grid(row=1, column=0, sticky="nsew", padx=24)
        self.pages.grid_columnconfigure(0, weight=1)
        self.pages.grid_rowconfigure(0, weight=1)
        self.form_page = ctk.CTkFrame(self.pages, fg_color="transparent")
        self.review_page = ctk.CTkFrame(self.pages, fg_color="transparent")
        self.form_page.grid(row=0, column=0, sticky="nsew")
        self._build_form(initial_target, initial_python, initial_channel)
        self._build_review()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 20))
        footer.grid_columnconfigure(1, weight=1)
        self.back_button = ctk.CTkButton(
            footer,
            text="Назад",
            width=92,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            command=self._back,
            state="disabled",
        )
        self.back_button.grid(row=0, column=0)
        self.error_label = ctk.CTkLabel(
            footer, text="", text_color=COLORS["red"], font=FONT_SMALL
        )
        self.error_label.grid(row=0, column=1, sticky="e", padx=12)
        self.next_button = ctk.CTkButton(
            footer,
            text="Далее",
            width=110,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._next,
        )
        self.next_button.grid(row=0, column=2)

    def _build_form(
        self,
        initial_target: Path | None,
        initial_python: Path | None,
        initial_channel: UpdateChannel,
    ) -> None:
        self.form_page.grid_columnconfigure(0, weight=1)
        self._field_label(self.form_page, "Папка ComfyUI", 0)
        target_row = ctk.CTkFrame(self.form_page, fg_color="transparent")
        target_row.grid(row=1, column=0, sticky="ew", pady=(4, 14))
        target_row.grid_columnconfigure(0, weight=1)
        self.target_entry = self._entry(target_row)
        self.target_entry.grid(row=0, column=0, sticky="ew")
        if initial_target is not None:
            self.target_entry.insert(0, str(initial_target))
        self._browse_button(target_row, self._browse_target).grid(row=0, column=1, padx=(7, 0))

        self._field_label(self.form_page, "Базовый Python", 2)
        python_row = ctk.CTkFrame(self.form_page, fg_color="transparent")
        python_row.grid(row=3, column=0, sticky="ew", pady=(4, 14))
        python_row.grid_columnconfigure(0, weight=1)
        self.python_entry = self._entry(python_row, placeholder="Автоматический выбор")
        self.python_entry.grid(row=0, column=0, sticky="ew")
        if initial_python:
            self.python_entry.insert(0, str(initial_python))
        self._browse_button(python_row, self._browse_python).grid(row=0, column=1, padx=(7, 0))

        controls = ctk.CTkFrame(self.form_page, fg_color="transparent")
        controls.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        controls.grid_columnconfigure((0, 1), weight=1, uniform="install-controls")
        self._field_label(controls, "Канал", 0, column=0)
        self._field_label(controls, "Профиль", 0, column=1)
        self.channel = ctk.CTkSegmentedButton(
            controls,
            values=["Stable", "Development"],
            selected_color=COLORS["blue"],
            selected_hover_color=COLORS["blue_hover"],
            unselected_color=COLORS["surface_raised"],
            unselected_hover_color=COLORS["surface_hover"],
        )
        self.channel.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(4, 0))
        self.channel.set("Stable" if initial_channel is UpdateChannel.STABLE else "Development")
        self.profile = ctk.CTkOptionMenu(
            controls,
            values=[self.NVIDIA_PROFILE, self.CPU_PROFILE],
            fg_color=COLORS["blue"],
            button_color=COLORS["blue"],
            button_hover_color=COLORS["blue_hover"],
            dropdown_fg_color=COLORS["surface_raised"],
            font=FONT_UI,
        )
        self.profile.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))
        self.profile.set(self.NVIDIA_PROFILE)

    def _build_review(self) -> None:
        self.review_page.grid_columnconfigure(0, weight=1)
        self.review_page.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            self.review_page,
            text="Проверьте параметры",
            text_color=COLORS["text"],
            font=FONT_UI_BOLD,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(4, 12))
        self.review_text = ctk.CTkTextbox(
            self.review_page,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=5,
            text_color=COLORS["text"],
            font=FONT_UI,
            activate_scrollbars=False,
        )
        self.review_text.grid(row=1, column=0, sticky="nsew")

    def _next(self) -> None:
        if self._page == 0:
            try:
                job = self._make_job()
            except ValueError as exc:
                self.error_label.configure(text=str(exc))
                return
            self.error_label.configure(text="")
            self._show_review(job)
            return
        job = self._make_job()
        if self._on_install(job):
            self.grab_release()
            self.destroy()
        else:
            self.error_label.configure(text="Установка уже выполняется")

    def _back(self) -> None:
        self._page = 0
        self.review_page.grid_remove()
        self.form_page.grid(row=0, column=0, sticky="nsew")
        self.step_label.configure(text="1 / 2")
        self.back_button.configure(state="disabled")
        self.next_button.configure(text="Далее")

    def _show_review(self, job: InstallationJob) -> None:
        self._page = 1
        self.form_page.grid_remove()
        self.review_page.grid(row=0, column=0, sticky="nsew")
        self.step_label.configure(text="2 / 2")
        self.back_button.configure(state="normal")
        self.next_button.configure(text="Установить")
        python = str(job.base_python) if job.base_python else "Автоматический выбор"
        profile = self.NVIDIA_PROFILE if job.torch_expectation.require_cuda else self.CPU_PROFILE
        summary = (
            f"Папка\n{job.request.target}\n\n"
            f"Python\n{python}\n\n"
            f"Канал\n{job.request.channel.value}\n\n"
            f"Профиль\n{profile}"
        )
        self.review_text.configure(state="normal")
        self.review_text.delete("1.0", "end")
        self.review_text.insert("1.0", summary)
        self.review_text.configure(state="disabled")

    def _make_job(self) -> InstallationJob:
        target_value = self.target_entry.get().strip()
        if not target_value:
            raise ValueError("Выберите папку установки")
        target = Path(target_value).expanduser()
        python_value = self.python_entry.get().strip()
        base_python = Path(python_value).expanduser() if python_value else None
        channel = (
            UpdateChannel.STABLE if self.channel.get() == "Stable" else UpdateChannel.DEVELOPMENT
        )
        if self.profile.get() == self.CPU_PROFILE:
            return InstallationJob(
                InstallationRequest(target, channel=channel),
                base_python,
                torch_index_url=None,
                torch_expectation=TorchExpectation(require_cuda=False, cuda_major=None),
            )
        return InstallationJob(
            InstallationRequest(target, channel=channel),
            base_python,
            torch_index_url="https://download.pytorch.org/whl/cu130",
            torch_expectation=TorchExpectation(require_cuda=True, cuda_major=13),
        )

    def _browse_target(self) -> None:
        value = filedialog.askdirectory(parent=self, title="Папка установки ComfyUI")
        if value:
            self.target_entry.delete(0, "end")
            self.target_entry.insert(0, value)

    def _browse_python(self) -> None:
        value = filedialog.askopenfilename(
            parent=self,
            title="Python executable",
            filetypes=[("Python", "python.exe"), ("Executable", "*.exe")],
        )
        if value:
            self.python_entry.delete(0, "end")
            self.python_entry.insert(0, value)

    @staticmethod
    def _entry(master, *, placeholder: str = "") -> ctk.CTkEntry:
        return ctk.CTkEntry(
            master,
            height=34,
            corner_radius=5,
            border_color=COLORS["border"],
            fg_color=COLORS["surface"],
            placeholder_text=placeholder,
            font=FONT_UI,
        )

    @staticmethod
    def _field_label(master, text: str, row: int, *, column: int = 0) -> ctk.CTkLabel:
        label = ctk.CTkLabel(
            master, text=text, text_color=COLORS["muted"], font=FONT_SMALL, anchor="w"
        )
        label.grid(row=row, column=column, sticky="w")
        return label

    @staticmethod
    def _browse_button(master, command) -> ctk.CTkButton:
        return ctk.CTkButton(
            master,
            text="...",
            width=38,
            height=34,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            command=command,
        )
