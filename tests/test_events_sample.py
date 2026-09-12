"""Checks demo/sample_run.jsonl against the frozen event schema in AGENTS.md §5.

The same checks are meant to run on real recorded runs (runs/*.jsonl) once the
negotiation runtime exists, so the rules here mirror AGENTS.md §5, not the sample.
"""
import json
import re
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "demo" / "sample_run.jsonl"

KEYS = {"seq", "ts", "run_id", "deal_id", "type", "from_agent", "to_agent", "model",
        "payload", "validator", "delivered"}
TYPES = {"run_started", "match", "route", "thread_started", "offer", "accept", "reject",
         "info_request", "info_response", "fallback", "thread_result", "released",
         "deal_closed", "recommendation", "run_completed", "run_failed"}
MOVES = {"offer", "accept", "reject", "info_request"}
FIXED_AGENTS = {"seller_agent", "logistics_agent", "circularity_agent", "orchestrator",
                "validator", "runtime"}
HANDLING, PROCESSING = 2.0, 1.5
MAX_ROUNDS = 6


def load_events(path=SAMPLE):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def numbers_in(text):
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def valid_agent(name, buyer_ids):
    if name in FIXED_AGENTS:
        return True
    return name.startswith("buyer_agent:") and name.split(":", 1)[1] in buyer_ids


class SampleRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = load_events()
        cls.seller = json.loads((ROOT / "data/seller.json").read_text(encoding="utf-8"))
        cls.buyers = {b["buyer_id"]: b for b in
                      json.loads((ROOT / "data/buyers.json").read_text(encoding="utf-8"))}
        route = next(e for e in cls.events if e["type"] == "route")["payload"]
        cls.freight = route["cost_per_tonne_usd"]

    def net(self, price, qty):
        return round((price - self.freight - HANDLING - PROCESSING) * qty, 2)

    def test_envelope(self):
        run_ids = {e["run_id"] for e in self.events}
        self.assertEqual(len(run_ids), 1)
        previous_ts = None
        for i, e in enumerate(self.events, start=1):
            self.assertEqual(set(e), KEYS, e["seq"])
            self.assertEqual(e["seq"], i)
            self.assertIn(e["type"], TYPES)
            self.assertTrue(valid_agent(e["from_agent"], self.buyers), e["seq"])
            self.assertTrue(e["to_agent"] is None or valid_agent(e["to_agent"], self.buyers), e["seq"])
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
            if previous_ts is not None:
                self.assertGreaterEqual(ts, previous_ts, e["seq"])
            previous_ts = ts
            if e["type"] in MOVES:
                self.assertIn(e["validator"]["gate"], ("move_legal", "deal_legal"))
                self.assertIsInstance(e["delivered"], bool)
            else:
                self.assertIsNone(e["validator"], e["seq"])
                self.assertIsNone(e["delivered"], e["seq"])
        self.assertEqual(self.events[0]["type"], "run_started")
        self.assertIn(self.events[-1]["type"], ("run_completed", "run_failed"))
        terminal = [e for e in self.events if e["type"] in ("run_completed", "run_failed")]
        self.assertEqual(len(terminal), 1)

    def test_offers_respect_own_bounds_and_ids(self):
        seller = self.seller
        counts = {}
        for e in self.events:
            if e["type"] != "offer":
                continue
            p, deal = e["payload"], e["deal_id"]
            buyer = self.buyers[deal]
            self.assertLessEqual(p["round"], MAX_ROUNDS)
            if e["from_agent"] == "seller_agent":
                within = (p["price_per_tonne_usd"] >= seller["min_acceptable_price_per_tonne_usd"]
                          and p["quantity_tonnes"] <= seller["available_quantity_tonnes_per_year"]
                          and seller["contract_months_min"] <= p["contract_months"] <= seller["contract_months_max"])
                counterparty_bounds = {buyer["max_acceptable_price_per_tonne_usd"]}
            else:
                within = (p["price_per_tonne_usd"] <= buyer["max_acceptable_price_per_tonne_usd"]
                          and p["quantity_tonnes"] <= buyer["annual_demand_tonnes"]
                          and buyer["contract_months_min"] <= p["contract_months"] <= buyer["contract_months_max"])
                counterparty_bounds = {seller["min_acceptable_price_per_tonne_usd"]}
            # Validator verdict must agree with the move-legal gate on real data.
            self.assertEqual(e["validator"]["ok"], within, e["seq"])
            self.assertEqual(e["delivered"], within, e["seq"])
            if within:
                counts[deal] = counts.get(deal, 0) + 1
                self.assertEqual(p["offer_id"], f"{deal}-o{counts[deal]}")
            else:
                self.assertIsNone(p["offer_id"])
                self.assertEqual(e["to_agent"], e["from_agent"], "bounce returns to author")
                # Bounce reason may not leak the counterparty's bound.
                self.assertFalse(numbers_in(e["validator"]["reason"]) & counterparty_bounds, e["seq"])

    def test_messages_only_state_structured_numbers_and_hide_own_reservation(self):
        offers = {e["payload"]["offer_id"]: e["payload"] for e in self.events
                  if e["type"] == "offer" and e["payload"]["offer_id"]}
        for e in self.events:
            if e["type"] not in ("offer", "accept", "reject"):
                continue
            p = e["payload"]
            terms = offers[p["offer_id"]] if e["type"] == "accept" else p
            allowed = {terms.get("price_per_tonne_usd"), terms.get("quantity_tonnes"),
                       terms.get("contract_months")} - {None}
            said = numbers_in(p["message"])
            self.assertLessEqual(said, {float(v) for v in allowed}, e["seq"])
            if e["from_agent"] == "seller_agent":
                own = {self.seller["min_acceptable_price_per_tonne_usd"]}
            else:
                own = {self.buyers[e["deal_id"]]["max_acceptable_price_per_tonne_usd"]}
            self.assertFalse(said & own, f"seq {e['seq']} states its own reservation value")

    def test_accepts_reference_latest_counterparty_offer_and_pass_both_bounds(self):
        latest = {}
        for e in self.events:
            if e["type"] == "offer" and e["delivered"]:
                latest[(e["deal_id"], e["from_agent"])] = e["payload"]
            if e["type"] == "accept":
                counterparty = ("seller_agent" if e["from_agent"].startswith("buyer_agent")
                                else f"buyer_agent:{e['deal_id']}")
                offer = latest[(e["deal_id"], counterparty)]
                self.assertEqual(e["payload"]["offer_id"], offer["offer_id"])
                buyer = self.buyers[e["deal_id"]]
                price = offer["price_per_tonne_usd"]
                self.assertGreaterEqual(price, self.seller["min_acceptable_price_per_tonne_usd"])
                self.assertLessEqual(price, buyer["max_acceptable_price_per_tonne_usd"])
                self.assertEqual(e["validator"]["gate"], "deal_legal")

    def test_results_and_recommendation_arithmetic(self):
        results = {e["deal_id"]: e["payload"] for e in self.events if e["type"] == "thread_result"}
        for deal, r in results.items():
            self.assertIn(r["status"], ("accepted", "countered", "rejected"))
            self.assertEqual(r["total_net_value_usd"], self.net(r["price_per_tonne_usd"], r["quantity_tonnes"]))
        eligible = {d: r for d, r in results.items() if r["status"] in ("accepted", "countered")}
        winner = max(eligible, key=lambda d: eligible[d]["total_net_value_usd"])
        closed = next(e for e in self.events if e["type"] == "deal_closed")
        self.assertEqual(closed["deal_id"], winner)
        released = {e["deal_id"] for e in self.events if e["type"] == "released"}
        self.assertEqual(released, set(eligible) - {winner})

        rec = next(e for e in self.events if e["type"] == "recommendation")["payload"]
        w = results[winner]
        self.assertEqual(rec["buyer_id"], winner)
        self.assertEqual(rec["price_per_tonne_usd"], w["price_per_tonne_usd"])
        self.assertEqual(rec["quantity_tonnes"], w["quantity_tonnes"])
        self.assertEqual(rec["route"]["cost_per_tonne_usd"], self.freight)
        margin = round(rec["price_per_tonne_usd"] - self.freight - HANDLING - PROCESSING, 2)
        self.assertEqual(rec["margin_per_tonne_usd"], margin)
        self.assertEqual(rec["total_net_value_usd"], round(margin * rec["quantity_tonnes"], 2))

    def test_run_summary_counts(self):
        summary = self.events[-1]["payload"]
        body = self.events[:-1]
        self.assertEqual(summary["llm_calls"], sum(1 for e in body if e["model"]))
        self.assertEqual(summary["validator_bounces"], sum(1 for e in body if e["delivered"] is False))
        self.assertEqual(summary["fallbacks"], sum(1 for e in body if e["type"] == "fallback"))


if __name__ == "__main__":
    unittest.main()
