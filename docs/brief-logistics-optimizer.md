# Brief: Logistics Optimizer

**Purpose:** given origin/destination ports and cargo details, return 2–3 route options
with distance, transit time, and cost. Contract is in AGENTS.md section 3.

**Scope guardrail:** this is NOT a general maritime routing system — no dark-vessel
detection, no global fuel-optimization, no debris collection. Just point-to-point
route/cost/ETA for a small, fixed set of real ports (Dhamra, Chittagong, Mongla — add
more only if the core scenario needs them).

**Dependencies:** `ports.json` (real distances — see DATA.md sourcing notes; use a
distance calculator, don't hand-estimate). Can start with placeholder distances and swap
once Track A delivers real ones.

**Core logic:**
1. Represent ports as nodes, routes as edges with distance/cost/time.
2. Use Dijkstra or A* for shortest/cheapest path — a graph of a handful of nodes doesn't
   need anything fancier.
3. Return the top 2–3 candidate routes plus a recommended one.

**Acceptance criteria:**
- Given the fixed scenario's origin/destination, returns realistic distance/cost/transit
  numbers (sanity-check against real-world shipping times, e.g. Bay of Bengal crossings
  are days, not hours)
- Runs standalone for independent testing
- Output's `cost_per_tonne_usd` is what the Negotiation Agent consumes — keep the field
  name and unit exactly as specified in AGENTS.md
