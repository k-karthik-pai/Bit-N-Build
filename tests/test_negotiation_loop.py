"""Offline tests for the Step 1 validator-gated loop (no API calls)."""
import json
import os
import re
import unittest
from unittest.mock import patch
from pathlib import Path

from agents.negotiation import (
    negotiate, _parse_limits, _get_limit, _bucket_key, _base_provider,
    _is_rpd_exhausted, _record_rpd, _call_llm_for_move, _NEXT_SLOT, _RPD_COUNTS,
)
from tests.helpers_events import assert_events_valid
import agents.negotiation as neg_module
import orchestrator as orch_module

ROOT = Path(__file__).resolve().parents[1]

def seller_constraints(floor=22, pref=25, avail=100000, cmin=6, cmax=36, pref_m=24):
    return {
        "min_acceptable_price_per_tonne_usd": floor,
        "preferred_price_per_tonne_usd": pref,
        "available_quantity_tonnes": avail,
        "contract_months_min": cmin,
        "contract_months_max": cmax,
        "preferred_contract_months": pref_m,
    }

def buyer(buyer_id="shah_cement", ceiling=28, demand=65000, cmin=12, cmax=36):
    return {
        "buyer_id": buyer_id,
        "max_acceptable_price_per_tonne_usd": ceiling,
        "annual_demand_tonnes": demand,
        "contract_months_min": cmin,
        "contract_months_max": cmax,
    }

class NegotiationLoopTests(unittest.TestCase):
    def setUp(self):
        # reset pacing state
        _NEXT_SLOT.clear()
        _RPD_COUNTS.clear()
        neg_module._RPD_DATE = ""
        # R2-1: the suite must never touch the network. Blank every provider
        # key, alias key, generic fallback key, and chain — load_dotenv() at
        # import time does not override already-set vars, so setting these to
        # "" here is what wins for the duration of each test. (Verified: the
        # proxy-blocked run in the verification step passes at the same speed.)
        env_overrides = {
            "GEMINI_API_KEY": "", "GEMINI_2_API_KEY": "", "GEMINI_3_API_KEY": "",
            "OPENROUTER_API_KEY": "", "OPENROUTER_2_API_KEY": "", "OPENROUTER_3_API_KEY": "",
            "OPENROUTER_4_API_KEY": "",
            "NVIDIA_API_KEY": "", "NVIDIA_2_API_KEY": "", "NVIDIA_3_API_KEY": "",
            "OPENAI_API_KEY": "", "LLM_API_KEY": "", "NEGOTIATION_LLM_MODEL": "",
            "SELLER_LLM_CHAIN": "", "BUYER_LLM_CHAIN": "",
            "BUYER_1_LLM_CHAIN": "", "BUYER_2_LLM_CHAIN": "", "BUYER_3_LLM_CHAIN": "",
            # never let RPD persistence touch the real (shared) runs/.quota.json
            "NEGOTIATION_QUOTA_FILE": str(ROOT / "runs" / f".quota-test-{id(self)}.json"),
        }
        self._patch_env = patch.dict("os.environ", env_overrides, clear=False)
        self._patch_env.start()
        self.addCleanup(self._patch_env.stop)
        # Loud tripwire: if any code path still tries to reach a provider
        # despite the blanked env/chains above, fail immediately instead of
        # silently making a real network call.
        self._patch_llm_call = patch(
            "agents.negotiation._call_llm_for_move",
            side_effect=AssertionError("network call in test"),
        )
        self._patch_llm_call.start()
        self.addCleanup(self._patch_llm_call.stop)
        self.addCleanup(self._cleanup_quota_file)

    def _cleanup_quota_file(self):
        path = Path(os.environ.get("NEGOTIATION_QUOTA_FILE", ""))
        for p in (path, path.with_name(path.name + ".tmp")):
            try:
                p.unlink()
            except OSError:
                pass

    # helpers
    def run_thread(self, seller=None, buyer_obj=None, fake_seller=None, fake_buyer=None, **kw):
        s = seller or seller_constraints()
        b = buyer_obj or buyer()
        events = []
        def emit(e):
            events.append(e)
        # default logistics 7
        result = negotiate(s, b, logistics_cost_per_tonne_usd=7, use_llm=True, emit=emit,
                           _fake_seller_moves=fake_seller, _fake_buyer_moves=fake_buyer, **kw)
        return result, events

    def test_seller_offer_below_own_floor_bounced_only_own_floor(self):
        fake_s = [{"action":"make_offer","price_per_tonne_usd":10,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $10.00/t for 65000 t, 24 months","rationale":"x"}]
        _, events = self.run_thread(fake_seller=fake_s, fake_buyer=[{"action":"make_offer","price_per_tonne_usd":25,"quantity_tonnes":65000,"contract_months":24,"message":"ok $25.00/t for 65000 t, 24 months","rationale":"x"}])
        bounced = [e for e in events if e["delivered"] is False][0]
        self.assertIn("floor", bounced["validator"]["reason"])
        self.assertNotIn("28", bounced["validator"]["reason"])  # buyer ceiling not leaked
        self.assertEqual(bounced["validator"]["gate"], "move_legal")
        self.assertEqual(bounced["to_agent"], bounced["from_agent"])  # bounce returns to author

    def test_buyer_offer_above_own_ceiling_bounced_no_seller_floor_leak(self):
        fake_s = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $26.00/t for 65000 t, 24 months","rationale":"x"}]
        fake_b = [{"action":"make_offer","price_per_tonne_usd":50,"quantity_tonnes":65000,"contract_months":24,"message":"We bid $50.00/t for 65000 t, 24 months","rationale":"x"}]
        _, events = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b)
        bounced = [e for e in events if e["delivered"] is False][0]
        self.assertIn("ceiling", bounced["validator"]["reason"])
        self.assertNotIn("22", bounced["validator"]["reason"])
        # contract months outside own range
        fake_b2 = [{"action":"make_offer","price_per_tonne_usd":25,"quantity_tonnes":65000,"contract_months":48,"message":"We bid $25.00/t for 65000 t, 48 months","rationale":"x"}]
        _, events2 = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b2)
        bounced2 = [e for e in events2 if e["delivered"] is False][0]
        self.assertIn("contract_months", bounced2["validator"]["reason"])
        self.assertNotIn("22", bounced2["validator"]["reason"])

    def test_message_states_own_reservation_bounced(self):
        # seller floor 22, message contains 22
        fake_s = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"Our floor is 22, we offer $26.00/t for 65000 t, 24 months","rationale":"x"}]
        _, events = self.run_thread(fake_seller=fake_s)
        bounced = [e for e in events if e["delivered"] is False][0]
        self.assertIn("own floor", bounced["validator"]["reason"])
        # buyer ceiling 28
        fake_s2 = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $26.00/t for 65000 t, 24 months","rationale":"x"}]
        fake_b2 = [{"action":"make_offer","price_per_tonne_usd":25,"quantity_tonnes":65000,"contract_months":24,"message":"Our ceiling is 28, we bid $25.00/t for 65000 t, 24 months","rationale":"x"}]
        _, events2 = self.run_thread(fake_seller=fake_s2, fake_buyer=fake_b2, buyer_obj=buyer(ceiling=28))
        bounced2 = [e for e in events2 if e["delivered"] is False][0]
        self.assertIn("own ceiling", bounced2["validator"]["reason"])

    def test_message_number_not_in_structured_fields_bounced_digits_and_words(self):
        # digit mismatch
        fake_s = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $26.00/t for 65000 t, 24 months and also 999","rationale":"x"}]
        _, events = self.run_thread(fake_seller=fake_s)
        self.assertTrue(any(e["delivered"] is False for e in events))
        # word mismatch: twenty-nine (29) not in allowed [26,65000,24]
        fake_s2 = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer twenty-nine dollars for 65000 t, 24 months","rationale":"x"}]
        _, events2 = self.run_thread(fake_seller=fake_s2)
        bounced = [e for e in events2 if e["delivered"] is False][0]
        self.assertIn("29", bounced["validator"]["reason"])

    def test_message_with_pronoun_one_and_twelve_month_matching_delivered(self):
        # pronoun "one" should NOT be bounced — first offer delivered
        fake_s = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"This is the best one we can do at $26.00/t for 65000 t, 24 months","rationale":"x"}]
        _, events = self.run_thread(fake_seller=fake_s)
        first = events[0]
        self.assertEqual(first["type"], "offer")
        self.assertTrue(first["delivered"])
        # twelve-month matching contract_months=12 should be delivered
        fake_s2 = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":12,"message":"We offer $26.00/t for 65000 t on a twelve-month term","rationale":"x"}]
        _, events2 = self.run_thread(seller=seller_constraints(cmin=6,cmax=36), buyer_obj=buyer(cmin=6,cmax=36), fake_seller=fake_s2)
        first2 = events2[0]
        self.assertTrue(first2["delivered"])
        self.assertIn("twelve", first2["payload"]["message"].lower())
        # sixty-five thousand = 65000 should be delivered when quantity matches
        fake_s3 = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We can supply sixty-five thousand t at $26.00/t, 24 months","rationale":"x"}]
        _, events3 = self.run_thread(fake_seller=fake_s3)
        first3 = events3[0]
        self.assertTrue(first3["delivered"])

    def test_two_bounces_trigger_fallback(self):
        fake_s = [
            {"action":"make_offer","price_per_tonne_usd":10,"quantity_tonnes":65000,"contract_months":24,"message":"bad $10.00/t for 65000 t, 24 months","rationale":"x"},
            {"action":"make_offer","price_per_tonne_usd":10,"quantity_tonnes":65000,"contract_months":24,"message":"bad again $10.00/t for 65000 t, 24 months","rationale":"x"},
        ]
        _, events = self.run_thread(fake_seller=fake_s)
        fallbacks = [e for e in events if e["type"]=="fallback"]
        self.assertTrue(any(f["payload"]["cause"]=="invalid_moves" for f in fallbacks))
        # next event after fallback should be deterministic move with model null
        idx = events.index(fallbacks[0])
        # find next offer after fallback
        next_offer = next(e for e in events[idx+1:] if e["type"]=="offer")
        self.assertIsNone(next_offer["model"])
        self.assertTrue(next_offer["delivered"])

    def test_accept_stale_own_unknown_offer_rejected(self):
        # seller offers o1, buyer tries to accept stale/unknown
        fake_s = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $26.00/t for 65000 t, 24 months","rationale":"x"}]
        # buyer accept of unknown id
        fake_b = [{"action":"accept_offer","offer_id":"unknown-o99","message":"Accept unknown","rationale":"x"}]
        _, events = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b)
        bounced = [e for e in events if e["type"]=="accept" and e["delivered"] is False]
        self.assertTrue(len(bounced) >= 1)
        self.assertEqual(bounced[0]["validator"]["gate"], "deal_legal")
        # accept of own offer (seller tries to accept own) - we can simulate by having seller's second turn accept its own first offer (but seller's turn is odd, buyer even, so need to craft)
        # Simpler: after seller o1, buyer o2, seller tries to accept o1 (own) instead of buyer o2
        fake_s2 = [
            {"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $26.00/t for 65000 t, 24 months","rationale":"x"},
            {"action":"accept_offer","offer_id":"shah_cement-o1","message":"Accept my own","rationale":"x"},
        ]
        fake_b2 = [{"action":"make_offer","price_per_tonne_usd":25,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $25.00/t for 65000 t, 24 months","rationale":"x"}]
        _, events2 = self.run_thread(fake_seller=fake_s2, fake_buyer=fake_b2)
        bounced2 = [e for e in events2 if e["type"]=="accept" and e["delivered"] is False]
        self.assertTrue(len(bounced2) >= 1)

    def test_accept_latest_counterparty_offer_accepted(self):
        fake_s = [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $26.00/t for 65000 t, 24 months","rationale":"x"}]
        fake_b = [
            {"action":"make_offer","price_per_tonne_usd":25,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $25.00/t for 65000 t, 24 months","rationale":"x"},
            {"action":"accept_offer","offer_id":"shah_cement-o3","message":"Accepted $26.00/t","rationale":"x"},
        ]
        # need seller second offer o3 after buyer o2
        # sequence: round1 seller o1, round2 buyer o2, round3 seller o3, round4 buyer accept o3
        fake_s_extended = fake_s + [{"action":"make_offer","price_per_tonne_usd":26,"quantity_tonnes":65000,"contract_months":24,"message":"We offer $26.00/t for 65000 t, 24 months second","rationale":"x"}]
        result, events = self.run_thread(fake_seller=fake_s_extended, fake_buyer=fake_b)
        # should be accepted
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["price_per_tonne_usd"], 26)
        self.assertEqual(result["final_offer_id"], "shah_cement-o3")

    def test_seller_better_offer_claim_no_live_offer_bounced_alone(self):
        # thread runs alone (shared_state empty or None) -> claim should bounce
        fake_s = [{"action":"make_offer","price_per_tonne_usd":27,"quantity_tonnes":65000,"contract_months":24,"message":"We have a better offer elsewhere, so $27.00/t for 65000 t, 24 months","rationale":"x"}]
        result, events = self.run_thread(fake_seller=fake_s, buyer_obj=buyer())
        bounced = [e for e in events if e["delivered"] is False]
        self.assertTrue(any("better offer" in e["validator"]["reason"] for e in bounced))
        s = seller_constraints()
        b = buyer()
        evs = []
        def emit(e): evs.append(e)
        negotiate(s,b, logistics_cost_per_tonne_usd=7, use_llm=True, emit=emit, _fake_seller_moves=fake_s, _shared_state={})
        bounced2 = [e for e in evs if e["delivered"] is False]
        self.assertTrue(any("better offer" in e["validator"]["reason"] for e in bounced2))
        # with qualifying live offer AND price above dynamic floor it should be delivered
        # other total net = (30-7-3.5)*100000=1.95M > cur (36-7-3.5)*65000=1.65M, so has_better true, floor 30, cur price 36 passes floor
        # _role must be buyer_agent: only a live BUYER bid elsewhere qualifies as
        # "a better offer" (R2-2) — a seller ask tagged here would not count.
        shared = {"other_buyer": [{"price_per_tonne_usd":30,"quantity_tonnes":100000,"contract_months":24,"_logistics_cost":7,"_role":"buyer_agent"}]}
        evs3 = []
        def emit3(e): evs3.append(e)
        fake_s_ok = [{"action":"make_offer","price_per_tonne_usd":36,"quantity_tonnes":65000,"contract_months":24,"message":"We have a better offer elsewhere, so $36.00/t for 65000 t, 24 months","rationale":"x"}]
        # need buyer ceiling high enough for 36
        b_high = buyer(buyer_id="shah_cement", ceiling=40, demand=65000)
        negotiate(s,b_high, logistics_cost_per_tonne_usd=7, use_llm=True, emit=emit3, _fake_seller_moves=fake_s_ok, _shared_state=shared)
        first_offer = evs3[0]
        self.assertTrue(first_offer["delivered"])
        self.assertEqual(first_offer["validator"]["gate"], "move_legal")

    def test_full_offline_concurrent_run_passes_helpers(self):
        seller = json.loads((ROOT / "data/seller.json").read_text())
        buyers = {b["buyer_id"]: b for b in json.loads((ROOT / "data/buyers.json").read_text())}
        route = None
        # run orchestrator offline concurrent (use_llm=False)
        events = []
        def emit(ev):
            events.append(ev)
        # use top_n 3
        from orchestrator import run_pipeline
        run_pipeline(use_llm=False, top_n=3, emit_event=emit)
        # find freight from route event
        route_ev = next(e for e in events if e["type"]=="route")
        freight = route_ev["payload"]["cost_per_tonne_usd"]
        assert_events_valid(self, events, seller, buyers, freight)

    def test_pacing_parser_and_rpd(self):
        # per-model RPM
        with patch.dict("os.environ", {"GEMINI_RPM":"gemini-3.1-flash-lite:15,gemini-3.5-flash:5"}):
            self.assertEqual(_get_limit("gemini", "gemini-3.1-flash-lite", "RPM"), 15)
            self.assertEqual(_get_limit("gemini", "gemini-3.5-flash", "RPM"), 5)
            self.assertIsNone(_get_limit("gemini", "unknown-model", "RPM"))
        # provider-wide
        with patch.dict("os.environ", {"OPENROUTER_RPM":"20"}):
            self.assertEqual(_get_limit("openrouter", "any-model", "RPM"), 20)
            self.assertEqual(_get_limit("openrouter", "other", "RPM"), 20)
            self.assertEqual(_bucket_key("openrouter","any-model"), "openrouter")
        with patch.dict("os.environ", {"GEMINI_RPD":"gemini-3.1-flash-lite:500"}):
            self.assertEqual(_get_limit("gemini", "gemini-3.1-flash-lite", "RPD"), 500)
        # per-model bucket
        self.assertEqual(_bucket_key("gemini","gemini-3.1-flash-lite"), "gemini:gemini-3.1-flash-lite")
        self.assertEqual(_bucket_key("nvidia",None), "nvidia")
        # RPD exhaustion
        with patch.dict("os.environ", {"OPENROUTER_RPD":"1"}):
            _RPD_COUNTS.clear()
            # first call not exhausted
            self.assertFalse(_is_rpd_exhausted("openrouter","any"))
            _record_rpd("openrouter","any")
            self.assertTrue(_is_rpd_exhausted("openrouter","any"))
            # different bucket not exhausted
            self.assertFalse(_is_rpd_exhausted("gemini","gemini-3.1-flash-lite"))
        # pacing with fake clock
        import time
        _NEXT_SLOT.clear()
        with patch.dict("os.environ", {"OPENROUTER_RPM":"60"}):  # 1 per sec
            with patch("time.monotonic", side_effect=[0,0,0.5,0.5]):
                with patch("time.sleep") as mock_sleep:
                    # import inside to use patched monotonic
                    from agents.negotiation import _pace_provider
                    _NEXT_SLOT.clear()
                    _pace_provider("openrouter","any")
                    # first call slot =0, no sleep
                    mock_sleep.assert_not_called()
                    # second call at 0, slot reserved at 1, should sleep 1
                    _pace_provider("openrouter","any")
                    mock_sleep.assert_called()
                    args, _ = mock_sleep.call_args
                    self.assertAlmostEqual(args[0], 1.0, delta=0.1)

    # -- R2-2: dynamic floor / leverage must only count live BUYER bids -----

    def test_dynamic_floor_only_counts_other_threads_buyer_bids_not_seller_asks(self):
        # doc example: seller anchors at $29 in thread A (a seller ASK — must
        # NOT raise thread B's floor); buyer A bids $24 (a buyer BID — SHOULD
        # raise it). floor for thread B = max(22, 24-7-3.5+7+3.5) = 24, not 29.
        # contract_months uses 18 (not 24) so it can never collide with the
        # $24 floor value in the own-reservation-leak check below.
        shared = {
            "thread_a": [
                {"price_per_tonne_usd": 29, "quantity_tonnes": 65000, "contract_months": 18,
                 "_logistics_cost": 7, "_role": "seller_agent"},
                {"price_per_tonne_usd": 24, "quantity_tonnes": 65000, "contract_months": 18,
                 "_logistics_cost": 7, "_role": "buyer_agent"},
            ],
        }
        # seller's own move at $25 sits strictly between the correct floor (24)
        # and the bugged floor (29): delivered only if the fix is in place.
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 25, "quantity_tonnes": 65000,
                   "contract_months": 18, "message": "We offer $25.00/t for 65000 t, 18 months",
                   "rationale": "x"}]
        _, events = self.run_thread(seller=seller_constraints(floor=22),
                                     buyer_obj=buyer(buyer_id="thread_b", ceiling=28),
                                     fake_seller=fake_s, _shared_state=shared)
        first = events[0]
        self.assertEqual(first["type"], "offer")
        self.assertTrue(first["delivered"], first.get("validator"))
        self.assertEqual(first["validator"]["gate"], "move_legal")

    def test_leverage_better_offer_claim_ignores_sellers_own_asks_elsewhere(self):
        # a seller ask in another thread must never justify "we have a better
        # offer elsewhere" — EVENTS.md requires a live BUYER bid (R2-2 blind spot
        # in the leverage check, not just _dynamic_floor).
        shared = {
            "other_buyer": [
                {"price_per_tonne_usd": 40, "quantity_tonnes": 100000, "contract_months": 24,
                 "_logistics_cost": 7, "_role": "seller_agent"},
            ],
        }
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 24,
                   "message": "We have a better offer elsewhere, so $26.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s, _shared_state=shared)
        bounced = [e for e in events if e["delivered"] is False]
        self.assertTrue(any("better offer" in e["validator"]["reason"] for e in bounced))

    # -- R2-3: countered below the seller's final floor must reject instead --

    def test_max_rounds_rejects_instead_of_countered_when_best_below_final_floor(self):
        shared: dict = {}

        def buyer_move_with_side_effect():
            # simulate a competing thread's buyer bid arriving mid-negotiation,
            # raising this thread's dynamic floor above the price already on
            # the table by the time max_rounds is reached.
            shared["competitor"] = [{"price_per_tonne_usd": 30, "quantity_tonnes": 65000,
                                      "contract_months": 24, "_logistics_cost": 7,
                                      "_role": "buyer_agent"}]
            return {"action": "make_offer", "price_per_tonne_usd": 24, "quantity_tonnes": 65000,
                    "contract_months": 24, "message": "We offer $24.00/t for 65000 t, 24 months",
                    "rationale": "x"}

        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 25, "quantity_tonnes": 65000,
                   "contract_months": 24, "message": "We offer $25.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        result, events = self.run_thread(
            seller=seller_constraints(floor=22), buyer_obj=buyer(buyer_id="buyer_b", ceiling=28),
            fake_seller=fake_s, fake_buyer=[buyer_move_with_side_effect],
            max_rounds=2, _shared_state=shared,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["price_per_tonne_usd"])
        self.assertIn("floor", result["validator_reason"])

    # -- R2-6: provider aliases (own key/bucket/limits, base URL/quirks) -----

    def test_alias_base_provider_and_bucket_and_limit_inheritance(self):
        self.assertEqual(_base_provider("openrouter_2"), "openrouter")
        self.assertEqual(_base_provider("gemini_3"), "gemini")
        self.assertEqual(_base_provider("nvidia_2"), "nvidia")
        self.assertEqual(_base_provider("openrouter"), "openrouter")
        self.assertEqual(_base_provider("unknown_9"), "unknown_9")  # unknown base: not an alias

        # bucket keyed on the ALIAS itself (own counters), shaped by the base
        self.assertEqual(_bucket_key("openrouter_2", "any-model"), "openrouter_2")
        self.assertEqual(_bucket_key("gemini_3", "gemini-3.1-flash-lite"), "gemini_3:gemini-3.1-flash-lite")
        self.assertEqual(_bucket_key("nvidia_2", None), "nvidia_2")

        # an alias's own limit wins over the base's
        with patch.dict("os.environ", {"OPENROUTER_RPM": "20", "OPENROUTER_2_RPM": "5"}):
            self.assertEqual(_get_limit("openrouter_2", "any", "RPM"), 5)
        # unset alias limit inherits the base provider's VALUE
        with patch.dict("os.environ", {"OPENROUTER_RPM": "20", "OPENROUTER_2_RPM": ""}):
            self.assertEqual(_get_limit("openrouter_2", "any", "RPM"), 20)
        with patch.dict("os.environ", {"GEMINI_RPM": "gemini-3.1-flash-lite:15", "GEMINI_2_RPM": ""}):
            self.assertEqual(_get_limit("gemini_2", "gemini-3.1-flash-lite", "RPM"), 15)

        # separate RPD buckets: exhausting the base does not exhaust the alias
        with patch.dict("os.environ", {"OPENROUTER_RPD": "1"}):
            _RPD_COUNTS.clear()
            _record_rpd("openrouter", "any")
            self.assertTrue(_is_rpd_exhausted("openrouter", "any"))
            self.assertFalse(_is_rpd_exhausted("openrouter_2", "any"))

    def test_alias_with_empty_key_skipped_without_a_call(self):
        # OPENROUTER_4_API_KEY is blanked by setUp; must short-circuit before
        # ever touching the network. Calls the real (unpatched-by-name)
        # function directly, not through the module attribute the class-level
        # tripwire patches.
        move, reason = _call_llm_for_move("openrouter_4", "some/model:free", "sys", [])
        self.assertIsNone(move)
        self.assertEqual(reason, "no_api_key")

    def test_alias_pacing_uses_its_own_bucket_with_fake_clock(self):
        from agents.negotiation import _pace_provider
        _NEXT_SLOT.clear()
        clock = {"t": 0.0}
        with patch.dict("os.environ", {"OPENROUTER_RPM": "60", "OPENROUTER_2_RPM": "60"}):
            with patch("time.monotonic", side_effect=lambda: clock["t"]):
                with patch("time.sleep") as mock_sleep:
                    _pace_provider("openrouter", "any")
                    mock_sleep.assert_not_called()
                    # openrouter_2 is a SEPARATE bucket: must not inherit the
                    # slot the base provider just reserved.
                    _pace_provider("openrouter_2", "any")
                    mock_sleep.assert_not_called()
                    # a second call on the SAME alias bucket, same instant,
                    # must now wait for its own reserved slot.
                    _pace_provider("openrouter_2", "any")
                    mock_sleep.assert_called_once()
                    args, _ = mock_sleep.call_args
                    self.assertAlmostEqual(args[0], 1.0, delta=0.1)

    # -- R2-4: quota persistence + accurate fallback cause -------------------

    def test_rpd_persists_across_in_process_reset_via_quota_file(self):
        # simulates a second "process" sharing runs/.quota.json: clearing the
        # in-process counters must not un-exhaust an already-persisted bucket.
        with patch.dict("os.environ", {"NVIDIA_RPD": "1"}):
            _RPD_COUNTS.clear()
            self.assertFalse(_is_rpd_exhausted("nvidia", "some-model"))
            _record_rpd("nvidia", "some-model")
            self.assertTrue(_is_rpd_exhausted("nvidia", "some-model"))
            _RPD_COUNTS.clear()  # "fresh process": in-memory counters gone
            self.assertTrue(_is_rpd_exhausted("nvidia", "some-model"))

    def test_classify_timeout_exception_returns_timeout(self):
        from agents.negotiation import _classify_llm_exception
        reason = _classify_llm_exception(Exception("Request timed out"), "gemini", "gemini-3.1-flash-lite")
        self.assertEqual(reason, "timeout")

    def test_classify_openrouter_daily_429_marks_alias_quota_exhausted(self):
        from agents.negotiation import _classify_llm_exception, _is_quota_exhausted_now

        class FakeResponse:
            headers = {"X-RateLimit-Reset": "9999999999999"}  # far-future ms epoch

        class FakeExc(Exception):
            status_code = 429
            response = FakeResponse()
            body = {"error": {"message": "Rate limit exceeded: free-models-per-day"}}

        self.assertFalse(_is_quota_exhausted_now("openrouter_2"))
        reason = _classify_llm_exception(FakeExc("429 rate limit"), "openrouter_2", "some/model:free")
        self.assertNotEqual(reason, "timeout")
        self.assertIn("free-models-per-day", reason)
        # marked exhausted for the ALIAS bucket only, not the base provider
        self.assertTrue(_is_quota_exhausted_now("openrouter_2"))
        self.assertFalse(_is_quota_exhausted_now("openrouter"))

    def test_fallback_cause_is_provider_error_not_timeout_for_classified_failure(self):
        # stop the loud tripwire for this one test and substitute a controlled
        # failure so we can assert the fallback event's cause/detail reflect
        # the real reason instead of a generic "timeout".
        self._patch_llm_call.stop()
        try:
            with patch(
                "agents.negotiation._call_llm_for_move",
                return_value=(None, "429 free-models-per-day exhausted for openrouter_2 (resets 2026-09-13T00:00:00Z)"),
            ):
                with patch.dict("os.environ", {"SELLER_LLM_CHAIN": "openrouter_2:some/model:free"}):
                    _, events = self.run_thread(max_rounds=1)
        finally:
            self._patch_llm_call.start()
        fallback = next(e for e in events if e["type"] == "fallback")
        self.assertEqual(fallback["payload"]["cause"], "provider_error")
        self.assertIn("free-models-per-day", fallback["payload"]["detail"])

    # -- R3-1: message numbers may quote public thread history -------------

    def test_quoting_counterparty_earlier_price_delivered(self):
        # round 1: seller delivers $26.00/t, 65000t, 20 months (own history).
        # round 2: buyer's message quotes that price while proposing its own
        # $23.00/t, 65000t, 20 months — 26.00 is covered by thread history
        # (R3-1), not the buyer's own fields, so it must NOT bounce.
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 20, "message": "We offer $26.00/t for 65000 t, 20 months",
                   "rationale": "x"}]
        fake_b = [{"action": "make_offer", "price_per_tonne_usd": 23, "quantity_tonnes": 65000,
                   "contract_months": 20,
                   "message": "You offered $26.00/t, but we propose $23.00/t for 65000 t, 20 months",
                   "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b)
        buyer_offer = next(e for e in events if e["from_agent"].startswith("buyer_agent") and e["type"] == "offer")
        self.assertTrue(buyer_offer["delivered"], buyer_offer.get("validator"))

    def test_number_from_a_different_thread_not_delivered_here_bounced(self):
        # a number that was delivered in ANOTHER thread (via shared_state)
        # but never in THIS thread must still bounce — history is thread-
        # scoped, not shared_state-scoped.
        shared = {"other_buyer": [{"price_per_tonne_usd": 31, "quantity_tonnes": 50000,
                                    "contract_months": 18, "_logistics_cost": 7,
                                    "_role": "buyer_agent"}]}
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 20,
                   "message": "Other buyers got $31.00/t but we offer $26.00/t for 65000 t, 20 months",
                   "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s, _shared_state=shared)
        first = events[0]
        self.assertFalse(first["delivered"])
        self.assertIn("31", first["validator"]["reason"])

    def test_bounced_offer_never_counts_as_history(self):
        # seller's first attempt (price 19, below floor 22) bounces and must
        # NOT become history; a valid retry delivers at 26. The buyer then
        # quotes 19 (the bounced attempt) instead of 26 (the real delivered
        # price) — 19 is covered by neither the buyer's own fields nor real
        # thread history, so it must still bounce.
        fake_s = [
            {"action": "make_offer", "price_per_tonne_usd": 19, "quantity_tonnes": 65000,
             "contract_months": 20, "message": "We offer $19.00/t for 65000 t, 20 months",
             "rationale": "x"},
            {"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
             "contract_months": 20, "message": "We offer $26.00/t for 65000 t, 20 months",
             "rationale": "x"},
        ]
        fake_b = [{"action": "make_offer", "price_per_tonne_usd": 24, "quantity_tonnes": 65000,
                   "contract_months": 20,
                   "message": "You mentioned $19.00/t but we propose $24.00/t for 65000 t, 20 months",
                   "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b)
        buyer_offer = next(e for e in events if e["from_agent"].startswith("buyer_agent") and e["type"] == "offer")
        self.assertFalse(buyer_offer["delivered"])
        self.assertIn("19", buyer_offer["validator"]["reason"])

    def test_accept_message_may_quote_thread_history_price(self):
        # accept message quotes both the accepted offer's own price (26.00)
        # and an EARLIER offer's price in the same thread (28.50, the
        # seller's opening anchor) - both covered by R3-1 (applies to
        # accept_offer's deal-legal number check too, not just make_offer).
        fake_s = [
            {"action": "make_offer", "price_per_tonne_usd": 28.5, "quantity_tonnes": 65000,
             "contract_months": 20, "message": "We offer $28.50/t for 65000 t, 20 months",
             "rationale": "x"},
            {"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
             "contract_months": 20, "message": "We offer $26.00/t for 65000 t, 20 months",
             "rationale": "x"},
        ]
        fake_b = [
            {"action": "make_offer", "price_per_tonne_usd": 25, "quantity_tonnes": 65000,
             "contract_months": 20, "message": "We propose $25.00/t for 65000 t, 20 months",
             "rationale": "x"},
            {"action": "accept_offer", "offer_id": "shah_cement-o3",
             "message": "We accept your $26.00/t, down from the $28.50/t you opened at.",
             "rationale": "x"},
        ]
        result, events = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b)
        self.assertEqual(result["status"], "accepted")
        accept_ev = next(e for e in events if e["type"] == "accept")
        self.assertTrue(accept_ev["delivered"], accept_ev.get("validator"))
        self.assertEqual(accept_ev["validator"]["gate"], "deal_legal")

    # -- R3-2: leak check exempts numbers covered by R3-1 --------------------

    def test_message_number_matching_dynamic_floor_via_own_field_not_a_leak(self):
        # dynamic floor pinned to $24.0 via a live buyer bid in another
        # thread (both routes freight 7, R2-2 pass-through). contract_months
        # is also 24, coinciding with the floor value — R3-2 says that
        # coincidence is not a leak since 24 is covered by the move's own
        # structured fields (R3-1). This is the exact false positive found in
        # Round 2's run-e1cdc425.
        shared = {"other_buyer": [{"price_per_tonne_usd": 24, "quantity_tonnes": 65000,
                                    "contract_months": 18, "_logistics_cost": 7,
                                    "_role": "buyer_agent"}]}
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 24, "message": "We offer $26.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        _, events = self.run_thread(seller=seller_constraints(floor=22), fake_seller=fake_s, _shared_state=shared)
        first = events[0]
        self.assertTrue(first["delivered"], first.get("validator"))

    def test_message_number_not_covered_still_bounced_mismatch_or_leak(self):
        # a number not covered by own fields/history still bounces regardless
        # of whether it happens to equal a reservation value — R3-2 narrows
        # the leak check, it doesn't loosen the base number-match rule.
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 24,
                   "message": "Our floor is 24.50, we offer $26.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        _, events = self.run_thread(seller=seller_constraints(floor=22), fake_seller=fake_s)
        first = events[0]
        self.assertFalse(first["delivered"])
        self.assertIn("24.5", first["validator"]["reason"])

    # -- R3-4: per-base-provider timeout, aliases inherit --------------------

    def test_provider_timeout_alias_inherits_from_base_default_global(self):
        from agents.negotiation import _provider_timeout_s, LLM_TIMEOUT_S
        with patch.dict("os.environ", {"NVIDIA_TIMEOUT_S": "10"}):
            self.assertEqual(_provider_timeout_s("nvidia"), 10.0)
            self.assertEqual(_provider_timeout_s("nvidia_2"), 10.0)  # alias inherits the base's value
            self.assertEqual(_provider_timeout_s("nvidia_3"), 10.0)
        with patch.dict("os.environ", {"NVIDIA_TIMEOUT_S": ""}):
            self.assertEqual(_provider_timeout_s("nvidia"), LLM_TIMEOUT_S)
        # a provider with no override uses the global default
        self.assertEqual(_provider_timeout_s("gemini"), LLM_TIMEOUT_S)

    def test_call_llm_passes_per_base_provider_timeout_to_client(self):
        # mocked call, no network: capture the kwargs _call_llm_for_move
        # builds for the openai client and confirm the alias's BASE provider
        # timeout override reaches it.
        captured: dict = {}

        class FakeCompletions:
            def create(self, **kwargs):
                raise RuntimeError("stop before any network call")

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch.dict("os.environ", {"NVIDIA_2_API_KEY": "test-only-placeholder-not-a-real-key",
                                        "NVIDIA_TIMEOUT_S": "10"}):
            with patch("openai.OpenAI", FakeClient):
                move, reason = _call_llm_for_move("nvidia_2", "deepseek-x", "sys", [])
        self.assertIsNone(move)
        self.assertEqual(captured.get("timeout"), 10.0)

    def test_helpers_events_leak_check_exempts_own_constraint_not_real_leaks(self):
        # tests/helpers_events.py's "leaks counterparty bound" check must
        # exempt a number that is the author's own (possibly dynamic, R2-2)
        # constraint self-referenced as "your floor/ceiling/range ..." even
        # when it numerically coincides with the counterparty's static bound
        # (confirmed in run-dc3d40f5: seller floor raised to 27.0 by a live
        # buyer bid elsewhere, same value as a DIFFERENT buyer's ceiling) -
        # but a reason phrased as the counterparty's own bound must still fail.
        from tests.helpers_events import own_constraint_numbers
        self.assertEqual(own_constraint_numbers("price 26.25 is below your floor 27.0"), {27.0})
        self.assertEqual(own_constraint_numbers("contract_months 24 is outside your range 6-36"), {6.0, 36.0})
        self.assertEqual(own_constraint_numbers("price 26.0 is above buyer ceiling 27.0"), set())

    def test_timeout_advances_chain_before_fallback_no_network(self):
        # a per-call timeout on one chain entry must advance to the next
        # model, not go straight to the deterministic fallback — only when
        # every chain entry fails does a `fallback` event fire (R3-4).
        self._patch_llm_call.stop()
        attempted = []

        def fake_call(provider, model, *_a, **_k):
            attempted.append(provider)
            if provider == "nvidia":
                return None, "timeout"
            return ({"action": "make_offer", "price_per_tonne_usd": 25, "quantity_tonnes": 65000,
                     "contract_months": 24, "message": "We offer $25.00/t for 65000 t, 24 months",
                     "rationale": "x"}, None)

        try:
            with patch("agents.negotiation._call_llm_for_move", side_effect=fake_call):
                with patch.dict("os.environ", {"SELLER_LLM_CHAIN": "nvidia:deepseek-x,gemini:gemini-3.1-flash-lite"}):
                    _, events = self.run_thread(max_rounds=1)
        finally:
            self._patch_llm_call.start()
        self.assertEqual(attempted, ["nvidia", "gemini"])
        self.assertFalse(any(e["type"] == "fallback" for e in events))
        first = events[0]
        self.assertEqual(first["type"], "offer")
        self.assertTrue(first["delivered"])
        self.assertEqual(first["model"], "gemini:gemini-3.1-flash-lite")

    # -- R4-1: quantity mismatch / actionable own-bound accept reasons -------

    def test_buyer_accept_over_demand_bounce_reason_actionable_no_seller_floor_leak(self):
        # seller offers 70000 t — within its own availability (default
        # 100000) — but the default buyer's own demand is only 65000 t.
        # Accepting must bounce at deal_legal with an actionable reason that
        # cites only the buyer's own demand, never the seller's floor
        # (run-dc3d40f5, Shah's thread: exactly this quantity mismatch).
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 70000,
                   "contract_months": 24, "message": "We offer $26.00/t for 70000 t, 24 months",
                   "rationale": "x"}]
        fake_b = [{"action": "accept_offer", "offer_id": "shah_cement-o1",
                   "message": "We accept", "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b)
        bounced = [e for e in events if e["type"] == "accept" and e["delivered"] is False]
        self.assertTrue(bounced)
        reason = bounced[0]["validator"]["reason"]
        self.assertEqual(bounced[0]["validator"]["gate"], "deal_legal")
        self.assertIn("counter", reason)
        self.assertIn("your demand", reason)
        self.assertNotIn("floor", reason)
        self.assertNotIn("22", reason)  # seller's floor (seller_constraints() default)

    def test_prompts_contain_quantity_guidance(self):
        from agents.negotiation.prompts import SELLER_TACTICS, BUYER_TACTICS
        self.assertIn("buyer's latest requested quantity", SELLER_TACTICS)
        self.assertIn("Never accept an offer whose quantity exceeds your own demand", BUYER_TACTICS)

    # -- R4-2: buyer deterministic fallback concession schedule -------------

    def test_buyer_fallback_concession_schedule_shrinking_steps_capped(self):
        from agents.negotiation import _deterministic_fallback_offer
        b = buyer(ceiling=28, demand=65000)
        s = seller_constraints()
        history: list = []
        prices = []
        for round_num in range(1, 8):
            move = _deterministic_fallback_offer(
                "buyer_agent", s, b, s["min_acceptable_price_per_tonne_usd"], 7, round_num, history,
            )
            prices.append(move["price_per_tonne_usd"])
            history.append({**move, "_role": "buyer_agent"})
        # never jumps straight to ceiling - 1.0 on the first fallback bid
        self.assertNotAlmostEqual(prices[0], 27.0, places=2)
        # opens at round(0.85 x ceiling, 2)
        self.assertAlmostEqual(prices[0], 23.8, places=2)
        # first four steps match next = last_bid + 0.4 x (ceiling - last_bid)
        self.assertAlmostEqual(prices[1], 25.48, places=2)
        self.assertAlmostEqual(prices[2], 26.49, places=2)
        self.assertAlmostEqual(prices[3], 27.09, places=2)
        self.assertAlmostEqual(prices[4], 27.45, places=2)
        # strictly increasing until the cap binds, then flat at the cap
        for a, c in zip(prices, prices[1:]):
            self.assertLessEqual(a, c + 1e-9)
        rising_steps = [round(c - a, 4) for a, c in zip(prices, prices[1:]) if c - a > 1e-9]
        for s1, s2 in zip(rising_steps, rising_steps[1:]):
            self.assertGreaterEqual(s1 + 1e-9, s2)  # shrinking steps
        # capped at ceiling - 0.5, always strictly below the ceiling itself
        for p in prices:
            self.assertLessEqual(p, 27.5 + 1e-9)
            self.assertLess(p, 28)

    def test_buyer_fallback_offer_uses_gradual_schedule_not_ceiling_minus_one(self):
        # end-to-end through the validator loop: two consecutive invalid
        # buyer moves force the buyer's deterministic fallback offer, which
        # must open at round(0.85 x ceiling, 2) = 23.80, not ceiling - 1.0 (27.0).
        b = buyer(ceiling=28, demand=65000)
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 24, "message": "We offer $26.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        fake_b = [
            {"action": "make_offer", "price_per_tonne_usd": 50, "quantity_tonnes": 65000,
             "contract_months": 24, "message": "bad $50.00/t for 65000 t, 24 months", "rationale": "x"},
            {"action": "make_offer", "price_per_tonne_usd": 50, "quantity_tonnes": 65000,
             "contract_months": 24, "message": "bad again $50.00/t for 65000 t, 24 months", "rationale": "x"},
        ]
        _, events = self.run_thread(buyer_obj=b, fake_seller=fake_s, fake_buyer=fake_b, max_rounds=2)
        fallback_offer = next(e for e in events if e["type"] == "offer" and e["model"] is None and e["delivered"])
        price = fallback_offer["payload"]["price_per_tonne_usd"]
        self.assertAlmostEqual(price, 23.8, places=2)
        self.assertNotAlmostEqual(price, 27.0, places=2)

    # -- R4-3: route freight allowed in message numbers ----------------------

    def test_message_quoting_route_freight_delivered(self):
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 24,
                   "message": "Our price includes $7.00/t freight; we offer $26.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s)  # run_thread uses logistics_cost_per_tonne_usd=7
        first = events[0]
        self.assertTrue(first["delivered"], first.get("validator"))

    def test_message_quoting_other_route_figures_still_bounced(self):
        # distance/transit days are NOT covered by the freight amendment.
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 24,
                   "message": "Route distance is 1200 km, we offer $26.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s)
        first = events[0]
        self.assertFalse(first["delivered"])
        self.assertIn("1200", first["validator"]["reason"])

    def test_accept_message_quoting_route_freight_delivered(self):
        fake_s = [{"action": "make_offer", "price_per_tonne_usd": 26, "quantity_tonnes": 65000,
                   "contract_months": 24, "message": "We offer $26.00/t for 65000 t, 24 months",
                   "rationale": "x"}]
        fake_b = [{"action": "accept_offer", "offer_id": "shah_cement-o1",
                   "message": "We accept your $26.00/t, freight is $7.00/t on top.",
                   "rationale": "x"}]
        _, events = self.run_thread(fake_seller=fake_s, fake_buyer=fake_b)
        accept_ev = next(e for e in events if e["type"] == "accept")
        self.assertTrue(accept_ev["delivered"], accept_ev.get("validator"))
        self.assertEqual(accept_ev["validator"]["gate"], "deal_legal")


if __name__ == "__main__":
    unittest.main()
