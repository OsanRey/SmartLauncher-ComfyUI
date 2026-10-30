from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from smart_launcher.domain.events import LogRecord
from smart_launcher.ui.theme import COLORS, FONT_MONO


class ConsoleView(ctk.CTkFrame):
    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._autoscroll = True
        self._carriage_return_pending = False
        self._scroll_pending = False
        self._scroll_after_id: str | None = None
        self.text = tk.Text(
            self,
            bg=COLORS["window"],
            fg="#27E04E",
            insertbackground=COLORS["text"],
            selectbackground=COLORS["blue"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            padx=10,
            pady=10,
            font=FONT_MONO,
            state="disabled",
        )
        scrollbar = ctk.CTkScrollbar(self, command=self.text.yview, width=12)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.bind("<Map>", self._handle_map, add="+")

    def set_autoscroll(self, enabled: bool) -> None:
        self._autoscroll = enabled
        if not enabled:
            self._scroll_pending = False
            return
        self._scroll_pending = True
        self.on_shown()

    def on_shown(self) -> None:
        if self._autoscroll and self._scroll_pending:
            self._request_scroll_to_bottom()

    def _handle_map(self, _event=None) -> None:
        self.on_shown()

    def append(self, records: tuple[LogRecord, ...]) -> None:
        if not records:
            return
        mapped = bool(self.winfo_ismapped())
        near_bottom = mapped and self.text.yview()[1] >= 0.98
        self.text.configure(state="normal")
        for record in records:
            self._insert_terminal_text(record.text)
        self.text.configure(state="disabled")
        if self._autoscroll and (near_bottom or not mapped):
            self._scroll_pending = True
            self._request_scroll_to_bottom()

    def clear_rendered(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._carriage_return_pending = False
        self._scroll_pending = False

    def _request_scroll_to_bottom(self) -> None:
        if self._scroll_after_id is None:
            self._scroll_after_id = self.after_idle(self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        self._scroll_after_id = None
        if not self._autoscroll or not self._scroll_pending:
            return
        if not self.winfo_ismapped():
            return
        self.text.see("end")
        self._scroll_pending = False

    def destroy(self) -> None:
        if self._scroll_after_id is not None:
            try:
                self.after_cancel(self._scroll_after_id)
            except tk.TclError:
                pass
            self._scroll_after_id = None
        super().destroy()

    def _insert_terminal_text(self, value: str) -> None:
        value = value.replace("\r\n", "\n")
        start = 0
        for index, character in enumerate(value):
            if character not in ("\r", "\n"):
                continue
            self._insert_terminal_segment(value[start:index])
            if character == "\r":
                self._carriage_return_pending = True
            else:
                self.text.insert("end", "\n")
                self._carriage_return_pending = False
            start = index + 1
        self._insert_terminal_segment(value[start:])

    def _insert_terminal_segment(self, value: str) -> None:
        if not value:
            return
        if self._carriage_return_pending:
            self.text.delete("end-1c linestart", "end-1c lineend")
            self._carriage_return_pending = False
        self.text.insert("end", value)
