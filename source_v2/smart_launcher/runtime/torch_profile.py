from __future__ import annotations

import json
from pathlib import Path

from packaging.version import InvalidVersion, Version

from smart_launcher.domain.commands import CommandSpec
from smart_launcher.domain.dependencies import TorchExpectation, TorchReport
from smart_launcher.infrastructure.command_runner import CommandRunner

_TORCH_PROBE = r"""
import json
try:
    import torch
except Exception as exc:
    print(json.dumps({"available": False, "error": f"{type(exc).__name__}: {exc}"}))
else:
    cuda_available = bool(torch.cuda.is_available())
    device_name = None
    if cuda_available:
        try:
            device_name = torch.cuda.get_device_name(0)
        except Exception:
            pass
    print(json.dumps({
        "available": True,
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cuda_available": cuda_available,
        "device_name": device_name,
    }))
"""


class TorchProfileValidator:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def validate(
        self,
        python: str | Path,
        expectation: TorchExpectation = TorchExpectation(),
    ) -> TorchReport:
        executable = Path(python)
        result = self._runner.run(
            CommandSpec(
                argv=(str(executable), "-I", "-c", _TORCH_PROBE),
                env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                source="torch-profile",
                timeout=45.0,
            )
        )
        if not result.succeeded:
            detail = result.exception or result.stderr.strip() or result.failure.value
            return TorchReport(executable, False, False, errors=(detail,))
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return TorchReport(executable, False, False, errors=(f"Invalid JSON: {exc}",))

        available = bool(payload.get("available"))
        torch_version = payload.get("torch_version")
        cuda_version = payload.get("cuda_version")
        cuda_available = bool(payload.get("cuda_available"))
        errors: list[str] = []
        if not available:
            errors.append(str(payload.get("error") or "PyTorch is not importable."))
        if available:
            try:
                if Version(str(torch_version).split("+", 1)[0]) < Version(
                    expectation.minimum_version
                ):
                    errors.append(
                        f"PyTorch {torch_version} is older than {expectation.minimum_version}."
                    )
            except InvalidVersion:
                errors.append(f"Invalid PyTorch version: {torch_version}")
        if expectation.require_cuda and not cuda_version:
            errors.append("PyTorch was built without a CUDA runtime.")
        elif expectation.require_cuda and not cuda_available:
            errors.append("CUDA is not available to PyTorch.")
        if expectation.cuda_major is not None and cuda_version:
            try:
                major = int(str(cuda_version).split(".", 1)[0])
            except ValueError:
                errors.append(f"Invalid CUDA version: {cuda_version}")
            else:
                if major != expectation.cuda_major:
                    errors.append(
                        f"PyTorch CUDA {cuda_version} does not match CUDA "
                        f"{expectation.cuda_major}.x profile."
                    )
        return TorchReport(
            python=executable,
            available=available,
            compatible=not errors,
            torch_version=str(torch_version) if torch_version is not None else None,
            cuda_version=str(cuda_version) if cuda_version is not None else None,
            cuda_available=cuda_available,
            device_name=(
                str(payload["device_name"]) if payload.get("device_name") is not None else None
            ),
            errors=tuple(errors),
        )
