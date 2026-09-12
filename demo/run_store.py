"""Storage boundary for recorded dashboard runs."""

from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from demo.events import validate_event


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class RunStore(ABC):
    """Minimal interface that can later be backed by Postgres."""

    @abstractmethod
    def create(self, run_id: str, metadata: dict[str, Any]) -> None: ...

    @abstractmethod
    def update(self, run_id: str, **changes: Any) -> dict[str, Any]: ...

    @abstractmethod
    def get(self, run_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def append(self, run_id: str, event: dict[str, Any]) -> None: ...

    @abstractmethod
    def events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_runs(self) -> list[dict[str, Any]]: ...


class FileRunStore(RunStore):
    """Thread-safe JSON metadata plus append-only JSONL events."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def validate_run_id(run_id: str) -> str:
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("invalid run_id")
        return run_id

    def _path(self, run_id: str, suffix: str) -> Path:
        return self.root / f"{self.validate_run_id(run_id)}{suffix}"

    def _write_metadata(self, run_id: str, metadata: dict[str, Any]) -> None:
        target = self._path(run_id, ".meta.json")
        temporary = self._path(run_id, ".meta.tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)

    def create(self, run_id: str, metadata: dict[str, Any]) -> None:
        with self._lock:
            metadata_path = self._path(run_id, ".meta.json")
            events_path = self._path(run_id, ".jsonl")
            if metadata_path.exists() or events_path.exists():
                raise FileExistsError(f"run already exists: {run_id}")
            self._write_metadata(run_id, dict(metadata))
            events_path.touch(exist_ok=False)

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            metadata = self.get(run_id)
            metadata.update(changes)
            self._write_metadata(run_id, metadata)
            return metadata

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(run_id, ".meta.json")
            if not path.is_file():
                raise FileNotFoundError(f"unknown run: {run_id}")
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"invalid metadata for run: {run_id}")
            return value

    def append(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            path = self._path(run_id, ".jsonl")
            if not path.is_file():
                raise FileNotFoundError(f"unknown run: {run_id}")
            existing = self.events(run_id)
            previous = existing[-1] if existing else None
            clean = validate_event(event, previous)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()

    def events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError("after_seq must be a non-negative integer")
        with self._lock:
            path = self._path(run_id, ".jsonl")
            if not path.is_file():
                raise FileNotFoundError(f"unknown run: {run_id}")
            result: list[dict[str, Any]] = []
            previous: dict[str, Any] | None = None
            with path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"invalid JSON in run {run_id} at line {line_number}"
                        ) from exc
                    clean = validate_event(raw, previous)
                    previous = clean
                    if clean["seq"] > after_seq:
                        result.append(clean)
            return result

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            result: list[dict[str, Any]] = []
            for path in sorted(self.root.glob("*.meta.json")):
                run_id = path.name.removesuffix(".meta.json")
                try:
                    result.append(self.get(run_id))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            return result
