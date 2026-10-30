from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from smart_launcher.application.existing_installation import (
    ExistingInstallationError,
    ExistingInstallationService,
)
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.environments import EnvironmentHealth, EnvironmentState
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource


class _Resolver:
    def probe(self, candidate) -> RuntimeProbe:
        return RuntimeProbe(
            RuntimeCandidate(candidate.executable, RuntimeSource.CONFIGURED),
            True,
            candidate.executable,
            version="3.13.15",
            version_info=(3, 13, 15),
            release_level="final",
            implementation="cpython",
            architecture="64bit",
            prefix=candidate.executable.parent,
            base_prefix=candidate.executable.parent,
            venv_available=True,
            ensurepip_available=True,
        )


class _Environments:
    def inspect(self, root, **_kwargs):
        python = Path(root) / "Scripts" / "python.exe"
        return EnvironmentHealth(Path(root), python, EnvironmentState.HEALTHY, "ok")


class ExistingInstallationTests(unittest.TestCase):
    def test_explicit_valid_installation_builds_profile_from_its_own_venv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ChosenComfy"
            (root / "venv").mkdir(parents=True)
            (root / "main.py").write_text("", encoding="utf-8")
            (root / "requirements.txt").write_text("", encoding="utf-8")
            (root / "venv" / "pyvenv.cfg").write_text(
                f"home = {root / 'BasePython'}\n", encoding="utf-8"
            )
            service = ExistingInstallationService(
                resolver=_Resolver(),  # type: ignore[arg-type]
                environments=_Environments(),  # type: ignore[arg-type]
                requirements=SimpleNamespace(
                    validate=lambda *_args: SimpleNamespace(healthy=True)
                ),
                torch=SimpleNamespace(
                    validate=lambda *_args: SimpleNamespace(compatible=True, errors=())
                ),
            )
            with patch(
                "smart_launcher.application.existing_installation.ComfyRepository"
            ) as repository:
                repository.return_value.inspect.return_value = SimpleNamespace(
                    valid=True, error=None
                )
                profile = service.connect(
                    root, expectation=TorchExpectation(require_cuda=False, cuda_major=None)
                )

            self.assertEqual(profile.settings.comfy_root, root.resolve())
            self.assertEqual(profile.settings.venv_path, root.resolve() / "venv")
            self.assertFalse(profile.settings.torch_require_cuda)
            self.assertIsNone(profile.settings.torch_index_url)

    def test_missing_contract_is_rejected_before_runtime_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExistingInstallationError):
                ExistingInstallationService().connect(Path(directory) / "NotComfy")


if __name__ == "__main__":
    unittest.main()
