"""FastAPI live dashboard and ``python -m demo.web`` entry point."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from demo.coordinator import DEFAULT_SAMPLE, PROJECT_ROOT, RunBusyError, RunCoordinator
from demo.events import EventValidationError
from demo.run_store import FileRunStore
from orchestrator import FIXED_SCENARIO


PUBLIC_DIR = Path(__file__).resolve().parent / "static"
load_dotenv(override=False)


class RunRequest(BaseModel):
    mode: Literal["live", "replay"] = "replay"
    top_n: int = Field(default=3, ge=3, le=5)
    replay_file: str | None = None
    replay_speed: float | None = Field(default=None, ge=0.1, le=1000)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _provider_context() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for public_name, env_name in (
        ("seller", "SELLER_LLM_CHAIN"),
        ("buyer_1", "BUYER_1_LLM_CHAIN"),
        ("buyer_2", "BUYER_2_LLM_CHAIN"),
        ("buyer_3", "BUYER_3_LLM_CHAIN"),
    ):
        value = os.getenv(env_name, "")
        result[public_name] = [item.strip() for item in value.split(",") if item.strip()]
    return result


def _audience_constraints() -> dict[str, object]:
    """Public demo overlays; these values are never sent to negotiation agents."""
    data_dir = PROJECT_ROOT / "data"
    try:
        seller = json.loads((data_dir / "seller.json").read_text(encoding="utf-8"))
        buyers = json.loads((data_dir / "buyers.json").read_text(encoding="utf-8"))
        return {
            "seller_floor": seller["min_acceptable_price_per_tonne_usd"],
            "buyer_ceilings": {
                buyer["buyer_id"]: buyer["max_acceptable_price_per_tonne_usd"]
                for buyer in buyers
            },
        }
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return {"seller_floor": None, "buyer_ceilings": {}}


def create_app(
    *,
    runs_dir: str | Path | None = None,
    coordinator: RunCoordinator | None = None,
    auto_replay_file: str | Path | None = None,
) -> FastAPI:
    run_path = Path(runs_dir or os.getenv("RUNS_DIR", PROJECT_ROOT / "runs"))
    run_coordinator = coordinator or RunCoordinator(FileRunStore(run_path))
    default_speed = _env_float("DEFAULT_REPLAY_SPEED", 4.0)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.coordinator.recover_interrupted_runs()
        yield

    application = FastAPI(
        title="Circularity Agent Live Demo",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.coordinator = run_coordinator
    application.state.default_replay_file = Path(auto_replay_file).resolve() if auto_replay_file else DEFAULT_SAMPLE
    application.state.auto_start_replay = auto_replay_file is not None
    application.mount("/assets", StaticFiles(directory=PUBLIC_DIR), name="assets")

    @application.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(PUBLIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/context")
    def context(request: Request) -> dict[str, object]:
        return {
            "scenario": dict(FIXED_SCENARIO),
            "providers": _provider_context(),
            "audience_constraints": _audience_constraints(),
            "default_top_n": 3,
            "default_replay_speed": default_speed,
            "sample_replay_available": DEFAULT_SAMPLE.is_file(),
            "auto_start_replay": bool(request.app.state.auto_start_replay),
            "deployment": "render" if os.getenv("RENDER") else "local",
        }

    @application.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    def create_run(body: RunRequest, request: Request) -> JSONResponse:
        coord: RunCoordinator = request.app.state.coordinator
        try:
            if body.mode == "live":
                metadata = coord.create_live(top_n=body.top_n)
            else:
                source = body.replay_file or request.app.state.default_replay_file
                metadata = coord.create_replay(
                    replay_file=source,
                    replay_speed=body.replay_speed or default_speed,
                )
        except RunBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, EventValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        run_id = metadata["run_id"]
        payload = {
            **metadata,
            "status_url": f"/api/runs/{run_id}",
            "events_url": f"/api/runs/{run_id}/events",
        }
        return JSONResponse(payload, status_code=status.HTTP_202_ACCEPTED)

    @application.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request) -> dict[str, object]:
        try:
            return request.app.state.coordinator.store.get(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @application.get("/api/runs/{run_id}/events")
    def stream_run(
        run_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            after_seq = int(last_event_id) if last_event_id is not None else 0
            if after_seq < 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be non-negative") from exc
        try:
            iterator = request.app.state.coordinator.stream(run_id, after_seq=after_seq)
            request.app.state.coordinator.store.get(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return StreamingResponse(
            iterator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_error(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            {"detail": "The dashboard could not complete the request."},
            status_code=500,
        )

    return application


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Circularity Agent live dashboard")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--runs-dir", default=os.getenv("RUNS_DIR", str(PROJECT_ROOT / "runs")))
    parser.add_argument("--replay", metavar="JSONL", help="auto-start a validated recording in the UI")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "info"))
    parser.add_argument("--env-file", help="Optional dotenv file; existing environment variables take precedence")
    args = parser.parse_args(argv)

    if args.env_file:
        env_path = Path(args.env_file)
        if not env_path.is_file():
            parser.error(f"environment file not found: {env_path}")
        load_dotenv(env_path, override=False)

    import uvicorn

    server_app = create_app(runs_dir=args.runs_dir, auto_replay_file=args.replay)
    uvicorn.run(server_app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
