"""Validation helpers for the frozen event contract in AGENTS.md §5."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional


EVENT_TYPES = {
    "run_started",
    "match",
    "route",
    "thread_started",
    "offer",
    "accept",
    "reject",
    "info_request",
    "info_response",
    "fallback",
    "thread_result",
    "released",
    "deal_closed",
    "recommendation",
    "run_completed",
    "run_failed",
}
MOVE_TYPES = {"offer", "accept", "reject", "info_request"}
TERMINAL_TYPES = {"run_completed", "run_failed"}
ENVELOPE_FIELDS = {
    "seq",
    "ts",
    "run_id",
    "deal_id",
    "type",
    "from_agent",
    "to_agent",
    "model",
    "payload",
    "validator",
    "delivered",
}
PAYLOAD_FIELDS = {
    "run_started": {"seller_id", "material_id", "quantity_tonnes", "objective", "top_n"},
    "match": {"matched_count", "selected"},
    "route": {
        "origin_port", "destination_port", "route_id", "distance_km",
        "transit_days", "cost_per_tonne_usd",
    },
    "thread_started": {"buyer_id", "buyer_name", "seller_model", "buyer_model"},
    "offer": {
        "offer_id", "round", "price_per_tonne_usd", "quantity_tonnes",
        "contract_months", "message", "rationale",
    },
    "accept": {"offer_id", "round", "message", "rationale"},
    "reject": {"round", "reason", "message", "rationale"},
    "info_request": {"round", "topic", "rationale"},
    "info_response": {"round", "topic", "data"},
    "fallback": {"round", "agent", "cause", "detail"},
    "thread_result": {
        "status", "price_per_tonne_usd", "quantity_tonnes", "contract_months",
        "rounds", "final_offer_id", "total_net_value_usd", "agreed_pending",
    },
    "released": {"reason"},
    "deal_closed": {"buyer_id", "offer_id", "total_net_value_usd"},
    "recommendation": {
        "material", "quantity_tonnes", "buyer_id", "buyer_name",
        "price_per_tonne_usd", "route", "margin_per_tonne_usd",
        "total_net_value_usd", "co2_avoided_tonnes_estimate",
    },
    "run_completed": {"duration_ms", "llm_calls", "validator_bounces", "fallbacks"},
    "run_failed": {"error"},
}


class EventValidationError(ValueError):
    """Raised when an event violates the frozen transport contract."""


def _agent_name(value: Any, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    return value in {
        "seller_agent", "logistics_agent", "circularity_agent", "orchestrator",
        "validator", "runtime",
    } or (isinstance(value, str) and value.startswith("buyer_agent:") and len(value) > 12)


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or "." not in value:
        raise EventValidationError("ts must be an ISO-8601 UTC timestamp with milliseconds")
    fraction = value.rsplit(".", 1)[1][:-1]
    if len(fraction) != 3 or not fraction.isdigit():
        raise EventValidationError("ts must contain exactly three millisecond digits")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EventValidationError("ts is not a valid ISO-8601 timestamp") from exc


def validate_event(
    event: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Validate one event and, optionally, its ordering after ``previous``."""
    if not isinstance(event, Mapping):
        raise EventValidationError("event must be a JSON object")
    missing = ENVELOPE_FIELDS - set(event)
    if missing:
        raise EventValidationError(f"event is missing envelope fields: {sorted(missing)}")

    seq = event["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise EventValidationError("seq must be a positive integer")
    timestamp = parse_timestamp(event["ts"])
    run_id = event["run_id"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise EventValidationError("run_id must be a non-empty string")
    if event["deal_id"] is not None and (
        not isinstance(event["deal_id"], str) or not event["deal_id"].strip()
    ):
        raise EventValidationError("deal_id must be null or a non-empty string")
    event_type = event["type"]
    if event_type not in EVENT_TYPES:
        raise EventValidationError(f"unsupported event type: {event_type!r}")
    if not _agent_name(event["from_agent"]):
        raise EventValidationError("from_agent is not a supported agent identifier")
    if not _agent_name(event["to_agent"], nullable=True):
        raise EventValidationError("to_agent is not a supported agent identifier")
    model = event["model"]
    if model is not None and (
        not isinstance(model, str) or ":" not in model or model.startswith(":") or model.endswith(":")
    ):
        raise EventValidationError("model must be null or provider:model")

    payload = event["payload"]
    if not isinstance(payload, Mapping):
        raise EventValidationError("payload must be an object")
    payload_missing = PAYLOAD_FIELDS[event_type] - set(payload)
    if payload_missing:
        raise EventValidationError(
            f"{event_type} payload is missing fields: {sorted(payload_missing)}"
        )

    validator = event["validator"]
    delivered = event["delivered"]
    if event_type in MOVE_TYPES:
        if not isinstance(validator, Mapping):
            raise EventValidationError(f"{event_type} requires a validator object")
        if set(("ok", "gate", "reason")) - set(validator):
            raise EventValidationError("validator requires ok, gate, and reason")
        if not isinstance(validator["ok"], bool):
            raise EventValidationError("validator.ok must be boolean")
        expected_gate = "deal_legal" if event_type == "accept" else "move_legal"
        if validator["gate"] != expected_gate:
            raise EventValidationError(f"{event_type} validator gate must be {expected_gate}")
        if not isinstance(validator["reason"], str):
            raise EventValidationError("validator.reason must be a string")
        if not isinstance(delivered, bool):
            raise EventValidationError(f"{event_type} delivered must be boolean")
        if validator["ok"] != delivered:
            raise EventValidationError("validator.ok and delivered must agree")
    elif validator is not None or delivered is not None:
        raise EventValidationError(f"{event_type} must have null validator and delivered")

    if event_type == "info_request" and payload["topic"] not in {
        "route_cost", "best_competing_offer",
    }:
        raise EventValidationError("info_request topic is unsupported")
    if event_type == "fallback" and payload["cause"] not in {
        "timeout", "provider_error", "invalid_moves",
    }:
        raise EventValidationError("fallback cause is unsupported")
    if event_type == "thread_result" and payload["status"] not in {
        "accepted", "countered", "rejected",
    }:
        raise EventValidationError("thread_result status is unsupported")

    if previous is not None:
        if run_id != previous.get("run_id"):
            raise EventValidationError("run_id changed within a run")
        if seq != previous.get("seq", 0) + 1:
            raise EventValidationError("seq must increase by exactly one")
        if timestamp < parse_timestamp(previous.get("ts")):
            raise EventValidationError("timestamps must be non-decreasing")
        if previous.get("type") in TERMINAL_TYPES:
            raise EventValidationError("terminal event must be last")

    return dict(event)


def validate_run(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate a complete run, including its unique final terminal event."""
    if not events:
        raise EventValidationError("run recording is empty")
    validated: list[dict[str, Any]] = []
    for raw in events:
        validated.append(validate_event(raw, validated[-1] if validated else None))
    if validated[0]["type"] != "run_started" or validated[0]["seq"] != 1:
        raise EventValidationError("run must begin with run_started at seq 1")
    terminals = [event for event in validated if event["type"] in TERMINAL_TYPES]
    if len(terminals) != 1 or validated[-1]["type"] not in TERMINAL_TYPES:
        raise EventValidationError("run must end with exactly one terminal event")
    return validated
