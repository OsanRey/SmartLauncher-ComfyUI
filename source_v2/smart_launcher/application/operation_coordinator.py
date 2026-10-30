from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from enum import Enum


class OperationKind(str, Enum):
    INSTALLATION = "installation"
    MAINTENANCE = "maintenance"
    CUSTOM_NODES = "custom-nodes"


@dataclass(frozen=True, slots=True)
class OperationSnapshot:
    operation_id: str
    kind: OperationKind


class OperationLease:
    def __init__(self, coordinator: "OperationCoordinator", snapshot: OperationSnapshot) -> None:
        self._coordinator = coordinator
        self.snapshot = snapshot
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._coordinator._release(self.snapshot)


class OperationCoordinator:
    """Grants one lease for operations that may mutate ComfyUI or its venv."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: OperationSnapshot | None = None

    @property
    def active(self) -> OperationSnapshot | None:
        with self._lock:
            return self._active

    @property
    def busy(self) -> bool:
        return self.active is not None

    def try_acquire(self, kind: OperationKind) -> OperationLease | None:
        with self._lock:
            if self._active is not None:
                return None
            snapshot = OperationSnapshot(uuid.uuid4().hex, kind)
            self._active = snapshot
            return OperationLease(self, snapshot)

    def _release(self, snapshot: OperationSnapshot) -> None:
        with self._lock:
            if self._active == snapshot:
                self._active = None
