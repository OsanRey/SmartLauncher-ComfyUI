from __future__ import annotations

import os
import shutil
import re
import sys
from collections.abc import Callable
from pathlib import Path

from smart_launcher.domain.commands import CommandResult, CommandSpec
from smart_launcher.domain.updates import (
    RepositoryState,
    UpdateChannel,
    UpdatePlan,
    UpdateResult,
)
from smart_launcher.infrastructure.command_runner import CommandRunner

RevisionValidator = Callable[[UpdatePlan], tuple[bool, str]]


class RepositoryError(RuntimeError):
    pass


class ComfyRepository:
    """Plans and applies revision changes without destructive Git resets."""

    MANIFESTS = ("requirements.txt", "manager_requirements.txt")
    LINKED_OVERLAY_DIRECTORIES = ("models", "custom_nodes")
    DISPENSABLE_PLACEHOLDERS = frozenset(
        {"output/_output_images_will_be_put_here"}
    )

    def __init__(
        self,
        root: str | Path,
        runner: CommandRunner | None = None,
        *,
        git: str | Path | None = None,
        remote: str = "origin",
        development_branch: str = "master",
    ) -> None:
        self.root = Path(root).resolve()
        discovered = str(git) if git is not None else shutil.which("git")
        self.git = Path(discovered).resolve() if discovered else Path("git")
        self.remote = remote
        self.development_branch = development_branch
        self._runner = runner or CommandRunner()

    def inspect(self) -> RepositoryState:
        top = self._git("rev-parse", "--show-toplevel")
        if not top.succeeded:
            return RepositoryState(self.root, False, error=self._detail(top))
        try:
            actual_root = Path(top.stdout.strip()).resolve()
        except OSError as exc:
            return RepositoryState(self.root, False, error=str(exc))
        if actual_root != self.root:
            return RepositoryState(
                self.root,
                False,
                error=f"Git root mismatch: expected {self.root}, got {actual_root}",
            )

        head = self._git("rev-parse", "HEAD")
        if not head.succeeded:
            return RepositoryState(self.root, False, error=self._detail(head))
        branch_result = self._git("branch", "--show-current")
        status = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if not status.succeeded:
            return RepositoryState(self.root, False, error=self._detail(status))
        dirty = self._filter_dispensable_placeholders(
            self._filter_linked_overlays(
                self._parse_porcelain(self._without_running_launcher(status.stdout)),
                self._active_overlays(),
            )
        )
        return RepositoryState(
            root=self.root,
            valid=True,
            head=head.stdout.strip(),
            branch=branch_result.stdout.strip() or None,
            dirty_paths=dirty,
        )

    def _without_running_launcher(self, status: str) -> str:
        if not getattr(sys, "frozen", False):
            return status
        executable = Path(sys.executable).resolve()
        if executable.parent != self.root:
            return status
        # Only the actual running, untracked EXE is exempt, never tracked changes.
        separator = "\0" if "\0" in status else "\n"
        return separator.join(
            record for record in status.split(separator)
            if record != f"?? {executable.name}"
        )

    def prepare(self, channel: UpdateChannel, *, fetch: bool = True) -> UpdatePlan:
        before = self.inspect()
        if not before.valid or before.head is None:
            raise RepositoryError(before.error or "Репозиторий ComfyUI повреждён.")
        if not before.clean:
            raise RepositoryError(
                "В ядре ComfyUI есть локальные изменения: "
                + ", ".join(before.dirty_paths[:8])
            )
        if fetch:
            fetched = self._git("fetch", "--prune", "--tags", self.remote)
            if not fetched.succeeded:
                raise RepositoryError(f"Git fetch failed: {self._detail(fetched)}")

        target_ref = self._target_ref(channel)
        target = self._git("rev-parse", f"{target_ref}^{{commit}}")
        if not target.succeeded or not target.stdout.strip():
            raise RepositoryError(f"Could not resolve {target_ref}: {self._detail(target)}")
        target_commit = target.stdout.strip()
        changed = self._changed_manifests(before.head, target_commit)
        # Optional presentation metadata must not prevent an otherwise valid update.
        label = self._git("describe", "--tags", "--exact-match", before.head)
        changes: tuple[str, ...] = ()
        requirements_diff = ""
        compare_url = ""
        if before.head != target_commit:
            remote_url = self._git("remote", "get-url", self.remote)
            if remote_url.succeeded:
                match = re.fullmatch(
                    r"(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?",
                    remote_url.stdout.strip(),
                )
                if match:
                    compare_url = f"https://github.com/{match[1]}/compare/{before.head}...{target_commit}"
            history = self._git(
                "log", "--no-decorate", "--format=%h %s", "-n", "50",
                f"{before.head}..{target_commit}",
            )
            if history.succeeded:
                changes = tuple(history.stdout.strip().splitlines())
            if changed:
                diff = self._git(
                    "diff", "--no-ext-diff", "--no-textconv", "--unified=0",
                    before.head, target_commit, "--", *changed,
                )
                if diff.succeeded:
                    requirements_diff = diff.stdout[:20000]
        return UpdatePlan(
            root=self.root,
            channel=channel,
            current_commit=before.head,
            target_commit=target_commit,
            target_ref=target_ref,
            changed_manifests=changed,
            current_label=label.stdout.strip() if label.succeeded else before.head[:12],
            changes=changes,
            requirements_diff=requirements_diff,
            compare_url=compare_url,
        )

    def apply(
        self,
        plan: UpdatePlan,
        *,
        validate: RevisionValidator | None = None,
    ) -> UpdateResult:
        if plan.root.resolve() != self.root:
            return UpdateResult(False, plan, None, "Update plan belongs to another repository.")
        current = self.inspect()
        if not current.clean or current.head != plan.current_commit:
            return UpdateResult(
                False,
                plan,
                current.head,
                "Repository changed after the update plan was created.",
            )
        if not plan.update_available:
            return UpdateResult(True, plan, current.head, "Core revision is already current.")

        switched, switch_detail = self._switch_preserving_overlays(plan.target_commit)
        if not switched:
            return UpdateResult(False, plan, current.head, switch_detail)

        if validate is not None:
            try:
                valid, detail = validate(plan)
            except Exception as exc:
                valid, detail = False, str(exc)
            if not valid:
                rolled_back, rollback_error = self._switch_preserving_overlays(
                    plan.current_commit
                )
                final = plan.current_commit if rolled_back else plan.target_commit
                rollback_detail = (
                    ""
                    if rolled_back
                    else f" Не удалось вернуть прежнее ядро: {rollback_error}"
                )
                return UpdateResult(
                    False,
                    plan,
                    final,
                    f"Revision validation failed: {detail}.{rollback_detail}",
                    rolled_back=rolled_back,
                )

        return UpdateResult(True, plan, plan.target_commit, "Core revision updated and validated.")

    def _switch_preserving_overlays(self, commit: str) -> tuple[bool, str]:
        overlays = self._active_overlays()
        if not overlays:
            result = self._git("switch", "--detach", commit)
            return result.succeeded, self._detail(result)

        parked: list[tuple[str, Path, Path]] = []
        outcome = (False, "Не удалось переключить ядро ComfyUI.")
        try:
            for name, source in overlays:
                parking = self.root.parent / (
                    f".{self.root.name}.smart-launcher-{name}-link"
                )
                if os.path.lexists(parking):
                    outcome = (
                        False,
                        f"Найдена незавершённая операция со ссылкой {name}: {parking}",
                    )
                    break
                os.replace(source, parking)
                parked.append((name, source, parking))
            else:
                restored = self._git(
                    "restore",
                    "--worktree",
                    "--source=HEAD",
                    "--",
                    *(name for name, _, _ in parked),
                )
                if not restored.succeeded:
                    outcome = (
                        False,
                        "Не удалось подготовить linked-каталоги: "
                        + self._detail(restored),
                    )
                else:
                    switched = self._git("switch", "--detach", commit)
                    outcome = (switched.succeeded, self._detail(switched))
        except OSError as exc:
            outcome = (False, f"Не удалось временно переместить ссылку: {exc}")
        finally:
            recovery_errors: list[str] = []
            for _name, source, parking in reversed(parked):
                try:
                    self._remove_generated_overlay(source)
                    if os.path.lexists(parking):
                        os.replace(parking, source)
                except OSError as exc:
                    # The parked link is deliberately retained for manual recovery.
                    recovery_errors.append(str(exc))
            if recovery_errors:
                outcome = (
                    False,
                    "Не удалось вернуть linked-каталоги: " + "; ".join(recovery_errors),
                )
        return outcome

    def _active_overlays(self) -> tuple[tuple[str, Path], ...]:
        return tuple(
            (name, path)
            for name in self.LINKED_OVERLAY_DIRECTORIES
            if _is_link_or_junction(path := self.root / name)
        )

    @staticmethod
    def _filter_linked_overlays(
        dirty_paths: tuple[str, ...], overlays: tuple[tuple[str, Path], ...]
    ) -> tuple[str, ...]:
        names = {name.casefold() for name, _ in overlays}
        return tuple(
            path
            for path in dirty_paths
            if path.replace("\\", "/").split("/", 1)[0].casefold() not in names
        )

    @classmethod
    def _filter_dispensable_placeholders(
        cls, dirty_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(
            path
            for path in dirty_paths
            if path.replace("\\", "/").casefold()
            not in {item.casefold() for item in cls.DISPENSABLE_PLACEHOLDERS}
        )

    @staticmethod
    def _remove_generated_overlay(path: Path) -> None:
        if not os.path.lexists(path):
            return
        if _is_link_or_junction(path):
            raise OSError(f"На месте linked-каталога появилась новая ссылка: {path}")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def _target_ref(self, channel: UpdateChannel) -> str:
        if channel is UpdateChannel.DEVELOPMENT:
            return f"refs/remotes/{self.remote}/{self.development_branch}"
        tags = self._git(
            "for-each-ref",
            "--sort=-version:refname",
            "--format=%(refname:short)",
            "refs/tags/v[0-9]*",
        )
        if not tags.succeeded:
            raise RepositoryError(f"Could not list stable tags: {self._detail(tags)}")
        latest = next((line.strip() for line in tags.stdout.splitlines() if line.strip()), None)
        if latest is None:
            raise RepositoryError("No stable ComfyUI tag was found.")
        return f"refs/tags/{latest}"

    def _changed_manifests(self, before: str, after: str) -> tuple[str, ...]:
        if before == after:
            return ()
        result = self._git("diff", "--name-only", before, after, "--", *self.MANIFESTS)
        if not result.succeeded:
            raise RepositoryError(f"Could not compare dependency manifests: {self._detail(result)}")
        changed = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return tuple(name for name in self.MANIFESTS if name in changed)

    def _git(self, *arguments: str) -> CommandResult:
        return self._runner.run(
            CommandSpec(
                argv=(str(self.git), *arguments),
                cwd=self.root,
                env={"GIT_TERMINAL_PROMPT": "0"},
                source="git",
                timeout=60.0,
            )
        )

    @staticmethod
    def _parse_porcelain(value: str) -> tuple[str, ...]:
        paths: list[str] = []
        records = value.split("\0") if "\0" in value else value.splitlines()
        for record in records:
            if not record:
                continue
            path = record[3:] if len(record) > 3 else record
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.append(path)
        return tuple(paths)

    @staticmethod
    def _detail(result: CommandResult) -> str:
        return result.exception or result.stderr.strip() or result.stdout.strip() or result.failure.value


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()
