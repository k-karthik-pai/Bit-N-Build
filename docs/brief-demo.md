# Brief: Demo Output

**Purpose:** present the orchestrator's run in a way that's clear and convincing to judges
in a short slot, without eating build time better spent on the agents.

**Scope guardrail:** a terminal-style run log plus a final summary card is enough. Only
attempt an interactive circularity graph or a polished live "simulation mode" UI if the
three agents + orchestrator are already demo-stable with real time left over — see the
cut list in PLAN.md.

**Minimum viable demo:**
1. A log showing each step as it happens, e.g.:
   ```
   [✓] Candidate buyers found: 12
   [✓] Compatibility filter: 5 remain
   [✓] Negotiating with Buyer X...
   [✓] Deal accepted: $24.10/t, 40,000t, 12-month term
   [✓] Route: Dhamra → Chittagong, 3.5 days, $8.20/t
   ```
2. A final summary of the recommendation object from AGENTS.md section 4 — material,
   buyer, price, route, margin, total net value, CO2 estimate (explicitly labeled as an
   estimate).

**Acceptance criteria:**
- Every number shown in the demo matches what the orchestrator actually produced — no
  hand-edited or hardcoded display values
- Rehearsed answer ready for: "how does the negotiation agent decide the price" and "how
  is the route cost calculated" — these are the most likely judge questions
