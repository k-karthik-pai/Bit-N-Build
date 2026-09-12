"""
Demo Output — Contract frozen in AGENTS.md section 4 (consumes the
orchestrator's final recommendation object + its additive pipeline_log).

Renders the terminal-style run log + summary card described in
docs/brief-demo.md. Every number printed here comes straight from
orchestrator.run_pipeline()'s output — nothing is hardcoded or re-derived.
"""

from __future__ import annotations

import sys
from typing import Any, Dict


def render(result: Dict[str, Any]) -> str:
    lines = []
    total_attempts = len(result["pipeline_log"])
    for i, attempt in enumerate(result["pipeline_log"], start=1):
        name = attempt["buyer_name"] or attempt["buyer_id"]
        if attempt["status"] == "rejected":
            lines.append(f"[x] ({i}/{total_attempts}) Negotiating with {name}... REJECTED — {attempt['reason']}")
            if i < total_attempts:
                lines.append("    -> autonomously moving to next candidate...")
        else:
            batna = attempt.get("batna_price_per_tonne_usd")
            batna_note = f" (walk-away floor ${batna:.2f}/t)" if batna else ""
            lines.append(
                f"[v] ({i}/{total_attempts}) Negotiating with {name}... "
                f"{attempt['status'].upper()} at ${attempt['price_per_tonne_usd']:.2f}/t{batna_note}"
            )

    lines.append("")
    lines.append(
        f"[v] Deal selected: {result['buyer_name']} — ${result['price_per_tonne_usd']:.2f}/t, "
        f"{result['quantity_tonnes']:,}t"
    )
    route = result["route"]
    lines.append(
        f"[v] Route: {route['origin_port']} -> {route['destination_port']}, "
        f"{route['transit_days']} days, ${route['cost_per_tonne_usd']:.2f}/t"
    )
    lines.append("")
    lines.append("=== Recommended deal ===")
    lines.append(f"  Material:        {result['material']}")
    lines.append(f"  Buyer:           {result['buyer_name']} ({result['buyer_id']})")
    lines.append(f"  Price:           ${result['price_per_tonne_usd']:.2f}/t")
    lines.append(f"  Quantity:        {result['quantity_tonnes']:,}t")
    lines.append(f"  Margin:          ${result['margin_per_tonne_usd']:.2f}/t")
    lines.append(f"  Total net value: ${result['total_net_value_usd']:,.2f}")
    lines.append(f"  CO2 avoided:     ~{result['co2_avoided_tonnes_estimate']:,.2f}t (estimate)")
    return "\n".join(lines)


if __name__ == "__main__":
    from orchestrator import run_pipeline

    print(render(run_pipeline()))
    sys.exit(0)
