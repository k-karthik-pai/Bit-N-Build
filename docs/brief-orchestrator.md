# Brief: Orchestrator

**Purpose:** run the fixed Tata Steel BSL scenario end-to-end: call the Circularity
Agent, then for candidate buyers call the Negotiation Agent (feeding in a real logistics
cost from the Logistics Optimizer), and assemble the final recommendation. Contract is in
AGENTS.md section 4.

**This is deterministic pipeline code, not a fourth reasoning agent.** Its job is
sequencing and assembly, not decision-making.

**Dependencies:** all three agents must expose the exact input/output shapes in
AGENTS.md. Can be stubbed against fake agent responses first and wired to the real ones
as they come online.

**Core logic:**
1. Call Circularity Agent → get ranked candidate buyers.
2. For each candidate (or just the top few, to save runtime), call Logistics Optimizer
   for a route, then Negotiation Agent with that route's cost.
3. Pick the outcome that maximizes `objective` (default: net value).
4. Assemble the final recommendation object, including margin and total net value math
   that must check out under manual verification (see PLAN.md polish block).

**Acceptance criteria:**
- One clean run against the fixed scenario, from raw material input to a final
  recommendation object
- Every number in the final output can be traced back to an agent's stated output — no
  numbers invented in the orchestrator itself
