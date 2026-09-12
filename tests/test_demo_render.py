"""Demo contract and output fidelity checks; fixtures are not live demo data."""

import copy
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from demo.render_log import main, render, show_progress
from orchestrator import run_pipeline


def recommendation():
    return {
        "material": "Fixture material",
        "quantity_tonnes": 1234.125,
        "buyer_id": "fixture_buyer",
        "buyer_name": "Fixture Buyer",
        "price_per_tonne_usd": 27.125,
        "route": {
            "origin_port": "Fixture origin",
            "destination_port": "Fixture destination",
            "distance_km": 987.125,
            "transit_days": 2.345,
            "cost_per_tonne_usd": 6.125,
        },
        "margin_per_tonne_usd": 17.5,
        # Deliberately independent values: rendering must not recalculate them.
        "total_net_value_usd": 98765.4321,
        "co2_avoided_tonnes_estimate": 456.789,
    }


class DemoTests(unittest.TestCase):
    def test_contract_only_preserves_all_values_and_precision(self):
        result = recommendation()
        before = copy.deepcopy(result)
        output = render(result)
        for text in (
            "Fixture material", "Fixture Buyer (fixture_buyer)", "1,234.125 t",
            "$27.125/t", "Fixture origin -> Fixture destination", "987.125 km",
            "2.345 days", "$6.125/t", "$17.50/t", "$98,765.4321",
            "CO2 avoided (estimate): 456.789 t", "Currency: USD",
        ):
            with self.subTest(text=text):
                self.assertIn(text, output)
        self.assertNotIn("run log", output)
        self.assertNotIn("ACCEPTED", output)
        self.assertEqual(result, before)
        result["pipeline_log"] = []
        self.assertEqual(render(result), output)

    def test_log_preserves_order_statuses_and_zero_batna(self):
        result = recommendation()
        result["pipeline_log"] = [
            {"buyer_id": "rejected_buyer", "status": "rejected", "reason": "No overlap"},
            {"buyer_id": "accepted_buyer", "status": "accepted", "price_per_tonne_usd": 20},
            {"buyer_id": "fixture_buyer", "status": "countered",
             "price_per_tonne_usd": 27.125, "batna_price_per_tonne_usd": 0},
        ]
        before = copy.deepcopy(result)
        output = render(result)
        self.assertIn("=== Completed run log ===", output)
        self.assertLess(output.index("rejected_buyer"), output.index("accepted_buyer"))
        self.assertLess(output.index("accepted_buyer"), output.index("fixture_buyer"))
        self.assertIn("REJECTED - No overlap", output)
        self.assertIn("ACCEPTED at $20.00/t", output)
        self.assertIn("walk-away floor $0.00/t", output)
        card = output.split("=== Recommendation ===")[1]
        self.assertIn("COUNTERED (pending confirmation)", card)
        self.assertNotIn("ACCEPTED", card)
        self.assertEqual(result, before)

    def test_zero_estimate_and_negative_margin_are_not_hidden(self):
        result = recommendation()
        result["co2_avoided_tonnes_estimate"] = 0
        result["margin_per_tonne_usd"] = -1.25
        self.assertIn("CO2 avoided (estimate): 0 t", render(result))
        self.assertIn("$-1.25/t", render(result))

    def test_missing_or_invalid_numbers_do_not_become_display_defaults(self):
        for value in (None, True, "25", float("nan"), float("inf")):
            with self.subTest(value=value):
                result = recommendation()
                result["price_per_tonne_usd"] = value
                with self.assertRaises(ValueError):
                    render(result)
        result = recommendation()
        del result["route"]["distance_km"]
        with self.assertRaises(KeyError):
            render(result)

    def test_failed_pipeline_prints_only_an_error(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("orchestrator.run_pipeline", side_effect=ValueError("No viable deal")):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("No viable deal", stderr.getvalue())

    def test_progress_is_delivered_during_run_without_changing_result(self):
        events = []
        def receive(event):
            if event["stage"] == "negotiation_completed":
                self.assertEqual(events[-1]["stage"], "negotiation_started")
            events.append(event)
        self.assertEqual(run_pipeline(on_progress=receive), run_pipeline())
        self.assertEqual(events[0]["stage"], "matching_started")
        outcomes = [e for e in events if e["stage"] == "negotiation_completed"]
        self.assertEqual(len(outcomes), len(run_pipeline()["pipeline_log"]))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            for event in events:
                show_progress(event)
        self.assertIn("month term", stdout.getvalue())
        self.assertIn("pending confirmation", stdout.getvalue())

    def test_progress_survives_failed_run(self):
        events = []
        with self.assertRaises(ValueError):
            run_pipeline(seller_overrides={"min_acceptable_price_per_tonne_usd": 100},
                         on_progress=events.append)
        self.assertTrue(any(e["stage"] == "negotiation_completed" for e in events))


    def test_real_pipeline_output_is_used_once_and_unchanged(self):
        result = run_pipeline()
        before = copy.deepcopy(result)
        stdout = io.StringIO()
        with patch("orchestrator.run_pipeline", return_value=result) as pipeline:
            with redirect_stdout(stdout):
                self.assertEqual(main(), 0)
            pipeline.assert_called_once_with(on_progress=show_progress)
        output = stdout.getvalue()
        self.assertEqual(output, render(result, include_log=False) + "\n")
        self.assertIn(result["buyer_name"], output)
        self.assertIn(f"{result['route']['distance_km']:,} km", output)
        self.assertIn(f"${result['total_net_value_usd']:,.2f}", output)
        self.assertEqual(result, before)


if __name__ == "__main__":
    unittest.main()
