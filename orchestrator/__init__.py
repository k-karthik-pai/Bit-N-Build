"""Deterministic orchestration for the fixed circular supply-chain scenario.

The Orchestrator is deliberately not a fourth reasoning agent. It sequences
the three agent contracts, validates their responses, selects the best viable
deal, and assembles the recommendation object.

The default adapters are deterministic mocks. They let this track run while
the Circularity, Negotiation, and Logistics tracks are developed independently.
Real implementations can be supplied later through :class:`AgentAdapters`
without changing the pipeline itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

JsonDict = Dict[str, Any]
AgentCallable = Callable[[Mapping[str, Any]], JsonDict]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

FIXED_SCENARIO: JsonDict = {
    "seller_id": "tata_steel_bsl",
    "material_id": "ld_slag",
    "quantity_tonnes": 100000,
    "objective": "maximize_net_value",
}

DEFAULT_DEADLINE_DAYS = 20
DEFAULT_HANDLING_COST_PER_TONNE_USD = 2.0
DEFAULT_PROCESSING_COST_PER_TONNE_USD = 1.5
CO2_AVOIDED_TONNES_PER_TONNE_ESTIMATE = 0.05
CONTRACT_TERM_MONTHS = 12


@dataclass(frozen=True)
class AgentAdapters:
    """Callables implementing the three frozen agent contracts."""

    matching: AgentCallable
    logistics: AgentCallable
    negotiation: AgentCallable


def _load_json(filename: str) -> Any:
    with (DATA_DIR / filename).open(encoding="utf-8") as handle:
        return json.load(handle)


def _find_record(
    records: Sequence[Mapping[str, Any]],
    key: str,
    value: str,
    kind: str,
) -> Mapping[str, Any]:
    for record in records:
        if record.get(key) == value:
            return record
    raise ValueError(f"Unknown {kind} '{value}'")


def _get_route(logistics: Mapping[str, Any], route_id: str) -> Mapping[str, Any]:
    """Return the named route, failing loudly if the response is inconsistent."""
    routes = logistics.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("Logistics response contains no routes")
    for route in routes:
        if isinstance(route, Mapping) and route.get("route_id") == route_id:
            return route
    raise ValueError(f"Recommended route '{route_id}' is missing from logistics response")


def _as_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if converted <= 0 or converted != value:
        raise ValueError(f"{field} must be a positive integer")
    return converted


def _quality_matches(material: Mapping[str, Any], buyer: Mapping[str, Any]) -> bool:
    composition = material.get("composition_pct", {})
    for requirement, minimum in buyer.get("min_quality_requirements", {}).items():
        if requirement.endswith("_min_pct"):
            component = requirement[: -len("_min_pct")]
            if float(composition.get(component, 0)) < float(minimum):
                return False
    return True


# ---------------------------------------------------------------------------
# Deterministic mock adapters
# ---------------------------------------------------------------------------

def mock_matching_agent(payload: Mapping[str, Any]) -> JsonDict:
    """Return a ranked response with the exact Circularity contract shape.

    This is intentionally a small fixture-backed mock, not a replacement for
    the Circularity Agent. It uses the current stub data only to make the
    Orchestrator independently runnable.
    """
    material_id = str(payload["material_id"])
    quantity = _as_positive_int(payload["quantity_tonnes"], "quantity_tonnes")
    materials = _load_json("materials.json")
    buyers = _load_json("buyers.json")
    material = _find_record(materials, "material_id", material_id, "material")

    candidates: List[JsonDict] = []
    for buyer in buyers:
        if buyer.get("material_required_id") != material_id:
            continue
        if not _quality_matches(material, buyer):
            continue
        demand = float(buyer.get("annual_demand_tonnes", 0))
        demand_fit = min(demand / quantity, 1.0)
        score = round(0.5 + 0.5 * demand_fit, 3)
        candidates.append(
            {
                "buyer_id": buyer["buyer_id"],
                "application": "cement_clinker_substitute",
                "compatibility_score": score,
                "notes": "Mock candidate response for Orchestrator integration testing.",
            }
        )

    candidates.sort(key=lambda item: (-item["compatibility_score"], item["buyer_id"]))
    return {"candidates": candidates}


def _mock_paths(origin: str, destination: str) -> List[Tuple[List[str], float, float]]:
    edges = _load_json("routes.json")
    adjacency: Dict[str, List[Mapping[str, Any]]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from_port_id"], []).append(edge)

    paths: List[Tuple[List[str], float, float]] = []

    def visit(current: str, path: List[str], distance: float, cost: float) -> None:
        if current == destination:
            paths.append((list(path), distance, cost))
            return
        for edge in adjacency.get(current, []):
            next_port = edge["to_port_id"]
            if next_port in path:
                continue
            visit(
                next_port,
                path + [next_port],
                distance + float(edge["distance_km"]),
                cost + float(edge["base_cost_per_tonne_usd"]),
            )

    visit(origin, [origin], 0.0, 0.0)
    return paths


def mock_logistics_agent(payload: Mapping[str, Any]) -> JsonDict:
    """Return route options with the exact Logistics contract shape."""
    origin = str(payload["origin_port_id"])
    destination = str(payload["destination_port_id"])
    _as_positive_int(payload["cargo_tonnes"], "cargo_tonnes")
    deadline_days = float(payload["deadline_days"])

    ports = _load_json("ports.json")
    _find_record(ports, "port_id", origin, "origin port")
    _find_record(ports, "port_id", destination, "destination port")
    paths = _mock_paths(origin, destination)
    if not paths:
        raise ValueError(f"No route found from {origin} to {destination}")

    route_options: List[JsonDict] = []
    for index, (path, distance, cost) in enumerate(paths, start=1):
        transit_days = round(distance / 444.5 + max(len(path) - 2, 0) * 0.5, 2)
        route_options.append(
            {
                "route_id": f"mock_route_{index}_{path[0]}_{path[-1]}",
                "path_port_ids": path,
                "distance_km": round(distance, 1),
                "transit_days": transit_days,
                "cost_per_tonne_usd": round(cost, 2),
            }
        )

    route_options.sort(key=lambda route: (route["cost_per_tonne_usd"], route["distance_km"]))
    route_options = route_options[:3]
    recommended = next(
        (route for route in route_options if route["transit_days"] <= deadline_days),
        route_options[0],
    )
    return {
        "routes": route_options,
        "recommended_route_id": recommended["route_id"],
    }


def mock_negotiation_agent(payload: Mapping[str, Any]) -> JsonDict:
    """Return a deterministic response with the exact Negotiation contract shape."""
    constraints = payload["seller_constraints"]
    buyer = payload["buyer"]
    logistics_cost = float(payload["logistics_cost_per_tonne_usd"])
    seller_min = float(constraints["min_acceptable_price_per_tonne_usd"])
    seller_preferred = float(constraints["preferred_price_per_tonne_usd"])
    available = float(constraints["available_quantity_tonnes"])
    buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
    demand = float(buyer["annual_demand_tonnes"])
    quantity = int(min(available, demand)) if available > 0 and demand > 0 else 0
    buyer_id = str(buyer["buyer_id"])

    if seller_min > buyer_max or quantity == 0:
        return {
            "status": "rejected",
            "price_per_tonne_usd": None,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": [
                {
                    "speaker": "seller_agent",
                    "message": f"Seller floor is ${seller_min:.2f}/t; logistics are ${logistics_cost:.2f}/t.",
                },
                {
                    "speaker": "buyer_agent",
                    "message": f"Buyer {buyer_id} cannot meet the seller constraints. Deal rejected.",
                },
            ],
        }

    if seller_preferred <= buyer_max:
        price = round(seller_preferred, 2)
        status = "accepted"
    else:
        price = round((seller_min + buyer_max) / 2.0, 2)
        status = "countered"

    return {
        "status": status,
        "price_per_tonne_usd": price,
        "quantity_tonnes": quantity,
        "contract_term_months": CONTRACT_TERM_MONTHS,
        "transcript": [
            {
                "speaker": "seller_agent",
                "message": f"Seller proposes ${seller_preferred:.2f}/t for {quantity}t.",
            },
            {
                "speaker": "buyer_agent",
                "message": f"Buyer {buyer_id} ceiling is ${buyer_max:.2f}/t.",
            },
            {
                "speaker": "seller_agent",
                "message": f"Validated {status} price: ${price:.2f}/t with logistics ${logistics_cost:.2f}/t.",
            },
        ],
    }


def mock_adapters() -> AgentAdapters:
    """Return the default fixture-backed adapters for this track."""
    return AgentAdapters(
        matching=mock_matching_agent,
        logistics=mock_logistics_agent,
        negotiation=mock_negotiation_agent,
    )


# ---------------------------------------------------------------------------
# Pipeline validation and selection
# ---------------------------------------------------------------------------

def _validate_matching_output(output: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    candidates = output.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("Matching response must contain a candidates list")
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or not candidate.get("buyer_id"):
            raise ValueError("Each matching candidate must contain buyer_id")
    return candidates


def _validate_negotiation_output(output: Mapping[str, Any]) -> None:
    if output.get("status") not in {"accepted", "rejected", "countered"}:
        raise ValueError("Negotiation response has an invalid status")
    transcript = output.get("transcript")
    if not isinstance(transcript, list):
        raise ValueError("Negotiation response must contain a transcript list")


def _resolve_adapters(
    adapters: Optional[AgentAdapters],
    matching_agent: Optional[AgentCallable],
    logistics_agent: Optional[AgentCallable],
    negotiation_agent: Optional[AgentCallable],
) -> AgentAdapters:
    overrides = [matching_agent, logistics_agent, negotiation_agent]
    if adapters is not None and any(adapter is not None for adapter in overrides):
        raise ValueError("Pass either adapters or individual agent callables, not both")
    if adapters is not None:
        return adapters
    if any(adapter is not None for adapter in overrides):
        return AgentAdapters(
            matching=matching_agent or mock_matching_agent,
            logistics=logistics_agent or mock_logistics_agent,
            negotiation=negotiation_agent or mock_negotiation_agent,
        )
    return mock_adapters()


def run_pipeline(
    seller_id: Optional[str] = None,
    material_id: Optional[str] = None,
    quantity_tonnes: Optional[int] = None,
    objective: Optional[str] = None,
    *,
    adapters: Optional[AgentAdapters] = None,
    matching_agent: Optional[AgentCallable] = None,
    logistics_agent: Optional[AgentCallable] = None,
    negotiation_agent: Optional[AgentCallable] = None,
    deadline_days: int = DEFAULT_DEADLINE_DAYS,
    handling_cost_per_tonne_usd: float = DEFAULT_HANDLING_COST_PER_TONNE_USD,
    processing_cost_per_tonne_usd: float = DEFAULT_PROCESSING_COST_PER_TONNE_USD,
) -> JsonDict:
    """Run Circularity → Logistics → Negotiation and select one recommendation."""
    seller_id = FIXED_SCENARIO["seller_id"] if seller_id is None else seller_id
    material_id = FIXED_SCENARIO["material_id"] if material_id is None else material_id
    quantity_tonnes = (
        FIXED_SCENARIO["quantity_tonnes"] if quantity_tonnes is None else quantity_tonnes
    )
    objective = FIXED_SCENARIO["objective"] if objective is None else objective

    if objective != "maximize_net_value":
        raise ValueError(f"Unsupported objective '{objective}'")
    quantity_tonnes = _as_positive_int(quantity_tonnes, "quantity_tonnes")
    deadline_days = _as_positive_int(deadline_days, "deadline_days")
    if handling_cost_per_tonne_usd < 0 or processing_cost_per_tonne_usd < 0:
        raise ValueError("Handling and processing costs cannot be negative")

    seller = _find_record([_load_json("seller.json")], "seller_id", seller_id, "seller")
    materials = _load_json("materials.json")
    material = _find_record(materials, "material_id", material_id, "material")
    buyers = _load_json("buyers.json")
    available = _as_positive_int(seller["available_quantity_tonnes_per_year"], "seller availability")
    if quantity_tonnes > available:
        raise ValueError("Requested quantity exceeds seller availability")

    agent_set = _resolve_adapters(adapters, matching_agent, logistics_agent, negotiation_agent)
    matching_output = agent_set.matching(
        {
            "material_id": material_id,
            "quantity_tonnes": quantity_tonnes,
            "seller_id": seller_id,
        }
    )
    candidates = _validate_matching_output(matching_output)
    if not candidates:
        raise ValueError("Circularity Agent returned no compatible buyers")

    seller_constraints = {
        "min_acceptable_price_per_tonne_usd": seller["min_acceptable_price_per_tonne_usd"],
        "preferred_price_per_tonne_usd": seller["preferred_price_per_tonne_usd"],
        "available_quantity_tonnes": available,
    }
    origin_port_id = seller["nearest_port_id"]
    viable_deals: List[JsonDict] = []

    for candidate in candidates:
        buyer = _find_record(buyers, "buyer_id", str(candidate["buyer_id"]), "buyer")
        destination_port_id = str(buyer["port_id"])
        logistics_output = agent_set.logistics(
            {
                "origin_port_id": origin_port_id,
                "destination_port_id": destination_port_id,
                "cargo_tonnes": quantity_tonnes,
                "deadline_days": deadline_days,
            }
        )
        recommended_route_id = logistics_output.get("recommended_route_id")
        if not recommended_route_id:
            raise ValueError("Logistics response is missing recommended_route_id")
        route = _get_route(logistics_output, str(recommended_route_id))
        logistics_cost = float(route["cost_per_tonne_usd"])

        negotiation_output = agent_set.negotiation(
            {
                "seller_constraints": seller_constraints,
                "buyer": buyer,
                "logistics_cost_per_tonne_usd": logistics_cost,
            }
        )
        _validate_negotiation_output(negotiation_output)
        status = negotiation_output["status"]
        deal_quantity = negotiation_output.get("quantity_tonnes")
        price = negotiation_output.get("price_per_tonne_usd")
        if status not in {"accepted", "countered"} or price is None or deal_quantity in (None, 0):
            continue
        deal_quantity = _as_positive_int(deal_quantity, "negotiated quantity")
        price = float(price)
        seller_min = float(seller_constraints["min_acceptable_price_per_tonne_usd"])
        buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
        if not seller_min <= price <= buyer_max:
            raise ValueError(f"Negotiation returned out-of-bounds price for buyer {buyer['buyer_id']}")
        if deal_quantity > available or deal_quantity > float(buyer["annual_demand_tonnes"]):
            raise ValueError(f"Negotiation returned an invalid quantity for buyer {buyer['buyer_id']}")

        margin = round(
            price - logistics_cost - handling_cost_per_tonne_usd - processing_cost_per_tonne_usd,
            2,
        )
        total_net_value = round(margin * deal_quantity, 2)
        viable_deals.append(
            {
                "buyer": buyer,
                "candidate": candidate,
                "route": route,
                "deal": negotiation_output,
                "margin": margin,
                "total_net_value": total_net_value,
            }
        )

    if not viable_deals:
        raise ValueError("No viable deal found across all compatible buyers")

    def selection_key(item: Mapping[str, Any]) -> Tuple[float, float, float, float, str]:
        route = item["route"]
        candidate = item["candidate"]
        return (
            float(item["total_net_value"]),
            float(item["margin"]),
            float(candidate.get("compatibility_score", 0.0)),
            -float(route["cost_per_tonne_usd"]),
            str(item["buyer"]["buyer_id"]),
        )

    best = max(viable_deals, key=selection_key)
    buyer = best["buyer"]
    route = best["route"]
    deal = best["deal"]
    deal_quantity = int(deal["quantity_tonnes"])

    return {
        "material": material["name"],
        "quantity_tonnes": deal_quantity,
        "buyer_id": buyer["buyer_id"],
        "buyer_name": buyer["name"],
        "price_per_tonne_usd": float(deal["price_per_tonne_usd"]),
        "route": {
            "origin_port": origin_port_id,
            "destination_port": buyer["port_id"],
            "distance_km": route["distance_km"],
            "transit_days": route["transit_days"],
            "cost_per_tonne_usd": route["cost_per_tonne_usd"],
        },
        "margin_per_tonne_usd": best["margin"],
        "total_net_value_usd": best["total_net_value"],
        "co2_avoided_tonnes_estimate": round(
            deal_quantity * CO2_AVOIDED_TONNES_PER_TONNE_ESTIMATE,
            2,
        ),
    }


def run(*args: Any, **kwargs: Any) -> JsonDict:
    """Contract-compatible alias for the Orchestrator pipeline."""
    return run_pipeline(*args, **kwargs)


__all__ = [
    "AgentAdapters",
    "FIXED_SCENARIO",
    "DEFAULT_DEADLINE_DAYS",
    "DEFAULT_HANDLING_COST_PER_TONNE_USD",
    "DEFAULT_PROCESSING_COST_PER_TONNE_USD",
    "CO2_AVOIDED_TONNES_PER_TONNE_ESTIMATE",
    "mock_matching_agent",
    "mock_logistics_agent",
    "mock_negotiation_agent",
    "mock_adapters",
    "run_pipeline",
    "run",
]
