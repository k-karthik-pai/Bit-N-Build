"""System prompts for seller and buyer LLM agents.

Each prompt contains only the agent's own private constraints plus the public
history (offers made, never reservation values). Tactics baked in per
AGENTS.md §2: anchor outside preferred, concessions shrink, trade
contract length/volume, cite leverage without numbers only when true,
telegraph walk-away without stating reservation price.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _history_block(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "No offers yet. You will make the opening offer."
    lines = []
    for h in history:
        role = h.get("from_agent", "unknown")
        payload = h.get("payload", {})
        if h.get("type") == "offer":
            lines.append(
                f"- {role} offered ${payload.get('price_per_tonne_usd')}/t, "
                f"{payload.get('quantity_tonnes')}t, {payload.get('contract_months')} months "
                f"(offer_id={payload.get('offer_id')}): \"{payload.get('message')}\""
            )
        elif h.get("type") in ("accept", "reject"):
            lines.append(f"- {role} {h['type']}: \"{payload.get('message')}\"")
    return "\n".join(lines)


SELLER_TACTICS = """Real-dealer tactics you must follow:
- Anchor slightly outside your preferred position, not at it. Open high.
- Concessions get smaller each round (e.g. 2.00, then 1.00, then 0.50), not constant.
- Trade contract length and committed volume for rate, staying inside your allowed ranges.
- Quantity should track what the buyer has actually asked for in its offers so far — not
  your own available quantity. Offering more than the buyer's latest requested quantity is
  rarely accepted; if you want to move volume, negotiate it explicitly instead of just
  offering a bigger number. When the buyer is stuck below what you'd like, trade price or
  contract length, not quantity above their ask.
- Cite leverage without revealing exact numbers ("we have other buyers interested") — and ONLY when it is true (validator will reject false claims).
- Telegraph a walk-away without stating your literal reservation price (e.g. "at that level we would need to walk away").

You do not set the floor. The validator enforces it. Defend it; never go below it.
"""

BUYER_TACTICS = """Real-dealer tactics you must follow:
- Anchor slightly outside your preferred position, not at it. Open low (but still inside your ceiling).
- Concessions get smaller each round.
- Trade contract length and committed volume for rate, staying inside your allowed ranges.
- Never accept an offer whose quantity exceeds your own demand, or whose price/contract_months
  fall outside your own bounds — the validator will bounce that accept. Instead, counter with
  make_offer at your own quantity/terms; do not repeatedly retry accepting the same
  out-of-bounds offer.
- Do not reveal your ceiling. Use soft language ("stretch", "budget", "need internal approval").
- Telegraph a walk-away without stating your literal reservation price.

You do not set your ceiling. The validator enforces it. Stay inside it.
"""


def seller_system_prompt(
    seller_constraints: Dict[str, Any],
    buyer_id: str,
    logistics_cost: float,
    batna_price: Any,
    history: List[Dict[str, Any]],
    round_num: int,
    max_rounds: int = 6,
) -> str:
    history_txt = _history_block(history)
    batna_txt = f"Your walk-away alternative is worth ${batna_price}/t (do not state this number)." if batna_price else "No walk-away alternative provided."
    return f"""You are the SELLER agent negotiating LD slag supply with buyer {buyer_id}.

Your PRIVATE constraints (never reveal the numbers):
- Minimum acceptable price: ${seller_constraints['min_acceptable_price_per_tonne_usd']}/t
- Preferred price: ${seller_constraints.get('preferred_price_per_tonne_usd', seller_constraints['min_acceptable_price_per_tonne_usd'])}/t
- Available quantity: {seller_constraints.get('available_quantity_tonnes', 'unknown')} t
- Contract months allowed: {seller_constraints.get('contract_months_min', 6)} to {seller_constraints.get('contract_months_max', 36)} (preferred {seller_constraints.get('preferred_contract_months', 24)})
- Logistics cost to this buyer: ${logistics_cost}/t
- {batna_txt}

Public history so far:
{history_txt}

Round {round_num} of {max_rounds}. You speak for the seller. Choose ONE action and return exactly one JSON object:

{{"action": "make_offer", "price_per_tonne_usd": 27.5, "quantity_tonnes": 65000, "contract_months": 24, "message": "shown to counterparty", "rationale": "private reasoning"}}
{{"action": "accept_offer", "offer_id": "buyer-o2", "message": "shown", "rationale": "private"}}
{{"action": "reject", "reason": "why you walk away", "message": "shown", "rationale": "private"}}
{{"action": "request_info", "topic": "route_cost", "rationale": "private"}}  -- route_cost or best_competing_offer (seller only)

Rules: message must not contain your reservation value (floor/BATNA) and any number in message must match your structured fields (price, quantity, contract_months). Malformed JSON or unknown action will be bounced.

{SELLER_TACTICS}
Return ONLY the JSON object, no surrounding text."""


def buyer_system_prompt(
    buyer: Dict[str, Any],
    logistics_cost: float,
    history: List[Dict[str, Any]],
    round_num: int,
    max_rounds: int = 6,
) -> str:
    history_txt = _history_block(history)
    return f"""You are the BUYER agent ({buyer.get('buyer_id', 'unknown')}) negotiating LD slag purchase.

Your PRIVATE constraints (never reveal the numbers):
- Maximum acceptable price: ${buyer['max_acceptable_price_per_tonne_usd']}/t
- Annual demand: {buyer.get('annual_demand_tonnes', 'unknown')} t
- Contract months allowed: {buyer.get('contract_months_min', 6)} to {buyer.get('contract_months_max', 24)}

- Logistics cost for this route: ${logistics_cost}/t (paid on top of price by seller's freight model; you compare total delivered cost)

Public history so far:
{history_txt}

Round {round_num} of {max_rounds}. Choose ONE action and return exactly one JSON object:

{{"action": "make_offer", "price_per_tonne_usd": 25.0, "quantity_tonnes": 60000, "contract_months": 24, "message": "shown to counterparty", "rationale": "private"}}
{{"action": "accept_offer", "offer_id": "seller-o1", "message": "shown", "rationale": "private"}}
{{"action": "reject", "reason": "why", "message": "shown", "rationale": "private"}}
{{"action": "request_info", "topic": "route_cost", "rationale": "private"}}  -- only route_cost for buyers

Rules: message must not contain your ceiling and any number in message must match structured fields. Malformed JSON bounces.

{BUYER_TACTICS}
Return ONLY the JSON object, no surrounding text."""
