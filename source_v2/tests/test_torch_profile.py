from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from smart_launcher.domain.commands import CommandFailure, CommandResult
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.runtime.torch_profile import TorchProfileValidator


class TorchRunner:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def run(self, spec, **_kwargs):
        now = datetime.now(UTC)
        return CommandResult(
            spec,
            CommandFailure.NONE,
            0,
            now,
            now,
            stdout=json.dumps(self.payload),
        )


class TorchProfileValidatorTests(unittest.TestCase):
    def test_accepts_current_cuda_profile(self) -> None:
        report = TorchProfileValidator(
            TorchRunner(
                {
                    "available": True,
                    "torch_version": "2.13.0+cu130",
                    "cuda_version": "13.0",
                    "cuda_available": True,
                    "device_name": "NVIDIA Test GPU",
                }
            )  # type: ignore[arg-type]
        ).validate("C:/venv/python.exe")

        self.assertTrue(report.compatible)
        self.assertEqual(report.device_name, "NVIDIA Test GPU")

    def test_rejects_cpu_build_for_cuda_profile(self) -> None:
        report = TorchProfileValidator(
            TorchRunner(
                {
                    "available": True,
                    "torch_version": "2.13.0",
                    "cuda_version": None,
                    "cuda_available": False,
                }
            )  # type: ignore[arg-type]
        ).validate("C:/venv/python.exe")

        self.assertFalse(report.compatible)
        self.assertEqual(report.errors, ("PyTorch was built without a CUDA runtime.",))

    def test_supports_explicit_cpu_expectation(self) -> None:
        report = TorchProfileValidator(
            TorchRunner(
                {
                    "available": True,
                    "torch_version": "2.13.0",
                    "cuda_version": None,
                    "cuda_available": False,
                }
            )  # type: ignore[arg-type]
        ).validate(
            "C:/venv/python.exe",
            TorchExpectation(require_cuda=False, cuda_major=None),
        )

        self.assertTrue(report.compatible)


if __name__ == "__main__":
    unittest.main()
