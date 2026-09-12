# Brief: Dataset (Track A)

**Purpose:** produce `seller.json`, `materials.json`, `buyers.json`, `ports.json` matching
the schema in DATA.md, so every agent has real data to swap in once built.

**Dependencies:** none — this can and should start immediately, in parallel with everyone
else building against a stub.

**Steps:**
1. Write a 5-entry stub `buyers.json` (fake names, plausible numbers, correct shape) within
   the first 5 minutes and hand it to the rest of the team — don't wait for real data to
   unblock others.
2. Research real anchors per DATA.md sourcing notes: 6–8 real Bangladeshi cement
   companies, one aggregate price anchor, port coordinates/distances for Dhamra,
   Chittagong, Mongla.
3. Hardcode the real anchors as `is_real_reference: true` entries.
4. Write a short script to generate the remaining synthetic buyer entries around those
   anchors, marked `is_real_reference: false`.
5. Fill in `materials.json` for the seller's material (composition + applications) and
   `seller.json` for the Tata Steel BSL–modeled scenario.
6. Replace the stub file in `/data/` with the final version — flag the swap to the team.

**Acceptance criteria:**
- All four files validate against the schema in DATA.md
- Every real-reference entry has a `source_note`
- 20–50 total buyer entries
- Port coordinates/distances came from a distance calculator, not estimation
