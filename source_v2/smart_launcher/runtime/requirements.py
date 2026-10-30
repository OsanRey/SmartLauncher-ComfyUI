from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from smart_launcher.domain.commands import CommandSpec
from smart_launcher.domain.dependencies import (
    RequirementCheck,
    RequirementState,
    RequirementsReport,
)
from smart_launcher.infrastructure.command_runner import CommandRunner

_COMMENT = re.compile(r"\s+#.*$")
_HASH = re.compile(r"\s+--hash(?:=|\s+)\S+")
_INCLUDE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)(.+)$")

_METADATA_PROBE = r"""
import importlib.metadata as metadata
import json
import os
import platform
import sys

packages = []
for distribution in metadata.distributions():
    name = distribution.metadata.get("Name")
    if name:
        packages.append({"name": name, "version": distribution.version})

environment = {
    "implementation_name": sys.implementation.name,
    "implementation_version": platform.python_version(),
    "os_name": os.name,
    "platform_machine": platform.machine(),
    "platform_python_implementation": platform.python_implementation(),
    "platform_release": platform.release(),
    "platform_system": platform.system(),
    "platform_version": platform.version(),
    "python_full_version": platform.python_version(),
    "python_version": ".".join(map(str, sys.version_info[:2])),
    "sys_platform": sys.platform,
    "extra": "",
}
print(json.dumps({"packages": packages, "environment": environment}))
"""


@dataclass(frozen=True, slots=True)
class ParsedRequirement:
    value: Requirement
    raw: str
    source: Path
    line: int


class RequirementsValidator:
    """Validates top-level manifests without starting pip."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def validate(self, python: str | Path, manifest: str | Path) -> RequirementsReport:
        executable = Path(python)
        root = Path(manifest)
        parsed, parse_errors = self.parse(root)
        if parse_errors:
            return RequirementsReport(root, executable, errors=tuple(parse_errors))

        snapshot, snapshot_error = self._snapshot(executable)
        if snapshot_error:
            return RequirementsReport(root, executable, errors=(snapshot_error,))

        packages = {
            canonicalize_name(record["name"]): record["version"]
            for record in snapshot.get("packages", [])
            if isinstance(record, dict)
            and isinstance(record.get("name"), str)
            and isinstance(record.get("version"), str)
        }
        marker_environment = default_environment()
        marker_environment.update(
            {
                str(key): str(value)
                for key, value in snapshot.get("environment", {}).items()
            }
        )

        checks: list[RequirementCheck] = []
        for item in parsed:
            requirement = item.value
            name = canonicalize_name(requirement.name)
            if requirement.marker and not requirement.marker.evaluate(marker_environment):
                checks.append(
                    RequirementCheck(
                        item.raw,
                        name,
                        item.source,
                        item.line,
                        RequirementState.SKIPPED,
                        detail="Environment marker does not apply to the target runtime.",
                    )
                )
                continue
            installed = packages.get(name)
            if installed is None:
                checks.append(
                    RequirementCheck(
                        item.raw,
                        name,
                        item.source,
                        item.line,
                        RequirementState.MISSING,
                        detail="Distribution is not installed.",
                    )
                )
                continue
            try:
                compatible = not requirement.specifier or Version(installed) in requirement.specifier
            except InvalidVersion:
                compatible = False
            checks.append(
                RequirementCheck(
                    item.raw,
                    name,
                    item.source,
                    item.line,
                    RequirementState.SATISFIED if compatible else RequirementState.INCOMPATIBLE,
                    installed_version=installed,
                    detail=(
                        "Installed version satisfies the requirement."
                        if compatible
                        else f"Installed version {installed} does not satisfy {requirement.specifier}."
                    ),
                )
            )
        return RequirementsReport(root, executable, tuple(checks))

    def parse(self, manifest: str | Path) -> tuple[tuple[ParsedRequirement, ...], list[str]]:
        parsed: list[ParsedRequirement] = []
        errors: list[str] = []
        self._parse_file(Path(manifest), parsed, errors, stack=())
        return tuple(parsed), errors

    def _parse_file(
        self,
        manifest: Path,
        parsed: list[ParsedRequirement],
        errors: list[str],
        *,
        stack: tuple[Path, ...],
    ) -> None:
        absolute = manifest.resolve()
        if absolute in stack:
            errors.append(f"Recursive requirements include: {absolute}")
            return
        try:
            text = absolute.read_text(encoding="utf-8-sig")
        except OSError as exc:
            errors.append(f"Could not read {absolute}: {exc}")
            return

        for line_number, logical in self._logical_lines(text):
            value = _COMMENT.sub("", logical).strip()
            if not value or value.startswith("#"):
                continue
            include = _INCLUDE.match(value)
            if include:
                child = (absolute.parent / include.group(1).strip()).resolve()
                self._parse_file(child, parsed, errors, stack=(*stack, absolute))
                continue
            if value.startswith("-"):
                # Index, trusted-host and constraint options are pip policy, not packages.
                continue
            value = _HASH.sub("", value).strip()
            try:
                requirement = Requirement(value)
            except InvalidRequirement as exc:
                errors.append(f"{absolute}:{line_number}: {exc}")
                continue
            parsed.append(ParsedRequirement(requirement, value, absolute, line_number))

    @staticmethod
    def _logical_lines(text: str) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        buffer = ""
        start = 0
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.rstrip()
            if not buffer:
                start = number
            if stripped.endswith("\\"):
                buffer += stripped[:-1] + " "
                continue
            result.append((start, buffer + line))
            buffer = ""
        if buffer:
            result.append((start, buffer))
        return result

    def _snapshot(self, python: Path) -> tuple[dict[str, Any], str | None]:
        result = self._runner.run(
            CommandSpec(
                argv=(str(python), "-I", "-c", _METADATA_PROBE),
                env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                source="requirements-metadata",
                timeout=20.0,
            )
        )
        if not result.succeeded:
            detail = result.exception or result.stderr.strip() or result.failure.value
            return {}, f"Could not inspect target environment: {detail}"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return {}, f"Target environment returned invalid metadata JSON: {exc}"
        if not isinstance(payload, dict):
            return {}, "Target environment returned an invalid metadata payload."
        return payload, None
