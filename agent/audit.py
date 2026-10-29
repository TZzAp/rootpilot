"""Append-only JSONL audit log, one file per run (SPEC §3.5)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import IO, Any, Optional

from agent.paths import ensure_private_dir
from agent.redact import Redactor


def recent_runs(runs_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    """Summaries of the newest runs (task, answer, outcome), newest first."""
    out: list[dict[str, Any]] = []
    files = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:limit]:
        start: dict[str, Any] = {}
        end: dict[str, Any] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("event") == "run_start":
                        start = event
                    elif event.get("event") == "run_end":
                        end = event
        except OSError:
            continue
        if not start:
            continue
        out.append(
            {
                "run_id": start.get("run_id", path.stem),
                "ts": start.get("ts"),
                "task": start.get("task", ""),
                "answer": end.get("answer"),
                "success": end.get("success"),
                "reason": end.get("reason", "running" if not end else ""),
                "steps": end.get("steps"),
            }
        )
    return out


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
