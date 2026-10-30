from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from smart_launcher.domain.commands import CommandFailure, CommandResult
from smart_launcher.domain.runtimes import (
    PythonPolicy,
    RuntimeCandidate,
    RuntimeProbe,
    RuntimeSource,
)
from smart_launcher.infrastructure.command_runner import CommandRunner
from smart_launcher.runtime.python_resolver import PythonResolver


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str]]) -> None:
        self.responses = responses

    def run(self, spec, **_kwargs):
        code, stdout, stderr = self.responses.get(spec.argv, (1, "", "missing fake response"))
        now = datetime.now(UTC)
        return CommandResult(
            spec=spec,
            failure=CommandFailure.NONE if code == 0 else CommandFailure.NONZERO_EXIT,
            return_code=code,
            started_at=now,
            finished_at=now,
            stdout=stdout,
            stderr=stderr,
        )


def probe(
    version: tuple[int, int, int],
    source: RuntimeSource,
    *,
    release_level: str = "final",
    free_threaded: bool = False,
) -> RuntimeProbe:
    path = Path(f"C:/Python{version[0]}{version[1]}/python.exe")
    candidate = RuntimeCandidate(path, source)
    return RuntimeProbe(
        candidate=candidate,
        valid=True,
        executable=path,
        version=".".join(map(str, version)),
        version_info=version,
        release_level=release_level,
        implementation="cpython",
        architecture="64bit",
        free_threaded=free_threaded,
        venv_available=True,
        ensurepip_available=True,
    )


class PythonResolverTests(unittest.TestCase):
    def test_actual_current_python_can_be_probed(self) -> None:
        resolver = PythonResolver(CommandRunner())
        result = resolver.probe(RuntimeCandidate(Path(sys.executable), RuntimeSource.CONFIGURED))

        self.assertTrue(result.valid, result.error)
        self.assertEqual(result.executable, Path(sys.executable))
        self.assertEqual(result.implementation, "cpython")

    def test_select_prefers_configured_compatible_runtime(self) -> None:
        configured = probe((3, 12, 9), RuntimeSource.CONFIGURED)
        manager = probe((3, 13, 15), RuntimeSource.PY_MANAGER)

        self.assertIs(PythonResolver.select((manager, configured)), configured)

    def test_select_rejects_prerelease_and_free_threaded(self) -> None:
        prerelease = probe((3, 13, 0), RuntimeSource.PY_MANAGER, release_level="candidate")
        free_threaded = probe((3, 13, 15), RuntimeSource.PY_MANAGER, free_threaded=True)
        regular = probe((3, 12, 10), RuntimeSource.LEGACY_LAUNCHER)

        self.assertIs(PythonResolver.select((prerelease, free_threaded, regular)), regular)

    def test_path_runtime_requires_explicit_approval(self) -> None:
        unmanaged = probe((3, 13, 15), RuntimeSource.PATH)

        self.assertIsNone(PythonResolver.select((unmanaged,)))
        self.assertIs(
            PythonResolver.select(
                (unmanaged,), PythonPolicy(allow_unmanaged_path=True)
            ),
            unmanaged,
        )

    def test_discovers_nested_python_manager_json(self) -> None:
        manager_path = "C:/Tools/pymanager.exe"
        python_path = "C:/Python313/python.exe"
        payload = json.dumps(
            {"runtimes": [{"tag": "3.13", "executable": python_path}]}
        )
        runner = FakeRunner(
            {(manager_path, "list", "--format=json"): (0, payload, "")}
        )
        resolver = PythonResolver(
            runner,  # type: ignore[arg-type]
            which=lambda command: manager_path if command == "pymanager" else None,
            install_manager_roots=(),
        )

        candidates, diagnostics = resolver.discover()

        self.assertEqual(diagnostics, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].executable, Path(python_path))
        self.assertEqual(candidates[0].source, RuntimeSource.PY_MANAGER)

    def test_falls_back_to_legacy_launcher_listing(self) -> None:
        launcher = "C:/Windows/py.exe"
        listing = " -V:3.13 * C:\\Python313\\python.exe\n"
        runner = FakeRunner(
            {
                (launcher, "list", "--format=json"): (2, "", "unsupported"),
                (launcher, "-0p"): (0, listing, ""),
            }
        )
        resolver = PythonResolver(
            runner,  # type: ignore[arg-type]
            which=lambda command: launcher if command == "py" else None,
            install_manager_roots=(),
        )

        candidates, _ = resolver.discover(include_unmanaged_path=True)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source, RuntimeSource.LEGACY_LAUNCHER)
        self.assertEqual(str(candidates[0].executable), "C:\\Python313\\python.exe")

    def test_empty_legacy_launcher_does_not_hide_python_on_path(self) -> None:
        launcher = "C:/Windows/py.exe"
        path_python = "D:/Tools/Python313/python.exe"
        runner = FakeRunner(
            {
                (launcher, "list", "--format=json"): (112, "", "legacy launcher"),
                (launcher, "-0p"): (0, "No installed Pythons found!", ""),
            }
        )

        def which(command: str) -> str | None:
            return {"py": launcher, "python": path_python}.get(command)

        resolver = PythonResolver(  # type: ignore[arg-type]
            runner, which=which, install_manager_roots=()
        )
        candidates, _ = resolver.discover(include_unmanaged_path=True)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source, RuntimeSource.PATH)
        self.assertEqual(candidates[0].executable, Path(path_python))

    def test_default_discovery_does_not_access_python_from_path(self) -> None:
        path_python = "D:/OtherProject/Python313/python.exe"
        requested: list[str] = []

        def which(command: str) -> str | None:
            requested.append(command)
            return path_python if command == "python" else None

        resolver = PythonResolver(  # type: ignore[arg-type]
            FakeRunner({}), which=which, install_manager_roots=()
        )
        candidates, _ = resolver.discover()

        self.assertEqual(candidates, ())
        self.assertNotIn("python", requested)

    def test_discovers_only_marker_owned_install_manager_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            managed = store / "pythoncore-3.13-64"
            managed.mkdir()
            executable = managed / "python.exe"
            executable.write_bytes(b"test")
            (managed / "__install__.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "company": "PythonCore",
                        "tag": "3.13-64",
                        "executable": ".\\python.exe",
                    }
                ),
                encoding="utf-8",
            )
            unrelated = store / "OtherProject"
            unrelated.mkdir()
            (unrelated / "python.exe").write_bytes(b"ignore")
            resolver = PythonResolver(
                FakeRunner({}),  # type: ignore[arg-type]
                which=lambda _command: None,
                install_manager_roots=(store,),
            )

            candidates, diagnostics = resolver.discover()

            self.assertEqual(diagnostics, [])
            self.assertEqual(tuple(candidate.executable for candidate in candidates), (executable,))

    def test_install_manager_marker_cannot_escape_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            managed = store / "managed"
            managed.mkdir()
            outside = store / "python.exe"
            outside.write_bytes(b"ignore")
            (managed / "__install__.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "company": "PythonCore",
                        "executable": "..\\python.exe",
                    }
                ),
                encoding="utf-8",
            )
            resolver = PythonResolver(
                FakeRunner({}),  # type: ignore[arg-type]
                which=lambda _command: None,
                install_manager_roots=(store,),
            )

            candidates, diagnostics = resolver.discover()

            self.assertEqual(candidates, ())
            self.assertTrue(any("escapes" in detail for detail in diagnostics))



if __name__ == "__main__":
    unittest.main()
