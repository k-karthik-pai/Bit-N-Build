"""Material-to-buyer knowledge graph for deterministic matching.

The graph is intentionally small and in-memory.  It models the relationships
needed by the Circularity Agent without introducing a graph database or a
dependency on the negotiation and logistics modules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


QUALITY_RULE_PATTERN = re.compile(
    r"^(?P<component>[A-Za-z][A-Za-z0-9]*)_(?P<operator>min|max)_pct$"
)


@dataclass(frozen=True)
class GraphNode:
    """A typed domain entity in the material–buyer graph."""

    kind: str
    identifier: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.identifier}"


@dataclass(frozen=True)
class GraphEdge:
    """A directed relationship with explainability/scoring attributes."""

    source: str
    relation: str
    target: str
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MaterialBuyerPath:
    """A qualifying graph traversal and its deterministic score."""

    material_id: str
    application: str
    buyer_id: str
    port_id: str
    compatibility_score: float
    notes: str
    application_specificity: int = 0


class MaterialBuyerGraph:
    """Small directed graph containing material, application, buyer, and port nodes."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._outgoing: dict[str, list[GraphEdge]] = {}

    def add_node(
        self, kind: str, identifier: str, attributes: Mapping[str, Any] | None = None
    ) -> str:
        node = GraphNode(kind, identifier, dict(attributes or {}))
        self.nodes[node.key] = node
        self._outgoing.setdefault(node.key, [])
        return node.key

    def add_edge(
        self,
        source_kind: str,
        source_id: str,
        relation: str,
        target_kind: str,
        target_id: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> GraphEdge:
        source = f"{source_kind}:{source_id}"
        target = f"{target_kind}:{target_id}"
        edge = GraphEdge(source, relation, target, dict(attributes or {}))
        self.edges.append(edge)
        self._outgoing.setdefault(source, []).append(edge)
        return edge

    def outgoing(self, node_key: str, relation: str | None = None) -> list[GraphEdge]:
        edges = self._outgoing.get(node_key, [])
        if relation is None:
            return list(edges)
        return [edge for edge in edges if edge.relation == relation]

    def find_paths(
        self, material_id: str, quantity_tonnes: float
    ) -> list[MaterialBuyerPath]:
        """Traverse qualifying Material→Application→Buyer→Port paths."""
        material_key = f"Material:{material_id}"
        material = self.nodes.get(material_key)
        if material is None:
            return []

        paths: list[MaterialBuyerPath] = []
        for suitable_edge in self.outgoing(material_key, "SUITABLE_FOR"):
            application = self.nodes[suitable_edge.target]
            application_score, application_checks = _requirement_score(
                material.attributes["composition"],
                suitable_edge.attributes["requirements"],
            )
            if application_score is None:
                continue

            for buyer_edge in self.outgoing(application.key, "REQUIRED_BY"):
                buyer = self.nodes[buyer_edge.target]
                if buyer.attributes.get("material_required_id") != material_id:
                    continue
                buyer_score, buyer_checks = _requirement_score(
                    material.attributes["composition"],
                    buyer_edge.attributes["requirements"],
                )
                if buyer_score is None:
                    continue

                demand = float(buyer.attributes.get("annual_demand_tonnes", 0))
                if demand <= 0:
                    continue
                covered_tonnes = min(demand, quantity_tonnes)
                demand_fit = covered_tonnes / quantity_tonnes
                # Demand fit remains part of the path score, while quality
                # headroom supplies the graph-specific explainability signal.
                quality_factor = (application_score * buyer_score) ** 0.5
                score = round(demand_fit * (0.5 + 0.5 * quality_factor), 4)

                port_edges = self.outgoing(buyer.key, "LOCATED_AT")
                if not port_edges:
                    continue
                port_id = port_edges[0].target.split(":", 1)[1]
                checks = list(dict.fromkeys(application_checks + buyer_checks))
                quality_note = (
                    "; quality headroom: " + "; ".join(checks)
                    if checks
                    else "; no numeric quality constraints supplied"
                )
                notes = (
                    f"Path: Material({material_id}) -[SUITABLE_FOR]-> "
                    f"Application({application.identifier}) -[REQUIRED_BY]-> "
                    f"Buyer({buyer.identifier}) -[LOCATED_AT]-> Port({port_id})"
                    f"{quality_note}; demand coverage {demand_fit * 100:.2f}%"
                )
                paths.append(
                    MaterialBuyerPath(
                        material_id=material_id,
                        application=application.identifier,
                        buyer_id=buyer.identifier,
                        port_id=port_id,
                        compatibility_score=score,
                        notes=notes,
                        application_specificity=len(
                            suitable_edge.attributes["requirements"]
                        ),
                    )
                )
        return paths

    def match(
        self, material_id: str, quantity_tonnes: float
    ) -> dict[str, list[dict[str, Any]]]:
        paths = self.find_paths(material_id, quantity_tonnes)
        # A buyer may be reachable through more than one suitable application.
        # Keep one explainable candidate per buyer, preferring the most specific
        # application and then the strongest normalized path score.
        best_paths: dict[str, MaterialBuyerPath] = {}
        for path in paths:
            current = best_paths.get(path.buyer_id)
            if current is None or (
                path.application_specificity,
                path.compatibility_score,
                path.application,
            ) > (
                current.application_specificity,
                current.compatibility_score,
                current.application,
            ):
                best_paths[path.buyer_id] = path
        paths = list(best_paths.values())
        paths.sort(key=lambda path: (-path.compatibility_score, path.buyer_id))
        return {
            "candidates": [
                {
                    "buyer_id": path.buyer_id,
                    "application": path.application,
                    "compatibility_score": path.compatibility_score,
                    "notes": path.notes,
                }
                for path in paths
            ]
        }


def _requirement_score(
    composition: Mapping[str, float], requirements: Mapping[str, float]
) -> tuple[float | None, list[str]]:
    """Return normalized threshold headroom and human-readable checks."""
    if not requirements:
        return 1.0, []

    scores: list[float] = []
    checks: list[str] = []
    for rule, threshold in requirements.items():
        match = QUALITY_RULE_PATTERN.fullmatch(rule)
        if match is None:
            return None, []
        component = match.group("component")
        actual = composition.get(component)
        if actual is None:
            return None, []
        threshold = float(threshold)
        actual = float(actual)
        operator = match.group("operator")
        if operator == "min":
            if actual < threshold:
                return None, []
            denominator = max(threshold, 1.0)
            headroom = min((actual - threshold) / denominator, 1.0)
            checks.append(f"{component} {actual:g}% >= {threshold:g}% ({headroom:.2f} headroom)")
        else:
            if actual > threshold:
                return None, []
            denominator = max(threshold, 1.0)
            headroom = min((threshold - actual) / denominator, 1.0)
            checks.append(f"{component} {actual:g}% <= {threshold:g}% ({headroom:.2f} headroom)")
        scores.append(headroom)
    return min(scores), checks


def build_material_buyer_graph(
    materials: Sequence[Mapping[str, Any]],
    buyers: Sequence[Mapping[str, Any]],
    ports: Sequence[Mapping[str, Any]] | None = None,
) -> MaterialBuyerGraph:
    """Build the standalone domain graph from validated Circularity records."""
    graph = MaterialBuyerGraph()
    port_records = {str(port["port_id"]): port for port in (ports or [])}

    for material in materials:
        material_id = str(material["material_id"])
        graph.add_node(
            "Material",
            material_id,
            {
                "name": material.get("name", material_id),
                "composition": dict(material["composition"]),
            },
        )
        for application in material["applications"]:
            application_id = str(application["application"])
            graph.add_node("Application", application_id, {"name": application_id})
            graph.add_edge(
                "Material",
                material_id,
                "SUITABLE_FOR",
                "Application",
                application_id,
                {"requirements": dict(application["requirements"])},
            )

    for buyer in buyers:
        buyer_id = str(buyer["buyer_id"])
        port_id = str(buyer.get("port_id", "unknown"))
        graph.add_node(
            "Buyer",
            buyer_id,
            {
                "name": buyer.get("name", buyer_id),
                "material_required_id": buyer["material_required_id"],
                "annual_demand_tonnes": buyer["annual_demand_tonnes"],
            },
        )
        graph.add_node("Port", port_id, dict(port_records.get(port_id, {"port_id": port_id})))
        graph.add_edge("Buyer", buyer_id, "LOCATED_AT", "Port", port_id)
        material_id = str(buyer["material_required_id"])
        material = graph.nodes.get(f"Material:{material_id}")
        if material is None:
            continue
        for application_edge in graph.outgoing(material.key, "SUITABLE_FOR"):
            application_id = application_edge.target.split(":", 1)[1]
            graph.add_edge(
                "Application",
                application_id,
                "REQUIRED_BY",
                "Buyer",
                buyer_id,
                {"requirements": dict(buyer["requirements"])},
            )
    return graph


__all__ = [
    "GraphEdge",
    "GraphNode",
    "MaterialBuyerGraph",
    "MaterialBuyerPath",
    "build_material_buyer_graph",
]
