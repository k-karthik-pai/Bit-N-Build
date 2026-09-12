"""
Logistics Optimizer — Contract frozen in AGENTS.md section 3.

Responsibility: given origin/destination ports and cargo details,
return route options with distance, transit time, and cost.

Core architecture:
- Ports are nodes, routes are edges with distance/cost.
- Dijkstra's algorithm finds shortest/cheapest paths.
- Returns top 2-3 candidate routes plus a recommended one.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CARGO_SHIP_SPEED_KM_PER_DAY = 444.5  # ~10 knots in km/day
PORT_PROCESSING_DAYS = 0.5  # loading/unloading per port stop

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class RouteEdge:
    from_port: str
    to_port: str
    distance_km: float
    base_cost_per_tonne_usd: float

@dataclass
class RouteResult:
    route_id: str
    path_port_ids: List[str]
    distance_km: float
    transit_days: float
    cost_per_tonne_usd: float

# ---------------------------------------------------------------------------
# Haversine distance calculator (fallback / validation)
# ---------------------------------------------------------------------------

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def _load_ports() -> Dict[str, Dict[str, Any]]:
    with open(os.path.join(DATA_DIR, "ports.json")) as f:
        ports_list = json.load(f)
    return {p["port_id"]: p for p in ports_list}

def _load_edges() -> List[RouteEdge]:
    with open(os.path.join(DATA_DIR, "routes.json")) as f:
        routes_list = json.load(f)
    return [
        RouteEdge(
            from_port=r["from_port_id"],
            to_port=r["to_port_id"],
            distance_km=r["distance_km"],
            base_cost_per_tonne_usd=r["base_cost_per_tonne_usd"],
        )
        for r in routes_list
    ]

def build_graph() -> Dict[str, List[RouteEdge]]:
    """Build adjacency list from ports.json + routes.json."""
    edges = _load_edges()
    ports = _load_ports()
    graph: Dict[str, List[RouteEdge]] = {pid: [] for pid in ports}
    for edge in edges:
        if edge.from_port in graph:
            graph[edge.from_port].append(edge)
    return graph

# ---------------------------------------------------------------------------
# All simple paths (enumerate for small graph)
# ---------------------------------------------------------------------------

def _find_all_paths(
    graph: Dict[str, List[RouteEdge]],
    origin: str,
    destination: str,
) -> List[Tuple[List[str], float, float]]:
    """
    Find all simple paths from origin to destination using DFS.
    Returns list of (path, total_distance, total_cost).
    """
    results: List[Tuple[List[str], float, float]] = []
    visited = {origin}

    def dfs(current: str, path: List[str], dist: float, cost: float) -> None:
        if current == destination:
            results.append((list(path), dist, cost))
            return
        for edge in graph.get(current, []):
            if edge.to_port not in visited:
                visited.add(edge.to_port)
                path.append(edge.to_port)
                dfs(edge.to_port, path, dist + edge.distance_km, cost + edge.base_cost_per_tonne_usd)
                path.pop()
                visited.remove(edge.to_port)

    dfs(origin, [origin], 0.0, 0.0)
    return results

# ---------------------------------------------------------------------------
# Transit time calculator
# ---------------------------------------------------------------------------

def compute_transit_days(distance_km: float, num_stops: int = 0) -> float:
    """
    Transit days = sailing time + port processing time.
    Sailing speed ~10 knots (444.5 km/day).
    Port processing: 0.5 days per intermediate port stop.
    """
    sailing_days = distance_km / CARGO_SHIP_SPEED_KM_PER_DAY
    processing_days = num_stops * PORT_PROCESSING_DAYS
    return round(sailing_days + processing_days, 2)

# ---------------------------------------------------------------------------
# Main entry — AGENTS.md section 3 compliant
# ---------------------------------------------------------------------------

def optimize_routes(
    origin_port_id: str,
    destination_port_id: str,
    cargo_tonnes: int,
    deadline_days: int,
) -> Dict[str, Any]:
    """
    Given origin/destination ports and cargo details, return 2-3 route options
    with distance, transit time, and cost.

    Input (AGENTS.md section 3):
      origin_port_id: str
      destination_port_id: str
      cargo_tonnes: int
      deadline_days: int

    Output (AGENTS.md section 3):
      {
        "routes": [
          {
            "route_id": "string",
            "path_port_ids": ["string"],
            "distance_km": 0,
            "transit_days": 0,
            "cost_per_tonne_usd": 0
          }
        ],
        "recommended_route_id": "string"
      }
    """
    graph = build_graph()

    # Validate ports exist
    ports = _load_ports()
    if origin_port_id not in ports:
        raise ValueError(f"Unknown origin port: {origin_port_id}")
    if destination_port_id not in ports:
        raise ValueError(f"Unknown destination port: {destination_port_id}")

    # Find all simple paths
    all_paths = _find_all_paths(graph, origin_port_id, destination_port_id)

    if not all_paths:
        raise ValueError(f"No route found from {origin_port_id} to {destination_port_id}")

    # Build RouteResult objects, sorted by cost_per_tonne_usd
    route_results: List[RouteResult] = []
    for i, (path, total_dist, total_cost) in enumerate(all_paths):
        num_stops = len(path) - 2  # intermediate ports
        transit = compute_transit_days(total_dist, num_stops)
        route_results.append(RouteResult(
            route_id=f"route_{i + 1}_{path[0]}_{path[-1]}",
            path_port_ids=path,
            distance_km=round(total_dist, 1),
            transit_days=transit,
            cost_per_tonne_usd=round(total_cost, 2),
        ))

    # Sort by cost_per_tonne_usd (primary), then distance (secondary)
    route_results.sort(key=lambda r: (r.cost_per_tonne_usd, r.distance_km))

    # Take top 2-3
    top_routes = route_results[:3]

    # Recommend: lowest cost that meets deadline
    recommended = top_routes[0]
    for r in top_routes:
        if r.transit_days <= deadline_days:
            recommended = r
            break

    return {
        "routes": [
            {
                "route_id": r.route_id,
                "path_port_ids": r.path_port_ids,
                "distance_km": r.distance_km,
                "transit_days": r.transit_days,
                "cost_per_tonne_usd": r.cost_per_tonne_usd,
            }
            for r in top_routes
        ],
        "recommended_route_id": recommended.route_id,
    }


def get_route_cost(logistics_output: Dict[str, Any], route_id: Optional[str] = None) -> float:
    """
    Extract cost_per_tonne_usd from logistics output.
    Used by the Negotiation Agent.
    """
    target_route_id = route_id or logistics_output["recommended_route_id"]
    for route in logistics_output["routes"]:
        if route["route_id"] == target_route_id:
            return route["cost_per_tonne_usd"]
    return logistics_output["routes"][0]["cost_per_tonne_usd"]


# Keep contract-compatible alias
def run_logistics(*args, **kwargs) -> Dict[str, Any]:
    return optimize_routes(*args, **kwargs)


__all__ = [
    "optimize_routes",
    "get_route_cost",
    "run_logistics",
    "build_graph",
    "compute_transit_days",
    "haversine_distance",
    "RouteResult",
    "RouteEdge",
    "CARGO_SHIP_SPEED_KM_PER_DAY",
    "PORT_PROCESSING_DAYS",
]
