# NEXT_STEPS.md

**Demo goal:** show multiple LLM agents genuinely communicating with each other — making
real decisions, reacting to each other's moves — while deterministic validators keep every
number honest. Everything below is prioritized against that goal.

Build order: (0) freeze the event schema, (1) multi-agent LLM negotiation, (2) live
streaming UI + run recording, (3) knowledge-graph matching as a stretch. Items 1 and 2 run
in parallel once the schema in section 0 is frozen — the UI is built against a hand-written
sample event file, not against finished negotiation code.

---

## 0. Freeze contracts first — ✅ DONE (team agreed)

| Item | Where |
|---|---|
| Direction agreed; negotiation contract revised (LLM makes moves, two validator gates: move-legal on the proposer's own bounds, deal-legal at accept) | `docs/AGENTS.md` §2 |
| Event schema frozen | `docs/EVENTS.md` |
| Reference run for the UI (36 events: 3 concurrent threads, 2 validator bounces + retries, 1 timeout fallback, accepted / countered / released, recommendation) | `demo/sample_run.jsonl` |
| Schema + sample checked against the real data files | `tests/test_events_sample.py` |
| Contract-length ranges added to the data (derived from demand tier, all contain 12) | `data/generate_buyers.py`, `data/buyers.json`, `data/seller.json`, `docs/DATA.md` |
| Provider/model assignment per agent role, with fallback chains and pacing | `.env.example`, "Model and provider" below |
| Supply decision: one deal closes per run; other agreed threads are released | `docs/AGENTS.md` §2, `docs/LIMITATIONS.md` |

**Verified:** OpenRouter free limits (see "Model and provider" below).
**Still to verify by hand:** Gemini's current per-model limits in AI Studio — fill
`GEMINI_RPM` in `.env` from that page, not from memory.

---

## 1. Multi-agent LLM negotiation

**Problem being fixed:** even with `use_llm=True`, the LLM only rewords a template around
a deterministically-computed price (`agents/negotiation/__init__.py` picks the midpoint;
the orchestrator also passes `use_llm=False`). This section makes the LLMs responsible
for the actual moves; the validator becomes a real gate instead of a rubber stamp.

### Architecture change
Invert control flow. Old: formula computes price → LLM narrates → validator confirms.
New: LLM proposes a move via tool call → validator checks it → accepted moves proceed,
rejected ones bounce back to the LLM with the reason for a re-proposal.

### Topology: one seller, N concurrent buyers (the multi-agent part)
- One **seller agent** negotiates with the **top 3–5 matched buyers at once**, each buyer
  its own **buyer agent** (separate LLM context, separate private constraints).
  Negotiations run concurrently (`asyncio`), so several threads visibly progress at once.
- The seller's leverage comes from **live competing offers on the table**, not a
  precomputed baseline. This replaces the current deterministic BATNA baseline pass (which
  would otherwise double the LLM calls) with genuine agent-to-agent interdependence: a
  move in one thread changes the seller's behavior in another.
- **Leverage claims are validated.** If the seller says "we have a better offer," the
  validator checks that such an offer actually exists in another live thread; a false
  claim is bounced like any other invalid move.
- The seller's hard floor stays deterministic: `max(seller_min, best live competing net
  value converted to $/t for this buyer's route)`. The LLM defends it; it never sets it.
- **Logistics is consulted as an agent.** `request_info("route_cost", port)` goes to the
  Logistics Optimizer and appears in the feed as a `logistics_agent` message, not a hidden
  function call. (The optimizer itself stays deterministic — it answers, it doesn't negotiate.)
- Only the top 3–5 buyers get LLM negotiation; 25 buyers × 6 rounds × 2 agents is too slow
  and too costly for a live demo.

### Tools exposed to the negotiation LLMs
- `make_offer(price_per_tonne, quantity_tonnes, contract_months, rationale, message)` —
  one tool for both opening offers and counters (they were redundant as separate tools)
- `accept_offer(offer_id)` — must reference a specific offer on the table, so the
  validator can confirm both sides accepted identical terms
- `reject(reason, message)` — walk away from this thread
- `request_info(topic)` — route cost from logistics, or (seller only) a summary of the
  best live competing offer from the orchestrator

**Non-price terms need data before they can be traded.** `contract_months` is only
negotiable once `buyers.json` / `seller.json` define acceptable ranges (e.g.
`contract_months_min/max` per buyer) and the validator checks against them. Payment
terms are **dropped** from this version — nothing in the data defines what faster payment
is worth, so letting the LLM trade it would be unbounded invention, which is exactly the
hallucination this architecture exists to prevent. Add it back only with a defined
valuation (e.g. seller's $/t discount per 30 days faster).

### Hidden information (mimics a real negotiation)
Each agent's prompt contains only its own private constraints plus the public history
(offers made, never reservation values). The validator holds both sides' true bounds.

**Leak handling:** if an agent's `message` states its own hidden reservation value (floor,
ceiling, BATNA), the turn is **bounced back for a rewrite**, not stripped — stripping
numbers produces broken sentences. Reuse the placeholder/number-extraction check already
in `_try_llm_transcript` for detection, and also reject any number in `message` that
doesn't match the structured tool-call fields (catches hallucinated figures in prose).

### Real-dealer tactics to bake into the system prompts
- Anchor slightly outside your preferred position, not at it
- Concessions get smaller each round, not constant-sized
- Trade contract length and committed volume for rate (within the data-defined ranges)
- Cite leverage without revealing exact numbers ("we have other buyers interested") —
  and only when it's true (validator enforces this for the seller)
- Telegraph a walk-away without stating the literal reservation price

### Termination and retry limits
- Acceptance of an `offer_id` by the counterparty → validator re-checks → `deal_closed`
- Explicit rejection → thread ends, logged with reason
- Max 6 rounds without convergence → best counter on the table becomes `countered`
  (pending), per `LIMITATIONS.md`
- **Retry cap:** 2 invalid proposals in a row from the same agent → that turn falls back
  to the deterministic engine, logged as a visible `fallback` event (never silent)
- Per-call timeout (existing 20s) → same visible fallback

### Final selection + explanation
Final buyer selection stays deterministic (highest total net value, as today). Optionally,
a **deal-desk LLM step** writes the "why this buyer won" explanation, grounded only on the
computed numbers — run the same number-extraction check on it so it can't cite a figure
that isn't in the result.

### Model and provider (decided in Step 0)
- **Mixed providers, one per agent role** — a Gemini free-tier key plus OpenRouter free
  models, configured as per-role chains in `.env.example`. Gemini is a separate quota pool,
  so it adds real headroom; its OpenAI-compatible endpoint means the existing `openai`
  client works with a different `base_url`. The vendor mix is also a demo asset: each chat
  bubble is badged with the model that produced it.
- **Seller on the biggest pool.** The seller speaks in every thread — with 3 buyers it
  makes about as many calls as all buyers combined (~19 of ~36 calls in a 6-round run).
  Never split it across pools.
- **Two OpenRouter free models do NOT double the quota** — confirmed 2026-09-12
  (openrouter.ai/docs/api-reference/limits): free-model limits are account-wide across
  all models and keys — 20 req/min, and 50 req/day for accounts with under $10 of credits
  ever purchased (1000 req/day after $10). Our account is on the 50/day tier
  (`is_free_tier: true`). Buyers use ~17 OpenRouter calls per run → only 2–3 runs/day,
  not enough for rehearsal plus the ~10-run acceptance check. **Action: one-time $10
  OpenRouter credit purchase** → 1000/day (~55 runs/day).
- **Per-minute limits matter more than daily ones** with concurrent threads: pace calls
  per provider (`*_RPM` in `.env`) and stagger thread starts.
- **Moves are validated JSON, not native tool calling** — free models vary widely in
  tool-call support; a malformed response is just an invalid move that bounces.
- Recorded replay (section 2) is the demo-day safety net if any free tier is exhausted.
- Temperature ~0.5–0.7 so trajectories differ between buyers without going erratic.

### Testing
- Keep the existing deterministic engine as both the fallback and the default test path —
  the current test suite stays valid and offline.
- Add scripted fake agents (canned tool-call sequences) to test the loop, the validator
  bounce, the retry cap, leak detection, and false-leverage detection without any API calls.

### What must be logged per round (as events per section 0)
- Which agent, which tool call, the raw proposed terms, public message, private rationale
- Validator verdict (pass / rejected + reason) — a rejection is a *good* thing to have in
  the log; it's the clearest evidence the guardrail is doing real work
- Fallbacks, timeouts, info requests/responses
- Final accepted / countered / rejected terms per buyer thread

### Acceptance criteria
- Across ~10 test runs, validator rejections followed by a successful re-proposal occur in
  a meaningful share of runs, with at least one real recorded instance kept for the demo.
  (Not "guaranteed on the first proposal" — LLM output is nondeterministic, and prompting
  an agent to fail on purpose would be staging.)
- Two different buyer threads show visibly different trajectories (round count, terms mix,
  who concedes) — not the same exchange shape with a different final price.
- At least one run where a competing offer in one thread measurably changes the seller's
  behavior in another (the multi-agent claim, demonstrated rather than asserted).
- No agent's `message` ever states its **own** hidden reservation value. (Not "never
  contains the other side's number" — a buyer can coincidentally offer exactly the seller's
  floor without knowing it.)
- Every number in every transcript message matches a validated structured field.

---

## 2. Live streaming UI + run recording

Built in parallel with section 1, against `demo/sample_run.jsonl` from section 0.

### Stack
- **FastAPI** backend: a thin wrapper over the existing orchestrator — no orchestrator
  logic duplicated in the API layer.
- **Server-Sent Events** for live streaming. This is not a big lift: the orchestrator
  already has an `on_progress` callback, so it's ~40 lines — run the pipeline in a worker
  thread/task, push events into a queue, stream them from a `StreamingResponse`, consume
  with `EventSource` in the browser. SSE is one-way, which is all this needs; no WebSockets.
- **Frontend:** a single plain HTML/JS page, no build step.

### Why live streaming, not batch-then-replay
A full run is ~60 LLM calls — minutes of wall time. Batch-then-replay means a blank screen
for minutes followed by a simulated feed, and the honest answer to a judge asking "is this
live?" becomes "no." Streaming shows the agents working as they actually work.

### Record and replay (the safety net)
- Every run writes its events to `runs/<run_id>.jsonl` as they stream.
- `--replay <file>` mode streams a recorded run with its original timing through the
  **same** SSE endpoint and event format — one frontend code path for both.
- Rehearse on live runs; keep a known-good recording ready in case wifi or the LLM
  provider fails mid-demo.

### Hosting
Demo from **localhost on the presenting laptop**. Vercel serverless functions have
execution time limits and Python streaming caveats that a multi-minute run is likely to
hit — verify the plan's limits before relying on it. Vercel is fine for hosting a
**shareable replay** of a recorded run.

### Layout
- **Left — buyer board:** one card per buyer with match score and a status chip
  (matched → negotiating → accepted / countered / rejected).
- **Center — negotiation threads:** one chat per buyer, seller/buyer bubbles on opposite
  sides in distinct colors, a price tag on every offer, a ✓ / ✗ validator badge, and the
  private rationale as a collapsible "thinking" line. Validator rejections and fallbacks are
  visibly distinct events in the same feed, not hidden. Logistics and circularity events
  appear as neutral system lines.
- **Right — price convergence chart:** offers per round for each thread, drawn inside the
  shaded floor–ceiling band (the band is revealed to the audience, never to the agents).
- **Bottom — recommendation card:** built directly from the `AGENTS.md` section 4 object,
  same fields as today's `render()`. CO2 stays explicitly labeled as an estimate.

### Fallback UI
If time runs short, a `rich.live` terminal dashboard over the same event stream is roughly
half a day of work — less impressive, but still shows concurrent threads. Avoid Streamlit:
multiple concurrently-streaming chats are awkward in it.

### Acceptance criteria
- A full live run — including at least one validator rejection-and-retry — renders
  correctly as it happens, not only in replay.
- Replay of a recorded run is indistinguishable in the UI from a live run (same code path).
- No backend logic duplicated in the FastAPI layer; it only calls the orchestrator.

---

## 3. Knowledge graph for material–buyer matching (stretch)

**Moved to stretch:** this is deterministic matching — it adds no LLM agent and no
agent-to-agent communication, so it doesn't serve the demo goal above. Build it only after
sections 1 and 2 are demo-stable.

**Problem it would fix:** the Circularity Agent does direct field matching
(`material_required_id` equality + threshold check). Graph traversal would give "why did
this buyer qualify" a structural answer, not just a boolean.

**Dependency note:** `networkx` is **not** currently a dependency — `requirements.txt`
has only `openai` and `python-dotenv`, and the Logistics Optimizer uses its own DFS. Adding
it is cheap, but it is a new dependency, not a reused one. No graph database (Neo4j etc.).

### Graph shape
- **Nodes:** `Material`, `Application`, `Buyer`, `Port`
- **Edges:**
  - `Material —SUITABLE_FOR→ Application` (attributes: composition thresholds, e.g. `{"CaO_min": 40}`)
  - `Application —REQUIRED_BY→ Buyer` (attributes: buyer's quality requirements)
  - `Buyer —LOCATED_AT→ Port`

### Matching becomes traversal
Traverse from the material node through `SUITABLE_FOR` edges whose thresholds the
material's composition satisfies, then on to buyers requiring those applications. The path
(`LD Slag → cement clinker substitute → Buyer X`) goes into the existing `notes` field — no
contract change.

### Scoring
Weight edges by how comfortably the material clears each threshold (normalized headroom);
path weight becomes `compatibility_score`, same shape and range as today.

### Stretch on the stretch
Highlight the traversal path in the UI when a buyer is selected.

### Acceptance criteria
- Same qualifying buyers as the current threshold check on the real dataset (reasoning
  upgrade, not a behavior change)
- `notes` contains a human-readable path, not just a score
- Runs standalone, independent of negotiation/logistics code

---

## 4. Doc housekeeping (do alongside section 1)

- ~~`docs/AGENTS.md` §2 contract change~~ — done in Step 0.
- `docs/PLAN.md` — the cut list still excludes the live step-by-step UI and real-time
  feeds; update it to reflect this plan.
- `docs/LIMITATIONS.md:9` and `docs/ROUTES_DISTANCE_FIX.md:17` — still say
  `data/RESEARCH_FINDINGS.md` "was not committed"; it now is (`c824ace`).
- `docs/LIMITATIONS.md` — the "Optional LLM dialogue is wording only" bullet becomes false
  once section 1 lands; rewrite it to describe the validator-gated agent loop.
- `README.md` / `demo/README.md` — add run instructions for the live UI and `--replay`.
