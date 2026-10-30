from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import re

from packaging.version import InvalidVersion, Version


_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    owner: str
    name: str

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"


def parse_github_repository(value: str) -> GitHubRepository:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("Нужна HTTPS-ссылка вида https://github.com/author/repository.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Ссылка на репозиторий не должна содержать учётные данные или параметры.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("Укажите ссылку на сам репозиторий, без /tree, /issues и других разделов.")
    owner, name = parts
    if name.lower().endswith(".git"):
        name = name[:-4]
    if (
        owner in {".", ".."}
        or name in {"", ".", ".."}
        or not _REPOSITORY_PART.fullmatch(owner)
        or not _REPOSITORY_PART.fullmatch(name)
    ):
        raise ValueError("Имя владельца или репозитория содержит недопустимые символы.")
    return GitHubRepository(owner, name)


def parse_github_origin(value: str) -> GitHubRepository:
    normalized = value.strip()
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    elif normalized.startswith("ssh://git@github.com/"):
        normalized = "https://github.com/" + normalized.removeprefix(
            "ssh://git@github.com/"
        )
    return parse_github_repository(normalized)


@dataclass(frozen=True, slots=True)
class CustomNodeRequest:
    repository_url: str
    comfy_root: Path
    venv_path: Path
    install_requirements: bool = True
    registry_id: str | None = None
    remove_target: Path | None = None
    repair_dependencies: bool = False


@dataclass(frozen=True, slots=True)
class CustomNodeResult:
    succeeded: bool
    detail: str
    target: Path | None = None
    updated: bool = False
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class InstalledCustomNode:
    name: str
    path: Path
    repository_url: str | None = None
    branch: str | None = None
    local_commit: str | None = None
    remote_commit: str | None = None
    dirty: bool = False
    error: str | None = None
    registry_id: str | None = None
    installed_version: str | None = None
    latest_version: str | None = None

    @property
    def managed(self) -> bool:
        return (
            self.repository_url is not None and self.local_commit is not None
        ) or (
            self.registry_id is not None and self.installed_version is not None
        )

    @property
    def is_registry(self) -> bool:
        return self.registry_id is not None and self.installed_version is not None

    @property
    def update_available(self) -> bool | None:
        if self.is_registry:
            if self.latest_version is None:
                return None
            try:
                return Version(self.latest_version) > Version(self.installed_version or "0")
            except InvalidVersion:
                return self.latest_version != self.installed_version
        if self.remote_commit is None or self.local_commit is None:
            return None
        return self.remote_commit != self.local_commit
