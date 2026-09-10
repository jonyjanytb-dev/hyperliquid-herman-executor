from __future__ import annotations

import json
import os
from pathlib import Path
from .models import RuntimeState


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            return RuntimeState.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            return RuntimeState()

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
