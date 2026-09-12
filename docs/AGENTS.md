# AGENTS.md

Contracts frozen at project start. Changing a shape here after building begins must be
flagged to everyone immediately — every other agent may depend on it.

---

## 1. Circularity / Matching Agent

**Responsibility:** given a material and quantity, find and rank compatible buyers.
**Does NOT:** negotiate price, compute logistics cost, or make the final decision.

Input:
```json
{
  "material_id": "string",
  "quantity_tonnes": 100000,
  "seller_id": "string"
}
```

Output:
```json
{
  "candidates": [
    {
      "buyer_id": "string",
      "application": "string",
      "compatibility_score": 0.0,
      "notes": "string"
    }
  ]
}
```

---

## 2. Negotiation Agent

> **Contract revised in Step 0 of `NEXT_STEPS.md` (agreed by the team).** The LLMs now
> make the negotiation moves; validators only enforce bounds. The previous version
> (LLM writes/paraphrases text around a formula-computed price) survives as the
> deterministic fallback and test path.

**Responsibility:** run one negotiation thread between a **seller agent** and one **buyer
agent** — separate LLM contexts, each holding only its own private constraints — and
return an accept/reject/counter outcome whose terms passed the validators.
**Does NOT:** let any LLM set a bound, see the other side's reservation values, or finalize
a deal the validators haven't passed. Does NOT pick the winning buyer (orchestrator does).

### Moves (validated JSON, not native tool calling)
Each agent turn returns exactly one JSON object. Malformed JSON or an unknown `action` is
an invalid move and bounces like any other (small/free models vary in tool-call support,
so the protocol doesn't depend on it).
```json
{"action": "make_offer", "price_per_tonne_usd": 26.5, "quantity_tonnes": 60000,
 "contract_months": 24, "message": "shown to the counterparty", "rationale": "private"}
{"action": "accept_offer", "offer_id": "shah_cement-o3", "message": "string", "rationale": "string"}
{"action": "reject", "reason": "string", "message": "string", "rationale": "string"}
{"action": "request_info", "topic": "route_cost | best_competing_offer", "rationale": "string"}
```
`best_competing_offer` is seller-only. `offer_id` is assigned by the runtime, never by the LLM.

### Two validator gates
- **move-legal** — checked on every `make_offer`, against the **proposing agent's own**
  bounds only: seller price ≥ its effective floor, buyer price ≤ its ceiling, quantity ≤
  own availability/demand, `contract_months` inside own range. Anchoring outside the
  counterparty's range is legal. The rejection reason may reference **only the proposing
  agent's own constraints** — citing the counterparty's bound would leak it.
- **deal-legal** — checked at `accept_offer(offer_id)`: the referenced offer is the latest
  open offer from the counterparty, and it satisfies both sides' bounds.

Also bounced as invalid moves: any number in `message` that is neither one of the move's
own structured fields nor a structured field of an offer already delivered in the same
thread (amended 2026-09-12 — quoting the counterparty's earlier offer is allowed), or the
thread's public route freight cost per tonne (amended 2026-09-12); a
`message` stating the agent's own reservation value (floor / ceiling / BATNA) as a number
not covered by the previous rule; a seller leverage claim ("we have a better offer") with
no qualifying live offer in another thread.

### Limits
Max 6 rounds per thread → best open counter becomes `countered`. Two invalid moves in a
row from one agent, or a provider timeout → that turn uses the deterministic engine and
emits a visible `fallback` event.

Input (one thread):
```json
{
  "seller_constraints": {
    "min_acceptable_price_per_tonne_usd": 22,
    "preferred_price_per_tonne_usd": 25,
    "available_quantity_tonnes": 100000,
    "contract_months_min": 6,
    "contract_months_max": 36,
    "preferred_contract_months": 24
  },
  "buyer": {
    "buyer_id": "string",
    "max_acceptable_price_per_tonne_usd": 26,
    "annual_demand_tonnes": 40000,
    "contract_months_min": 6,
    "contract_months_max": 24
  },
  "logistics_cost_per_tonne_usd": 7,
  "batna_price_per_tonne_usd": null,
  "emit": "callable(event) — see docs/EVENTS.md"
}
```

Output (existing fields unchanged; `rounds`, `final_offer_id`, `agents` are additive):
```json
{
  "status": "accepted",
  "price_per_tonne_usd": 27,
  "quantity_tonnes": 65000,
  "contract_term_months": 24,
  "transcript": [
    {"speaker": "seller_agent", "message": "string", "offer_id": "string|null",
     "validator": {"ok": true, "reason": "string"}},
    {"speaker": "buyer_agent", "message": "string", "offer_id": "string|null",
     "validator": {"ok": true, "reason": "string"}}
  ],
  "rounds": 4,
  "final_offer_id": "shah_cement-o4",
  "agents": {"seller_agent": "provider/model", "buyer_agent": "provider/model"}
}
```
`status` is one of: `accepted`, `rejected`, `countered` (unchanged). Private `rationale`
never appears in `transcript` — only in events, flagged private.

### Concurrency (orchestrator-driven)
The orchestrator runs one thread per top-3–5 matched buyer concurrently, sharing one seller
agent persona. Threads that reach `accepted` are **agreed-pending**: the orchestrator picks
one by total net value (section 4, unchanged) and **releases** the others. At most one deal
closes per run — see `LIMITATIONS.md`.

### Model assignment
Each role reads its provider chain from env (`.env.example`); the model used is recorded
per transcript entry and event so the UI can badge it.

---

## 3. Logistics Optimizer

**Responsibility:** given origin/destination ports and cargo details, return route options
with cost and transit time.
**Does NOT:** decide which buyer to ship to — it only prices routes it's asked about.

Input:
```json
{
  "origin_port_id": "string",
  "destination_port_id": "string",
  "cargo_tonnes": 40000,
  "deadline_days": 20
}
```

Output:
```json
{
  "routes": [
    {
      "route_id": "string",
      "path_port_ids": ["string"],
      "distance_km": 0,
      "transit_days": 0,
      "cost_per_tonne_usd": 0
    }
  ],
  "recommended_route_id": "string"
}
```

---

## 4. Orchestrator

**Responsibility:** call the three agents above in sequence for the fixed scenario, and
assemble the final recommendation. Not a fourth "agent" with its own reasoning — a
deterministic pipeline function.

Input: `{ "seller_id": "string", "material_id": "string", "quantity_tonnes": 100000, "objective": "maximize_net_value" }`

Output (final recommendation, shown in the demo):
```json
{
  "material": "string",
  "quantity_tonnes": 0,
  "buyer_id": "string",
  "buyer_name": "string",
  "price_per_tonne_usd": 0,
  "route": {
    "origin_port": "string",
    "destination_port": "string",
    "distance_km": 0,
    "transit_days": 0,
    "cost_per_tonne_usd": 0
  },
  "margin_per_tonne_usd": 0,
  "total_net_value_usd": 0,
  "co2_avoided_tonnes_estimate": 0
}
```
`co2_avoided_tonnes_estimate` must be labeled as an estimate in the UI — do not present it
as a measured figure.
