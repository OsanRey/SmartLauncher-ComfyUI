from __future__ import annotations

import argparse
from pathlib import Path

import customtkinter as ctk

from smart_launcher.application.download_controller import DownloadController
from smart_launcher.application.custom_node_controller import CustomNodeController
from smart_launcher.application.custom_node_catalog import CustomNodeCatalog
from smart_launcher.application.custom_node_pipeline import CustomNodePipeline
from smart_launcher.application.operation_coordinator import OperationCoordinator
from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.downloads.resolver import ModelLinkResolver
from smart_launcher.downloads.transport import ModelDownloadTransport
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore
from smart_launcher.infrastructure.command_runner import CommandRunner
from smart_launcher.ui.downloader_window import DownloaderWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--tab", choices=("models", "nodes"), default="models")
    args = parser.parse_args()
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()
    progress = ProgressStore()
    logs = LogStore()
    events = EventJournal()
    controller = DownloadController(ModelDownloadTransport(), logs, events, progress)
    custom_nodes = CustomNodeController(
        CustomNodePipeline(CommandRunner(), server_is_stopped=lambda: True),
        logs,
        events,
        progress,
        OperationCoordinator(),
    )
    window = DownloaderWindow(
        root,
        LauncherSettings(Path("D:/IndependentComfy"), Path("D:/IndependentComfy/venv")),
        controller,
        ModelLinkResolver(),
        progress,
        custom_nodes,
        CustomNodeCatalog(CommandRunner()),
        on_tokens_changed=lambda *_args: None,
        on_custom_node_start=custom_nodes.start,
        on_custom_node_cancel=custom_nodes.cancel,
    )
    window.geometry("720x650+120+70")
    if args.tab == "nodes":
        window.tabs.set("Custom Nodes")
    window.attributes("-topmost", True)
    window.after(900, lambda: _capture(window, controller, custom_nodes, args.screenshot))
    root.mainloop()
    return 0


def _capture(
    window: DownloaderWindow,
    controller: DownloadController,
    custom_nodes: CustomNodeController,
    path: Path,
) -> None:
    from PIL import ImageGrab

    window.update_idletasks()
    box = (
        window.winfo_rootx(),
        window.winfo_rooty(),
        window.winfo_rootx() + window.winfo_width(),
        window.winfo_rooty() + window.winfo_height(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=box).save(path)
    controller.close()
    custom_nodes.close()
    window.destroy()
    window.master.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
