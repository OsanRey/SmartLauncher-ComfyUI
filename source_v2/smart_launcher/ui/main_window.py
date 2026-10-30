from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from smart_launcher.domain.comfy import ServerState
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore
from smart_launcher.ui.console_view import ConsoleView
from smart_launcher.ui.event_view import EventView
from smart_launcher.ui.progress_view import ProgressView
from smart_launcher.ui.projection import UiProjection
from smart_launcher.ui.theme import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD
from smart_launcher.ui.window_position import center_on_screen


class MainWindow(ctk.CTk):
    def __init__(
        self,
        logs: LogStore,
        events: EventJournal,
        progress: ProgressStore,
        *,
        on_start: Callable[[], None] | None = None,
        on_open_browser: Callable[[], None] | None = None,
        on_copy_lan: Callable[[], str | None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_maintenance: Callable[[], bool] | None = None,
        on_update_details: Callable[[], bool] | None = None,
        on_install: Callable[[], None] | None = None,
        on_cancel_maintenance: Callable[[], bool] | None = None,
        state_provider: Callable[[], ServerState] | None = None,
        maintenance_running_provider: Callable[[], bool] | None = None,
        installation_required_provider: Callable[[], bool] | None = None,
        configured_provider: Callable[[], bool] | None = None,
        update_available_provider: Callable[[], bool] | None = None,
        lan_enabled_provider: Callable[[], bool] | None = None,
        on_settings: Callable[[], None] | None = None,
        on_components: Callable[[], None] | None = None,
        on_downloader: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(fg_color=COLORS["window"])
        self.title("Smart Launcher ComfyUI 2.0")
        self.geometry("960x720")
        self.minsize(760, 560)
        self._projection = UiProjection(logs, events, progress)
        self._on_start = on_start
        self._on_open_browser = on_open_browser
        self._on_copy_lan = on_copy_lan
        self._on_stop = on_stop
        self._on_maintenance = on_maintenance
        self._on_update_details = on_update_details
        self._on_install = on_install
        self._on_cancel_maintenance = on_cancel_maintenance
        self._state_provider = state_provider
        self._maintenance_running_provider = maintenance_running_provider
        self._installation_required_provider = installation_required_provider
        self._configured_provider = configured_provider
        self._update_available_provider = update_available_provider
        self._lan_enabled_provider = lan_enabled_provider
        self._on_settings = on_settings
        self._on_components = on_components
        self._on_downloader = on_downloader
        self._rendered_state: ServerState | None = None
        self._rendered_maintenance_running: bool | None = None
        self._rendered_installation_required: bool | None = None
        self._rendered_configured: bool | None = None
        self._rendered_update_available: bool | None = None
        self._rendered_lan_enabled: bool | None = None
        self._event_count = 0
        self._active_tab = "События"
        self._unread_notices = False
        self._closed = False
        self._projection_after_id: str | None = None
        self._copy_feedback_after_id: str | None = None
        self._build()
        center_on_screen(self)
        self.bind("<FocusIn>", self._resume_visuals, add="+")
        self.bind("<Map>", self._resume_visuals, add="+")
        self._projection_after_id = self.after(40, self._drain_projection)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent", height=52)
        toolbar.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 7))
        toolbar.grid_columnconfigure(1, weight=1)
        title = ctk.CTkLabel(
            toolbar,
            text="Smart Launcher 2.0",
            text_color=COLORS["text"],
            font=("Segoe UI Semibold", 16),
        )
        title.grid(row=0, column=0, sticky="w")
        self.settings_button = ctk.CTkButton(
            toolbar,
            text="⚙",
            width=42,
            height=36,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            font=FONT_UI_BOLD,
            command=self._invoke_settings,
            state="normal" if self._on_settings else "disabled",
        )
        self.settings_button.grid(row=0, column=2, padx=(8, 0))
        self.components_button = ctk.CTkButton(
            toolbar,
            text="Система",
            width=76,
            height=36,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            font=FONT_UI_BOLD,
            command=self._invoke_components,
            state="normal" if self._on_components else "disabled",
        )
        self.components_button.grid(row=0, column=3, padx=(6, 0))
        self.downloader_button = ctk.CTkButton(
            toolbar,
            text="Модели",
            width=72,
            height=36,
            corner_radius=5,
            fg_color=COLORS["surface_raised"],
            hover_color=COLORS["surface_hover"],
            font=FONT_UI_BOLD,
            command=self._invoke_downloader,
            state="normal" if self._on_downloader else "disabled",
        )
        self.downloader_button.grid(row=0, column=4, padx=(6, 0))
        self.launch_actions = ctk.CTkFrame(
            toolbar,
            width=150,
            height=36,
            fg_color="transparent",
            corner_radius=0,
        )
        self.launch_actions.grid(row=0, column=6, padx=(8, 6))
        self.launch_actions.grid_propagate(False)
        self.launch_actions.grid_rowconfigure(0, weight=1)
        self.launch_actions.grid_columnconfigure(0, weight=3, uniform="launch")
        self.launch_actions.grid_columnconfigure(1, weight=1, uniform="launch")
        self.start_button = ctk.CTkButton(
            self.launch_actions,
            text="Запустить ComfyUI",
            width=1,
            height=36,
            corner_radius=5,
            fg_color="#237A42",
            hover_color="#2A9150",
            font=FONT_UI_BOLD,
            command=self._invoke_start,
            state="normal" if self._on_start else "disabled",
        )
        self.start_button.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.copy_lan_button = ctk.CTkButton(
            self.launch_actions,
            text="⧉",
            width=1,
            height=36,
            corner_radius=5,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            font=FONT_UI_BOLD,
            command=self._invoke_copy_lan,
        )
        self._copy_lan_tooltip = _Tooltip(
            self.copy_lan_button, "Копировать LAN-адрес"
        )
        self.maintenance_button = ctk.CTkButton(
            toolbar,
            text="Обновить",
            width=104,
            height=36,
            corner_radius=5,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            font=FONT_UI_BOLD,
            command=self._invoke_maintenance,
            state="normal" if self._on_maintenance or self._on_install else "disabled",
        )
        self.maintenance_button.grid(row=0, column=5, padx=(8, 0))
        self.stop_button = ctk.CTkButton(
            toolbar,
            text="Остановить",
            width=92,
            height=36,
            corner_radius=5,
            fg_color="#A8323B",
            hover_color="#C13C47",
            font=FONT_UI_BOLD,
            command=self._invoke_stop,
            state="normal" if self._on_stop else "disabled",
        )
        self.stop_button.grid(row=0, column=7)

        status_band = ctk.CTkFrame(
            self,
            height=34,
            fg_color=COLORS["surface"],
            corner_radius=5,
            border_width=1,
            border_color=COLORS["border"],
        )
        status_band.grid(row=1, column=0, sticky="ew", padx=20)
        status_band.grid_propagate(False)
        status_band.grid_columnconfigure(1, weight=1)
        self.status_label = ctk.CTkLabel(
            status_band,
            text="Готов",
            text_color=COLORS["green"],
            font=FONT_SMALL,
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=10)
        self.profile_label = ctk.CTkLabel(
            status_band,
            text="Профиль: Обычный  |  LAN: выкл  |  Порт: 8188",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
            anchor="e",
        )
        self.profile_label.grid(row=0, column=1, sticky="e", padx=10)

        tabs = ctk.CTkFrame(self, fg_color="transparent")
        tabs.grid(row=2, column=0, sticky="ew", padx=20, pady=(10, 7))
        tabs.grid_columnconfigure(1, weight=1)
        self.tab_switch = ctk.CTkSegmentedButton(
            tabs,
            values=["События", "Консоль", "Предупреждения"],
            width=340,
            dynamic_resizing=False,
            command=self._show_tab,
            selected_color=COLORS["blue"],
            selected_hover_color=COLORS["blue_hover"],
            unselected_color=COLORS["surface_raised"],
            unselected_hover_color=COLORS["surface_hover"],
            corner_radius=5,
            font=FONT_UI_BOLD,
        )
        self.tab_switch.grid(row=0, column=0, sticky="w")
        self.tab_switch.set("События")
        self.event_count_label = ctk.CTkLabel(
            tabs,
            text="0 событий",
            text_color=COLORS["muted"],
            font=FONT_SMALL,
        )
        self.event_count_label.grid(row=0, column=1, sticky="e", padx=(0, 12))
        self.autoscroll = ctk.CTkCheckBox(
            tabs,
            text="Автопрокрутка",
            width=112,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            text_color=COLORS["text"],
            font=FONT_SMALL,
            command=self._toggle_autoscroll,
        )
        self.autoscroll.grid(row=0, column=2, sticky="e")
        self.autoscroll.select()

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=3, column=0, sticky="nsew", padx=20)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        self.event_view = EventView(content, on_update_details=self._on_update_details)
        self.notice_view = EventView(content, selectable=True)
        self.console_view = ConsoleView(content)
        self.event_view.grid(row=0, column=0, sticky="nsew")

        self.progress_view = ProgressView(self)
        self.progress_view.grid(row=4, column=0, sticky="ew", padx=20, pady=(7, 12))

    def set_status(self, text: str, *, color: str | None = None) -> None:
        self.status_label.configure(text=text, text_color=color or COLORS["text"])

    def set_profile_summary(self, text: str) -> None:
        self.profile_label.configure(text=text)

    def show_tab(self, value: str) -> None:
        if value not in {"События", "Консоль", "Предупреждения"}:
            raise ValueError(f"Unknown tab: {value}")
        self.tab_switch.set(value)
        self._show_tab(value)

    def _show_tab(self, value: str) -> None:
        value = value.removesuffix(" •")
        self._active_tab = value
        self.event_view.grid_remove()
        self.console_view.grid_remove()
        self.notice_view.grid_remove()
        self.event_count_label.grid_remove()
        if value == "Консоль":
            self.console_view.grid(row=0, column=0, sticky="nsew")
            self.console_view.on_shown()
        elif value == "Предупреждения":
            self._set_unread_notices(False)
            self.notice_view.grid(row=0, column=0, sticky="nsew")
        else:
            self.event_view.grid(row=0, column=0, sticky="nsew")
        if value != "Предупреждения":
            self.event_count_label.grid()

    def _set_unread_notices(self, unread: bool) -> None:
        if unread == self._unread_notices:
            return
        self._unread_notices = unread
        label = "Предупреждения •" if unread else "Предупреждения"
        self.tab_switch.configure(values=["События", "Консоль", label])
        self.tab_switch.set(label if self._active_tab == "Предупреждения" else self._active_tab)

    def _toggle_autoscroll(self) -> None:
        enabled = bool(self.autoscroll.get())
        self.console_view.set_autoscroll(enabled)
        self.event_view.set_autoscroll(enabled)
        self.notice_view.set_autoscroll(enabled)

    def _invoke_start(self) -> None:
        if self._on_start is not None:
            try:
                self._on_start()
            except Exception:
                self.set_status("Ошибка запуска ComfyUI", color=COLORS["red"])

    def _invoke_open_browser(self) -> None:
        if self._on_open_browser is not None:
            self._on_open_browser()

    def _invoke_copy_lan(self) -> None:
        if self._on_copy_lan is None:
            return
        value = self._on_copy_lan()
        if not value:
            self.set_status("Не удалось определить LAN-адрес", color=COLORS["red"])
            return
        self.copy_lan_button.configure(text="✓")
        if self._copy_feedback_after_id is not None:
            self.after_cancel(self._copy_feedback_after_id)
        self._copy_feedback_after_id = self.after(1600, self._restore_copy_icon)

    def _restore_copy_icon(self) -> None:
        self._copy_feedback_after_id = None
        if not self._closed:
            self.copy_lan_button.configure(text="⧉")

    def _invoke_settings(self) -> None:
        if self._on_settings is not None:
            self._on_settings()

    def _invoke_components(self) -> None:
        if self._on_components is not None:
            self._on_components()

    def _invoke_downloader(self) -> None:
        if self._on_downloader is not None:
            self._on_downloader()

    def _invoke_stop(self) -> None:
        if self._on_stop is not None:
            try:
                self._on_stop()
            except Exception:
                self.set_status("Ошибка остановки ComfyUI", color=COLORS["red"])

    def _invoke_maintenance(self) -> None:
        running = bool(
            self._maintenance_running_provider
            and self._maintenance_running_provider()
        )
        installation_required = bool(
            self._installation_required_provider
            and self._installation_required_provider()
        )
        callback = (
            self._on_cancel_maintenance
            if running
            else self._on_install
            if installation_required
            else self._on_maintenance
        )
        if callback is not None:
            callback()

    def _drain_projection(self) -> None:
        if self._closed:
            return
        self._refresh_server_state()
        batch = self._projection.drain()
        self.console_view.append(batch.logs)
        notices = tuple(entry for entry in batch.events if entry.event.kind == "ConsoleNotice")
        events = tuple(entry for entry in batch.events if entry.event.kind != "ConsoleNotice")
        self.event_view.append(events)
        self.notice_view.append(notices)
        if notices and self._active_tab != "Предупреждения":
            self._set_unread_notices(True)
        if events:
            self._event_count += len(events)
            self.event_count_label.configure(text=_event_count_text(self._event_count))
        if batch.progress_changed:
            self.progress_view.render(batch.progress)
        self.progress_view.refresh_animation()
        self._projection_after_id = self.after(40, self._drain_projection)

    def _resume_visuals(self, _event=None) -> None:
        if self._closed:
            return
        self.progress_view.refresh_animation()

    def destroy(self) -> None:
        self._closed = True
        if self._projection_after_id is not None:
            try:
                self.after_cancel(self._projection_after_id)
            except Exception:
                pass
            self._projection_after_id = None
        if self._copy_feedback_after_id is not None:
            try:
                self.after_cancel(self._copy_feedback_after_id)
            except Exception:
                pass
            self._copy_feedback_after_id = None
        super().destroy()

    def _refresh_server_state(self) -> None:
        self._refresh_maintenance_state()
        if self._state_provider is None:
            return
        state = self._state_provider()
        lan_enabled = bool(self._lan_enabled_provider and self._lan_enabled_provider())
        if state is self._rendered_state and lan_enabled == self._rendered_lan_enabled:
            return
        self._rendered_state = state
        self._rendered_lan_enabled = lan_enabled
        labels = {
            ServerState.STOPPED: ("Остановлено", COLORS["muted"]),
            ServerState.STARTING: ("Запуск ComfyUI...", COLORS["yellow"]),
            ServerState.READY: ("ComfyUI запущен", COLORS["green"]),
            ServerState.STOPPING: ("Остановка ComfyUI...", COLORS["yellow"]),
            ServerState.FAILED: ("Ошибка ComfyUI", COLORS["red"]),
        }
        text, color = labels[state]
        self.set_status(text, color=color)
        self._render_launch_actions(state, lan_enabled)
        self.stop_button.configure(
            state="normal" if state in (ServerState.STARTING, ServerState.READY) else "disabled"
        )
        self._refresh_maintenance_state(force=True)

    def _render_launch_actions(self, state: ServerState, lan_enabled: bool) -> None:
        self.copy_lan_button.grid_remove()
        if state is ServerState.READY:
            self.start_button.configure(
                text="Открыть" if lan_enabled else "Открыть в браузере",
                command=self._invoke_open_browser,
                state="normal" if self._on_open_browser else "disabled",
                fg_color=COLORS["blue"],
                hover_color=COLORS["blue_hover"],
            )
            if lan_enabled:
                self.start_button.grid_configure(column=0, columnspan=1, padx=(0, 3))
                self.copy_lan_button.grid(
                    row=0, column=1, sticky="nsew", padx=(3, 0)
                )
                self.copy_lan_button.configure(
                    state="normal" if self._on_copy_lan else "disabled"
                )
            else:
                self.start_button.grid_configure(column=0, columnspan=2, padx=0)
            return
        self.start_button.grid_configure(column=0, columnspan=2, padx=0)
        self.start_button.configure(
            text="Запустить ComfyUI",
            command=self._invoke_start,
            state=(
                "normal"
                if state in (ServerState.STOPPED, ServerState.FAILED) and self._on_start
                else "disabled"
            ),
            fg_color="#237A42",
            hover_color="#2A9150",
        )

    def _refresh_maintenance_state(self, *, force: bool = False) -> None:
        if self._maintenance_running_provider is None:
            return
        running = self._maintenance_running_provider()
        installation_required = bool(
            self._installation_required_provider
            and self._installation_required_provider()
        )
        configured = bool(self._configured_provider and self._configured_provider())
        update_available = bool(
            self._update_available_provider and self._update_available_provider()
        )
        if (
            not force
            and running == self._rendered_maintenance_running
            and installation_required == self._rendered_installation_required
            and configured == self._rendered_configured
            and update_available == self._rendered_update_available
        ):
            return
        self._rendered_maintenance_running = running
        self._rendered_installation_required = installation_required
        self._rendered_configured = configured
        self._rendered_update_available = update_available
        if running:
            self.maintenance_button.configure(
                text="Отменить",
                state="normal" if self._on_cancel_maintenance else "disabled",
                fg_color=COLORS["red"],
                hover_color="#C13C47",
            )
            self.start_button.configure(state="disabled")
            return
        server_state = self._state_provider() if self._state_provider else ServerState.STOPPED
        self.maintenance_button.configure(
            text=(
                "Настроить"
                if not configured
                else "Установить"
                if installation_required
                else "Обновить •"
                if update_available
                else "Обновить"
            ),
            state=(
                "normal"
                if (self._on_install if installation_required else self._on_maintenance)
                and server_state in (ServerState.STOPPED, ServerState.FAILED)
                else "disabled"
            ),
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
        )
        lan_enabled = bool(self._lan_enabled_provider and self._lan_enabled_provider())
        self._render_launch_actions(server_state, lan_enabled)
        if installation_required:
            self.start_button.configure(state="disabled")


class _Tooltip:
    def __init__(self, widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.window is not None:
            return
        self.window = ctk.CTkToplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        x = self.widget.winfo_rootx() + self.widget.winfo_width() - 160
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window.geometry(f"+{max(0, x)}+{y}")
        ctk.CTkLabel(
            self.window,
            text=self.text,
            fg_color=COLORS["surface_raised"],
            text_color=COLORS["text"],
            corner_radius=4,
            padx=8,
            pady=4,
            font=FONT_SMALL,
        ).pack()

    def _hide(self, _event=None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


def _event_count_text(count: int) -> str:
    remainder_100 = count % 100
    remainder_10 = count % 10
    if remainder_10 == 1 and remainder_100 != 11:
        word = "событие"
    elif remainder_10 in (2, 3, 4) and remainder_100 not in (12, 13, 14):
        word = "события"
    else:
        word = "событий"
    return f"{count} {word}"
