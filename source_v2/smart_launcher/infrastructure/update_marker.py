from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class UpdateMarker:
    def __init__(self, root: Path):
        root = root.resolve()
        self.path = root.parent / f".{root.name}.smart-launcher-update.json"

    @property
    def pending(self) -> bool:
        # Even an empty/corrupt marker means completion was not confirmed.
        return os.path.lexists(self.path)

    def begin(self, plan) -> None:
        fd, name = tempfile.mkstemp(prefix=self.path.name, dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"schema": 1, "root": str(plan.root),
                           "started": datetime.now(timezone.utc).isoformat(),
                           "current_commit": plan.current_commit,
                           "target_commit": plan.target_commit}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def complete(self) -> None:
        self.path.unlink(missing_ok=True)
