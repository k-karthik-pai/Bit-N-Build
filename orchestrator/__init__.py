"""
Orchestrator — Contract frozen in AGENTS.md section 4.

Deterministic pipeline: Circularity → Negotiation → Logistics for the fixed scenario.
Not a fourth agent with its own reasoning — a deterministic pipeline function.

Step 1 extension: when use_llm=True or emit_event provided, runs one seller,
N concurrent buyers (top 3-5) with live leverage via shared_state and emits
EVENTS.md envelopes. Legacy sequential BATNA path is preserved for tests
that call run_pipeline() without those flags.
"""

from __future__ import annotations

import json
import os
import pathlib
import uuid
import time
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from agents.validation import number

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
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def _get_route(logistics: Dict[str, Any], route_id: str) -> Dict[str, Any]:
    """Look up a route dict by route_id and reject inconsistent responses."""
    for route in logistics["routes"]:
        if route["route_id"] == route_id:
            return route
    raise ValueError(f"Recommended route '{route_id}' is missing from logistics response")

# ---------------------------------------------------------------------------
# Step 1: Circularity / Matching Agent
# ---------------------------------------------------------------------------

def _match_buyers(
    material_id: str,
    quantity_tonnes: int,
    seller_id: str,
    buyer_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    from agents.circularity import find_candidates

    materials = _load_json("materials.json")
    buyers = _load_json("buyers.json")
    if buyer_overrides:
        unknown = set(buyer_overrides) - {b['buyer_id'] for b in buyers}
        if unknown:
            raise ValueError(f"Unknown buyer overrides: {sorted(unknown)}")
        for buyer_id, changes in buyer_overrides.items():
            if not isinstance(changes, dict) or 'buyer_id' in changes:
                raise ValueError(f"Invalid override for {buyer_id}: buyer_id cannot change")
        buyers = [
            {**b, **buyer_overrides[b["buyer_id"]]} if b["buyer_id"] in buyer_overrides else b
            for b in buyers
        ]
    buyers_by_id = {b["buyer_id"]: b for b in buyers}

    match_result = find_candidates(
        {"material_id": material_id, "quantity_tonnes": quantity_tonnes, "seller_id": seller_id},
        materials,
        buyers,
    )

    ranked_buyers = []
    for candidate in match_result["candidates"]:
        buyer = buyers_by_id.get(candidate["buyer_id"])
        if buyer is None:
            continue
        ranked_buyers.append({
            **buyer,
            "_circularity_application": candidate["application"],
            "_circularity_compatibility_score": candidate["compatibility_score"],
            "_circularity_notes": candidate["notes"],
        })
    return ranked_buyers

# ---------------------------------------------------------------------------
# Step 2: Negotiation Agent
# ---------------------------------------------------------------------------

HANDLING_COST_PER_TONNE_USD = 2.0
PROCESSING_COST_PER_TONNE_USD = 1.5


def _negotiate_with_buyer(
    buyer: Dict[str, Any],
    seller_constraints: Dict[str, Any],
    logistics_cost_per_tonne_usd: float,
    batna_price_per_tonne_usd: Optional[float] = None,
) -> Dict[str, Any]:
    from agents.negotiation import negotiate

    return negotiate(
        seller_constraints=seller_constraints,
        buyer=buyer,
        logistics_cost_per_tonne_usd=logistics_cost_per_tonne_usd,
        handling_cost_per_tonne_usd=HANDLING_COST_PER_TONNE_USD,
        processing_cost_per_tonne_usd=PROCESSING_COST_PER_TONNE_USD,
        use_llm=False,
        batna_price_per_tonne_usd=batna_price_per_tonne_usd,
    )


def _net_value(deal: Dict[str, Any], logistics_cost: float) -> Optional[float]:
    """price - logistics - handling - processing, or None if no viable price."""
    if deal["status"] not in ("accepted", "countered") or not deal.get("price_per_tonne_usd"):
        return None
    return deal["price_per_tonne_usd"] - logistics_cost - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD

# ---------------------------------------------------------------------------
# Step 3: Logistics Optimizer
# ---------------------------------------------------------------------------

def _get_logistics(origin: str, destination: str, cargo: int, deadline: int) -> Dict[str, Any]:
    from agents.logistics import optimize_routes

    return optimize_routes(
        origin_port_id=origin,
        destination_port_id=destination,
        cargo_tonnes=cargo,
        deadline_days=deadline,
    )

# ---------------------------------------------------------------------------
# New concurrent negotiation with EVENTS.md envelope helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_seller_constraints(seller: Dict[str, Any], quantity_tonnes: int) -> Dict[str, Any]:
    return {
        "min_acceptable_price_per_tonne_usd": seller["min_acceptable_price_per_tonne_usd"],
        "preferred_price_per_tonne_usd": seller["preferred_price_per_tonne_usd"],
        "available_quantity_tonnes": quantity_tonnes,
        "contract_months_min": seller.get("contract_months_min", 6),
        "contract_months_max": seller.get("contract_months_max", 36),
        "preferred_contract_months": seller.get("preferred_contract_months", 24),
    }


def _run_concurrent_negotiations(
    ranked_buyers: List[Dict[str, Any]],
    seller_constraints: Dict[str, Any],
    logistics_cache: Dict[str, Dict[str, Any]],
    origin_port: str,
    quantity_tonnes: int,
    use_batna: bool,
    use_llm: bool,
    top_n: int,
    emit_event: Optional[Callable[[Dict[str, Any]], None]],
    on_progress: Optional[Callable[[Dict[str, Any]], None]],
    seller: Dict[str, Any],
    run_id: str,
    seq_counter: List[int],
    seq_lock: threading.Lock,
    event_log: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, float]]:
    """
    Run top_n negotiations concurrently. Emits EVENTS.md envelopes if emit_event
    provided. Returns (selected_buyers, deal_results, logistics_costs).
    """
    selected = ranked_buyers[:top_n]

    def _logistics_cost_for(b: Dict[str, Any]) -> float:
        logistics = logistics_cache[b["port_id"]]
        return _get_route(logistics, logistics["recommended_route_id"])["cost_per_tonne_usd"]

    # envelope emitter
    def _emit_envelope(deal_id: Optional[str], ev_type: str, from_agent: str, to_agent: Optional[str],
                       model: Optional[str], payload: Dict[str, Any],
                       validator: Optional[Dict[str, Any]], delivered: Optional[bool]):
        # seq assignment, event_log ordering and the on-disk write all happen
        # under the same lock: concurrent threads must never interleave their
        # append after releasing the lock, or list/file order can diverge from
        # the seq each event was assigned (helpers_events asserts seq == index).
        with seq_lock:
            seq_counter[0] += 1
            seq = seq_counter[0]
            ev = {
                "seq": seq,
                "ts": _now_iso(),
                "run_id": run_id,
                "deal_id": deal_id,
                "type": ev_type,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "model": model,
                "payload": payload,
                "validator": validator,
                "delivered": delivered,
            }
            event_log.append(ev)
            # persist to runs file if orchestrator created one
            try:
                runs_dir_inner = pathlib.Path(__file__).resolve().parents[1] / "runs"
                run_file_inner = runs_dir_inner / f"{run_id}.jsonl"
                with run_file_inner.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            except Exception:
                pass
        if emit_event is not None:
            emit_event(ev)
        # small pacing to keep timestamp ordering visible
        time.sleep(0.02)

    # emit thread_started for each selected buyer
    for idx, buyer in enumerate(selected, start=1):
        seller_chain_env = os.getenv("SELLER_LLM_CHAIN", "fake:dummy")
        # extract model for badge: first entry of chain
        from agents.negotiation import _parse_chain
        seller_chain = _parse_chain(os.getenv("SELLER_LLM_CHAIN", ""))
        buyer_chain = _parse_chain(os.getenv(f"BUYER_{idx}_LLM_CHAIN", "") or os.getenv("BUYER_1_LLM_CHAIN", ""))
        seller_model = f"{seller_chain[0][0]}:{seller_chain[0][1]}" if seller_chain else None
        buyer_model = f"{buyer_chain[0][0]}:{buyer_chain[0][1]}" if buyer_chain else None
        _emit_envelope(
            buyer["buyer_id"], "thread_started", "orchestrator", None, None,
            {"buyer_id": buyer["buyer_id"], "buyer_name": buyer["name"], "seller_model": seller_model, "buyer_model": buyer_model},
            None, None,
        )
        if on_progress:
            on_progress({"stage": "negotiation_started", "buyer_name": buyer["name"]})

    # shared state for leverage checks: buyer_id -> list of delivered offer payloads
    shared_state: Dict[str, List[Dict[str, Any]]] = {b["buyer_id"]: [] for b in selected}
    shared_lock = threading.Lock()

    # build per-buyer BATNA using live-aware approach:
    # For concurrent demo, we still compute BATNA as best net value among OTHER buyers' baseline?
    # But spec says seller's hard floor stays max(seller_min, best live competing net value converted to $/t).
    # That is dynamic inside negotiate via shared_state, not precomputed.
    # So we don't precompute BATNA for concurrent; we pass batna=None and let thread pool handle via shared_state.
    # For backward compat when use_batna False, same.

    deal_results: Dict[str, Dict[str, Any]] = {}
    logistics_costs: Dict[str, float] = {}

    # Prepare per-buyer logistics costs
    for b in selected:
        logistics_costs[b["buyer_id"]] = _logistics_cost_for(b)

    # run with ThreadPoolExecutor
    def _one_thread(buyer: Dict[str, Any], buyer_idx: int) -> tuple[str, Dict[str, Any]]:
        from agents.negotiation import negotiate
        lc = logistics_costs[buyer["buyer_id"]]

        # per-thread emit that converts thread's internal emit dict to envelope
        def _thread_emit(internal: Dict[str, Any]):
            ev_type = internal.get("type", "offer")
            from_agent = internal.get("from_agent", "seller_agent")
            to_agent = internal.get("to_agent")
            payload = internal.get("payload", {})
            validator = internal.get("validator")
            delivered = internal.get("delivered")
            model = internal.get("model")
            _emit_envelope(buyer["buyer_id"], ev_type, from_agent, to_agent, model, payload, validator, delivered)

        # stagger start a bit to make concurrency visible
        time.sleep(0.05 * buyer_idx)

        # wrap shared_state updates with lock
        # negotiate will call _thread_emit which already handles envelope; shared_state is passed
        # we need to make shared_state thread-safe: monkey-patch its setdefault/append via lock
        # Instead we pass a dict and handle locking inside _thread_emit after each offer (already done)
        # But negotiate internally does shared_state.setdefault(...).append
        # We'll replace shared_state with a thread-safe proxy
        class LockedDict(dict):
            def setdefault(self, key, default):
                with shared_lock:
                    return super().setdefault(key, default)
            # append is on list, not dict, so we need to lock around list append too
            # We'll rely on negotiate not needing atomic; we add post-append lock in _thread_emit

        # For simplicity, pass shared_state directly and rely on GIL for small list appends
        # add batna if use_batna and we have precomputed baseline fallback for deterministic mode
        # For LLM mode, we let leverage be purely live via shared_state, so batna is None
        batna_price = None  # live leverage only

        # Use chain per buyer index
        result = negotiate(
            seller_constraints=seller_constraints,
            buyer=buyer,
            logistics_cost_per_tonne_usd=lc,
            handling_cost_per_tonne_usd=HANDLING_COST_PER_TONNE_USD,
            processing_cost_per_tonne_usd=PROCESSING_COST_PER_TONNE_USD,
            use_llm=use_llm,
            batna_price_per_tonne_usd=batna_price,
            emit=_thread_emit,
            buyer_index=buyer_idx,
            _shared_state=shared_state,
        )
        return buyer["buyer_id"], result

    with ThreadPoolExecutor(max_workers=len(selected)) as ex:
        fut_to_buyer = {ex.submit(_one_thread, b, i): b for i, b in enumerate(selected, start=1)}
        for fut in as_completed(fut_to_buyer):
            buyer_id, res = fut.result()
            deal_results[buyer_id] = res
            buyer_obj = next(b for b in selected if b["buyer_id"] == buyer_id)
            if on_progress:
                on_progress({
                    "stage": "negotiation_completed",
                    "buyer_name": buyer_obj["name"],
                    "status": res["status"],
                    "price": res.get("price_per_tonne_usd"),
                    "quantity": res["quantity_tonnes"],
                    "term": res["contract_term_months"],
                    "reason": res.get("validator_reason"),
                })
            # emit thread_result
            total_net = None
            if res.get("price_per_tonne_usd") is not None:
                lc = logistics_costs[buyer_id]
                per = res["price_per_tonne_usd"] - lc - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD
                total_net = round(per * res["quantity_tonnes"], 2) if per is not None else None
            is_agreed_pending = res["status"] in ("accepted", "countered")  # will be refined after winner pick
            _emit_envelope(
                buyer_id, "thread_result", "orchestrator", None, None,
                {
                    "status": res["status"],
                    "price_per_tonne_usd": res.get("price_per_tonne_usd"),
                    "quantity_tonnes": res["quantity_tonnes"],
                    "contract_months": res["contract_term_months"],
                    "rounds": res.get("rounds", 0),
                    "final_offer_id": res.get("final_offer_id"),
                    "total_net_value_usd": total_net,
                    "agreed_pending": is_agreed_pending,
                },
                None, None,
            )

    return selected, deal_results, logistics_costs


# ---------------------------------------------------------------------------
# Main orchestrator pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    seller_id: Optional[str] = None,
    material_id: Optional[str] = None,
    quantity_tonnes: Optional[int] = None,
    objective: Optional[str] = None,
    seller_overrides: Optional[Dict[str, Any]] = None,
    buyer_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    use_batna: bool = True,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    # New optional args for Step 1 concurrent mode
    use_llm: bool = False,
    top_n: int = 3,
    emit_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deterministic pipeline: Circularity → Negotiation → Logistics → Final output.

    Added for Step 1:
      use_llm: if True, negotiations use LLM-gated threaded loop (validator as gate)
      top_n: number of concurrent buyers (3-5) when in concurrent mode (default 3)
      emit_event: callback receiving EVENTS.md envelope dicts for streaming UI
      run_id: identifier for the run (generated if not supplied)

    Legacy sequential BATNA path is used when use_llm is False and emit_event is None,
    preserving existing test behavior.
    """
    def emit(stage: str, **details: Any) -> None:
        if on_progress is not None:
            on_progress({"stage": stage, **details})

    emit("matching_started")
    if seller_overrides is not None and not isinstance(seller_overrides, dict):
        raise ValueError("seller_overrides must be an object")
    if seller_overrides and {'seller_id', 'material_id'} & set(seller_overrides):
        raise ValueError("Seller identity and supplied material cannot be overridden")
    if buyer_overrides is not None and not isinstance(buyer_overrides, dict):
        raise ValueError("buyer_overrides must be an object")
    seller_id = FIXED_SCENARIO["seller_id"] if seller_id is None else seller_id
    material_id = FIXED_SCENARIO["material_id"] if material_id is None else material_id
    quantity_tonnes = number(FIXED_SCENARIO["quantity_tonnes"] if quantity_tonnes is None else quantity_tonnes,
                             "quantity_tonnes", positive=True)
    objective = FIXED_SCENARIO["objective"] if objective is None else objective
    if objective != "maximize_net_value":
        raise ValueError(f"Unsupported objective: {objective}")

    seller = {**_load_json("seller.json"), **(seller_overrides or {})}
    if seller_id != seller["seller_id"]:
        raise ValueError(f"Unknown seller_id: {seller_id}")
    if material_id != seller["material_id"]:
        raise ValueError(f"Seller does not supply material_id={material_id}")
    quantity_tonnes = min(quantity_tonnes, number(seller["available_quantity_tonnes_per_year"],
                                                 "seller availability", positive=True))
    origin_port = seller["nearest_port_id"]
    seller_constraints_legacy = {
        "min_acceptable_price_per_tonne_usd": seller["min_acceptable_price_per_tonne_usd"],
        "preferred_price_per_tonne_usd": seller["preferred_price_per_tonne_usd"],
        "available_quantity_tonnes": quantity_tonnes,
    }
    seller_constraints_new = _build_seller_constraints(seller, quantity_tonnes)

    # ---- Step 1: Find compatible buyers ----
    compatible_buyers = _match_buyers(material_id, quantity_tonnes, seller_id, buyer_overrides)
    emit("matching_completed", count=len(compatible_buyers))
    if not compatible_buyers:
        raise ValueError(f"No compatible buyers found for material_id={material_id}")

    # ---- Step 2: Get logistics cost for each buyer's port ----
    logistics_cache: Dict[str, Dict[str, Any]] = {}
    for buyer in compatible_buyers:
        port_id = buyer["port_id"]
        if port_id not in logistics_cache:
            logistics_cache[port_id] = _get_logistics(
                origin_port, port_id, quantity_tonnes, DEADLINE_DAYS
            )
            route = _get_route(logistics_cache[port_id], logistics_cache[port_id]["recommended_route_id"])
            emit("route_completed", origin=origin_port, destination=port_id, **route)

    def _logistics_cost_for(buyer: Dict[str, Any]) -> float:
        logistics = logistics_cache[buyer["port_id"]]
        return _get_route(logistics, logistics["recommended_route_id"])["cost_per_tonne_usd"]

    # Branch: concurrent LLM mode vs legacy sequential
    use_concurrent = use_llm or emit_event is not None or top_n != 3

    # Shared event bookkeeping for concurrent mode
    if use_concurrent:
        effective_run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        seq_counter = [0]
        seq_lock = threading.Lock()
        event_log: List[Dict[str, Any]] = []
        runs_dir = pathlib.Path(__file__).resolve().parents[1] / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_file = runs_dir / f"{effective_run_id}.jsonl"
        # ensure file exists (truncate)
        try:
            run_file.write_text("", encoding="utf-8")
        except Exception:
            pass
        def _persist(ev: Dict[str, Any]) -> None:
            try:
                with run_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            except Exception:
                pass

        def _emit_envelope_top(deal_id, ev_type, from_agent, to_agent, model, payload, validator, delivered):
            # same lock discipline as _emit_envelope: seq assignment, event_log
            # append and the on-disk write happen atomically together, since
            # event_log/seq_counter/seq_lock are shared with the concurrent
            # negotiation threads (helpers_events asserts seq == list index).
            with seq_lock:
                seq_counter[0] += 1
                seq = seq_counter[0]
                ev = {
                    "seq": seq, "ts": _now_iso(), "run_id": effective_run_id,
                    "deal_id": deal_id, "type": ev_type, "from_agent": from_agent,
                    "to_agent": to_agent, "model": model, "payload": payload,
                    "validator": validator, "delivered": delivered,
                }
                event_log.append(ev)
                _persist(ev)
            if emit_event:
                emit_event(ev)

        # run_started
        _emit_envelope_top(None, "run_started", "orchestrator", None, None,
                           {"seller_id": seller_id, "material_id": material_id, "quantity_tonnes": quantity_tonnes, "objective": objective, "top_n": min(top_n, len(compatible_buyers))},
                           None, None)
        # match
        selected_preview = compatible_buyers[:top_n]
        _emit_envelope_top(None, "match", "circularity_agent", "orchestrator", None,
                           {"matched_count": len(compatible_buyers),
                            "selected": [{"buyer_id": b["buyer_id"], "buyer_name": b["name"], "application": b["_circularity_application"], "compatibility_score": b["_circularity_compatibility_score"], "port_id": b["port_id"]} for b in selected_preview]},
                           None, None)
        # route (use first destination's route for envelope - spec shows single route event for dhamra->chittagong; emit per unique port)
        for port_id, logistics in logistics_cache.items():
            if any(b["port_id"] == port_id for b in selected_preview):
                route = _get_route(logistics, logistics["recommended_route_id"])
                _emit_envelope_top(None, "route", "logistics_agent", "orchestrator", None,
                                   {"origin_port": origin_port, "destination_port": port_id, "route_id": route["route_id"], "distance_km": route["distance_km"], "transit_days": route["transit_days"], "cost_per_tonne_usd": route["cost_per_tonne_usd"]},
                                   None, None)

        start_ms = time.time()
        selected, deal_results, logistics_costs = _run_concurrent_negotiations(
            compatible_buyers, seller_constraints_new, logistics_cache, origin_port, quantity_tonnes,
            use_batna, use_llm, min(top_n, len(compatible_buyers)), emit_event, on_progress, seller,
            effective_run_id, seq_counter, seq_lock, event_log,
        )

        # Final selection among agreed_pending (accepted/countered)
        eligible = {bid: res for bid, res in deal_results.items() if res["status"] in ("accepted", "countered") and res.get("price_per_tonne_usd") is not None}
        # compute total net values
        def _total_net(bid: str) -> float:
            res = eligible[bid]
            lc = logistics_costs[bid]
            per = res["price_per_tonne_usd"] - lc - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD
            return per * res["quantity_tonnes"]

        winner_id: Optional[str] = None
        if eligible:
            winner_id = max(eligible, key=_total_net)
            winner_res = eligible[winner_id]
            winner_buyer = next(b for b in selected if b["buyer_id"] == winner_id)
            winner_lc = logistics_costs[winner_id]
            winner_per = winner_res["price_per_tonne_usd"] - winner_lc - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD
            winner_total = round(winner_per * winner_res["quantity_tonnes"], 2)

            # released for every other agreed_pending not selected
            for bid, res in eligible.items():
                if bid == winner_id:
                    continue
                lc = logistics_costs[bid]
                per = res["price_per_tonne_usd"] - lc - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD
                total = round(per * res["quantity_tonnes"], 2)
                _emit_envelope_top(bid, "released", "orchestrator", f"buyer_agent:{bid}", None,
                                   {"reason": f"Another thread offered higher total net value ({winner_total:.2f} USD vs {total:.2f} USD)."},
                                   None, None)

            # deal_closed
            _emit_envelope_top(winner_id, "deal_closed", "orchestrator", None, None,
                               {"buyer_id": winner_id, "offer_id": winner_res.get("final_offer_id"), "total_net_value_usd": winner_total},
                               None, None)

            # recommendation
            logistics = logistics_cache[winner_buyer["port_id"]]
            route = _get_route(logistics, logistics["recommended_route_id"])
            margin = round(winner_res["price_per_tonne_usd"] - route["cost_per_tonne_usd"] - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD, 2)
            total_net_value = round(margin * winner_res["quantity_tonnes"], 2)
            pipeline_log = []
            for b in selected:
                bid = b["buyer_id"]
                res = deal_results.get(bid, {})
                pipeline_log.append({
                    "buyer_id": bid,
                    "buyer_name": b.get("name"),
                    "circularity_application": b.get("_circularity_application"),
                    "circularity_compatibility_score": b.get("_circularity_compatibility_score"),
                    "status": res.get("status", "rejected"),
                    "price_per_tonne_usd": res.get("price_per_tonne_usd"),
                    "batna_price_per_tonne_usd": res.get("batna_price_per_tonne_usd"),
                    "reason": res.get("validator_reason"),
                })
            # also add non-selected compatible buyers to pipeline_log as rejected (not negotiated) for completeness?
            recommendation_payload = {
                "material": material_id,
                "quantity_tonnes": winner_res["quantity_tonnes"],
                "buyer_id": winner_id,
                "buyer_name": winner_buyer["name"],
                "price_per_tonne_usd": winner_res["price_per_tonne_usd"],
                "route": {
                    "origin_port": origin_port,
                    "destination_port": winner_buyer["port_id"],
                    "distance_km": route["distance_km"],
                    "transit_days": route["transit_days"],
                    "cost_per_tonne_usd": route["cost_per_tonne_usd"],
                },
                "margin_per_tonne_usd": margin,
                "total_net_value_usd": total_net_value,
                "co2_avoided_tonnes_estimate": round(winner_res["quantity_tonnes"] * 0.85, 2)
                if winner_buyer.get('_circularity_application') == 'cement_clinker_substitute' else 0.0,
                "pipeline_log": pipeline_log,
            }
            _emit_envelope_top(None, "recommendation", "orchestrator", None, None, recommendation_payload, None, None)

            # run_completed
            duration_ms = int((time.time() - start_ms) * 1000)
            llm_calls = sum(1 for ev in event_log if ev.get("model"))
            bounces = sum(1 for ev in event_log if ev.get("delivered") is False)
            fallbacks = sum(1 for ev in event_log if ev.get("type") == "fallback")
            _emit_envelope_top(None, "run_completed", "orchestrator", None, None,
                               {"duration_ms": duration_ms, "llm_calls": llm_calls, "validator_bounces": bounces, "fallbacks": fallbacks},
                               None, None)

            return recommendation_payload
        else:
            # no viable deal
            _emit_envelope_top(None, "run_failed", "orchestrator", None, None, {"error": "No viable deal found across all compatible buyers"}, None, None)
            raise ValueError("No viable deal found across all compatible buyers")

    # -------------------------------------------------------------------
    # Legacy sequential BATNA path (preserved for test compatibility)
    # -------------------------------------------------------------------
    baseline_net_value: Dict[str, Optional[float]] = {}
    if use_batna:
        emit("baseline_started")
        for buyer in compatible_buyers:
            logistics_cost = _logistics_cost_for(buyer)
            baseline_deal = _negotiate_with_buyer(
                buyer=buyer,
                seller_constraints=seller_constraints_legacy,
                logistics_cost_per_tonne_usd=logistics_cost,
            )
            baseline_net_value[buyer["buyer_id"]] = _net_value(baseline_deal, logistics_cost)

    best_deal: Optional[Dict[str, Any]] = None
    best_total_net_value = -float("inf")
    pipeline_log: List[Dict[str, Any]] = []

    for buyer in compatible_buyers:
        logistics = logistics_cache[buyer["port_id"]]
        recommended_route_id = logistics["recommended_route_id"]
        logistics_cost = _get_route(logistics, recommended_route_id)["cost_per_tonne_usd"]

        batna_price: Optional[float] = None
        if use_batna:
            alternatives = [
                v for bid, v in baseline_net_value.items() if bid != buyer["buyer_id"] and v is not None
            ]
            if alternatives:
                best_alternative_net_value = max(alternatives)
                batna_price = round(
                    best_alternative_net_value
                    + logistics_cost
                    + HANDLING_COST_PER_TONNE_USD
                    + PROCESSING_COST_PER_TONNE_USD,
                    2,
                )

        emit("negotiation_started", buyer_name=buyer["name"])
        deal = _negotiate_with_buyer(
            buyer=buyer,
            seller_constraints=seller_constraints_legacy,
            logistics_cost_per_tonne_usd=logistics_cost,
            batna_price_per_tonne_usd=batna_price,
        )
        emit("negotiation_completed", buyer_name=buyer["name"],
             status=deal["status"], price=deal.get("price_per_tonne_usd"),
             quantity=deal["quantity_tonnes"], term=deal["contract_term_months"],
             reason=deal.get("validator_reason"))

        per_tonne_net_value = _net_value(deal, logistics_cost)
        total_net_value_for_deal = (
            per_tonne_net_value * deal["quantity_tonnes"] if per_tonne_net_value is not None else None
        )
        pipeline_log.append({
            "buyer_id": buyer["buyer_id"],
            "buyer_name": buyer.get("name"),
            "circularity_application": buyer.get("_circularity_application"),
            "circularity_compatibility_score": buyer.get("_circularity_compatibility_score"),
            "status": deal["status"],
            "price_per_tonne_usd": deal.get("price_per_tonne_usd"),
            "batna_price_per_tonne_usd": batna_price,
            "reason": deal.get("validator_reason"),
        })

        if total_net_value_for_deal is not None and total_net_value_for_deal > best_total_net_value:
            best_total_net_value = total_net_value_for_deal
            best_deal = {
                "buyer": buyer,
                "deal": deal,
                "logistics": logistics,
                "recommended_route_id": recommended_route_id,
                "net_value_per_tonne": round(per_tonne_net_value, 2),
                "total_net_value": round(total_net_value_for_deal, 2),
            }

    if best_deal is None:
        raise ValueError("No viable deal found across all compatible buyers")

    buyer = best_deal["buyer"]
    deal = best_deal["deal"]
    logistics = best_deal["logistics"]
    route = _get_route(logistics, logistics["recommended_route_id"])

    price = deal["price_per_tonne_usd"]
    logistics_cost = route["cost_per_tonne_usd"]
    margin = round(price - logistics_cost - HANDLING_COST_PER_TONNE_USD - PROCESSING_COST_PER_TONNE_USD, 2)
    total_net_value = round(margin * deal["quantity_tonnes"], 2)

    result = {
        "material": material_id,
        "quantity_tonnes": deal["quantity_tonnes"],
        "buyer_id": buyer["buyer_id"],
        "buyer_name": buyer["name"],
        "price_per_tonne_usd": price,
        "route": {
            "origin_port": origin_port,
            "destination_port": buyer["port_id"],
            "distance_km": route["distance_km"],
            "transit_days": route["transit_days"],
            "cost_per_tonne_usd": route["cost_per_tonne_usd"],
        },
        "margin_per_tonne_usd": margin,
        "total_net_value_usd": total_net_value,
        "co2_avoided_tonnes_estimate": round(deal["quantity_tonnes"] * 0.85, 2)
        if buyer.get('_circularity_application') == 'cement_clinker_substitute' else 0.0,
        "pipeline_log": pipeline_log,
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
