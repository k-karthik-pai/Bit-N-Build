"""Run coordination, recording, replay, and SSE transport."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from demo.events import TERMINAL_TYPES, EventValidationError, parse_timestamp, validate_run
from demo.run_store import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = PROJECT_ROOT / "demo" / "sample_run.jsonl"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_error(exc: BaseException) -> str:
    message = str(exc).strip() or type(exc).__name__
    for name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        secret = os.getenv(name)
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:1000]


class RunBusyError(RuntimeError):
    """Raised when the single-user demo already has a running job."""


class RunCoordinator:
    def __init__(
        self,
        store: RunStore,
        *,
        replay_roots: Optional[list[str | Path]] = None,
        live_runner: Optional[Callable[..., Any]] = None,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        self.store = store
        self.live_runner = live_runner
        self.heartbeat_seconds = heartbeat_seconds
        roots = replay_roots or [DEFAULT_SAMPLE.parent, getattr(store, "root", PROJECT_ROOT / "runs")]
        self.replay_roots = [Path(root).expanduser().resolve() for root in roots]
        self._condition = threading.Condition(threading.RLock())
        self._active_run_id: str | None = None

    def recover_interrupted_runs(self) -> None:
        """Close runs left active by a previous server process."""
        for metadata in self.store.list_runs():
            if metadata.get("status") not in {"queued", "running"}:
                continue
            run_id = metadata.get("run_id")
            if not isinstance(run_id, str):
                continue
            events = self.store.events(run_id)
            if events and events[-1]["type"] in TERMINAL_TYPES:
                status = "completed" if events[-1]["type"] == "run_completed" else "failed"
                self.store.update(run_id, status=status, updated_at=utc_timestamp())
                continue
            self._append(
                run_id,
                self._terminal_event(
                    run_id,
                    "run_failed",
                    {"error": "Run was interrupted by a server restart."},
                ),
            )

    def _new_run_id(self) -> str:
        return f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"

    def _claim(self, run_id: str) -> None:
        with self._condition:
            if self._active_run_id is not None:
                try:
                    active = self.store.get(self._active_run_id)
                except (FileNotFoundError, ValueError):
                    active = {}
                if active.get("status") in {"queued", "running"}:
                    raise RunBusyError(f"run {self._active_run_id} is already active")
            self._active_run_id = run_id

    def create_live(self, *, top_n: int = 3) -> dict[str, Any]:
        if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 5:
            raise ValueError("top_n must be an integer from 1 to 5")
        run_id = self._new_run_id()
        self._claim(run_id)
        metadata = {
            "run_id": run_id,
            "mode": "live",
            "status": "queued",
            "top_n": top_n,
            "created_at": utc_timestamp(),
            "updated_at": utc_timestamp(),
            "event_count": 0,
        }
        try:
            self.store.create(run_id, metadata)
        except Exception:
            with self._condition:
                self._active_run_id = None
            raise
        threading.Thread(
            target=self._run_live,
            name=f"live-{run_id}",
            args=(run_id, top_n),
            daemon=True,
        ).start()
        return metadata

    def create_replay(
        self,
        *,
        replay_file: str | Path | None = None,
        replay_speed: float = 4.0,
    ) -> dict[str, Any]:
        if isinstance(replay_speed, bool) or not isinstance(replay_speed, (int, float)):
            raise ValueError("replay_speed must be numeric")
        if not 0.1 <= float(replay_speed) <= 1000:
            raise ValueError("replay_speed must be between 0.1 and 1000")
        source = self.resolve_replay_file(replay_file or DEFAULT_SAMPLE)
        source_events = self.load_recording(source)
        run_id = self._new_run_id()
        self._claim(run_id)
        metadata = {
            "run_id": run_id,
            "mode": "replay",
            "status": "queued",
            "replay_source": source.name,
            "replay_speed": float(replay_speed),
            "created_at": utc_timestamp(),
            "updated_at": utc_timestamp(),
            "event_count": 0,
        }
        try:
            self.store.create(run_id, metadata)
        except Exception:
            with self._condition:
                self._active_run_id = None
            raise
        threading.Thread(
            target=self._run_replay,
            name=f"replay-{run_id}",
            args=(run_id, source_events, float(replay_speed)),
            daemon=True,
        ).start()
        return metadata

    def resolve_replay_file(self, value: str | Path) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate.suffix.lower() != ".jsonl":
            raise ValueError("replay file must have a .jsonl extension")
        if not any(candidate.is_relative_to(root) for root in self.replay_roots):
            raise ValueError("replay file is outside the allowed replay directories")
        if not candidate.is_file():
            raise FileNotFoundError(f"replay file not found: {candidate.name}")
        return candidate

    @staticmethod
    def load_recording(path: Path) -> list[dict[str, Any]]:
        raw_events: list[Any] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    raw_events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise EventValidationError(
                        f"invalid JSON in replay at line {line_number}"
                    ) from exc
        return validate_run(raw_events)

    def _append(self, run_id: str, event: dict[str, Any]) -> None:
        if event.get("run_id") != run_id:
            raise EventValidationError("emitted event run_id does not match the active run")
        existing = self.store.events(run_id)
        if not existing and event.get("type") not in {"run_started", "run_failed"}:
            raise EventValidationError("the first live event must be run_started")
        self.store.append(run_id, event)
        status = None
        if event["type"] == "run_completed":
            status = "completed"
        elif event["type"] == "run_failed":
            status = "failed"
        changes: dict[str, Any] = {
            "updated_at": utc_timestamp(),
            "event_count": event["seq"],
        }
        if status:
            changes["status"] = status
        self.store.update(run_id, **changes)
        with self._condition:
            self._condition.notify_all()

    def _terminal_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.store.events(run_id)
        timestamp = utc_timestamp()
        if existing and parse_timestamp(timestamp) < parse_timestamp(existing[-1]["ts"]):
            timestamp = existing[-1]["ts"]
        return {
            "seq": len(existing) + 1,
            "ts": timestamp,
            "run_id": run_id,
            "deal_id": None,
            "type": event_type,
            "from_agent": "orchestrator",
            "to_agent": None,
            "model": None,
            "payload": payload,
            "validator": None,
            "delivered": None,
        }

    def _finish_active(self, run_id: str) -> None:
        with self._condition:
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._condition.notify_all()

    def _resolve_live_runner(self) -> Callable[..., Any]:
        if self.live_runner is not None:
            return self.live_runner
        import orchestrator

        runner = getattr(orchestrator, "run_multi_agent", None)
        if not callable(runner):
            raise RuntimeError(
                "Live multi-agent runtime is not available yet; use Replay Sample until Part 1 exposes orchestrator.run_multi_agent"
            )
        return runner

    def _run_live(self, run_id: str, top_n: int) -> None:
        started = time.monotonic()
        self.store.update(run_id, status="running", updated_at=utc_timestamp())
        try:
            runner = self._resolve_live_runner()
            result = runner(run_id=run_id, top_n=top_n, emit=lambda event: self._append(run_id, event))
            if inspect.isawaitable(result):
                asyncio.run(result)
            events = self.store.events(run_id)
            if not events:
                raise EventValidationError("live runner returned without emitting run_started")
            if events[-1]["type"] not in TERMINAL_TYPES:
                body = events
                terminal = self._terminal_event(
                    run_id,
                    "run_completed",
                    {
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "llm_calls": sum(event["model"] is not None for event in body),
                        "validator_bounces": sum(event["delivered"] is False for event in body),
                        "fallbacks": sum(event["type"] == "fallback" for event in body),
                    },
                )
                self._append(run_id, terminal)
        except Exception as exc:
            existing = self.store.events(run_id)
            if not existing or existing[-1]["type"] not in TERMINAL_TYPES:
                self._append(
                    run_id,
                    self._terminal_event(run_id, "run_failed", {"error": _safe_error(exc)}),
                )
        finally:
            self._finish_active(run_id)

    def _run_replay(
        self,
        run_id: str,
        source_events: list[dict[str, Any]],
        speed: float,
    ) -> None:
        self.store.update(run_id, status="running", updated_at=utc_timestamp())
        try:
            previous_time: datetime | None = None
            for source_event in source_events:
                current_time = parse_timestamp(source_event["ts"])
                if previous_time is not None:
                    delay = max(0.0, (current_time - previous_time).total_seconds() / speed)
                    if delay:
                        time.sleep(delay)
                event = dict(source_event)
                event["run_id"] = run_id
                self._append(run_id, event)
                previous_time = current_time
        except Exception as exc:
            existing = self.store.events(run_id)
            if not existing or existing[-1]["type"] not in TERMINAL_TYPES:
                self._append(
                    run_id,
                    self._terminal_event(run_id, "run_failed", {"error": _safe_error(exc)}),
                )
        finally:
            self._finish_active(run_id)

    def stream(self, run_id: str, *, after_seq: int = 0) -> Iterator[str]:
        self.store.get(run_id)
        cursor = after_seq
        while True:
            events = self.store.events(run_id, after_seq=cursor)
            for event in events:
                cursor = event["seq"]
                yield (
                    f"id: {cursor}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
                if event["type"] in TERMINAL_TYPES:
                    return
            metadata = self.store.get(run_id)
            if metadata.get("status") in {"completed", "failed"}:
                return
            with self._condition:
                notified = self._condition.wait(timeout=self.heartbeat_seconds)
            if not notified:
                yield ": heartbeat\n\n"
