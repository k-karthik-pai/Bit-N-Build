"""Shared EVENTS.md validation helpers — reused by test_events_sample and test_negotiation_loop."""
import re
import unittest
from datetime import datetime
from typing import Any, Dict, List

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

def numbers_in(text: str):
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}

# The runtime's move-legal reason strings always self-reference as "your
# floor/ceiling/range/availability/demand <value(s)>" (AGENTS.md sec2: "the
# rejection reason may reference only the proposing agent's own
# constraints"). A number appearing there is the author's OWN (possibly
# dynamic, R2-2) constraint, not a disclosure of the counterparty's bound --
# even when it numerically coincides with it (e.g. seller floor raised to
# 27.0 by a live buyer bid elsewhere, same value as a DIFFERENT buyer's
# static ceiling). A real leak phrases it differently ("buyer ceiling",
# "seller floor") and must still be caught.
_OWN_CONSTRAINT_RE = re.compile(r"\byour (?:floor|ceiling|range|availability|demand)\b.*", re.I)

def own_constraint_numbers(reason: str):
    m = _OWN_CONSTRAINT_RE.search(reason or "")
    return numbers_in(m.group(0)) if m else set()

def valid_agent(name, buyer_ids):
    if name in FIXED_AGENTS:
        return True
    return name.startswith("buyer_agent:") and name.split(":", 1)[1] in buyer_ids

def assert_events_valid(testcase: unittest.TestCase, events: List[Dict[str, Any]], seller: Dict[str, Any], buyers: Dict[str, Dict[str, Any]], freight: float):
    """Run all EVENTS.md checks on a list of events."""
    # envelope
    run_ids = {e["run_id"] for e in events}
    testcase.assertEqual(len(run_ids), 1)
    previous_ts = None
    for i, e in enumerate(events, start=1):
        testcase.assertEqual(set(e), KEYS, f"seq {e.get('seq')} keys")
        testcase.assertEqual(e["seq"], i)
        testcase.assertIn(e["type"], TYPES)
        testcase.assertTrue(valid_agent(e["from_agent"], buyers), f"seq {e['seq']} from_agent {e['from_agent']}")
        testcase.assertTrue(e["to_agent"] is None or valid_agent(e["to_agent"], buyers), f"seq {e['seq']} to_agent {e['to_agent']}")
        ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        if previous_ts is not None:
            testcase.assertGreaterEqual(ts, previous_ts, f"seq {e['seq']}")
        previous_ts = ts
        if e["type"] in MOVES:
            testcase.assertIn(e["validator"]["gate"], ("move_legal", "deal_legal"))
            testcase.assertIsInstance(e["delivered"], bool)
        else:
            testcase.assertIsNone(e["validator"], f"seq {e['seq']}")
            testcase.assertIsNone(e["delivered"], f"seq {e['seq']}")
    testcase.assertEqual(events[0]["type"], "run_started")
    testcase.assertIn(events[-1]["type"], ("run_completed", "run_failed"))
    terminal = [e for e in events if e["type"] in ("run_completed", "run_failed")]
    testcase.assertEqual(len(terminal), 1)

    # offers respect own bounds and ids
    counts = {}
    for e in events:
        if e["type"] != "offer":
            continue
        p, deal = e["payload"], e["deal_id"]
        buyer = buyers[deal]
        testcase.assertLessEqual(p["round"], MAX_ROUNDS)
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
        # validator.ok and delivered always agree for moves (runtime invariant).
        testcase.assertEqual(e["validator"]["ok"], e["delivered"], f"seq {e['seq']}")
        # delivered ⟹ within its own bounds always holds. The converse does
        # NOT hold: a within-bounds move can still be legitimately bounced for
        # a reason "within" doesn't model — a message-number mismatch, an
        # own-reservation leak, or a false leverage claim (AGENTS.md §2) — all
        # of which live LLM output triggers routinely even though no fake-move
        # test fixture does. So only assert the direction that always holds.
        if e["delivered"]:
            testcase.assertTrue(within, f"seq {e['seq']} delivered outside its own bounds")
            counts[deal] = counts.get(deal, 0) + 1
            testcase.assertEqual(p["offer_id"], f"{deal}-o{counts[deal]}")
        else:
            testcase.assertIsNone(p["offer_id"])
            testcase.assertEqual(e["to_agent"], e["from_agent"], "bounce returns to author")
            # a number coinciding with the counterparty's bound is not a leak
            # when it is the author's own (possibly dynamic) constraint value
            # self-referenced in the reason text (see own_constraint_numbers).
            leaked = (numbers_in(e["validator"]["reason"]) & counterparty_bounds) - own_constraint_numbers(e["validator"]["reason"])
            testcase.assertFalse(leaked, f"seq {e['seq']} leaks counterparty bound")

    # messages only state structured numbers (own fields, OR a structured
    # field of an offer already delivered EARLIER in the same thread by
    # either side — R3-1, EVENTS.md "Numbers in message" amended 2026-09-12 —
    # OR the thread's route freight cost_per_tonne_usd, public to both sides
    # via the route/info_response events — R4-3, same section amended again
    # 2026-09-12) and hide own reservation UNLESS that number is covered by
    # the same rule (R3-2, "Own-reservation leak" amended 2026-09-12).
    # Applies to DELIVERED moves only: a bounced move's message never reached
    # the counterparty, and a bounce may itself be *caused* by violating one
    # of these very rules (message-number mismatch, own-reservation leak) —
    # asserting the rule on the bounced message it exists to catch is backwards.
    offers = {e["payload"]["offer_id"]: e["payload"] for e in events if e["type"] == "offer" and e["payload"]["offer_id"]}
    history_by_deal: Dict[str, set] = {}
    for e in events:
        if e["type"] not in ("offer", "accept", "reject"):
            continue
        if e["delivered"] is False:
            continue
        p = e["payload"]
        deal = e["deal_id"]
        terms = offers[p["offer_id"]] if e["type"] == "accept" else p
        own_fields = {float(v) for v in (terms.get("price_per_tonne_usd"), terms.get("quantity_tonnes"), terms.get("contract_months")) if v is not None}
        # R4-3: the thread's route freight cost_per_tonne_usd is public to both
        # sides (route / info_response) — allowed in message numbers too
        # (EVENTS.md "Numbers in message", amended 2026-09-12).
        allowed = own_fields | {float(freight)} | history_by_deal.get(deal, set())
        said = numbers_in(p["message"])
        testcase.assertLessEqual(said, allowed, f"seq {e['seq']} numbers {said} not subset of {allowed}")
        if e["from_agent"] == "seller_agent":
            own = {float(seller["min_acceptable_price_per_tonne_usd"])}
        else:
            # from_agent may be buyer_agent:xxx
            own = {float(buyers[deal]["max_acceptable_price_per_tonne_usd"])}
        # a number equal to the agent's own reservation value is a leak only
        # if it is NOT also covered by `allowed` (own fields / thread history).
        testcase.assertFalse((said & own) - allowed, f"seq {e['seq']} states its own reservation value")
        if e["type"] == "offer":
            history_by_deal.setdefault(deal, set()).update(own_fields)

    # accepts reference latest counterparty offer and pass both bounds
    latest = {}
    for e in events:
        if e["type"] == "offer" and e["delivered"]:
            latest[(e["deal_id"], e["from_agent"])] = e["payload"]
        if e["type"] == "accept":
            counterparty = ("seller_agent" if e["from_agent"].startswith("buyer_agent") else f"buyer_agent:{e['deal_id']}")
            offer = latest[(e["deal_id"], counterparty)]
            testcase.assertEqual(e["payload"]["offer_id"], offer["offer_id"])
            buyer = buyers[e["deal_id"]]
            price = offer["price_per_tonne_usd"]
            testcase.assertGreaterEqual(price, seller["min_acceptable_price_per_tonne_usd"])
            testcase.assertLessEqual(price, buyer["max_acceptable_price_per_tonne_usd"])
            testcase.assertEqual(e["validator"]["gate"], "deal_legal")

    # results arithmetic
    def net(price, qty):
        return round((price - freight - HANDLING - PROCESSING) * qty, 2)
    results = {e["deal_id"]: e["payload"] for e in events if e["type"] == "thread_result"}
    for deal, r in results.items():
        testcase.assertIn(r["status"], ("accepted", "countered", "rejected"))
        if r["price_per_tonne_usd"] is not None:
            testcase.assertEqual(r["total_net_value_usd"], net(r["price_per_tonne_usd"], r["quantity_tonnes"]))
    if results:
        eligible = {d: r for d, r in results.items() if r["status"] in ("accepted", "countered")}
        if eligible:
            winner = max(eligible, key=lambda d: eligible[d]["total_net_value_usd"])
            closed = next((e for e in events if e["type"] == "deal_closed"), None)
            if closed:
                testcase.assertEqual(closed["deal_id"], winner)
            released = {e["deal_id"] for e in events if e["type"] == "released"}
            testcase.assertEqual(released, set(eligible) - {winner})
            if any(e["type"] == "recommendation" for e in events):
                rec = next(e for e in events if e["type"] == "recommendation")["payload"]
                w = results[winner]
                testcase.assertEqual(rec["buyer_id"], winner)
                testcase.assertEqual(rec["price_per_tonne_usd"], w["price_per_tonne_usd"])

    # summary counts
    summary = next((e for e in events if e["type"] in ("run_completed", "run_failed")), None)
    if summary and summary["type"] == "run_completed":
        body = events[:-1]
        testcase.assertEqual(summary["payload"]["llm_calls"], sum(1 for e in body if e["model"]))
        testcase.assertEqual(summary["payload"]["validator_bounces"], sum(1 for e in body if e["delivered"] is False))
        testcase.assertEqual(summary["payload"]["fallbacks"], sum(1 for e in body if e["type"] == "fallback"))
