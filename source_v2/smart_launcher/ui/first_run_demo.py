from __future__ import annotations

import argparse
from pathlib import Path

import customtkinter as ctk

from smart_launcher.ui.setup_dialog import SetupDialog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path, required=True)
    args = parser.parse_args()
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()
    dialog = SetupDialog(
        root,
        on_new_install=lambda: None,
        on_connect=lambda *_args: None,  # type: ignore[arg-type]
        on_connected=lambda _profile: None,
    )
    dialog.geometry("560x365+140+100")
    dialog.attributes("-topmost", True)
    dialog.after(900, lambda: _capture(dialog, args.screenshot))
    root.mainloop()
    return 0


def _capture(window: SetupDialog, path: Path) -> None:
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
    window.grab_release()
    window.destroy()
    window.master.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
