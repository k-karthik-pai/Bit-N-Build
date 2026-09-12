# AGENTS.md — guide for coding agents working on CIRCUIT

Read this before changing code. `README.md` is the user-facing overview; this file holds
the rules, architecture map and **contracts** that the code, tests and dashboard depend
on. Code comments cite sections of this file ("AGENTS.md §2", "§5 events").

---

## Ground rules

1. **Never write, print or log API key values** — not in code, tests, docs, events,
   commit messages or output. `.env` (and a team `env` file) are gitignored; inspect them
   by variable *name* only. `.env.example` holds settings and empty key slots only.
2. **Tests stay fully offline.** `tests/test_negotiation_loop.py` blanks every provider
   key and chain, strips timeout/pacing settings, and patches
   `agents.negotiation._call_llm_for_move` to raise on any call. New tests that touch the
   negotiation loop must use the same setup and script agent moves with
   `_fake_seller_moves` / `_fake_buyer_moves`. The suite must pass in seconds and with
   `HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9`.
3. **Contracts below are frozen.** §1–§5 are shared by the agents, orchestrator, tests
   and dashboard. Change code to match them; a contract change needs team agreement and
   an update here in the same commit.
4. **Don't hand-edit `data/buyers.json`.** Edit `data/generate_buyers.py` and run it;
   a test asserts the file equals the generator output. The generator must not add draws
   from its seeded RNG (that would silently change existing buyers).
5. **Live runs spend real API quota** (~25–50 calls each). Verify offline first; do one
   live run only when the change needs it.
6. `runs/` (recordings, `runs/.quota.json`) is generated and gitignored.
7. Match the surrounding code style; don't refactor unrelated code.

Comments tagged `R2-1`, `R3-2`, `R4-1`, … refer to earlier review rounds; the reasoning is
in the adjacent comment.

---

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests          # offline test suite
.venv/bin/python -m demo.web --replay demo/sample_run.jsonl  # dashboard, no network
.venv/bin/python -m demo.web                             # dashboard, "Live" uses .env keys
.venv/bin/python -m orchestrator.run --live --top-n 3    # live run in the terminal
.venv/bin/python -m orchestrator.run                     # deterministic pipeline
.venv/bin/python data/generate_buyers.py                 # regenerate buyers.json
```

All entry points accept `--env-file PATH` (existing environment variables take precedence).

---

## Architecture map

| Path | Role | Key symbols |
|---|---|---|
| `agents/circularity/` | §1 matching via a material→application→buyer knowledge graph | `find_candidates`, `run_circularity`, `knowledge_graph.py` |
| `agents/logistics/__init__.py` | §3 routing over the port graph (DFS over simple paths) | `optimize_routes`, `compute_transit_days` |
| `agents/negotiation/__init__.py` | §2 LLM seller/buyer loop, validators, fallback engine, provider chains, pacing, quotas | `negotiate`, `_run_thread_negotiation`, `_validate_move_legal`, `_validate_deal_legal`, `_deterministic_fallback_offer`, `_call_llm_for_move`, `_get_llm_move`, `_dynamic_floor` |
| `agents/negotiation/prompts.py` | seller/buyer system prompts and tactics | `seller_system_prompt`, `buyer_system_prompt` |
| `agents/validation.py` | shared finite/non-negative number check | `number` |
| `orchestrator/__init__.py` | §4 pipeline; concurrent threads; event envelopes; dashboard adapter | `run_pipeline`, `run_multi_agent`, `_run_concurrent_negotiations` |
| `orchestrator/run.py` | CLI (`--live`, `--top-n`, `--record/--no-record`, `--scenario`) | `main` |
| `demo/web.py` | FastAPI app + SSE stream | `create_app` |
| `demo/coordinator.py` | live/replay run lifecycle; calls `orchestrator.run_multi_agent` | `RunCoordinator` |
| `demo/events.py` | §5 validator used by the dashboard | `validate_run` |
| `demo/run_store.py` | recordings under `RUNS_DIR` (default `runs/`) | `RunStore` |
| `demo/render_log.py` | terminal log/card; `format_event` one-line event view | `render`, `format_event` |
| `demo/static/` | dashboard UI (vanilla JS/CSS) | |
| `demo/sample_run.jsonl` | hand-written reference run (36 events) used by replay and tests | |
| `tests/helpers_events.py` | shared §5 checks for any event list | `assert_events_valid` |

Two paths through the negotiation:

- **Legacy deterministic path** — `run_pipeline()` with defaults: sequential, formula
  price, BATNA baseline pass. Kept for the plain CLI and old tests.
- **Concurrent multi-agent path** — `run_pipeline(use_llm=True | emit_event=… | top_n≠3)`:
  one seller agent vs the top 3–5 buyers in parallel threads, validator-gated, emits §5
  events. `use_llm=False` with `emit_event` runs the same loop with the deterministic
  engine making every move (offline).

---

## §1 Circularity / matching agent

**Responsibility:** given a material and quantity, find and rank compatible buyers.
**Does not:** negotiate, price logistics, or choose the winner.

Input `{material_id, quantity_tonnes, seller_id}` → output
`{candidates: [{buyer_id, application, compatibility_score, notes}]}`. A buyer qualifies
only if the material's composition meets both the application's and the buyer's quality
thresholds. `notes` carries the graph path
(`Material(ld_slag) -[SUITABLE_FOR]-> Application(...) -[REQUIRED_BY]-> Buyer(...)`).

---

## §2 Negotiation agent

**Responsibility:** run one thread between a **seller agent** and one **buyer agent** —
separate LLM contexts, each holding only its own private constraints — and return an
accept/reject/counter outcome whose terms passed the validators.
**Does not:** let an LLM set a bound, see the other side's reservation values, finalize
an unvalidated deal, or pick the winning buyer.

### Moves (validated JSON, not native tool calling)

```json
{"action": "make_offer", "price_per_tonne_usd": 26.5, "quantity_tonnes": 60000,
 "contract_months": 24, "message": "shown to the counterparty", "rationale": "private"}
{"action": "accept_offer", "offer_id": "shah_cement-o3", "message": "…", "rationale": "…"}
{"action": "reject", "reason": "…", "message": "…", "rationale": "…"}
{"action": "request_info", "topic": "route_cost | best_competing_offer", "rationale": "…"}
```

Malformed JSON or an unknown action is an invalid move. `best_competing_offer` is
seller-only. `offer_id` is assigned by the runtime (`<deal_id>-o<n>`), never by the LLM.

### Validator gates

- **move-legal** (every `make_offer`) — checks only the **proposer's own** bounds: seller
  price ≥ its current floor, buyer price ≤ its ceiling, quantity ≤ own availability /
  demand, `contract_months` inside own range. Anchoring outside the counterparty's range
  is legal. A bounce reason may cite only the proposer's own constraints.
- **deal-legal** (`accept_offer`) — the offer is the counterparty's latest open offer and
  satisfies both sides' bounds. A bounce citing the acceptor's own bound tells it what to
  do next (e.g. counter at its own quantity); a counterparty-bound failure stays generic.

Also invalid:
- a number in `message` that is not one of the move's own fields, a field of an offer
  already delivered **in the same thread**, or the thread's route freight per tonne;
- a message stating the agent's own reservation value (floor / ceiling / BATNA) as a
  number not covered by the rule above;
- a seller claim of "other interest" with no other live thread, or of "a better offer"
  with no live **buyer** bid elsewhere of higher total net value.

### Seller floor

`max(seller_min, BATNA)`, raised by the best live **buyer bid** in other live (not
rejected/released) threads, converted to this buyer's route. The seller's own asks never
raise it. The LLM defends the floor; it never sets it.

### Limits and fallback

Max 6 rounds; at the limit the best open counter becomes `countered` only if it still
passes the seller's floor, else `rejected`. Two invalid moves in a row, or a provider
chain that fails entirely, → the deterministic engine makes that move and a `fallback`
event is emitted. Fallbacks never retract a concession: the seller re-offers
`max(last delivered price, current floor)` with the same quantity/months; the buyer never
bids below its last bid (first buyer fallback `0.85 × ceiling`, then
`last + 0.4 × (ceiling − last)`, capped at `ceiling − 0.5`).

### Output

`{status, price_per_tonne_usd, quantity_tonnes, contract_term_months, transcript,
rounds, final_offer_id, agents}`; `status` ∈ `accepted | rejected | countered`. Private
`rationale` appears only in events.

### Concurrency

The orchestrator runs one thread per top-N buyer. Accepted/countered threads are
agreed-pending; the orchestrator selects one by total net value (§4) and releases the
rest. **At most one deal closes per run.**

---

## §3 Logistics optimizer

**Responsibility:** price routes it is asked about; it does not choose buyers.

Input `{origin_port_id, destination_port_id, cargo_tonnes, deadline_days}` → output
`{routes: [{route_id, path_port_ids, distance_km, transit_days, cost_per_tonne_usd}],
recommended_route_id}`. Recommended = cheapest route meeting the deadline across all
paths (error if none). Transit days = distance / 444.5 km/day + 0.5 day per intermediate
stop, rounded to 2 decimals.

---

## §4 Orchestrator

A deterministic pipeline, not an agent. Input
`{seller_id, material_id, quantity_tonnes, objective: "maximize_net_value"}` → the
**recommendation**:

```json
{"material": "ld_slag", "quantity_tonnes": 65000, "buyer_id": "shah_cement",
 "buyer_name": "Shah Cement Industries Ltd", "price_per_tonne_usd": 26.5,
 "route": {"origin_port": "dhamra", "destination_port": "chittagong",
           "distance_km": 540.8, "transit_days": 1.22, "cost_per_tonne_usd": 7.0},
 "margin_per_tonne_usd": 16.0, "total_net_value_usd": 1040000.0,
 "co2_avoided_tonnes_estimate": 55250.0, "pipeline_log": []}
```

Net value everywhere = `(price − freight − 2.00 handling − 1.50 processing) × quantity`.
Selection compares **total** net value; BATNA compares per-tonne rates (a total-value BATNA
made small-buyer floors explode). `co2_avoided_tonnes_estimate` = quantity × 0.85 for
cement-clinker buyers, else 0, and must be labelled an estimate in any UI.

`run_multi_agent(*, run_id, top_n, emit)` is the dashboard entry point: it calls
`run_pipeline(use_llm=True, run_id=run_id, emit_event=emit, record=False)` — the
dashboard's `RunStore` is the single recorder.

---

## §5 Event schema

One JSON object per event; live runs stream them over SSE and record them as JSONL.

```json
{"seq": 12, "ts": "2026-09-12T10:15:07.300Z", "run_id": "…", "deal_id": "shah_cement",
 "type": "offer", "from_agent": "seller_agent", "to_agent": "buyer_agent:shah_cement",
 "model": "gemini:gemini-3.1-flash-lite", "payload": {},
 "validator": {"ok": true, "gate": "move_legal", "reason": "within your bounds"},
 "delivered": true}
```

| Field | Rule |
|---|---|
| `seq` | starts at 1, +1 per event, the ordering key |
| `ts` | ISO-8601 UTC with **exactly 3** millisecond digits, non-decreasing |
| `run_id` | same for every event; must equal the id the dashboard passed |
| `deal_id` | buyer_id of the thread, or `null` for run-level events |
| `from_agent` / `to_agent` | `seller_agent`, `buyer_agent:<id>`, `logistics_agent`, `circularity_agent`, `orchestrator`, `validator`, `runtime` (to_agent may be `null`) |
| `model` | `provider:model` (alias included, e.g. `gemini_2:…`) for LLM-produced events, else `null` |
| `validator` | on agent moves (`offer`, `accept`, `reject`, `info_request`), else `null` |
| `delivered` | moves only; `false` = bounced back to its author |

| type | payload |
|---|---|
| `run_started` | `{seller_id, material_id, quantity_tonnes, objective, top_n}` |
| `match` | `{matched_count, selected: [{buyer_id, buyer_name, application, compatibility_score, port_id}]}` |
| `route` | `{origin_port, destination_port, route_id, distance_km, transit_days, cost_per_tonne_usd}` |
| `thread_started` | `{buyer_id, buyer_name, seller_model, buyer_model}` |
| `offer` | `{offer_id, round, price_per_tonne_usd, quantity_tonnes, contract_months, message, rationale}` |
| `accept` | `{offer_id, round, message, rationale}` |
| `reject` | `{round, reason, message, rationale}` |
| `info_request` / `info_response` | `{round, topic, rationale}` / `{round, topic, data}` |
| `fallback` | `{round, agent, cause, detail}`; cause ∈ `timeout`, `provider_error`, `invalid_moves`; the deterministic move follows with `model: null` |
| `thread_result` | `{status, price_per_tonne_usd, quantity_tonnes, contract_months, rounds, final_offer_id, total_net_value_usd, agreed_pending}` |
| `released` | `{reason}` — every accepted/countered thread not selected |
| `deal_closed` | `{buyer_id, offer_id, total_net_value_usd}` |
| `recommendation` | the §4 object |
| `run_completed` / `run_failed` | `{duration_ms, llm_calls, validator_bounces, fallbacks}` / `{error}` — exactly one, last |

Rules: `rationale` is never sent to the counterparty. Bounced offers have
`offer_id: null` and go back to their author, whose next event is the retry. A round is one
delivered move; info requests and bounces share the round of the move they precede.
Validate any event list with `demo.events.validate_run` (dashboard) and
`tests.helpers_events.assert_events_valid` (tests).

---

## §6 Data

| File | Content |
|---|---|
| `data/seller.json` | seller, `nearest_port_id`, `material_id`, availability, min/preferred price, `contract_months_min/max`, `preferred_contract_months` |
| `data/buyers.json` | 25 buyers (13 real-reference, 12 synthetic): port, demand, price ceiling, quality thresholds, `contract_months_min/max`, `is_real_reference`, `source_note` — **generated** |
| `data/materials.json` | composition and applications with quality thresholds |
| `data/ports.json`, `data/routes.json` | port coordinates; route distances and base freight |
| `data/RESEARCH_FINDINGS.md` | sources for the real-reference anchors and price/freight benchmarks |

Buyer contract ranges come from demand tier (≥40,000 t: 12–36 months; ≥15,000 t: 6–24;
else 3–12); every tier contains 12, the legacy fallback's fixed term. Prices, demand and
contract terms are illustrative, not real quotes.

---

## §7 LLM providers and configuration

Configured entirely through `.env` (template: `.env.example`).

- **Chains:** `SELLER_LLM_CHAIN`, `BUYER_1_LLM_CHAIN` … `BUYER_3_LLM_CHAIN` =
  comma-separated `provider:model`, split on the **first** colon (OpenRouter ids contain
  `:free`). Tried left to right on failure; the deterministic engine is the last resort.
  Buyer slots follow match rank.
- **Providers:** `gemini` (OpenAI-compatible endpoint), `nvidia`, `openrouter`, `openai`.
  Extra keys are **aliases** (`gemini_2`, `nvidia_3`, `openrouter_4`): same URL and quirks
  as the base, own key `<ALIAS>_API_KEY`; an alias with an empty key is skipped.
- **Quirks:** Gemini needs `reasoning_effort` (`GEMINI_REASONING_EFFORT=low`) or it can
  spend its token budget thinking; NVIDIA DeepSeek needs `chat_template_kwargs.thinking=false`.
- **Pacing / quotas:** `GEMINI_RPM` / `GEMINI_RPD` are per model (`model:limit,…`);
  `OPENROUTER_*` and `NVIDIA_RPM` are plain numbers. Gemini keys share one bucket unless
  `GEMINI_QUOTA_SCOPE=key` (only when every key is in a different Google project).
  Daily counts and exhausted-until times persist in `runs/.quota.json`
  (`NEGOTIATION_QUOTA_FILE` overrides). Per-provider timeouts: `<BASE>_TIMEOUT_S`.
- Reality check from live runs: Gemini 3.1 Flash-Lite is the reliable workhorse; 3.5 Flash
  and 3 Flash have tiny free daily quotas (backups only); NVIDIA's trial endpoint often
  stalls; OpenRouter free models are rate-limited per account.
