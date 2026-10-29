"""Append-only JSONL audit log, one file per run (SPEC §3.5)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import IO, Any, Optional

from agent.paths import ensure_private_dir
from agent.redact import Redactor


class AuditLog:
    def __init__(self, path: Optional[Path], redactor: Redactor) -> None:
        self.path = path
        self._redactor = redactor
        self._fh: Optional[IO[str]] = None
        if path is not None:
            ensure_private_dir(path.parent)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            self._fh = os.fdopen(fd, "a", encoding="utf-8")

    @classmethod
    def for_run(cls, state_dir: Path, run_id: str, redactor: Redactor) -> AuditLog:
        return cls(state_dir / "runs" / f"{run_id}.jsonl", redactor)

    @classmethod
    def disabled(cls) -> AuditLog:
        return cls(None, Redactor())

    def event(self, kind: str, /, **data: Any) -> None:
        if self._fh is None:
            return
        record = {"ts": round(time.time(), 3), "event": kind, **self._redactor.obj(data)}
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
