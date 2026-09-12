# Brief: Circularity / Matching Agent

**Purpose:** given a material + quantity, return a ranked list of compatible buyers.
Contract is in AGENTS.md section 1 — build against it, do not deviate without flagging.

**Dependencies:** `materials.json`, `buyers.json` (stub is fine to start; see brief-dataset.md).

**Build against the stub first.** Swapping in real data later should require no code
changes if the schema is respected.

**Core logic:**
1. Look up the material's applications (e.g. LD slag → cement raw material, aggregate,
   road construction).
2. Filter buyers whose `material_required_id` matches and whose quality requirements are
   met by the material's composition.
3. Score/rank remaining candidates (e.g. by demand fit, or a simple weighted score) —
   keep the scoring function simple and explainable; a judge may ask "why did agent rank
   this buyer first."

**Explicitly out of scope for this agent:** price negotiation, logistics cost, final
buyer selection — that happens downstream.

**Acceptance criteria:**
- Given the stub dataset, returns a non-empty, ranked candidate list matching the output
  shape in AGENTS.md
- Runs standalone (no dependency on the negotiation or logistics code) for independent
  testing
