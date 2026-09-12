"""
Orchestrator — Contract frozen in AGENTS.md section 4.

Deterministic pipeline: Circularity → Negotiation → Logistics for the fixed scenario.
Not a fourth agent with its own reasoning — a deterministic pipeline function.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# ---------------------------------------------------------------------------
# Fixed scenario (from PLAN.md, DATA.md, AGENTS.md)
# ---------------------------------------------------------------------------

FIXED_SCENARIO = {
    "seller_id": "tata_steel_bsl",
    "material_id": "ld_slag",
    "quantity_tonnes": 100000,
    "objective": "maximize_net_value",
}

ORIGIN_PORT = "dhamra"
DEADLINE_DAYS = 20

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def _load_json(filename: str) -> Any:
    with open(os.path.join(DATA_DIR, filename)) as f:
        return json.load(f)


def _get_route(logistics: Dict[str, Any], route_id: str) -> Dict[str, Any]:
    """Look up a route dict by route_id (falls back to routes[0] if not found)."""
    for route in logistics["routes"]:
        if route["route_id"] == route_id:
            return route
    return logistics["routes"][0]

# ---------------------------------------------------------------------------
# Step 1: Circularity / Matching Agent
# ---------------------------------------------------------------------------

def _match_buyers(
    material_id: str,
    quantity_tonnes: int,
    seller_id: str,
    buyer_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Find and rank compatible buyers via the Circularity Agent (AGENTS.md
    section 1) — it decides ranking (compatibility_score) and which buyers
    even qualify (material match + quality requirements met by the
    material's composition); the orchestrator just attaches each ranked
    candidate's full buyer record (port, price ceiling, demand, etc.) for
    the downstream Logistics/Negotiation steps, which the Circularity Agent's
    own output doesn't carry.

    buyer_overrides (for live judge-driven re-negotiation, e.g. "what if this
    buyer's demand doubled?"): {buyer_id: {field: new_value, ...}}, merged
    onto the loaded record before matching/ranking. Omit for the plain
    fixed-scenario run.
    """
    from agents.circularity import find_candidates

    materials = _load_json("materials.json")
    buyers = _load_json("buyers.json")
    if buyer_overrides:
        buyers = [
            {**b, **buyer_overrides[b["buyer_id"]]} if b["buyer_id"] in buyer_overrides else b
            for b in buyers
        ]
    buyers_by_id = {b["buyer_id"]: b for b in buyers}

    match_result = find_candidates(
        {"material_id": material_id, "quantity_tonnes": quantity_tonnes, "seller_id": seller_id},
        materials,
        buyers,
    )

    ranked_buyers = []
    for candidate in match_result["candidates"]:
        buyer = buyers_by_id.get(candidate["buyer_id"])
        if buyer is None:
            continue  # candidate ids always come from the buyers list passed in
        ranked_buyers.append({
            **buyer,
            "_circularity_application": candidate["application"],
            "_circularity_compatibility_score": candidate["compatibility_score"],
            "_circularity_notes": candidate["notes"],
        })
    return ranked_buyers

# ---------------------------------------------------------------------------
# Step 2: Negotiation Agent
# ---------------------------------------------------------------------------

HANDLING_COST_PER_TONNE_USD = 2.0
PROCESSING_COST_PER_TONNE_USD = 1.5


def _negotiate_with_buyer(
    buyer: Dict[str, Any],
    seller_constraints: Dict[str, Any],
    logistics_cost_per_tonne_usd: float,
    batna_price_per_tonne_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run negotiation for a single buyer using the Negotiation Agent.

    batna_price_per_tonne_usd: this buyer's walk-away floor, derived from the
    best net value achievable with a different buyer (see run_pipeline).
    """
    from agents.negotiation import negotiate

    return negotiate(
        seller_constraints=seller_constraints,
        buyer=buyer,
        logistics_cost_per_tonne_usd=logistics_cost_per_tonne_usd,
        handling_cost_per_tonne_usd=HANDLING_COST_PER_TONNE_USD,
        processing_cost_per_tonne_usd=PROCESSING_COST_PER_TONNE_USD,
        use_llm=False,
        batna_price_per_tonne_usd=batna_price_per_tonne_usd,
    )


def _net_value(deal: Dict[str, Any], logistics_cost: float) -> Optional[float]:
    """price - logistics - handling - processing, or None if no viable price."""
    if deal["status"] not in ("accepted", "countered") or not deal.get("price_per_tonne_usd"):
        return None
    return deal["price_per_tonne_usd"] - logistics_cost - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD

# ---------------------------------------------------------------------------
# Step 3: Logistics Optimizer
# ---------------------------------------------------------------------------

def _get_logistics(origin: str, destination: str, cargo: int, deadline: int) -> Dict[str, Any]:
    """
    Get route optimization from the Logistics Optimizer.
    """
    from agents.logistics import optimize_routes

    return optimize_routes(
        origin_port_id=origin,
        destination_port_id=destination,
        cargo_tonnes=cargo,
        deadline_days=deadline,
    )

# ---------------------------------------------------------------------------
# Main orchestrator pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    seller_id: Optional[str] = None,
    material_id: Optional[str] = None,
    quantity_tonnes: Optional[int] = None,
    objective: Optional[str] = None,
    seller_overrides: Optional[Dict[str, Any]] = None,
    buyer_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    use_batna: bool = True,
) -> Dict[str, Any]:
    """
    Deterministic pipeline: Circularity → Negotiation → Logistics → Final output.

    Input (AGENTS.md section 4):
      {seller_id, material_id, quantity_tonnes, objective}

    seller_overrides / buyer_overrides (additive, not in the frozen AGENTS.md
    shape): live "what if" knobs for a demo — e.g.
    seller_overrides={"min_acceptable_price_per_tonne_usd": 27} or
    buyer_overrides={"stub_buyer_3": {"annual_demand_tonnes": 120000}}.
    Merged onto the loaded seller.json / buyers.json records before the
    pipeline runs, so a judge's live scenario tweak produces a genuinely
    different outcome rather than a scripted one. Omit both for the plain
    fixed-scenario run.

    use_batna: when True (default), each buyer negotiation is given a
    walk-away floor derived from the best net value achievable with any
    OTHER compatible buyer (see _compute_batna_prices below) — a real BATNA,
    not just the raw seller floor. Set False to negotiate every buyer in
    isolation (pre-BATNA behavior).

    Output (AGENTS.md section 4):
      Final recommendation object with material, buyer, price, route, margin,
      etc. Also includes an additive "pipeline_log" field: every buyer
      attempted, in order, with status/price/reason — so an autonomous
      recovery from a rejection (moving on to the next candidate) is visible
      rather than silently discarded.
    """
    # Resolve defaults from fixed scenario
    seller_id = seller_id or FIXED_SCENARIO["seller_id"]
    material_id = material_id or FIXED_SCENARIO["material_id"]
    quantity_tonnes = quantity_tonnes or FIXED_SCENARIO["quantity_tonnes"]
    objective = objective or FIXED_SCENARIO["objective"]

    seller = {**_load_json("seller.json"), **(seller_overrides or {})}
    seller_constraints = {
        "min_acceptable_price_per_tonne_usd": seller["min_acceptable_price_per_tonne_usd"],
        "preferred_price_per_tonne_usd": seller["preferred_price_per_tonne_usd"],
        "available_quantity_tonnes": seller["available_quantity_tonnes_per_year"],
    }

    # ---- Step 1: Find compatible buyers ----
    compatible_buyers = _match_buyers(material_id, quantity_tonnes, seller_id, buyer_overrides)
    if not compatible_buyers:
        raise ValueError(f"No compatible buyers found for material_id={material_id}")

    # ---- Step 2: Get logistics cost for each buyer's port ----
    # Pre-compute logistics for each unique destination port
    logistics_cache: Dict[str, Dict[str, Any]] = {}
    for buyer in compatible_buyers:
        port_id = buyer["port_id"]
        if port_id not in logistics_cache:
            logistics_cache[port_id] = _get_logistics(
                ORIGIN_PORT, port_id, quantity_tonnes, DEADLINE_DAYS
            )

    def _logistics_cost_for(buyer: Dict[str, Any]) -> float:
        logistics = logistics_cache[buyer["port_id"]]
        return _get_route(logistics, logistics["recommended_route_id"])["cost_per_tonne_usd"]

    # ---- Step 3a: baseline negotiation per buyer (no BATNA yet) ----
    # Needed to know what "the best alternative elsewhere" actually is before
    # each buyer's real negotiation runs.
    baseline_net_value: Dict[str, Optional[float]] = {}
    if use_batna:
        for buyer in compatible_buyers:
            logistics_cost = _logistics_cost_for(buyer)
            baseline_deal = _negotiate_with_buyer(
                buyer=buyer,
                seller_constraints=seller_constraints,
                logistics_cost_per_tonne_usd=logistics_cost,
            )
            baseline_net_value[buyer["buyer_id"]] = _net_value(baseline_deal, logistics_cost)

    # ---- Step 3b: negotiate for real, each buyer holding out for its BATNA ----
    best_deal: Optional[Dict[str, Any]] = None
    best_net_value = -float("inf")
    pipeline_log: List[Dict[str, Any]] = []

    for buyer in compatible_buyers:
        logistics = logistics_cache[buyer["port_id"]]
        recommended_route_id = logistics["recommended_route_id"]
        logistics_cost = _get_route(logistics, recommended_route_id)["cost_per_tonne_usd"]

        batna_price: Optional[float] = None
        if use_batna:
            alternatives = [
                v for bid, v in baseline_net_value.items() if bid != buyer["buyer_id"] and v is not None
            ]
            if alternatives:
                best_alternative_net_value = max(alternatives)
                batna_price = round(
                    best_alternative_net_value
                    + logistics_cost
                    + HANDLING_COST_PER_TONNE_USD
                    + PROCESSING_COST_PER_TONNE_USD,
                    2,
                )

        deal = _negotiate_with_buyer(
            buyer=buyer,
            seller_constraints=seller_constraints,
            logistics_cost_per_tonne_usd=logistics_cost,
            batna_price_per_tonne_usd=batna_price,
        )

        net_value = _net_value(deal, logistics_cost)
        pipeline_log.append({
            "buyer_id": buyer["buyer_id"],
            "buyer_name": buyer.get("name"),
            "circularity_application": buyer.get("_circularity_application"),
            "circularity_compatibility_score": buyer.get("_circularity_compatibility_score"),
            "status": deal["status"],
            "price_per_tonne_usd": deal.get("price_per_tonne_usd"),
            "batna_price_per_tonne_usd": batna_price,
            "reason": deal.get("validator_reason"),
        })

        if net_value is not None and net_value > best_net_value:
            best_net_value = net_value
            best_deal = {
                "buyer": buyer,
                "deal": deal,
                "logistics": logistics,
                "recommended_route_id": recommended_route_id,
                "net_value_per_tonne": round(net_value, 2),
            }

    if best_deal is None:
        raise ValueError("No viable deal found across all compatible buyers")

    # ---- Step 4: Assemble final recommendation ----
    buyer = best_deal["buyer"]
    deal = best_deal["deal"]
    logistics = best_deal["logistics"]
    route = _get_route(logistics, logistics["recommended_route_id"])

    price = deal["price_per_tonne_usd"]
    logistics_cost = route["cost_per_tonne_usd"]
    margin = round(price - logistics_cost - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD, 2)
    total_net_value = round(margin * buyer["annual_demand_tonnes"], 2)

    result = {
        "material": material_id,
        "quantity_tonnes": deal["quantity_tonnes"],
        "buyer_id": buyer["buyer_id"],
        "buyer_name": buyer["name"],
        "price_per_tonne_usd": price,
        "route": {
            "origin_port": ORIGIN_PORT,
            "destination_port": buyer["port_id"],
            "distance_km": route["distance_km"],
            "transit_days": route["transit_days"],
            "cost_per_tonne_usd": route["cost_per_tonne_usd"],
        },
        "margin_per_tonne_usd": margin,
        "total_net_value_usd": total_net_value,
        # 0.85 t CO2 avoided per t of clinker replaced by LD slag: clinker
        # calcination emits ~0.8-0.9 t CO2/t (industry-standard figure); using
        # LD slag as a substitute skips that step. See data/RESEARCH_FINDINGS.md §8.
        "co2_avoided_tonnes_estimate": round(deal["quantity_tonnes"] * 0.85, 2),
        "pipeline_log": pipeline_log,
    }

    return result


def run(*args, **kwargs) -> Dict[str, Any]:
    """Contract-compatible alias for the orchestrator pipeline."""
    return run_pipeline(*args, **kwargs)


__all__ = [
    "run_pipeline",
    "run",
    "FIXED_SCENARIO",
    "ORIGIN_PORT",
    "DEADLINE_DAYS",
    "_match_buyers",
    "_negotiate_with_buyer",
    "_get_logistics",
]
