from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from smart_launcher.domain.commands import CommandResult, CommandSpec
from smart_launcher.domain.environments import (
    EnvironmentHealth,
    EnvironmentState,
    RepairResult,
    RepairStage,
)
from smart_launcher.domain.runtimes import PythonPolicy, RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.infrastructure.command_runner import ChunkCallback, CommandRunner
from smart_launcher.runtime.python_resolver import PythonResolver

Provisioner = Callable[[Path], CommandResult | None]
Validator = Callable[[Path], tuple[bool, str]]


class VenvManager:
    """Inspects and transactionally recreates one virtual environment."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        resolver: PythonResolver | None = None,
        *,
        allowed_names: tuple[str, ...] = ("venv", ".venv"),
        trusted_bases: Iterable[str | Path] = (),
    ) -> None:
        self._runner = runner or CommandRunner()
        self._resolver = resolver or PythonResolver(self._runner)
        self._allowed_names = frozenset(name.casefold() for name in allowed_names)
        self._trusted_bases = frozenset(self._path_key(Path(path)) for path in trusted_bases)

    def inspect(
        self,
        root: str | Path,
        policy: PythonPolicy = PythonPolicy(),
        *,
        trusted_base: str | Path | None = None,
    ) -> EnvironmentHealth:
        environment = Path(root)
        python = self.python_path(environment)
        if not environment.exists():
            return EnvironmentHealth(
                environment, python, EnvironmentState.MISSING, "Environment directory is missing."
            )
        if not python.is_file():
            return EnvironmentHealth(
                environment,
                python,
                EnvironmentState.EXECUTABLE_MISSING,
                "The environment Python executable is missing.",
            )

        config = environment / "pyvenv.cfg"
        base = self._read_venv_home(config)
        if base is None:
            return EnvironmentHealth(
                environment,
                python,
                EnvironmentState.CONFIG_BROKEN,
                "pyvenv.cfg is missing or does not contain a valid home path.",
            )
        trusted = set(self._trusted_bases)
        if trusted_base is not None:
            trusted.add(self._path_key(Path(trusted_base)))
        if self._path_key(base) not in trusted:
            return EnvironmentHealth(
                environment,
                python,
                EnvironmentState.UNTRUSTED_BASE,
                f"The environment base runtime is not trusted: {base}",
            )

        probe = self._resolver.probe(
            RuntimeCandidate(python, RuntimeSource.CONFIGURED, "target-venv")
        )
        if not probe.valid:
            return EnvironmentHealth(
                environment,
                python,
                EnvironmentState.EXECUTABLE_BROKEN,
                probe.error or "The environment Python executable could not start.",
                probe,
            )
        if not probe.is_venv:
            return EnvironmentHealth(
                environment,
                python,
                EnvironmentState.NOT_A_VENV,
                "sys.prefix equals sys.base_prefix; this is not an isolated environment.",
                probe,
            )
        compatibility_error = self._compatibility_error(probe, policy)
        if compatibility_error:
            return EnvironmentHealth(
                environment,
                python,
                EnvironmentState.INCOMPATIBLE_RUNTIME,
                compatibility_error,
                probe,
            )

        pip = self._runner.run(
            CommandSpec(
                argv=(str(python), "-I", "-m", "pip", "--version"),
                env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                source="venv-health",
                timeout=15.0,
            )
        )
        if not pip.succeeded:
            detail = pip.exception or pip.stderr.strip() or "python -m pip --version failed."
            return EnvironmentHealth(
                environment, python, EnvironmentState.PIP_BROKEN, detail, probe
            )
        return EnvironmentHealth(
            environment,
            python,
            EnvironmentState.HEALTHY,
            pip.stdout.strip() or "Environment is healthy.",
            probe,
        )

    def recreate(
        self,
        root: str | Path,
        runtime: RuntimeProbe,
        *,
        policy: PythonPolicy = PythonPolicy(),
        provision: Provisioner | None = None,
        validate: Validator | None = None,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
        keep_backup: bool = False,
    ) -> RepairResult:
        environment = self._safe_environment_path(Path(root))
        before = self.inspect(environment, policy)
        runtime_error = self._base_runtime_error(runtime, policy)
        if runtime_error:
            return RepairResult(
                False, RepairStage.PREPARE, before, None, None, runtime_error
            )

        environment.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        try:
            if environment.exists():
                backup = self._next_backup_path(environment)
                environment.rename(backup)
        except OSError as exc:
            return RepairResult(
                False,
                RepairStage.BACKUP,
                before,
                None,
                backup,
                f"Could not preserve the existing environment: {exc}",
            )

        stage = RepairStage.CREATE
        detail = "Environment creation failed."
        after: EnvironmentHealth | None = None
        try:
            create = self._runner.run(
                CommandSpec(
                    argv=(str(runtime.executable), "-I", "-m", "venv", str(environment)),
                    env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                    source="venv-create",
                    timeout=180.0,
                ),
                on_chunk=on_chunk,
                cancel_event=cancel_event,
            )
            if not create.succeeded:
                detail = create.exception or create.stderr.strip() or create.failure.value
                raise _RepairFailure

            if provision is not None:
                stage = RepairStage.PROVISION
                provisioned = provision(self.python_path(environment))
                if provisioned is not None and not provisioned.succeeded:
                    detail = (
                        provisioned.exception
                        or provisioned.stderr.strip()
                        or provisioned.failure.value
                    )
                    raise _RepairFailure

            stage = RepairStage.VALIDATE
            trusted_base = runtime.prefix or runtime.base_prefix or runtime.executable.parent
            after = self.inspect(environment, policy, trusted_base=trusted_base)
            if not after.healthy:
                detail = after.detail
                raise _RepairFailure
            if validate is not None:
                valid, validation_detail = validate(self.python_path(environment))
                if not valid:
                    detail = validation_detail
                    raise _RepairFailure

            stage = RepairStage.COMMIT
            if backup is not None and not keep_backup:
                try:
                    shutil.rmtree(backup)
                    backup = None
                except OSError as exc:
                    return RepairResult(
                        True,
                        RepairStage.COMMIT,
                        before,
                        after,
                        backup,
                        "Environment validated, but its recovery copy could not be removed: "
                        f"{exc}",
                    )
            return RepairResult(
                True,
                RepairStage.COMMIT,
                before,
                after,
                backup,
                "Environment recreated and validated.",
            )
        except Exception as exc:
            if not isinstance(exc, _RepairFailure):
                detail = str(exc)
            rolled_back, rollback_detail = self._rollback(environment, backup)
            if rollback_detail:
                detail = f"{detail} Rollback: {rollback_detail}"
            return RepairResult(
                False,
                stage if rolled_back else RepairStage.ROLLBACK,
                before,
                after,
                backup,
                detail,
                rolled_back=rolled_back,
            )

    def finalize_recovery(self, root: str | Path, backup: str | Path) -> tuple[bool, str]:
        environment = self._safe_environment_path(Path(root))
        recovery = Path(os.path.abspath(backup))
        expected_prefix = f"{environment.name}.recovery-"
        if recovery.parent != environment.parent or not recovery.name.startswith(expected_prefix):
            return False, f"Refusing to remove an unrelated recovery path: {recovery}"
        if not recovery.exists():
            return True, "Recovery environment was already removed."
        try:
            shutil.rmtree(recovery)
        except OSError as exc:
            return False, str(exc)
        return True, "Recovery environment removed."

    @staticmethod
    def python_path(root: Path) -> Path:
        return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @staticmethod
    def _read_venv_home(config: Path) -> Path | None:
        try:
            for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip().casefold() == "home" and value.strip():
                    return Path(value.strip())
        except OSError:
            return None
        return None

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(path))

    @staticmethod
    def _compatibility_error(probe: RuntimeProbe, policy: PythonPolicy) -> str | None:
        if probe.implementation != "cpython":
            return "Only CPython environments are supported."
        if probe.version_info is None or probe.version_info[:2] not in policy.preferred_minors:
            return "The environment Python version is outside the supported profile."
        if policy.require_64bit and probe.architecture != "64bit":
            return "A 64-bit Python environment is required."
        if not policy.allow_prerelease and probe.release_level != "final":
            return "Prerelease Python builds are disabled."
        if not policy.allow_free_threaded and probe.free_threaded:
            return "Free-threaded Python builds are disabled for the normal profile."
        return None

    @classmethod
    def _base_runtime_error(cls, probe: RuntimeProbe, policy: PythonPolicy) -> str | None:
        if not probe.valid:
            return probe.error or "The selected base runtime is not executable."
        if (
            probe.candidate.source is RuntimeSource.PATH
            and not policy.allow_unmanaged_path
        ):
            return (
                "A Python runtime discovered only through PATH requires explicit approval "
                "before it can create or repair an environment."
            )
        compatibility_error = cls._compatibility_error(probe, policy)
        if compatibility_error:
            return compatibility_error
        if not probe.venv_available or not probe.ensurepip_available:
            return "The selected runtime cannot create bootstrapped virtual environments."
        return None

    def _safe_environment_path(self, path: Path) -> Path:
        absolute = Path(os.path.abspath(path))
        if absolute == Path(absolute.anchor) or not absolute.name:
            raise ValueError(f"Unsafe environment path: {absolute}")
        if absolute.name.casefold() not in self._allowed_names:
            allowed = ", ".join(sorted(self._allowed_names))
            raise ValueError(
                f"Refusing to recreate '{absolute.name}'; allowed environment names: {allowed}"
            )
        is_junction = getattr(absolute, "is_junction", lambda: False)
        if absolute.is_symlink() or is_junction():
            raise ValueError(f"Refusing to recreate a linked environment path: {absolute}")
        return absolute

    @staticmethod
    def _next_backup_path(environment: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = environment.with_name(f"{environment.name}.recovery-{stamp}")
        candidate = base
        suffix = 1
        while candidate.exists():
            candidate = environment.with_name(f"{base.name}-{suffix}")
            suffix += 1
        return candidate

    @staticmethod
    def _rollback(environment: Path, backup: Path | None) -> tuple[bool, str]:
        try:
            if environment.exists():
                shutil.rmtree(environment)
            if backup is None:
                return True, "Failed environment removed."
            if not backup.exists():
                return False, "The recovery copy is no longer available."
            backup.rename(environment)
            return True, "Previous environment restored."
        except OSError as exc:
            return False, str(exc)


class _RepairFailure(Exception):
    pass
