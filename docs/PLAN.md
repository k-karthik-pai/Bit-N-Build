# PLAN.md

## Problem statement
Supply Chain Circularity & Industrial Symbiosis.

## Core scenario (fixed, do not swap mid-build)
Tata Steel BSL exports LD slag via Dhamra Port to cement manufacturers in Bangladesh
(real reference case; our per-buyer prices/quantities are illustrative, anchored to
published aggregate trade data — see DATA.md).

## What we are building
1. Circularity / Matching Agent — material → applications → ranked candidate buyers
2. Negotiation Agent — constraint-validated deal-making between seller and a buyer
3. Logistics Optimizer — route, cost, transit time between two ports (graph + shortest path)
4. Orchestrator — wires the three above into one end-to-end run against the fixed scenario
5. Demo output — terminal-style run log + a final "recommended deal" summary

## What we are explicitly NOT building (cut list)
- Agricultural Micro-Economies module — reference it verbally in the pitch as "the same
  abstraction generalizes," do not implement it
- General-purpose maritime optimizer (dark-vessel detection, global fuel-efficient routing,
  debris collection) — out of scope; we only need point-to-point route/cost/ETA for our
  own shipment
- Interactive circularity graph visualization — only build if everything else is done and
  demo-stable with time to spare
- Live step-by-step "simulation mode" UI — a clean log + summary card is enough
- Real-time/live data feeds of any kind — dataset is a fixed snapshot, not a live pipeline

## Timeline (adjust hours to actual slot length)
- 0:00–0:15 — Freeze contracts together (see AGENTS.md, DATA.md). No one starts coding before this.
- 0:15–1:00 — Parallel: Track A (data) does real-anchor research + generates full dataset;
  Track B (everyone else) builds against a 5-entry stub dataset matching the frozen schema.
- 1:00 — Swap stub data for Track A's real dataset. Should be a one-line change if the
  schema was actually frozen.
- Integration block — wire Circularity → Negotiation → Logistics through the Orchestrator,
  get ONE clean end-to-end run on the fixed scenario before anything else.
- Polish block — check that price − logistics − handling actually produces the claimed
  margin everywhere in the output; build demo log/summary; rehearse Q&A on negotiation
  constraints and routing calculation.
- Buffer block — always reserved before submission.

## Ownership
(fill in names)
- Dataset (Track A): ___
- Circularity Agent: ___
- Negotiation Agent: ___
- Logistics Optimizer: ___
- Orchestrator + Demo: ___

## Judging-criteria notes (keep visible while building)
- Agentic AI Implementation: the LLM proposes, a deterministic validator/optimizer decides —
  never let the LLM assert a number that wasn't checked against constraints.
- Technical Implementation: fewer, clearly distinct agents beats many overlapping ones —
  be ready to explain what each of the three does that the others couldn't.
- Solution Effectiveness: numbers must be internally consistent under questioning.
