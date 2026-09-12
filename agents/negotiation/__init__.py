"""
Negotiation Agent — Contract frozen in AGENTS.md section 2.

Responsibility: given seller constraints, a specific buyer, and a logistics cost figure,
produce an accept/reject/counter outcome inside hard numeric bounds.

Core architecture (non-negotiable per brief-negotiation-agent.md):
- LLM generates negotiation proposals/messages (the `transcript` field).
- A separate deterministic validator function checks every numeric proposal against the
  seller's `min_acceptable_price_per_tonne_usd` and the buyer's
  `max_acceptable_price_per_tonne_usd` before it can be marked `accepted`.
- The LLM never gets to unilaterally decide the final price/quantity — the validator does.

Core logic:
1. Compute margin: price − logistics_cost − handling − processing
2. Generate negotiation exchange (2–4 turns) bounded by seller/buyer price limits.
3. Validate final numeric proposal against both parties' constraints.
4. Return accepted / rejected / countered with full transcript.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Hardcoded placeholder — swap with Logistics Optimizer output when ready
# ---------------------------------------------------------------------------
DEFAULT_LOGISTICS_COST_PER_TONNE_USD: float = 8.0
DEFAULT_HANDLING_COST_PER_TONNE_USD: float = 2.0
DEFAULT_PROCESSING_COST_PER_TONNE_USD: float = 1.5

CONTRACT_TERM_MONTHS: int = 12

# ---------------------------------------------------------------------------
# Deterministic validator — the gatekeeper
# ---------------------------------------------------------------------------

def validate_proposal(
    price_per_tonne_usd: Optional[float],
    seller_min: float,
    buyer_max: float,
) -> Tuple[bool, str]:
    """
    Check every numeric proposal against hard bounds.

    Returns (is_valid, reason).  A price can only be marked `accepted` if
    seller_min <= price <= buyer_max (inclusive, with tiny epsilon).
    """
    if price_per_tonne_usd is None:
        return False, "price is None"
    if not isinstance(price_per_tonne_usd, (int, float)):
        return False, f"price must be numeric, got {type(price_per_tonne_usd)}"
    eps = 1e-9
    if price_per_tonne_usd + eps < seller_min:
        return False, f"price {price_per_tonne_usd} below seller min {seller_min}"
    if price_per_tonne_usd - eps > buyer_max:
        return False, f"price {price_per_tonne_usd} above buyer max {buyer_max}"
    return True, "within bounds"


def validate_quantity(
    quantity_tonnes: Optional[float],
    available_quantity_tonnes: float,
    annual_demand_tonnes: float,
) -> Tuple[bool, str]:
    if quantity_tonnes is None:
        return False, "quantity is None"
    if quantity_tonnes < 0:
        return False, "quantity negative"
    if quantity_tonnes - 1e-9 > available_quantity_tonnes:
        return False, "quantity exceeds seller availability"
    if quantity_tonnes - 1e-9 > annual_demand_tonnes:
        return False, "quantity exceeds buyer demand"
    return True, "quantity valid"


# ---------------------------------------------------------------------------
# Margin helper — Step 1 of brief
# ---------------------------------------------------------------------------

def compute_margin_per_tonne(
    price_per_tonne_usd: float,
    logistics_cost_per_tonne_usd: float = DEFAULT_LOGISTICS_COST_PER_TONNE_USD,
    handling_cost_per_tonne_usd: float = DEFAULT_HANDLING_COST_PER_TONNE_USD,
    processing_cost_per_tonne_usd: float = DEFAULT_PROCESSING_COST_PER_TONNE_USD,
) -> float:
    """margin = price − logistics − handling − processing"""
    return (
        price_per_tonne_usd
        - logistics_cost_per_tonne_usd
        - handling_cost_per_tonne_usd
        - processing_cost_per_tonne_usd
    )


# ---------------------------------------------------------------------------
# Transcript generation — LLM proposes, validator decides
# ---------------------------------------------------------------------------

def _deterministic_transcript(
    *,
    seller_min: float,
    seller_preferred: float,
    buyer_max: float,
    logistics_cost: float,
    price: Optional[float],
    quantity: int,
    status: str,
    margin: Optional[float],
    buyer_id: str,
) -> List[Dict[str, str]]:
    """Fallback when no LLM key is configured.  Messages are bounded by real limits."""
    if status == "rejected":
        return [
            {
                "speaker": "seller_agent",
                "message": (
                    f"Seller opening: we can supply LD slag at ${seller_preferred:.2f}/t "
                    f"(minimum acceptable ${seller_min:.2f}/t, quantity {quantity}t, "
                    f"logistics ~${logistics_cost:.2f}/t). Margin at preferred would be "
                    f"${compute_margin_per_tonne(seller_preferred, logistics_cost):.2f}/t."
                ),
            },
            {
                "speaker": "buyer_agent",
                "message": (
                    f"Buyer {buyer_id}: our ceiling is ${buyer_max:.2f}/t for this grade "
                    f"(annual demand {quantity}t). At ${seller_min:.2f}/t min we still "
                    f"cannot meet — gap is ${seller_min - buyer_max:.2f}/t."
                ),
            },
            {
                "speaker": "seller_agent",
                "message": (
                    "Seller: no overlap between our floor and your ceiling. "
                    "Deal cannot proceed at current constraints. Rejected."
                ),
            },
        ]

    if status == "countered":
        # seller had to come down or margin is tight
        return [
            {
                "speaker": "seller_agent",
                "message": (
                    f"Seller opening: offering LD slag at ${seller_preferred:.2f}/t "
                    f"(floor ${seller_min:.2f}/t). Quantity {quantity}t, logistics "
                    f"${logistics_cost:.2f}/t."
                ),
            },
            {
                "speaker": "buyer_agent",
                "message": (
                    f"Buyer {buyer_id}: we can stretch to ${buyer_max:.2f}/t max. "
                    f"Your preferred ${seller_preferred:.2f}/t is above our ceiling."
                ),
            },
            {
                "speaker": "seller_agent",
                "message": (
                    f"Seller counter: we can meet at ${price:.2f}/t "
                    f"(margin ${margin:.2f}/t after logistics/handling/processing). "
                    f"Quantity {quantity}t, term {CONTRACT_TERM_MONTHS} months. "
                    f"Please confirm or counter."
                ),
            },
            {
                "speaker": "buyer_agent",
                "message": (
                    f"Buyer {buyer_id}: ${price:.2f}/t is at our limit — need internal "
                    f"approval at this margin. Countered, pending confirmation."
                ),
            },
        ]

    # accepted
    return [
        {
            "speaker": "seller_agent",
            "message": (
                f"Seller opening: LD slag available, ${seller_preferred:.2f}/t preferred "
                f"(floor ${seller_min:.2f}/t), logistics ${logistics_cost:.2f}/t, "
                f"quantity {quantity}t."
            ),
        },
        {
            "speaker": "buyer_agent",
            "message": (
                f"Buyer {buyer_id}: we accept the quality; our ceiling is "
                f"${buyer_max:.2f}/t for {quantity}t annual demand. "
                f"Can we settle near ${price:.2f}/t?"
            ),
        },
        {
            "speaker": "seller_agent",
            "message": (
                f"Seller: confirmed at ${price:.2f}/t for {quantity}t "
                f"(margin ${margin:.2f}/t net of logistics/handling/processing). "
                f"Term {CONTRACT_TERM_MONTHS} months. Ready to proceed."
            ),
        },
        {
            "speaker": "buyer_agent",
            "message": (
                f"Buyer {buyer_id}: accepted at ${price:.2f}/t, {quantity}t, "
                f"{CONTRACT_TERM_MONTHS} months. Proceed to contract."
            ),
        },
    ]


def _resolve_llm_config() -> Optional[Tuple[Dict[str, Any], str]]:
    """
    Returns (openai_client_kwargs, model) for whichever provider is configured
    via env vars, or None if none is set. OpenRouter takes priority since it's
    the one used for free-tier testing; falls back to direct OpenAI.
    """
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return (
            {"api_key": openrouter_key, "base_url": "https://openrouter.ai/api/v1"},
            os.getenv("NEGOTIATION_LLM_MODEL", "nex-agi/nex-n2.5-mini:free"),
        )
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    if api_key:
        return {"api_key": api_key}, os.getenv("NEGOTIATION_LLM_MODEL", "gpt-4o-mini")
    return None


def _try_llm_transcript(
    *,
    seller_min: float,
    seller_preferred: float,
    buyer_max: float,
    buyer_id: str,
    logistics_cost: float,
    price: Optional[float],
    quantity: int,
    status: str,
    margin: Optional[float],
) -> Optional[List[Dict[str, str]]]:
    """
    Attempt LLM generation.  Returns None on any failure so caller can fall back
    to deterministic transcript.  The LLM is NEVER allowed to set price outside
    bounds — price is injected from the deterministic engine.
    """
    config = _resolve_llm_config()
    if config is None:
        return None
    client_kwargs, model = config
    try:
        # Lazy import so module works without openai installed
        from openai import OpenAI  # type: ignore

        client = OpenAI(**client_kwargs)

        # We constrain the LLM with the already-validated numeric proposal.
        # It only writes the dialogue, not the price decision.
        if status == "rejected":
            instruction = (
                f"Write a 3-turn negotiation transcript that ends in rejection. "
                f"Seller floor ${seller_min:.2f}/t, preferred ${seller_preferred:.2f}/t; "
                f"buyer {buyer_id} ceiling ${buyer_max:.2f}/t. No overlap. "
                f"Quantity {quantity}t. Be concise, professional."
            )
        elif status == "countered":
            instruction = (
                f"Write a 4-turn negotiation transcript that ends in a counter-offer. "
                f"Seller floor ${seller_min:.2f}/t, preferred ${seller_preferred:.2f}/t; "
                f"buyer {buyer_id} ceiling ${buyer_max:.2f}/t. "
                f"Agreed counter price is ${price:.2f}/t, margin ${margin:.2f}/t, "
                f"quantity {quantity}t, logistics ${logistics_cost:.2f}/t. "
                f"Show the seller lowering expectations to meet the buyer at the validated price."
            )
        else:
            instruction = (
                f"Write a 4-turn negotiation transcript that ends in acceptance. "
                f"Seller floor ${seller_min:.2f}/t, preferred ${seller_preferred:.2f}/t; "
                f"buyer {buyer_id} ceiling ${buyer_max:.2f}/t. "
                f"Final validated price ${price:.2f}/t, margin ${margin:.2f}/t, "
                f"quantity {quantity}t, logistics ${logistics_cost:.2f}/t, "
                f"term {CONTRACT_TERM_MONTHS} months."
            )

        resp = client.chat.completions.create(
            model=model,
            temperature=0.7,
            max_tokens=1200,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a negotiation dialogue generator for industrial by-product sales. "
                        "You write ONLY the transcript messages. Do not invent a different price — "
                        "use exactly the numbers given. Output JSON list of {speaker, message} "
                        "objects with speaker in ['seller_agent','buyer_agent']."
                    ),
                },
                {"role": "user", "content": instruction},
            ],
            response_format={"type": "json_object"},
        )
        import json

        raw = resp.choices[0].message.content or ""
        parsed = json.loads(raw)
        # Accept either {"transcript": [...]} or bare list
        if isinstance(parsed, dict) and "transcript" in parsed:
            transcript = parsed["transcript"]
        elif isinstance(parsed, list):
            transcript = parsed
        else:
            # try to find list inside dict
            transcript = next((v for v in parsed.values() if isinstance(v, list)), None)
            if transcript is None:
                return None

        # Validate shape, clamp to 2–4 turns, ensure alternating speakers
        cleaned: List[Dict[str, str]] = []
        for entry in transcript[:4]:
            if not isinstance(entry, dict):
                continue
            speaker = entry.get("speaker", "")
            message = entry.get("message", "")
            if speaker not in ("seller_agent", "buyer_agent"):
                # infer by position
                speaker = "seller_agent" if len(cleaned) % 2 == 0 else "buyer_agent"
            cleaned.append({"speaker": speaker, "message": str(message)[:600]})

        if 2 <= len(cleaned) <= 4:
            return cleaned
        return None
    except Exception:
        return None


def generate_transcript(
    *,
    seller_min: float,
    seller_preferred: float,
    buyer_max: float,
    buyer_id: str,
    logistics_cost: float,
    price: Optional[float],
    quantity: int,
    status: str,
    margin: Optional[float],
    use_llm: bool = True,
) -> List[Dict[str, str]]:
    """
    LLM generates negotiation proposals/messages (transcript field).
    Deterministic fallback guarantees standalone testing without an API key.
    """
    if use_llm:
        llm_result = _try_llm_transcript(
            seller_min=seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=price,
            quantity=quantity,
            status=status,
            margin=margin,
        )
        if llm_result is not None:
            return llm_result

    return _deterministic_transcript(
        seller_min=seller_min,
        seller_preferred=seller_preferred,
        buyer_max=buyer_max,
        logistics_cost=logistics_cost,
        price=price,
        quantity=quantity,
        status=status,
        margin=margin,
        buyer_id=buyer_id,
    )


# ---------------------------------------------------------------------------
# Main entry — AGENTS.md section 2 compliant
# ---------------------------------------------------------------------------

def negotiate(
    seller_constraints: Dict[str, Any],
    buyer: Dict[str, Any],
    logistics_cost_per_tonne_usd: float = DEFAULT_LOGISTICS_COST_PER_TONNE_USD,
    handling_cost_per_tonne_usd: float = DEFAULT_HANDLING_COST_PER_TONNE_USD,
    processing_cost_per_tonne_usd: float = DEFAULT_PROCESSING_COST_PER_TONNE_USD,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Negotiate a deal between seller and buyer.

    Input shape (AGENTS.md section 2):
      seller_constraints: {min_acceptable_price_per_tonne_usd, preferred_price_per_tonne_usd, available_quantity_tonnes}
      buyer: {buyer_id, max_acceptable_price_per_tonne_usd, annual_demand_tonnes}
      logistics_cost_per_tonne_usd: float (hardcoded $8/t placeholder until Logistics Optimizer ready)

    Output shape:
      {status, price_per_tonne_usd, quantity_tonnes, contract_term_months, transcript}
      status in {"accepted","rejected","countered"}
    """
    # ---- Input validation ----
    try:
        seller_min = float(seller_constraints["min_acceptable_price_per_tonne_usd"])
        seller_preferred = float(seller_constraints.get("preferred_price_per_tonne_usd", seller_min))
        available = float(seller_constraints.get("available_quantity_tonnes", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid seller_constraints: {exc}") from exc

    try:
        buyer_id = str(buyer.get("buyer_id", "unknown_buyer"))
        buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
        demand = float(buyer.get("annual_demand_tonnes", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid buyer: {exc}") from exc

    try:
        logistics_cost = float(logistics_cost_per_tonne_usd)
        handling_cost = float(handling_cost_per_tonne_usd)
        processing_cost = float(processing_cost_per_tonne_usd)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid cost figures: {exc}") from exc

    quantity = int(min(available, demand)) if demand > 0 and available > 0 else 0

    # ---- No-overlap check ----
    if seller_min > buyer_max:
        status = "rejected"
        price: Optional[float] = None
        margin: Optional[float] = None
        transcript = generate_transcript(
            seller_min=seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=seller_min,  # for transcript context even though rejected
            quantity=quantity,
            status=status,
            margin=margin,
            use_llm=use_llm,
        )
        return {
            "status": status,
            "price_per_tonne_usd": price,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript,
            # Extra debug fields (not in frozen contract but useful for orchestrator)
            "margin_per_tonne_usd": margin,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "validator_reason": f"no overlap: seller_min {seller_min} > buyer_max {buyer_max}",
        }

    # ---- Determine numeric proposal (deterministic) ----
    # If seller preferred is within buyer ceiling, seller gets preferred.
    # Otherwise compromise toward the feasible range.
    if seller_preferred <= buyer_max:
        proposed_price = seller_preferred
    else:
        # Compromise: midpoint between floor and ceiling, rounded to cents
        proposed_price = round((seller_min + buyer_max) / 2.0, 2)

    proposed_price = round(float(proposed_price), 2)

    # ---- Validator gates `accepted` ----
    is_valid, reason = validate_proposal(proposed_price, seller_min, buyer_max)
    if not is_valid:
        # Should not happen due to branching above, but guard anyway
        transcript = generate_transcript(
            seller_min=seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=proposed_price,
            quantity=quantity,
            status="rejected",
            margin=None,
            use_llm=use_llm,
        )
        return {
            "status": "rejected",
            "price_per_tonne_usd": None,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript,
            "margin_per_tonne_usd": None,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "validator_reason": reason,
        }

    margin = round(
        compute_margin_per_tonne(proposed_price, logistics_cost, handling_cost, processing_cost), 2
    )

    # ---- Status decision: accepted vs countered ----
    # Countered signals a feasible but tight deal needing further confirmation:
    #   - seller had to compromise below preferred, OR
    #   - spread is very narrow (< $2/t), OR
    #   - margin is thin (< $2/t)
    overlap = buyer_max - seller_min
    had_to_compromise = seller_preferred > buyer_max
    thin_spread = overlap < 2.0
    thin_margin = margin < 2.0

    if had_to_compromise or thin_spread or thin_margin:
        # Still validated, but mark as countered so orchestrator knows it was a compromise
        status = "countered"
    else:
        status = "accepted"

    transcript = generate_transcript(
        seller_min=seller_min,
        seller_preferred=seller_preferred,
        buyer_max=buyer_max,
        buyer_id=buyer_id,
        logistics_cost=logistics_cost,
        price=proposed_price,
        quantity=quantity,
        status=status,
        margin=margin,
        use_llm=use_llm,
    )

    # Final validator check before returning accepted/countered — never leak out-of-bounds.
    # Must always run (not an assert, which python -O would strip), since this is the
    # one guarantee the module's docstring calls non-negotiable.
    if not is_valid:
        raise ValueError(f"Invariant violated: price out of bounds ({reason})")

    q_valid, q_reason = validate_quantity(quantity, available, demand)
    # quantity of 0 is allowed for edge cases but flag it
    if not q_valid and quantity != 0:
        raise ValueError(f"Quantity validation failed: {q_reason}")

    if quantity == 0:
        # Zero-tonne deals are not meaningful accepts/counters — reject instead.
        status = "rejected"
        transcript = generate_transcript(
            seller_min=seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=proposed_price,
            quantity=quantity,
            status="rejected",
            margin=margin,
            use_llm=use_llm,
        )
        return {
            "status": status,
            "price_per_tonne_usd": None,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript,
            "margin_per_tonne_usd": None,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "validator_reason": "quantity is zero: no viable deal (available or demand is 0)",
        }

    return {
        "status": status,
        "price_per_tonne_usd": proposed_price,
        "quantity_tonnes": quantity,
        "contract_term_months": CONTRACT_TERM_MONTHS,
        "transcript": transcript,
        "margin_per_tonne_usd": margin,
        "logistics_cost_per_tonne_usd": logistics_cost,
        "handling_cost_per_tonne_usd": handling_cost,
        "processing_cost_per_tonne_usd": processing_cost,
        "validator_reason": reason,
    }


# Keep contract-compatible alias for orchestrator wiring
def run_negotiation(*args, **kwargs) -> Dict[str, Any]:
    return negotiate(*args, **kwargs)


__all__ = [
    "DEFAULT_LOGISTICS_COST_PER_TONNE_USD",
    "DEFAULT_HANDLING_COST_PER_TONNE_USD",
    "DEFAULT_PROCESSING_COST_PER_TONNE_USD",
    "CONTRACT_TERM_MONTHS",
    "validate_proposal",
    "validate_quantity",
    "compute_margin_per_tonne",
    "generate_transcript",
    "negotiate",
    "run_negotiation",
]
