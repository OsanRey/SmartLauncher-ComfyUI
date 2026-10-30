from __future__ import annotations

from collections import deque
from collections.abc import Callable
import tkinter as tk

import customtkinter as ctk

from smart_launcher.domain.events import EventSeverity, JournalEntry
from smart_launcher.ui.theme import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD

_ACCENTS = {
    EventSeverity.INFO: COLORS["cyan"],
    EventSeverity.SUCCESS: COLORS["green"],
    EventSeverity.WARNING: COLORS["yellow"],
    EventSeverity.ERROR: COLORS["red"],
}


class EventView(ctk.CTkFrame):
    def __init__(self, master, *, rendered_limit: int = 500,
                 on_update_details: Callable[[], object] | None = None,
                 selectable: bool = False, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._rendered_limit = rendered_limit
        self._on_update_details = on_update_details
        self._selectable = selectable
        self._autoscroll = True
        self._rows: deque[tk.Frame] = deque()
        self._content = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["muted"],
        )
        self._content.pack(fill="both", expand=True)
        self._content.grid_columnconfigure(0, weight=1)

    def append(self, entries: tuple[JournalEntry, ...]) -> None:
        canvas = getattr(self._content, "_parent_canvas", None)
        near_bottom = canvas is None or canvas.yview()[1] >= 0.98
        for entry in entries:
            event = entry.event
            row = tk.Frame(
                self._content,
                background=COLORS["surface"],
                highlightthickness=1,
                highlightbackground=COLORS["border"],
                highlightcolor=COLORS["border"],
                borderwidth=0,
            )
            accent = tk.Frame(
                row,
                width=3,
                background=_ACCENTS[event.severity],
                borderwidth=0,
            )
            accent.grid(
                row=0,
                column=0,
                rowspan=2 if event.detail else 1,
                sticky="ns",
                padx=(7, 7),
                pady=6,
            )
            label_type = _SelectableText if self._selectable else tk.Label
            title = label_type(
                row,
                text=event.title,
                foreground=COLORS["text"],
                background=COLORS["surface"],
                font=FONT_UI_BOLD,
                anchor="w",
                justify="left",
                borderwidth=0,
            )
            title.grid(row=0, column=1, sticky="ew", pady=(5, 1))
            stamp = tk.Label(
                row,
                text=event.timestamp.astimezone().strftime("%H:%M:%S"),
                foreground=COLORS["muted"],
                background=COLORS["surface"],
                font=FONT_SMALL,
                anchor="e",
                borderwidth=0,
            )
            stamp.grid(row=0, column=2, sticky="e", padx=(8, 9), pady=(5, 1))
            if event.detail:
                detail = label_type(
                    row,
                    text=event.detail,
                    foreground=COLORS["muted"],
                    background=COLORS["surface"],
                    font=FONT_UI,
                    anchor="w",
                    justify="left",
                    wraplength=620,
                    borderwidth=0,
                )
                detail.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(0, 5), padx=(0, 8))
            row.grid_columnconfigure(1, weight=1)
            if event.kind == "UpdateAvailable" and self._on_update_details:
                ctk.CTkButton(
                    row, text="Подробнее", width=105, height=28,
                    command=self._on_update_details,
                ).grid(row=2, column=1, sticky="w", pady=(2, 8))
            row.grid(
                row=entry.sequence,
                column=0,
                sticky="ew",
                padx=(8, 12),
                pady=(0, 5),
            )
            self._rows.append(row)
            while len(self._rows) > self._rendered_limit:
                self._rows.popleft().destroy()
        if entries and self._autoscroll and near_bottom:
            self.after_idle(self._scroll_to_bottom)

    def set_autoscroll(self, enabled: bool) -> None:
        self._autoscroll = enabled
        if enabled:
            self.after_idle(self._scroll_to_bottom)

    def clear_rendered(self) -> None:
        while self._rows:
            self._rows.popleft().destroy()

    def _scroll_to_bottom(self) -> None:
        canvas = getattr(self._content, "_parent_canvas", None)
        if canvas is not None:
            canvas.yview_moveto(1.0)


class _SelectableText(tk.Text):
    """Read-only label replacement that retains native text selection."""

    def __init__(self, master, *, text: str, **kwargs):
        for option in ("anchor", "justify", "wraplength"):
            kwargs.pop(option, None)
        super().__init__(
            master, **kwargs, width=1, height=1, wrap="word",
            highlightthickness=0, padx=0, pady=0, cursor="xterm",
            selectbackground=COLORS["blue"], selectforeground=COLORS["text"],
            exportselection=False,
        )
        self.insert("1.0", text)
        self.configure(state="disabled")
        self.bind("<Configure>", self._fit_height)
        self.bind("<Map>", self._fit_height)
        self.bind("<Button-1>", lambda _event: self.focus_set())
        self.bind("<Control-a>", self._select_all)
        self.bind("<Control-c>", self._copy)
        self.bind("<Control-C>", self._copy)
        self.bind("<Button-3>", self._popup)
        self.bind("<MouseWheel>", self._wheel)
        self._menu = tk.Menu(self, tearoff=False)
        self._menu.add_command(label="Копировать", command=self._copy)
        self._menu.add_command(label="Выделить всё", command=self._select_all)

    def _fit_height(self, _event=None):
        count = int(self.tk.call(self._w, "count", "-update", "-displaylines", "1.0", "end"))
        if int(self.cget("height")) != max(1, count):
            self.configure(height=max(1, count))

    def _select_all(self, _event=None):
        self.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _copy(self, _event=None):
        if self.tag_ranges("sel"):
            self.clipboard_clear()
            self.clipboard_append(self.get("sel.first", "sel.last"))
        return "break"

    def _popup(self, event):
        self.focus_set()
        self._menu.entryconfigure(0, state="normal" if self.tag_ranges("sel") else "disabled")
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()
        return "break"

    def _wheel(self, event):
        canvas = getattr(self.master.master, "_parent_canvas", None)
        if canvas is not None:
            canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"
