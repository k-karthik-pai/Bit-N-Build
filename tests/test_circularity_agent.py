"""Tests for the deterministic circularity matching agent."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents.circularity import (
    DataValidationError,
    RequestValidationError,
    find_candidates,
    run_from_files,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def material(
    composition: dict[str, float] | None = None,
    requirements: dict[str, float] | None = None,
) -> dict[str, object]:
    return {
        "material_id": "ld_slag",
        "name": "LD slag",
        "composition_pct": composition or {"CaO": 45, "Fe2O3": 20},
        "applications": [
            {
                "application": "cement_clinker_substitute",
                "min_quality_requirements": requirements or {},
            }
        ],
    }


def buyer(
    buyer_id: str,
    demand: float,
    requirements: dict[str, float] | None = None,
    material_id: str = "ld_slag",
) -> dict[str, object]:
    return {
        "buyer_id": buyer_id,
        "name": buyer_id,
        "country": "Bangladesh",
        "port_id": "chittagong",
        "material_required_id": material_id,
        "annual_demand_tonnes": demand,
        "max_acceptable_price_per_tonne_usd": 25,
        "min_quality_requirements": requirements or {},
        "is_real_reference": False,
        "source_note": "test fixture",
    }


class CircularityAgentTests(unittest.TestCase):
    request = {
        "material_id": "ld_slag",
        "quantity_tonnes": 100_000,
        "seller_id": "seller_1",
    }

    def test_committed_stub_is_ranked_by_demand_fit(self) -> None:
        result = run_from_files(
            self.request,
            REPOSITORY_ROOT / "data" / "materials.json",
            REPOSITORY_ROOT / "data" / "buyers.json",
        )

        self.assertEqual(
            [candidate["buyer_id"] for candidate in result["candidates"]],
            [
                "stub_buyer_3",
                "stub_buyer_5",
                "stub_buyer_1",
                "stub_buyer_2",
                "stub_buyer_4",
            ],
        )
        self.assertEqual(
            [candidate["compatibility_score"] for candidate in result["candidates"]],
            [0.6, 0.5, 0.4, 0.25, 0.15],
        )
        for candidate in result["candidates"]:
            self.assertEqual(
                set(candidate),
                {"buyer_id", "application", "compatibility_score", "notes"},
            )

    def test_material_and_quality_mismatches_are_excluded(self) -> None:
        buyers = [
            buyer("wrong_material", 100_000, material_id="fly_ash"),
            buyer("minimum_failed", 100_000, {"CaO_min_pct": 46}),
            buyer("maximum_failed", 100_000, {"Fe2O3_max_pct": 19}),
            buyer("compatible", 25_000, {"CaO_min_pct": 40}),
        ]

        result = find_candidates(self.request, [material()], buyers)

        self.assertEqual(
            [candidate["buyer_id"] for candidate in result["candidates"]],
            ["compatible"],
        )

    def test_unknown_composition_component_fails_closed(self) -> None:
        result = find_candidates(
            self.request,
            [material()],
            [buyer("unknown_quality", 100_000, {"Al2O3_min_pct": 1})],
        )

        self.assertEqual(result, {"candidates": []})

    def test_most_specific_compatible_application_is_selected(self) -> None:
        test_material = material()
        test_material["applications"] = [
            {
                "application": "general_aggregate",
                "min_quality_requirements": {},
            },
            {
                "application": "cement_clinker_substitute",
                "min_quality_requirements": {
                    "CaO_min_pct": 40,
                    "Fe2O3_max_pct": 25,
                },
            },
        ]

        result = find_candidates(
            self.request,
            [test_material],
            [buyer("specific_application", 10_000)],
        )

        self.assertEqual(
            result["candidates"][0]["application"],
            "cement_clinker_substitute",
        )

    def test_demand_score_is_capped_and_ties_are_deterministic(self) -> None:
        result = find_candidates(
            self.request,
            [material()],
            [buyer("buyer_b", 120_000), buyer("buyer_a", 100_000)],
        )

        self.assertEqual(
            [candidate["buyer_id"] for candidate in result["candidates"]],
            ["buyer_a", "buyer_b"],
        )
        self.assertEqual(
            [candidate["compatibility_score"] for candidate in result["candidates"]],
            [1.0, 1.0],
        )

    def test_no_matching_buyers_returns_empty_contract(self) -> None:
        result = find_candidates(
            self.request,
            [material()],
            [buyer("other", 10_000, material_id="fly_ash")],
        )

        self.assertEqual(result, {"candidates": []})

    def test_missing_material_and_invalid_quantities_fail_clearly(self) -> None:
        with self.assertRaisesRegex(RequestValidationError, "was not found"):
            find_candidates(
                {**self.request, "material_id": "missing"},
                [material()],
                [],
            )

        for invalid_quantity in (0, -1, float("inf"), True):
            with self.subTest(quantity=invalid_quantity):
                with self.assertRaisesRegex(
                    RequestValidationError, "quantity_tonnes"
                ):
                    find_candidates(
                        {**self.request, "quantity_tonnes": invalid_quantity},
                        [material()],
                        [],
                    )

        for missing_field in ("material_id", "seller_id"):
            with self.subTest(field=missing_field):
                invalid_request = dict(self.request)
                invalid_request.pop(missing_field)
                with self.assertRaisesRegex(
                    RequestValidationError, missing_field
                ):
                    find_candidates(invalid_request, [material()], [])

    def test_duplicate_ids_and_invalid_numbers_fail_clearly(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "duplicate buyer_id"):
            find_candidates(
                self.request,
                [material()],
                [buyer("duplicate", 1), buyer("duplicate", 2)],
            )

        with self.assertRaisesRegex(DataValidationError, "annual_demand_tonnes"):
            find_candidates(
                self.request,
                [material()],
                [buyer("invalid", -1)],
            )

        invalid_material = material(composition={"CaO": float("nan")})
        with self.assertRaisesRegex(DataValidationError, "composition_pct.CaO"):
            find_candidates(self.request, [invalid_material], [])

    def test_unsupported_quality_rule_fails_clearly(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "unsupported quality rule"):
            find_candidates(
                self.request,
                [material()],
                [buyer("invalid_rule", 10_000, {"CaO": 40})],
            )

    def test_malformed_json_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_directory = Path(directory)
            materials_path = temporary_directory / "materials.json"
            buyers_path = temporary_directory / "buyers.json"
            materials_path.write_text("not json", encoding="utf-8")
            buyers_path.write_text(json.dumps([]), encoding="utf-8")

            with self.assertRaisesRegex(DataValidationError, "invalid JSON"):
                run_from_files(self.request, materials_path, buyers_path)


if __name__ == "__main__":
    unittest.main()
