from __future__ import annotations

import argparse
import json
from pathlib import Path

from smart_launcher.comfy.repository import ComfyRepository, RepositoryError
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.runtime.python_resolver import PythonResolver
from smart_launcher.runtime.requirements import RequirementsValidator
from smart_launcher.runtime.torch_profile import TorchProfileValidator
from smart_launcher.runtime.venv_manager import VenvManager


def main() -> int:
    parser = argparse.ArgumentParser(prog="smart-launcher")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="start the Smart Launcher 2.0 UI")
    run_parser.add_argument("--config", type=Path, help="override configuration path")
    subparsers.add_parser("smoke-ui", help=argparse.SUPPRESS)
    subparsers.add_parser("diagnose-python", help="discover and probe Python runtimes")
    venv_parser = subparsers.add_parser(
        "diagnose-venv", help="inspect an existing virtual environment"
    )
    venv_parser.add_argument("path", help="path to the virtual environment")
    requirements_parser = subparsers.add_parser(
        "diagnose-requirements", help="validate a requirements manifest without starting pip"
    )
    requirements_parser.add_argument("python", help="target Python executable")
    requirements_parser.add_argument("manifest", help="requirements.txt path")
    torch_parser = subparsers.add_parser(
        "diagnose-torch", help="validate the target PyTorch and CUDA profile"
    )
    torch_parser.add_argument("python", help="target Python executable")
    repository_parser = subparsers.add_parser(
        "diagnose-repository", help="inspect a ComfyUI Git checkout"
    )
    repository_parser.add_argument("path", help="ComfyUI repository path")
    update_parser = subparsers.add_parser(
        "plan-update", help="fetch and plan a core update without switching revisions"
    )
    update_parser.add_argument("path", help="ComfyUI repository path")
    update_parser.add_argument("channel", choices=[channel.value for channel in UpdateChannel])
    update_parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()

    if args.command in (None, "run"):
        from smart_launcher.bootstrap import run

        return run(getattr(args, "config", None))
    if args.command == "smoke-ui":
        import customtkinter as ctk

        root = ctk.CTk()
        root.withdraw()
        root.update_idletasks()
        root.update()
        root.destroy()
        return 0
    if args.command == "diagnose-python":
        report = PythonResolver().resolve()
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if report.selected else 1
    if args.command == "diagnose-venv":
        health = VenvManager().inspect(args.path)
        print(json.dumps(health.as_dict(), ensure_ascii=False, indent=2))
        return 0 if health.healthy else 1
    if args.command == "diagnose-requirements":
        report = RequirementsValidator().validate(args.python, args.manifest)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if report.healthy else 1
    if args.command == "diagnose-torch":
        report = TorchProfileValidator().validate(args.python)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if report.compatible else 1
    if args.command == "diagnose-repository":
        state = ComfyRepository(args.path).inspect()
        print(json.dumps(state.as_dict(), ensure_ascii=False, indent=2))
        return 0 if state.valid else 1
    if args.command == "plan-update":
        try:
            plan = ComfyRepository(args.path).prepare(
                UpdateChannel(args.channel), fetch=not args.no_fetch
            )
        except RepositoryError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
