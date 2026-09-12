"""Tests for the standalone material–buyer knowledge graph."""

from __future__ import annotations

import unittest

from agents.circularity import build_material_buyer_graph


class MaterialBuyerGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.materials = [
            {
                "material_id": "m1",
                "name": "Material one",
                "composition": {"CaO": 50.0},
                "applications": [
                    {
                        "application": "general_use",
                        "requirements": {},
                    },
                    {
                        "application": "specialized_use",
                        "requirements": {"CaO_min_pct": 40.0},
                    },
                ],
            }
        ]
        self.buyers = [
            {
                "buyer_id": "buyer_1",
                "name": "Buyer one",
                "port_id": "port_1",
                "material_required_id": "m1",
                "annual_demand_tonnes": 100.0,
                "requirements": {"CaO_min_pct": 45.0},
            }
        ]
        self.ports = [
            {"port_id": "port_1", "name": "Port one", "country": "Testland"}
        ]

    def test_graph_contains_required_domain_nodes_and_edges(self) -> None:
        graph = build_material_buyer_graph(self.materials, self.buyers, self.ports)

        self.assertEqual(
            {node.kind for node in graph.nodes.values()},
            {"Material", "Application", "Buyer", "Port"},
        )
        self.assertEqual(
            {edge.relation for edge in graph.edges},
            {"SUITABLE_FOR", "REQUIRED_BY", "LOCATED_AT"},
        )
        self.assertEqual(graph.nodes["Port:port_1"].attributes["country"], "Testland")

    def test_match_returns_one_explainable_candidate_per_buyer(self) -> None:
        graph = build_material_buyer_graph(self.materials, self.buyers, self.ports)

        result = graph.match("m1", 50.0)

        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["buyer_id"], "buyer_1")
        self.assertEqual(candidate["application"], "specialized_use")
        self.assertEqual(candidate["compatibility_score"], 0.5833)
        self.assertIn("Material(m1)", candidate["notes"])
        self.assertIn("Application(specialized_use)", candidate["notes"])
        self.assertIn("Buyer(buyer_1)", candidate["notes"])
        self.assertIn("Port(port_1)", candidate["notes"])
        self.assertIn("CaO 50% >= 40%", candidate["notes"])
        self.assertIn("CaO 50% >= 45%", candidate["notes"])

    def test_failed_threshold_has_no_graph_path(self) -> None:
        buyers = [dict(self.buyers[0], requirements={"CaO_min_pct": 60.0})]
        graph = build_material_buyer_graph(self.materials, buyers, self.ports)

        self.assertEqual(graph.match("m1", 50.0), {"candidates": []})


if __name__ == "__main__":
    unittest.main()
