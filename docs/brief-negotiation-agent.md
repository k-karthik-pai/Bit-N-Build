# Brief: Negotiation Agent

**Purpose:** given seller constraints, one buyer, and a logistics cost figure, produce an
accept/reject/counter outcome. Contract is in AGENTS.md section 2.

**Dependencies:** needs a `logistics_cost_per_tonne_usd` number as input — use a
hardcoded placeholder (e.g. $8/t) until the Logistics Optimizer is ready, then swap.
Do not wait for the real logistics module to start building this.

**Core architecture (non-negotiable):**
- LLM generates negotiation proposals/messages (the `transcript` field).
- A separate deterministic validator function checks every numeric proposal against the
  seller's `min_acceptable_price_per_tonne_usd` and the buyer's
  `max_acceptable_price_per_tonne_usd` before it can be marked `accepted`.
- The LLM never gets to unilaterally decide the final price/quantity — the validator does.

**Core logic:**
1. Compute margin for the given buyer: `price − logistics_cost − handling − processing`.
2. Generate a negotiation exchange (2–4 turns is enough) bounded by seller/buyer price
   limits.
3. Validate the final numeric proposal against both parties' constraints.
4. Return `accepted`, `rejected`, or `countered` with the full transcript.

**Acceptance criteria:**
- Given two different buyers with different price ceilings, produces different outcomes
  that respect both parties' constraints
- Never returns a price outside `[min_acceptable_price, max_acceptable_price]` for
  `accepted` status
- Runs standalone against a hardcoded logistics cost for independent testing
