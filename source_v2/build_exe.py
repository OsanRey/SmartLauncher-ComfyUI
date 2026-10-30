"""Build the independent Smart Launcher 2.0 executable."""

from __future__ import annotations

import shutil
import sys
import os
import subprocess
import tempfile
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parent
ROOT_DIR = SOURCE_DIR.parent
RELEASE_DIR = ROOT_DIR / "release_v2"
WORK_DIR = ROOT_DIR / ".build_v2" / "work"
SPEC_DIR = ROOT_DIR / ".build_v2" / "spec"
RUNTIME_STAGE_DIR = ROOT_DIR / ".build_v2" / "runtime"
ENTRY_POINT = SOURCE_DIR / "smart_launcher" / "__main__.py"
ICON_PATH = SOURCE_DIR / "assets" / "ComfyUI.ico"
VERSION_INFO_PATH = SOURCE_DIR / "assets" / "version_info.txt"
HOOKS_DIR = SOURCE_DIR / "build_hooks"


def main() -> int:
    try:
        from PyInstaller.__main__ import run as pyinstaller_run
    except ImportError:
        print("ERROR: PyInstaller is not installed in the source_v2 environment.")
        print(f'Run: "{sys.executable}" -m pip install pyinstaller')
        return 1

    if not ENTRY_POINT.is_file():
        print(f"ERROR: Entry point is missing: {ENTRY_POINT}")
        return 1

    base = Path(sys.base_prefix)
    tcl_root = base / "tcl"
    tcl_library = tcl_root / "tcl8.6"
    tk_library = tcl_root / "tk8.6"
    tkinter_binary = base / "DLLs" / "_tkinter.pyd"
    tcl_dll = base / "DLLs" / "tcl86t.dll"
    tk_dll = base / "DLLs" / "tk86t.dll"
    if not all(
        path.exists()
        for path in (tcl_library, tk_library, tkinter_binary, tcl_dll, tk_dll)
    ):
        print(f"ERROR: The build Python has no complete Tcl/Tk runtime: {base}")
        return 1
    # Python Install Manager runtimes may not expose these paths to PyInstaller.
    os.environ["TCL_LIBRARY"] = str(tcl_library)
    os.environ["TK_LIBRARY"] = str(tk_library)

    # Tcl can enumerate files inside some managed-runtime projections while
    # still being unable to open them. A real staging copy also makes the
    # packaged runtime independent from that filesystem implementation.
    staged_tcl_library = RUNTIME_STAGE_DIR / "tcl8.6"
    staged_tk_library = RUNTIME_STAGE_DIR / "tk8.6"
    shutil.rmtree(RUNTIME_STAGE_DIR, ignore_errors=True)
    shutil.copytree(tcl_library, staged_tcl_library)
    shutil.copytree(tk_library, staged_tk_library)

    RELEASE_DIR.mkdir(exist_ok=True)
    arguments = [
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "SmartLauncher2",
        "--paths",
        str(SOURCE_DIR),
        "--additional-hooks-dir",
        str(HOOKS_DIR),
        "--collect-all",
        "customtkinter",
        "--copy-metadata",
        "packaging",
        "--hidden-import",
        "tkinter",
        "--hidden-import",
        "_tkinter",
        "--add-data",
        f"{staged_tcl_library};_tcl_data",
        "--add-data",
        f"{staged_tk_library};_tk_data",
        "--add-binary",
        f"{tcl_dll};.",
        "--add-binary",
        f"{tk_dll};.",
        "--distpath",
        str(RELEASE_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
    ]
    if ICON_PATH.is_file():
        arguments.extend(("--icon", str(ICON_PATH)))
    if VERSION_INFO_PATH.is_file():
        arguments.extend(("--version-file", str(VERSION_INFO_PATH)))
    arguments.append(str(ENTRY_POINT))

    print(f"Source: {SOURCE_DIR}")
    print(f"Output: {RELEASE_DIR}")
    try:
        pyinstaller_run(arguments)
    except SystemExit as error:
        return int(error.code or 0)

    executable = RELEASE_DIR / "SmartLauncher2.exe"
    with tempfile.TemporaryDirectory(prefix="smart-launcher-v2-build-") as directory:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = directory
        smoke = subprocess.Popen(
            [str(executable), "smoke-ui"],
            cwd=directory,
            env=environment,
        )
        try:
            smoke_exit = smoke.wait(timeout=30)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(smoke.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                smoke.kill()
            smoke.wait(timeout=10)
            print("ERROR: Built executable UI smoke test timed out.")
            return 1
        profile = Path(directory) / "SmartLauncherComfyUI2" / "profile.json"
        if smoke_exit != 0 or profile.exists():
            print(
                "ERROR: Built executable failed the isolated UI smoke test "
                f"(exit={smoke_exit}, profile_created={profile.exists()})."
            )
            return 1

    shutil.rmtree(ROOT_DIR / ".build_v2", ignore_errors=True)
    print(f"DONE: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
