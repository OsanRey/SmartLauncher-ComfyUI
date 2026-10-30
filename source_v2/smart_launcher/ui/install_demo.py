from __future__ import annotations

import argparse
from pathlib import Path

import customtkinter as ctk

from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.ui.install_wizard import InstallWizard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--page", choices=("form", "review"), default="form")
    args = parser.parse_args()

    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()
    wizard = InstallWizard(
        root,
        initial_target=Path("D:/ComfyUI"),
        initial_python=Path("C:/Users/Camsi/AppData/Local/Python/python.exe"),
        initial_channel=UpdateChannel.STABLE,
        on_install=lambda _job: True,
    )
    wizard.geometry("620x470+140+100")
    wizard.attributes("-topmost", True)
    if args.page == "review":
        wizard._next()
    wizard.after(1000, lambda: _capture(wizard, args.screenshot))
    root.mainloop()
    return 0


def _capture(window: InstallWizard, path: Path) -> None:
    from PIL import ImageGrab

    window.update_idletasks()
    left = window.winfo_rootx()
    top = window.winfo_rooty()
    right = left + window.winfo_width()
    bottom = top + window.winfo_height()
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(left, top, right, bottom)).save(path)
    window.grab_release()
    window.destroy()
    window.master.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
