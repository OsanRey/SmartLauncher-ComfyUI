from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from smart_launcher.domain.commands import CommandSpec
from smart_launcher.domain.runtimes import (
    PythonPolicy,
    PythonResolution,
    RuntimeCandidate,
    RuntimeProbe,
    RuntimeSource,
)
from smart_launcher.infrastructure.command_runner import CommandRunner

_PROBE_SCRIPT = r"""
import importlib.util
import json
import platform
import sys
import sysconfig

print(json.dumps({
    "executable": sys.executable,
    "version": platform.python_version(),
    "version_info": list(sys.version_info[:3]),
    "release_level": sys.version_info.releaselevel,
    "implementation": sys.implementation.name,
    "architecture": platform.architecture()[0],
    "machine": platform.machine(),
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "pip_available": importlib.util.find_spec("pip") is not None,
    "venv_available": importlib.util.find_spec("venv") is not None,
    "ensurepip_available": importlib.util.find_spec("ensurepip") is not None,
}))
""".strip()

_WINDOWS_PYTHON_PATH = re.compile(
    r"(?P<path>[A-Za-z]:\\[^\r\n]*?python(?:w)?\.exe)", re.IGNORECASE
)


class PythonResolver:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        which: Callable[[str], str | None] = shutil.which,
        install_manager_roots: Iterable[str | Path] | None = None,
    ) -> None:
        self._runner = runner or CommandRunner()
        self._which = which
        self._install_manager_roots = tuple(
            Path(path)
            for path in (
                install_manager_roots
                if install_manager_roots is not None
                else self._default_install_manager_roots()
            )
        )

    def resolve(
        self,
        configured: Iterable[str | Path] = (),
        policy: PythonPolicy = PythonPolicy(),
    ) -> PythonResolution:
        candidates, diagnostics = self.discover(
            configured, include_unmanaged_path=policy.allow_unmanaged_path
        )
        probes = tuple(self.probe(candidate) for candidate in candidates)
        selected = self.select(probes, policy)
        if selected is None:
            diagnostics.append("No compatible launcher-managed Python runtime was found.")
        return PythonResolution(selected, probes, tuple(diagnostics))

    def discover(
        self,
        configured: Iterable[str | Path] = (),
        *,
        include_unmanaged_path: bool = False,
    ) -> tuple[tuple[RuntimeCandidate, ...], list[str]]:
        candidates: list[RuntimeCandidate] = []
        diagnostics: list[str] = []
        for path in configured:
            candidates.append(RuntimeCandidate(Path(path), RuntimeSource.CONFIGURED))

        manager = self._which("pymanager")
        if manager:
            candidates.extend(self._discover_manager(manager, diagnostics))
        else:
            py = self._which("py")
            if py:
                managed = self._discover_manager(py, diagnostics)
                if managed:
                    candidates.extend(managed)
                else:
                    candidates.extend(self._discover_legacy_launcher(py, diagnostics))

        candidates.extend(self._discover_install_manager_store(diagnostics))

        if include_unmanaged_path:
            for command in ("python", "python3"):
                path = self._which(command)
                if path:
                    candidates.append(RuntimeCandidate(Path(path), RuntimeSource.PATH, command))

        return tuple(self._deduplicate(candidates)), diagnostics

    def _discover_install_manager_store(
        self, diagnostics: list[str]
    ) -> list[RuntimeCandidate]:
        found: list[RuntimeCandidate] = []
        for store in self._install_manager_roots:
            try:
                children = tuple(path for path in store.iterdir() if path.is_dir())
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                diagnostics.append(f"Cannot inspect Python Install Manager store: {exc}")
                continue
            for runtime in children:
                marker = runtime / "__install__.json"
                if not marker.is_file():
                    continue
                try:
                    payload = json.loads(marker.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise TypeError("metadata root is not an object")
                    if payload.get("schema") != 1 or payload.get("company") != "PythonCore":
                        continue
                    relative = payload.get("executable")
                    if not isinstance(relative, str) or not relative.strip():
                        raise TypeError("executable is missing")
                    executable = (runtime / relative).resolve()
                    if not executable.is_relative_to(runtime.resolve()):
                        raise ValueError("executable escapes the managed runtime")
                    if not executable.is_file():
                        raise FileNotFoundError(executable)
                    tag = payload.get("tag") or payload.get("sort-version")
                    found.append(
                        RuntimeCandidate(
                            executable,
                            RuntimeSource.PY_MANAGER,
                            str(tag) if tag is not None else None,
                        )
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    diagnostics.append(f"Ignored invalid Python runtime metadata {marker}: {exc}")
        return found

    @staticmethod
    def _default_install_manager_roots() -> tuple[Path, ...]:
        if os.name != "nt":
            return ()
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) / "Python",) if local else ()

    def probe(self, candidate: RuntimeCandidate) -> RuntimeProbe:
        result = self._runner.run(
            CommandSpec(
                argv=(str(candidate.executable), "-I", "-c", _PROBE_SCRIPT),
                env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                source="python-probe",
                timeout=8.0,
            )
        )
        if not result.succeeded:
            detail = result.exception or result.stderr.strip() or result.failure.value
            return RuntimeProbe(candidate, False, candidate.executable, error=detail)
        try:
            data = json.loads(result.stdout.strip())
            version_info = tuple(int(value) for value in data["version_info"][:3])
            executable = Path(data.get("executable") or candidate.executable)
            prefix = Path(data["prefix"]) if data.get("prefix") else None
            base_prefix = Path(data["base_prefix"]) if data.get("base_prefix") else None
            return RuntimeProbe(
                candidate=candidate,
                valid=True,
                executable=executable,
                version=data.get("version"),
                version_info=version_info,  # type: ignore[arg-type]
                release_level=data.get("release_level"),
                implementation=data.get("implementation"),
                architecture=data.get("architecture"),
                machine=data.get("machine"),
                free_threaded=bool(data.get("free_threaded")),
                prefix=prefix,
                base_prefix=base_prefix,
                is_venv=prefix is not None and base_prefix is not None and prefix != base_prefix,
                pip_available=bool(data.get("pip_available")),
                venv_available=bool(data.get("venv_available")),
                ensurepip_available=bool(data.get("ensurepip_available")),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return RuntimeProbe(
                candidate,
                False,
                candidate.executable,
                error=f"Invalid probe response: {exc}",
            )

    @staticmethod
    def select(
        probes: Iterable[RuntimeProbe], policy: PythonPolicy = PythonPolicy()
    ) -> RuntimeProbe | None:
        preferred = {minor: index for index, minor in enumerate(policy.preferred_minors)}
        source_rank = {
            RuntimeSource.CONFIGURED: 0,
            RuntimeSource.PY_MANAGER: 1,
            RuntimeSource.PATH: 2,
            RuntimeSource.LEGACY_LAUNCHER: 3,
        }

        def eligible(probe: RuntimeProbe) -> bool:
            if not probe.valid or not probe.version_info:
                return False
            if (
                probe.candidate.source is RuntimeSource.PATH
                and not policy.allow_unmanaged_path
            ):
                return False
            if probe.implementation != "cpython":
                return False
            if probe.version_info[:2] not in preferred:
                return False
            if policy.require_64bit and probe.architecture != "64bit":
                return False
            if not policy.allow_prerelease and probe.release_level != "final":
                return False
            if not policy.allow_free_threaded and probe.free_threaded:
                return False
            return probe.venv_available and probe.ensurepip_available

        compatible = [probe for probe in probes if eligible(probe)]
        if not compatible:
            return None
        compatible.sort(
            key=lambda probe: (
                source_rank[probe.candidate.source],
                preferred[probe.version_info[:2]],  # type: ignore[index]
                -probe.version_info[2],  # type: ignore[index]
                os.path.normcase(str(probe.executable)),
            )
        )
        return compatible[0]

    def _discover_manager(
        self, executable: str, diagnostics: list[str]
    ) -> list[RuntimeCandidate]:
        result = self._runner.run(
            CommandSpec(
                argv=(executable, "list", "--format=json"),
                source="python-manager",
                timeout=8.0,
            )
        )
        if not result.succeeded:
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            diagnostics.append(f"Python Manager returned invalid JSON: {exc}")
            return []

        found: list[RuntimeCandidate] = []
        for record in self._walk_dicts(payload):
            path = next(
                (
                    record[key]
                    for key in ("executable", "executable_path", "exe")
                    if isinstance(record.get(key), str)
                ),
                None,
            )
            if path:
                tag = next(
                    (
                        str(record[key])
                        for key in ("tag", "version", "display-name")
                        if record.get(key) is not None
                    ),
                    None,
                )
                found.append(RuntimeCandidate(Path(path), RuntimeSource.PY_MANAGER, tag))
        return found

    def _discover_legacy_launcher(
        self, executable: str, diagnostics: list[str]
    ) -> list[RuntimeCandidate]:
        result = self._runner.run(
            CommandSpec(
                argv=(executable, "-0p"),
                source="legacy-python-launcher",
                timeout=8.0,
            )
        )
        if not result.succeeded:
            diagnostics.append("Legacy py launcher could not enumerate Python runtimes.")
            return []
        return [
            RuntimeCandidate(Path(match.group("path").strip()), RuntimeSource.LEGACY_LAUNCHER)
            for match in _WINDOWS_PYTHON_PATH.finditer(result.stdout + "\n" + result.stderr)
        ]

    @staticmethod
    def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from PythonResolver._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from PythonResolver._walk_dicts(child)

    @staticmethod
    def _deduplicate(candidates: Iterable[RuntimeCandidate]) -> list[RuntimeCandidate]:
        unique: list[RuntimeCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = os.path.normcase(os.path.abspath(candidate.executable))
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique
