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

**Responsibility:** given seller constraints, a specific buyer, and a logistics cost figure,
produce an accept/reject/counter outcome inside hard numeric bounds.
**Does NOT:** invent prices outside the constraint bounds. LLM proposes text; a validator
function checks every numeric proposal before it's accepted as the agent's output.

Input:
```json
{
  "seller_constraints": {
    "min_acceptable_price_per_tonne_usd": 22,
    "preferred_price_per_tonne_usd": 25,
    "available_quantity_tonnes": 100000
  },
  "buyer": {
    "buyer_id": "string",
    "max_acceptable_price_per_tonne_usd": 26,
    "annual_demand_tonnes": 40000
  },
  "logistics_cost_per_tonne_usd": 8
}
```

Output:
```json
{
  "status": "accepted",
  "price_per_tonne_usd": 24,
  "quantity_tonnes": 40000,
  "contract_term_months": 12,
  "transcript": [
    {"speaker": "seller_agent", "message": "string"},
    {"speaker": "buyer_agent", "message": "string"}
  ]
}
```
`status` is one of: `accepted`, `rejected`, `countered`.

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
