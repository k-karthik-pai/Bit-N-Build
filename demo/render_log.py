"""
Demo Output — Contract frozen in AGENTS.md section 4 (consumes the
orchestrator's final recommendation object + its additive pipeline_log).

Renders the terminal-style run log + summary card described in
README.md. Every number printed here comes straight from
orchestrator.run_pipeline()'s output — nothing is hardcoded or re-derived.
"""

from __future__ import annotations

import math
import sys
from decimal import Decimal
from typing import Any, Dict


def _number(value: Any, *, money: bool = False) -> str:
    """Add separators/units without rounding away any returned precision."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Display values must be finite numbers")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Display values must be finite numbers")
    text = format(Decimal(str(value)), ",f")
    if money:
        whole, _, fraction = text.partition(".")
        return f"${whole}.{fraction.ljust(2, '0')}"
    return text


def render(result: Dict[str, Any], *, include_log: bool = True) -> str:
    """Render section 4 alone, or include the optional completed pipeline log."""
    attempts = result.get("pipeline_log", [])
    lines = ["=== Completed run log ==="] if attempts else []
    selected_status = None
    for attempt in attempts:
        name = attempt.get("buyer_name") or attempt["buyer_id"]
        status = attempt["status"]
        if status not in ("accepted", "countered", "rejected"):
            raise ValueError(f"Unknown negotiation status: {status}")
        if attempt["buyer_id"] == result["buyer_id"]:
            selected_status = status
        application = attempt.get("circularity_application")
        score = attempt.get("circularity_compatibility_score")
        match_note = (
            f" [{application}, match {score:.0%}]" if application and score is not None else ""
        )
        if attempt["status"] == "rejected":
            reason = attempt.get("reason")
            reason_note = f" - {reason}" if reason else ""
            lines.append(f"[x] Negotiation with {name}{match_note}: REJECTED{reason_note}")
        else:
            batna = attempt.get("batna_price_per_tonne_usd")
            batna_note = (
                f" (walk-away floor {_number(batna, money=True)}/t)"
                if batna is not None else ""
            )
            marker = "v" if status == "accepted" else "?"
            pending = " (pending confirmation)" if status == "countered" else ""
            lines.append(
                f"[{marker}] Negotiation with {name}{match_note}: {status.upper()}{pending} at "
                f"{_number(attempt['price_per_tonne_usd'], money=True)}/t{batna_note}"
            )

    if not include_log:
        lines = []
    if lines:
        lines.append("")
    route = result["route"]
    lines.append("=== Recommendation ===")
    if selected_status:
        status_text = selected_status.upper()
        if selected_status == "countered":
            status_text += " (pending confirmation)"
        lines.append(f"  Negotiation status:     {status_text}")
    lines.append(f"  Material:               {result['material']}")
    lines.append(f"  Buyer:                  {result['buyer_name']} ({result['buyer_id']})")
    lines.append(f"  Quantity:               {_number(result['quantity_tonnes'])} t")
    lines.append(f"  Price:                  {_number(result['price_per_tonne_usd'], money=True)}/t")
    lines.append(f"  Route:                  {route['origin_port']} -> {route['destination_port']}")
    lines.append(f"  Distance:               {_number(route['distance_km'])} km")
    lines.append(f"  Transit time:           {_number(route['transit_days'])} days")
    lines.append(f"  Freight:                {_number(route['cost_per_tonne_usd'], money=True)}/t")
    lines.append(f"  Margin:                 {_number(result['margin_per_tonne_usd'], money=True)}/t")
    lines.append(f"  Total net value:        {_number(result['total_net_value_usd'], money=True)}")
    lines.append(f"  CO2 avoided (estimate): {_number(result['co2_avoided_tonnes_estimate'])} t")
    lines.append("  Currency: USD; t = metric tonnes")
    lines.append("  Scenario assumptions: buyer terms/freight are illustrative; CO2 uses a clinker-displacement assumption, not measured savings.")
    return "\n".join(lines)


def show_progress(event: Dict[str, Any]) -> None:
    """Print and flush actual pipeline events, without simulated delays."""
    stage = event["stage"]
    if stage == "matching_started":
        line = "[...] Finding candidate buyers..."
    elif stage == "matching_completed":
        line = f"[v] Material-matched candidates: {_number(event['count'])}"
    elif stage == "route_completed":
        line = (f"[v] Route: {event['origin']} -> {event['destination']}, "
                f"{_number(event['distance_km'])} km, {_number(event['transit_days'])} days, "
                f"{_number(event['cost_per_tonne_usd'], money=True)}/t")
    elif stage == "baseline_started":
        line = "[...] Evaluating baseline deals for walk-away alternatives..."
    elif stage == "negotiation_started":
        line = f"[...] Negotiating with {event['buyer_name']}..."
    elif stage == "negotiation_completed":
        status = event["status"]
        if status == "rejected":
            line = f"[x] {event['buyer_name']}: REJECTED - {event.get('reason') or 'No viable deal'}"
        else:
            pending = " (pending confirmation)" if status == "countered" else ""
            line = (f"[{'?' if status == 'countered' else 'v'}] {event['buyer_name']}: "
                    f"{status.upper()}{pending}, {_number(event['price'], money=True)}/t, "
                    f"{_number(event['quantity'])} t, {_number(event['term'])}-month term")
    else:
        return
    print(line, flush=True)


def format_event(event: Dict[str, Any]) -> str:
    """One-line readable form for an AGENTS.md §5 event — also used by Step 2 SSE."""
    t = event.get("type")
    payload = event.get("payload", {})
    validator = event.get("validator")
    model = event.get("model") or "deterministic"
    if t == "offer":
        badge = "✓" if validator and validator.get("ok") else "✗"
        price = payload.get("price_per_tonne_usd")
        qty = payload.get("quantity_tonnes")
        months = payload.get("contract_months")
        oid = payload.get("offer_id") or "bounced"
        msg = payload.get("message", "")[:80]
        return f"[{badge}] {event['from_agent']} → {event['to_agent']} offer {oid} ${price}/t {qty}t {months}m \"{msg}\" [{model}]"
    if t == "accept":
        badge = "✓" if validator and validator.get("ok") else "✗"
        return f"[{badge}] {event['from_agent']} → {event['to_agent']} accept {payload.get('offer_id')} \"{payload.get('message','')[:60]}\" [{model}]"
    if t == "reject":
        return f"[x] {event['from_agent']} → {event['to_agent']} reject \"{payload.get('message','')[:60]}\" reason:{payload.get('reason','')}"
    if t == "info_request":
        return f"[...] {event['from_agent']} → {event['to_agent']} info_request {payload.get('topic')}"
    if t == "info_response":
        return f"[...] {event['from_agent']} → {event['to_agent']} info_response {payload.get('topic')}: {payload.get('data')}"
    if t == "fallback":
        return f"[!] fallback {payload.get('agent')} cause={payload.get('cause')} {payload.get('detail','')[:60]}"
    if t == "thread_started":
        return f"[...] thread {payload.get('buyer_id')} seller:{payload.get('seller_model')} buyer:{payload.get('buyer_model')}"
    if t in ("run_started","match","route","thread_result","released","deal_closed","recommendation","run_completed","run_failed"):
        return f"[{t}] {payload}"
    return f"[{t}] {event}"


def main(argv=None) -> int:
    import argparse
    from pathlib import Path

    from dotenv import load_dotenv
    from orchestrator import run_pipeline

    parser = argparse.ArgumentParser(description="Render demo recommendation")
    parser.add_argument("--live", action="store_true", help="Run live LLM negotiations with streaming events (requires API keys)")
    parser.add_argument("--top-n", type=int, choices=range(3, 6), default=3, help="Number of concurrent buyers (3-5)")
    parser.add_argument(
        "--record",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write live events to runs/<run_id>.jsonl (default: enabled)",
    )
    parser.add_argument("--env-file", help="Optional dotenv file; existing environment variables take precedence")
    if argv is None:
        args, _ = parser.parse_known_args()
    else:
        args, _ = parser.parse_known_args(argv)

    try:
        if args.env_file:
            env_path = Path(args.env_file)
            if not env_path.is_file():
                raise OSError(f"Environment file not found: {env_path}")
            load_dotenv(env_path, override=False)
        if args.live:
            events = []
            def emit(ev):
                events.append(ev)
                line = format_event(ev)
                if line:
                    print(line, flush=True)
            # also show legacy progress for compatibility
            result = run_pipeline(
                use_llm=True,
                top_n=args.top_n,
                emit_event=emit,
                on_progress=show_progress,
                record=args.record,
            )
            print("\n" + render(result, include_log=False))
            print(f"\nRecorded {len(events)} events to runs/<run_id>.jsonl", file=sys.stderr)
        else:
            output = render(run_pipeline(on_progress=show_progress), include_log=False)
            print(output)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Demo could not produce a recommendation: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
