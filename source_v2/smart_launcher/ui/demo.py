from __future__ import annotations

import argparse
from pathlib import Path

import customtkinter as ctk

from smart_launcher.domain.comfy import ServerState
from smart_launcher.domain.events import (
    DomainEvent,
    EventSeverity,
    ProgressChannel,
    ProgressMode,
    ProgressState,
)
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore
from smart_launcher.ui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--tab", choices=("events", "console"), default="events")
    parser.add_argument("--geometry", default="960x720")
    args = parser.parse_args()

    ctk.set_appearance_mode("dark")
    logs = LogStore()
    events = EventJournal()
    progress = ProgressStore()
    _seed(logs, events, progress)
    window = MainWindow(
        logs,
        events,
        progress,
        on_start=lambda: None,
        on_stop=lambda: None,
        on_maintenance=lambda: True,
        on_cancel_maintenance=lambda: True,
        state_provider=lambda: ServerState.STOPPED,
        maintenance_running_provider=lambda: False,
    )
    if args.tab == "console":
        window.show_tab("Консоль")
    if args.screenshot is not None:
        window.geometry(f"{args.geometry}+120+80")
        window.attributes("-topmost", True)
        window.lift()
        window.focus_force()
        window.after(1200, lambda: _capture(window, args.screenshot))
    window.mainloop()
    return 0


def _seed(logs: LogStore, events: EventJournal, progress: ProgressStore) -> None:
    events.append(DomainEvent("LauncherReady", "Лаунчер готов к запуску", EventSeverity.SUCCESS))
    events.append(DomainEvent("CoreCheck", "Проверка обновлений ядра завершена"))
    events.append(
        DomainEvent(
            "DependencyWarning",
            "Требуется синхронизация зависимостей",
            EventSeverity.WARNING,
            "Изменился корневой requirements.txt. Установка ещё не запускалась.",
        )
    )
    events.append(
        DomainEvent(
            "ServerReady",
            "ComfyUI API доступен",
            EventSeverity.SUCCESS,
            "http://127.0.0.1:8188",
        )
    )
    logs.append(
        source="comfyui",
        stream="stdout",
        text="[INFO] Starting ComfyUI server\n[INFO] Device: NVIDIA GeForce RTX 5070\n",
    )
    logs.append(
        source="comfyui",
        stream="stderr",
        text='  File "node.py", line 42, in execute\n    await operation()\n    ^^^^^^^^^^^^^^^^^\n',
    )
    progress.set(
        ProgressState(
            ProgressChannel.GENERATION,
            "prompt-demo",
            "Генерация: KSampler",
            ProgressMode.DETERMINATE,
            62,
            100,
        )
    )
    progress.set(
        ProgressState(
            ProgressChannel.DOWNLOAD,
            "download-demo",
            "Загрузка модели: 1.8 / 5.6 GB",
            ProgressMode.DETERMINATE,
            18,
            56,
        )
    )


def _capture(window: MainWindow, path: Path) -> None:
    from PIL import ImageGrab

    window.update_idletasks()
    window.update()
    left = window.winfo_rootx()
    top = window.winfo_rooty()
    right = left + window.winfo_width()
    bottom = top + window.winfo_height()
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(left, top, right, bottom)).save(path)
    window.after(200, window.destroy)


if __name__ == "__main__":
    raise SystemExit(main())
