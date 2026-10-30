from __future__ import annotations

import json
import os
import threading
import tomllib
from dataclasses import replace
from pathlib import Path

from packaging.utils import canonicalize_name

from smart_launcher.domain.commands import CommandFailure, CommandSpec
from smart_launcher.domain.custom_nodes import InstalledCustomNode, parse_github_origin
from smart_launcher.infrastructure.command_runner import CommandRunner


_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}


class CustomNodeCatalog:
    """Inspects installed nodes locally and checks their remote branch on demand."""

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def scan(self, comfy_root: Path) -> tuple[InstalledCustomNode, ...]:
        folder = comfy_root / "custom_nodes"
        if not folder.is_dir():
            return ()
        entries: list[InstalledCustomNode] = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if path.name.startswith(".") or path.name == "__pycache__":
                continue
            if path.is_file():
                if path.suffix.casefold() == ".py":
                    entries.append(InstalledCustomNode(path.name, path))
                continue
            if not path.is_dir() or path.is_symlink():
                entries.append(
                    InstalledCustomNode(path.name, path, error="Небезопасная ссылка")
                )
                continue
            registry_metadata = (path / "pyproject.toml").is_file() and (
                path / ".tracking"
            ).is_file()
            if not (path / "__init__.py").is_file() and not registry_metadata:
                continue
            if not (path / ".git").is_dir():
                entries.append(self._inspect_non_git_node(path))
                continue
            entries.append(self._inspect_git_node(path))
        return tuple(entries)

    def check_updates(
        self,
        entries: tuple[InstalledCustomNode, ...],
        *,
        comfy_root: Path | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[InstalledCustomNode, ...]:
        registry_versions: dict[str, str] = {}
        registry_entries = [entry for entry in entries if entry.is_registry]
        if registry_entries and comfy_root is not None:
            self._refresh_registry_cache(comfy_root, cancel_event)
            registry_versions = self._read_registry_versions(comfy_root)

        checked: list[InstalledCustomNode] = []
        for index, entry in enumerate(entries):
            if cancel_event is not None and cancel_event.is_set():
                checked.extend(entries[index:])
                break
            if entry.is_registry:
                latest = registry_versions.get(canonicalize_name(entry.registry_id or ""))
                checked.append(
                    replace(
                        entry,
                        latest_version=latest,
                        error=None if latest else "Нет данных Registry",
                    )
                )
                continue
            if not entry.managed or entry.dirty:
                checked.append(entry)
                continue
            reference = (
                f"refs/heads/{entry.branch}"
                if entry.branch and entry.branch not in {"(detached)", "HEAD"}
                else "HEAD"
            )
            result = self.runner.run(
                CommandSpec(
                    ("git", "ls-remote", "--exit-code", entry.repository_url or "", reference),
                    env=_GIT_ENV,
                    source="custom-node-catalog",
                    timeout=45,
                    idle_timeout=20,
                ),
                cancel_event=cancel_event,
            )
            if result.succeeded and result.stdout.strip():
                remote = result.stdout.split()[0].casefold()
                checked.append(replace(entry, remote_commit=remote, error=None))
            else:
                detail = (
                    "Проверка отменена"
                    if result.failure is CommandFailure.CANCELLED
                    else "Не удалось проверить удалённую версию"
                )
                checked.append(replace(entry, error=detail))
        return tuple(checked)

    def _inspect_non_git_node(self, path: Path) -> InstalledCustomNode:
        metadata = path / "pyproject.toml"
        tracking = path / ".tracking"
        if not metadata.is_file() or not tracking.is_file():
            return InstalledCustomNode(path.name, path)
        try:
            with metadata.open("rb") as handle:
                document = tomllib.load(handle)
            project = document.get("project", {})
            registry_id = str(project.get("name", "")).strip()
            version = str(project.get("version", "")).strip()
            repository_url = str(
                project.get("urls", {}).get("Repository", "")
            ).strip() or None
            if not registry_id or not version:
                raise ValueError("missing Registry name or version")
            if repository_url:
                try:
                    repository_url = parse_github_origin(repository_url).clone_url
                except ValueError:
                    repository_url = None
            return InstalledCustomNode(
                path.name,
                path,
                repository_url=repository_url,
                registry_id=canonicalize_name(registry_id),
                installed_version=version,
            )
        except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError):
            return InstalledCustomNode(
                path.name, path, error="Повреждены метаданные Registry"
            )

    def _refresh_registry_cache(
        self, comfy_root: Path, cancel_event: threading.Event | None
    ) -> None:
        executable = comfy_root / "venv" / (
            "Scripts/cm-cli.exe" if os.name == "nt" else "bin/cm-cli"
        )
        if not executable.is_file():
            return
        self.runner.run(
            CommandSpec(
                (str(executable), "update-cache"),
                cwd=comfy_root,
                env={"COMFYUI_PATH": str(comfy_root)},
                source="custom-node-registry",
                timeout=180,
                idle_timeout=60,
            ),
            cancel_event=cancel_event,
        )

    @staticmethod
    def _read_registry_versions(comfy_root: Path) -> dict[str, str]:
        cache = comfy_root / "user" / "__manager" / "cache"
        candidates = sorted(
            cache.glob("*_nodes.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ) if cache.is_dir() else []
        for candidate in candidates:
            try:
                document = json.loads(candidate.read_text(encoding="utf-8"))
                nodes = document.get("nodes", [])
                versions = {
                    canonicalize_name(str(node["id"])): str(node["latest_version"]["version"])
                    for node in nodes
                    if node.get("id") and node.get("latest_version", {}).get("version")
                }
                if versions:
                    return versions
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return {}

    def _inspect_git_node(self, path: Path) -> InstalledCustomNode:
        status = self.runner.run(
            CommandSpec(
                ("git", "-C", str(path), "status", "--porcelain=v2", "--branch"),
                source="custom-node-catalog",
                timeout=20,
            )
        )
        origin = self.runner.run(
            CommandSpec(
                ("git", "-C", str(path), "remote", "get-url", "origin"),
                source="custom-node-catalog",
                timeout=20,
            )
        )
        if not status.succeeded or not origin.succeeded:
            return InstalledCustomNode(
                path.name, path, error="Не удалось прочитать Git-репозиторий"
            )
        local_commit: str | None = None
        branch: str | None = None
        dirty = False
        for line in status.stdout.splitlines():
            if line.startswith("# branch.oid "):
                value = line.removeprefix("# branch.oid ").strip()
                local_commit = None if value == "(initial)" else value.casefold()
            elif line.startswith("# branch.head "):
                branch = line.removeprefix("# branch.head ").strip()
            elif line and not line.startswith("#"):
                dirty = True
        try:
            repository_url = parse_github_origin(origin.stdout).clone_url
        except ValueError:
            return InstalledCustomNode(
                path.name,
                path,
                branch=branch,
                local_commit=local_commit,
                dirty=dirty,
                error="Origin не поддерживается",
            )
        return InstalledCustomNode(
            path.name,
            path,
            repository_url,
            branch,
            local_commit,
            dirty=dirty,
        )
