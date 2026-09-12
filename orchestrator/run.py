"""Thin command-line runner for the Orchestrator track."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from orchestrator import FIXED_SCENARIO, run_pipeline


def _scenario_from_file(path: str) -> Mapping[str, Any]:
    with Path(path).open(encoding="utf-8-sig") as handle:
        scenario = json.load(handle)
    if not isinstance(scenario, Mapping):
        raise ValueError("Scenario file must contain a JSON object")
    return scenario


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic Orchestrator pipeline")
    parser.add_argument(
        "--scenario",
        help="Optional JSON file containing seller_id, material_id, quantity_tonnes, and objective",
    )
    parser.add_argument("--live", action="store_true", help="Run live LLM negotiations (concurrent, validator-gated)")
    parser.add_argument("--top-n", type=int, default=3, help="Buyers to negotiate with concurrently (3-5)")
    parser.add_argument("--record", action="store_true", default=True, help="Write events to runs/<run_id>.jsonl (live mode)")
    args, _ = parser.parse_known_args(argv)
    try:
        scenario = _scenario_from_file(args.scenario) if args.scenario else FIXED_SCENARIO
        unknown = set(scenario) - set(FIXED_SCENARIO)
        if unknown:
            raise ValueError(f"Unknown scenario fields: {sorted(unknown)}")
        if args.live:
            top_n = max(3, min(5, args.top_n))
            # stream events to stderr as readable lines
            from demo.render_log import format_event
            def emit(ev):
                line = format_event(ev)
                if line:
                    print(line, file=sys.stderr, flush=True)
            recommendation = run_pipeline(use_llm=True, top_n=top_n, emit_event=emit, **scenario)
        else:
            recommendation = run_pipeline(**scenario)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Pipeline could not produce a recommendation: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(recommendation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
