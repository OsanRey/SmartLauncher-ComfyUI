from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class RuntimeSource(str, Enum):
    CONFIGURED = "configured"
    PY_MANAGER = "python_manager"
    PATH = "path"
    LEGACY_LAUNCHER = "legacy_launcher"


@dataclass(frozen=True, slots=True)
class RuntimeCandidate:
    executable: Path
    source: RuntimeSource
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeProbe:
    candidate: RuntimeCandidate
    valid: bool
    executable: Path
    version: str | None = None
    version_info: tuple[int, int, int] | None = None
    release_level: str | None = None
    implementation: str | None = None
    architecture: str | None = None
    machine: str | None = None
    free_threaded: bool = False
    prefix: Path | None = None
    base_prefix: Path | None = None
    is_venv: bool = False
    pip_available: bool = False
    venv_available: bool = False
    ensurepip_available: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["candidate"]["source"] = self.candidate.source.value  # type: ignore[index]
        for key in ("executable", "prefix", "base_prefix"):
            value = data.get(key)
            data[key] = str(value) if value is not None else None
        data["candidate"]["executable"] = str(self.candidate.executable)  # type: ignore[index]
        return data


@dataclass(frozen=True, slots=True)
class PythonPolicy:
    preferred_minors: tuple[tuple[int, int], ...] = ((3, 13), (3, 12), (3, 14))
    require_64bit: bool = True
    allow_prerelease: bool = False
    allow_free_threaded: bool = False
    allow_unmanaged_path: bool = False


@dataclass(frozen=True, slots=True)
class PythonResolution:
    selected: RuntimeProbe | None
    probes: tuple[RuntimeProbe, ...]
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "selected": self.selected.as_dict() if self.selected else None,
            "probes": [probe.as_dict() for probe in self.probes],
            "diagnostics": list(self.diagnostics),
        }
