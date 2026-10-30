from __future__ import annotations

import time
import tkinter as tk

import customtkinter as ctk

from smart_launcher.domain.events import ProgressChannel, ProgressMode, ProgressState
from smart_launcher.ui.theme import COLORS, FONT_SMALL

_CHANNEL_COLORS = {
    ProgressChannel.STARTUP: COLORS["blue"],
    ProgressChannel.GENERATION: COLORS["green"],
    ProgressChannel.DOWNLOAD: COLORS["cyan"],
    ProgressChannel.MAINTENANCE: COLORS["yellow"],
}


def _activity_segment_bounds(width: float, phase: float) -> tuple[float, float]:
    segment_width = max(28.0, width * 0.22)
    left = -segment_width + min(1.0, max(0.0, phase)) * (width + segment_width)
    return left, left + segment_width


def _progress_layout(count: int) -> tuple[tuple[int, int, int], ...]:
    if count <= 0:
        return ()
    if count == 1:
        return ((0, 0, 2),)
    if count == 2:
        return ((0, 0, 1), (0, 1, 1))
    if count == 3:
        return ((0, 0, 1), (0, 1, 1), (1, 0, 2))
    return ((0, 0, 1), (0, 1, 1), (1, 0, 1), (1, 1, 1))


class _ActivityBar(tk.Canvas):
    def __init__(self, master) -> None:
        super().__init__(
            master,
            height=5,
            background=COLORS["surface"],
            borderwidth=0,
            highlightthickness=0,
        )
        self._running = False
        self._started_at = 0.0
        self._duration = 1.35
        self._color = COLORS["green"]
        self._after_id: str | None = None
        self._fraction: float | None = None
        self.bind("<Configure>", lambda _event: self._redraw())

    def start(self, color: str) -> None:
        self._color = color
        self._fraction = None
        self.configure(background=COLORS["border"])
        if not self._running:
            self._running = True
            self._started_at = time.monotonic()
        self.refresh_animation()

    def set_fraction(self, value: float, color: str) -> None:
        self._stop_animation()
        self._color = color
        self._fraction = min(1.0, max(0.0, value))
        self.configure(background=COLORS["border"])
        self._redraw()

    def clear(self) -> None:
        self._stop_animation()
        self._fraction = None
        self.delete("all")
        self.configure(background=COLORS["surface"])

    def _stop_animation(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._after_id = None

    def _animate(self) -> None:
        self._after_id = None
        if not self._running:
            return
        try:
            self._redraw()
        finally:
            if self._running and self._after_id is None:
                self._after_id = self.after(32, self._animate)

    def refresh_animation(self) -> None:
        if not self._running:
            return
        self._redraw()
        if self._after_id is None:
            self._after_id = self.after(32, self._animate)

    def _redraw(self) -> None:
        self.delete("all")
        width = max(1.0, float(self.winfo_width()))
        height = max(1.0, float(self.winfo_height()))
        if self._running:
            phase = ((time.monotonic() - self._started_at) % self._duration) / self._duration
            left, right = _activity_segment_bounds(width, phase)
            self.create_rectangle(left, 0, right, height, fill=self._color, outline="")
        elif self._fraction is not None and self._fraction > 0:
            self.create_rectangle(
                0,
                0,
                width * self._fraction,
                height,
                fill=self._color,
                outline="",
            )


class ProgressView(ctk.CTkFrame):
    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master,
            height=76,
            fg_color=COLORS["surface"],
            corner_radius=5,
            border_width=1,
            border_color=COLORS["border"],
            **kwargs,
        )
        self.grid_propagate(False)
        self._rows: list[tuple[ctk.CTkLabel, _ActivityBar]] = []
        for _index in range(4):
            label = ctk.CTkLabel(
                self,
                text="",
                text_color=COLORS["muted"],
                font=FONT_SMALL,
                anchor="w",
            )
            bar = _ActivityBar(self)
            self._rows.append((label, bar))
        self.grid_columnconfigure((0, 1), weight=1, uniform="progress")
        self.bind("<Map>", lambda _event: self.refresh_animation())
        self.bind("<Visibility>", lambda _event: self.refresh_animation())

    def render(self, states: tuple[ProgressState, ...]) -> None:
        layout = _progress_layout(min(len(states), len(self._rows)))
        compact = len(layout) >= 3
        for index, (label, bar) in enumerate(self._rows):
            state = states[index] if index < len(states) else None
            if state is None:
                label.grid_remove()
                bar.grid_remove()
                label.configure(text="")
                bar.clear()
                continue
            row, column, span = layout[index]
            vertical_padding = 5 if compact else 14
            label.grid(
                row=row * 2,
                column=column,
                columnspan=span,
                sticky="ew",
                padx=9,
                pady=(vertical_padding, 0),
            )
            bar.grid(
                row=row * 2 + 1,
                column=column,
                columnspan=span,
                sticky="ew",
                padx=9,
                pady=(0, vertical_padding),
            )
            label.configure(text=state.label)
            color = _CHANNEL_COLORS[state.channel]
            if state.mode is ProgressMode.INDETERMINATE:
                bar.start(color)
            else:
                bar.set_fraction(state.fraction or 0.0, color)

    def refresh_animation(self) -> None:
        for _label, bar in self._rows:
            bar.refresh_animation()
