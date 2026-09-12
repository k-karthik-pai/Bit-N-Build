import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from demo.coordinator import RunCoordinator
from demo.events import (
    EVENT_TYPES,
    EventValidationError,
    validate_event,
    validate_run,
)
from demo.run_store import FileRunStore
from demo.web import create_app


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "demo" / "sample_run.jsonl"


def wait_for_terminal(store, run_id, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        metadata = store.get(run_id)
        if metadata["status"] in {"completed", "failed"}:
            return metadata
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not finish")


def envelope(event_type, payload, *, seq=1, run_id="test-run"):
    return {
        "seq": seq,
        "ts": "2026-09-12T10:15:00.000Z",
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


class FrozenEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = RunCoordinator.load_recording(SAMPLE)

    def test_sample_is_a_complete_valid_run(self):
        self.assertEqual(len(self.sample), 36)
        self.assertEqual(self.sample[0]["type"], "run_started")
        self.assertEqual(self.sample[-1]["type"], "run_completed")

    def test_every_frozen_event_type_is_validatable(self):
        seen = {event["type"] for event in self.sample}
        reject = envelope(
            "reject",
            {"round": 1, "reason": "terms", "message": "We decline.", "rationale": "Outside strategy."},
        )
        reject.update({
            "deal_id": "buyer-1",
            "from_agent": "buyer_agent:buyer-1",
            "to_agent": "seller_agent",
            "validator": {"ok": True, "gate": "move_legal", "reason": "within your bounds"},
            "delivered": True,
        })
        validate_event(reject)
        failed = envelope("run_failed", {"error": "provider unavailable"})
        validate_event(failed)
        seen.update({"reject", "run_failed"})
        self.assertEqual(seen, EVENT_TYPES)

    def test_invalid_envelope_payload_and_order_fail_closed(self):
        invalid = dict(self.sample[0])
        invalid.pop("delivered")
        with self.assertRaisesRegex(EventValidationError, "envelope"):
            validate_event(invalid)
        invalid = dict(self.sample[0])
        invalid["payload"] = {}
        with self.assertRaisesRegex(EventValidationError, "payload"):
            validate_event(invalid)
        invalid = dict(self.sample[1])
        invalid["seq"] = 3
        with self.assertRaisesRegex(EventValidationError, "exactly one"):
            validate_event(invalid, self.sample[0])

    def test_complete_run_requires_one_final_terminal(self):
        with self.assertRaisesRegex(EventValidationError, "terminal"):
            validate_run(self.sample[:-1])
        extra_terminal = dict(self.sample[-1])
        extra_terminal["seq"] = 37
        with self.assertRaisesRegex(EventValidationError, "terminal event must be last"):
            validate_run(self.sample + [extra_terminal])


class StoreAndCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FileRunStore(self.root / "runs")

    def tearDown(self):
        self.temp.cleanup()

    def test_store_records_in_order_and_rejects_duplicates(self):
        self.store.create("abc", {"run_id": "abc", "status": "running"})
        first = envelope(
            "run_started",
            {"seller_id": "s", "material_id": "m", "quantity_tonnes": 1, "objective": "maximize_net_value", "top_n": 1},
            run_id="abc",
        )
        self.store.append("abc", first)
        with self.assertRaisesRegex(EventValidationError, "exactly one"):
            self.store.append("abc", first)
        self.assertEqual(self.store.events("abc"), [first])

    def test_replay_rewrites_run_id_and_preserves_event_order(self):
        coordinator = RunCoordinator(self.store, replay_roots=[SAMPLE.parent, self.root])
        metadata = coordinator.create_replay(replay_file=SAMPLE, replay_speed=1000)
        wait_for_terminal(self.store, metadata["run_id"])
        events = self.store.events(metadata["run_id"])
        self.assertEqual([event["seq"] for event in events], list(range(1, 37)))
        self.assertEqual({event["run_id"] for event in events}, {metadata["run_id"]})
        self.assertEqual(events[-1]["type"], "run_completed")

    def test_replay_rejects_paths_outside_allowlist_and_malformed_json(self):
        coordinator = RunCoordinator(self.store, replay_roots=[self.root / "allowed"])
        with self.assertRaisesRegex(ValueError, "outside"):
            coordinator.resolve_replay_file(SAMPLE)
        allowed = self.root / "allowed"
        allowed.mkdir()
        malformed = allowed / "bad.jsonl"
        malformed.write_text("{nope}\n", encoding="utf-8")
        with self.assertRaisesRegex(EventValidationError, "line 1"):
            coordinator.create_replay(replay_file=malformed, replay_speed=1000)

    def test_only_one_run_can_be_active(self):
        release = threading.Event()

        async def runner(*, run_id, top_n, emit):
            emit(envelope(
                "run_started",
                {"seller_id": "s", "material_id": "m", "quantity_tonnes": 1, "objective": "maximize_net_value", "top_n": top_n},
                run_id=run_id,
            ))
            await __import__("asyncio").to_thread(release.wait)

        coordinator = RunCoordinator(self.store, live_runner=runner)
        first = coordinator.create_live(top_n=1)
        with self.assertRaisesRegex(RuntimeError, "already active"):
            coordinator.create_live(top_n=1)
        release.set()
        wait_for_terminal(self.store, first["run_id"])

    def test_idle_stream_emits_sse_heartbeat(self):
        self.store.create("waiting", {"run_id": "waiting", "status": "running"})
        coordinator = RunCoordinator(self.store, heartbeat_seconds=0.01)
        self.assertEqual(next(coordinator.stream("waiting")), ": heartbeat\n\n")

    def test_live_failure_is_terminal_and_redacts_credentials(self):
        def runner(**_kwargs):
            raise RuntimeError("provider rejected secret-value")

        coordinator = RunCoordinator(self.store, live_runner=runner)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret-value"}):
            metadata = coordinator.create_live(top_n=1)
            wait_for_terminal(self.store, metadata["run_id"])
        events = self.store.events(metadata["run_id"])
        self.assertEqual(events[-1]["type"], "run_failed")
        self.assertNotIn("secret-value", events[-1]["payload"]["error"])
        self.assertIn("[redacted]", events[-1]["payload"]["error"])

    def test_interrupted_run_is_closed_during_restart_recovery(self):
        self.store.create("interrupted", {"run_id": "interrupted", "status": "running"})
        RunCoordinator(self.store).recover_interrupted_runs()
        metadata = self.store.get("interrupted")
        events = self.store.events("interrupted")
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(events[-1]["type"], "run_failed")
        self.assertIn("server restart", events[-1]["payload"]["error"])


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FileRunStore(Path(self.temp.name) / "runs")
        coordinator = RunCoordinator(
            self.store,
            replay_roots=[SAMPLE.parent, Path(self.temp.name)],
            heartbeat_seconds=0.01,
        )
        self.client = TestClient(create_app(coordinator=coordinator))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_dashboard_health_and_context_do_not_expose_keys(self):
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "never-show-this",
            "SELLER_LLM_CHAIN": "gemini:flash",
        }):
            self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
            context = self.client.get("/api/context")
        self.assertEqual(context.status_code, 200)
        body = context.text
        self.assertIn("gemini:flash", body)
        self.assertNotIn("never-show-this", body)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Circularity Exchange", page.text)

    def test_replay_stream_and_last_event_id_reconnection(self):
        created = self.client.post("/api/runs", json={
            "mode": "replay", "replay_file": str(SAMPLE), "replay_speed": 1000,
        })
        self.assertEqual(created.status_code, 202, created.text)
        run_id = created.json()["run_id"]
        wait_for_terminal(self.store, run_id)
        response = self.client.get(f"/api/runs/{run_id}/events")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count("data: "), 36)
        self.assertIn("event: recommendation", response.text)
        resumed = self.client.get(
            f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "35"},
        )
        self.assertEqual(resumed.text.count("data: "), 1)
        self.assertIn("event: run_completed", resumed.text)

    def test_api_rejects_invalid_input_and_unknown_runs(self):
        self.assertEqual(self.client.post("/api/runs", json={"mode": "live", "top_n": 9}).status_code, 422)
        self.assertEqual(self.client.post("/api/runs", json={"mode": "replay", "replay_file": "README.md"}).status_code, 422)
        self.assertEqual(self.client.get("/api/runs/no-such-run").status_code, 404)
        self.assertEqual(
            self.client.get("/api/runs/no-such-run/events", headers={"Last-Event-ID": "bad"}).status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
