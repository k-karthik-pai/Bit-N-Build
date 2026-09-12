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
# Step 1: Circularity / Matching Agent (simplified — stub with real data)
# ---------------------------------------------------------------------------

def _match_buyers(material_id: str, quantity_tonnes: int) -> List[Dict[str, Any]]:
    """
    Find compatible buyers for the given material and quantity.
    In a full implementation, this would use the Circularity Agent.
    For now, load from buyers.json and filter by material and capacity.
    """
    buyers = _load_json("buyers.json")
    materials = _load_json("materials.json")
    material_ids = [m["material_id"] for m in materials]
    target_material = material_id or "ld_slag"

    compatible = [b for b in buyers if b["material_required_id"] == target_material]
    # Sort by compatibility (higher demand = better match)
    compatible.sort(key=lambda b: b["annual_demand_tonnes"], reverse=True)
    return compatible

# ---------------------------------------------------------------------------
# Step 2: Negotiation Agent
# ---------------------------------------------------------------------------

def _negotiate_with_buyer(
    buyer: Dict[str, Any],
    seller_constraints: Dict[str, Any],
    logistics_cost_per_tonne_usd: float,
) -> Dict[str, Any]:
    """
    Run negotiation for a single buyer using the Negotiation Agent.
    """
    from agents.negotiation import negotiate

    return negotiate(
        seller_constraints=seller_constraints,
        buyer=buyer,
        logistics_cost_per_tonne_usd=logistics_cost_per_tonne_usd,
        handling_cost_per_tonne_usd=2.0,
        processing_cost_per_tonne_usd=1.5,
        use_llm=False,
    )

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
) -> Dict[str, Any]:
    """
    Deterministic pipeline: Circularity → Negotiation → Logistics → Final output.

    Input (AGENTS.md section 4):
      {seller_id, material_id, quantity_tonnes, objective}

    Output (AGENTS.md section 4):
      Final recommendation object with material, buyer, price, route, margin, etc.
    """
    # Resolve defaults from fixed scenario
    seller_id = seller_id or FIXED_SCENARIO["seller_id"]
    material_id = material_id or FIXED_SCENARIO["material_id"]
    quantity_tonnes = quantity_tonnes or FIXED_SCENARIO["quantity_tonnes"]
    objective = objective or FIXED_SCENARIO["objective"]

    seller = _load_json("seller.json")
    seller_constraints = {
        "min_acceptable_price_per_tonne_usd": seller["min_acceptable_price_per_tonne_usd"],
        "preferred_price_per_tonne_usd": seller["preferred_price_per_tonne_usd"],
        "available_quantity_tonnes": seller["available_quantity_tonnes_per_year"],
    }

    # ---- Step 1: Find compatible buyers ----
    compatible_buyers = _match_buyers(material_id, quantity_tonnes)

    # ---- Step 2: Get logistics cost for each buyer's port ----
    # Pre-compute logistics for each unique destination port
    logistics_cache: Dict[str, Dict[str, Any]] = {}
    for buyer in compatible_buyers:
        port_id = buyer["port_id"]
        if port_id not in logistics_cache:
            logistics_cache[port_id] = _get_logistics(
                ORIGIN_PORT, port_id, quantity_tonnes, DEADLINE_DAYS
            )

    # ---- Step 3: Negotiate with each buyer ----
    best_deal: Optional[Dict[str, Any]] = None
    best_net_value = -float("inf")

    for buyer in compatible_buyers:
        port_id = buyer["port_id"]
        logistics = logistics_cache[port_id]
        recommended_route_id = logistics["recommended_route_id"]
        logistics_cost = _get_route(logistics, recommended_route_id)["cost_per_tonne_usd"]

        deal = _negotiate_with_buyer(
            buyer=buyer,
            seller_constraints=seller_constraints,
            logistics_cost_per_tonne_usd=logistics_cost,
        )

        if deal["status"] in ("accepted", "countered") and deal.get("price_per_tonne_usd"):
            price = deal["price_per_tonne_usd"]
            net_value = price - logistics_cost - 2.0 - 1.5  # price - logistics - handling - processing
            if net_value > best_net_value:
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
    margin = round(price - logistics_cost - 2.0 - 1.5, 2)
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
        "co2_avoided_tonnes_estimate": round(deal["quantity_tonnes"] * 0.05, 2),
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
