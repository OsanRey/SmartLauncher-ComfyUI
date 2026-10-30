from __future__ import annotations

from collections.abc import Callable
import webbrowser
import re

import customtkinter as ctk

from smart_launcher.application.maintenance_controller import MaintenanceController
from smart_launcher.domain.updates import UpdatePlan
from smart_launcher.ui.theme import COLORS, FONT_UI
from smart_launcher.ui.window_position import center_over_parent


def update_summary(plan: UpdatePlan) -> str:
    lines = ["Что будет обновлено", "• Ядро ComfyUI."]
    if plan.changed_manifests:
        lines.append("• Требования к зависимостям. Недостающие или несовместимые пакеты могут быть заменены.")
    else:
        lines.append("• Зависимости не изменяются: установка пакетов не требуется.")
    titles = "\n".join(plan.changes).lower()
    topics = [label for pattern, label in (
        (r"\bfix(?:es|ed)?\b|\bbug", "исправления ошибок"),
        (r"\bfrontend\b", "веб-интерфейс"),
        (r"\btemplates?\b|\bblueprints?\b", "шаблоны рабочих процессов"),
        (r"\boptim\w*|\bfaster\b|\bspeed\b|\bperformance\b", "оптимизации"),
        (r"\[partner nodes\]", "интеграции внешних моделей"),
    ) if re.search(pattern, titles)]
    if topics:
        lines.extend(["", "Темы в названиях изменений", "• " + "; ".join(topics) + "."])
    lines.extend(["", "Что останется", "• Окружение Python не пересоздаётся.",
                  "• Обновление установленных Custom Nodes не запускается."])
    return "\n".join(lines)


class UpdateDialog(ctk.CTkToplevel):
    def __init__(
        self, master, *, controller: MaintenanceController,
        on_check: Callable[[], bool], on_update: Callable[[], bool],
    ) -> None:
        super().__init__(master, fg_color=COLORS["window"])
        self.title("Обновление ComfyUI")
        self.geometry("720x540")
        self.minsize(600, 440)
        self.transient(master)
        self.grab_set()
        self._controller = controller
        self._on_update = on_update
        self._closed = False
        self._timer = None
        self._rendered = object()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.heading = ctk.CTkLabel(
            self, text="Проверка обновлений...", anchor="w", font=("Segoe UI", 18),
        )
        self.heading.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        self.versions = ctk.CTkLabel(self, text="", anchor="w", justify="left", font=FONT_UI, wraplength=550)
        self.versions.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=3, column=0, sticky="nsew", padx=20)
        for name in ("Кратко", "Технические детали"):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
        self.summary = ctk.CTkTextbox(self.tabs.tab("Кратко"), wrap="word", font=("Segoe UI", 14))
        self.summary.grid(row=0, column=0, sticky="nsew")
        self.content = ctk.CTkTextbox(self.tabs.tab("Технические детали"), wrap="word", font=FONT_UI)
        self.content.grid(row=0, column=0, sticky="nsew")
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=20, pady=16)
        actions.grid_columnconfigure(0, weight=1)
        self.compare = ctk.CTkButton(
            actions, text="Сравнить на GitHub", width=165,
            command=self._compare, state="disabled",
        )
        self.compare.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(actions, text="Закрыть", command=self.destroy, width=110).grid(
            row=0, column=1, padx=(0, 10),
        )
        self.install = ctk.CTkButton(
            actions, text="Обновить", command=self._install, state="disabled", width=140,
        )
        self.install.grid(row=0, column=2)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        center_over_parent(self, master)
        if controller.available_plan is None and not controller.running:
            if not on_check():
                self._show("Проверка недоступна", "Остановите ComfyUI и дождитесь завершения других операций.")
                return
        self._poll()

    def _show(self, heading: str, body: str, *, summary: str | None = None) -> None:
        self.heading.configure(text=heading)
        for widget, text in ((self.content, body), (self.summary, summary if summary is not None else body)):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.yview_moveto(0)
            widget.configure(state="disabled")

    def _poll(self) -> None:
        if self._closed:
            return
        plan = self._controller.available_plan
        result = self._controller.last_result
        running = self._controller.running
        state = (plan, result, running)
        if state != self._rendered:
            self._rendered = state
            self.install.configure(state="normal" if plan and not running else "disabled")
            self.compare.configure(state="normal" if plan and plan.compare_url else "disabled")
            if plan:
                current = plan.current_label or plan.current_commit[:12]
                target = plan.target_ref.removeprefix("refs/tags/")
                self.versions.configure(text=f"Установлено: {current}   →   Доступно: {target}")
                history = "\n".join(plan.changes) or "Список коммитов недоступен."
                body = (
                    plan.version_summary + "\n\n" + plan.dependency_summary
                    + "\n\nИзменения ядра (до 50 последних коммитов):\n" + history
                    + "\n\nCustom Nodes этой операцией не обновляются. Venv не пересоздаётся."
                )
                if plan.changed_manifests:
                    body += (
                        "\n\nИзменения требований (+ добавлено, - удалено):\n"
                        + (plan.requirements_diff or "Подробности недоступны.")
                        + "\n\nФактический набор изменяемых пакетов определяется pip при обновлении."
                    )
                self._show("Доступно обновление ComfyUI", body, summary=update_summary(plan))
            elif running:
                self.versions.configure(text="")
                self._show("Проверка обновлений...", "Получение информации о версиях ComfyUI.")
            elif result:
                self.versions.configure(text="")
                self._show("ComfyUI актуален" if result.succeeded else "Проверка не выполнена", result.detail)
        self._timer = self.after(150, self._poll)

    def _install(self) -> None:
        if self._on_update():
            self.destroy()
        else:
            self._show("Обновление недоступно", "Остановите ComfyUI и дождитесь завершения других операций.")

    def _compare(self) -> None:
        plan = self._controller.available_plan
        if plan and plan.compare_url:
            webbrowser.open(plan.compare_url, new=2)

    def destroy(self) -> None:
        self._closed = True
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        super().destroy()
