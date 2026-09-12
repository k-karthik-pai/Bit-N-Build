"""Thin command-line runner for the Orchestrator track."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from orchestrator import FIXED_SCENARIO, run_pipeline


def _scenario_from_file(path: str) -> Mapping[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        scenario = json.load(handle)
    if not isinstance(scenario, Mapping):
        raise ValueError("Scenario file must contain a JSON object")
    return scenario


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic Orchestrator pipeline")
    parser.add_argument(
        "--scenario",
        help="Optional JSON file containing seller_id, material_id, quantity_tonnes, and objective",
    )
    args = parser.parse_args()
    scenario = _scenario_from_file(args.scenario) if args.scenario else FIXED_SCENARIO
    recommendation = run_pipeline(
        seller_id=scenario.get("seller_id"),
        material_id=scenario.get("material_id"),
        quantity_tonnes=scenario.get("quantity_tonnes"),
        objective=scenario.get("objective"),
    )
    print(json.dumps(recommendation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
