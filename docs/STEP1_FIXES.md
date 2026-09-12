# STEP1_FIXES.md — review findings for the Step 1 negotiation work

## Round 5 (2026-09-12) — OPEN, not yet fixed

Round 4 verified: 71 tests offline (2.7 s, proxy-blocked); live run `runs/run-e32f3cd7.jsonl`
58 s, 20 LLM moves, 4 legitimate bounces, 2 fallbacks, **Shah accepted at $26.90/t ×
65,000 t (net $1,066,000)**, Crown accepted then released, Seven Circle correctly rejected.

### R5-1. Seller fallback "hold" retracts a concession

**Evidence (`run-e32f3cd7`):** Crown thread — seller delivered $26.50 (round 3), then two
LLM offers bounced ($25.80, $25.95, below the $26.50 floor), then the deterministic
fallback emitted **$27.00** and Crown accepted at its $27.00 ceiling. Seven Circle thread:
same pattern, $26.50 → bounced ×2 → fallback **$27.00** → buyer rejected. The seller's last
delivered offer ($26.50) was still valid (≥ the $26.50 floor), yet the fallback moved the
price *up* by $0.50 — the seller visibly takes back a concession, which a judge can spot
and which contradicts the spec ("deterministic engine holds the last valid offer",
`docs/NEXT_STEPS.md` §1, `docs/EVENTS.md` fallback detail).

**Fix:** in the seller branch of `_deterministic_fallback_offer`
(`agents/negotiation/__init__.py`), when the seller has a last delivered offer in this
thread that still passes move-legal against the *current* floor, re-emit exactly that
offer's terms (price, quantity, contract_months). Only if it no longer passes (the dynamic
floor rose above it since) move to the current floor — never above
`max(last_delivered_price, current_floor)`. With no prior seller offer, keep today's opening
behavior. Same principle for the buyer fallback (R4-2): never below its own last
delivered bid.

**Tests (offline):** (a) seller last delivered $26.50, floor $26.50, two bounces →
fallback offer is $26.50 with the same qty/months; (b) floor rose to $27.00 after the
seller's $26.50 → fallback is $27.00; (c) property: across a thread, a seller fallback
price is never above its previous delivered price unless the floor forced it; buyer
fallback never below its previous delivered bid.

**Verify:** no live run needed — the offline tests cover it. Optionally re-check
`run-e32f3cd7`'s scenario by replaying its moves as fake moves.

### Also noted (not a code defect, decision for the team)

NVIDIA DeepSeek produced **0 moves** in `run-e32f3cd7` (3/3 attempts on `nvidia_3` stalled
or failed, silently advanced to `gemini_3`). The demo currently shows one vendor (Gemini,
three accounts, two models). If vendor diversity matters for the pitch, NVIDIA's trial
endpoint is not reliable enough to depend on.

---

## Round 4 (2026-09-12) — done, verified

Round 3 verified: 64 tests offline (2.5 s, proxy-blocked); live run `runs/run-dc3d40f5.jsonl`
81 s, 11 bounces (all legitimate), 5 fallbacks (all `invalid_moves`), 28 provider attempts
→ 24 moves, no OpenRouter, NVIDIA stalls absorbed by chain-advance. **Still 0 `accepted`.**
Round 4 targets that.

### R4-1. Quantity mismatch blocks every accept

In `run-dc3d40f5`, Shah's thread: the buyer bid 65,000 t (its `annual_demand_tonnes`) in
every offer, but the seller LLM kept offering 80,000 → 75,000 → 70,000 t. The buyer LLM
then tried twice to `accept_offer` the 70,000 t offer; `deal_legal` correctly bounced it
(`quantity exceeds bounds`), two bounces → fallback, max rounds → only `countered`.

**Fix (no rule changes — the gates are right):**
- Seller system prompt (`agents/negotiation/prompts.py`): quantity should track what the
  buyer has asked for in its offers; offering more than the buyer's latest requested
  quantity is rarely accepted — trade price/term instead.
- Buyer system prompt: never accept an offer whose quantity exceeds your demand or whose
  terms fall outside your bounds — counter with `make_offer` at your quantity instead.
- Bounce reason text for a buyer accept over its demand should be actionable and cite
  only the buyer's own constraint, e.g. `offer quantity 70000 exceeds your demand 65000 —
  counter with make_offer at your quantity instead of accepting`. (Same pattern for any
  own-bound accept failure: say what to do next.)
- Tests: the buyer-accept-over-demand bounce reason contains "counter" and does not
  contain the seller's floor; prompts contain the new guidance (string check is fine).

### R4-2. Buyer deterministic fallback concedes too much at once

The buyer fallback bids `ceiling − 1.0` immediately (Shah: $27.00 at seq 9/24). Because a
live buyer bid raises the seller's floor in *other* threads (R2-2, correct), this one
fallback bid pushed the floor to $27.00 — above Seven Circle's $26 ceiling and at Crown's
$27 — causing 8 of 11 bounces and 3 of 5 fallbacks.

**Fix:** give the buyer fallback a gradual concession schedule mirroring the seller's:
with no prior bid, open at `round(0.85 × ceiling, 2)`; after that,
`next = last_bid + 0.4 × (ceiling − last_bid)` rounded to cents (shrinking steps),
capped at `ceiling − 0.5`; never jump straight to `ceiling − 1`. Document the formula
in the code comment. Deterministic and unit-tested (sequence of fallback
bids is strictly increasing with shrinking steps, all ≤ ceiling).

### R4-3. Allow quoting route freight (contract amended, team-approved)

`docs/EVENTS.md` and `docs/AGENTS.md` §2 now also allow a message to contain the thread's
route freight `cost_per_tonne_usd` (public via the `route` event / logistics
`info_response`). In `run-dc3d40f5`, Crown's buyer was bounced for "number 7.0" (freight).
Add freight to the allowed values in `_message_numbers_match_structured` (and the
accept-message check in `_validate_deal_legal`), and in `tests/helpers_events.py`
(freight is available from the run's `route` event). Leak check stays exempt for allowed
values (R3-2). Tests: message quoting "$7.00/t freight" → delivered; other route figures
(distance, transit days) are **not** covered by this amendment and still bounce.

### Round 4 live-run checklist (one live run after R4-1…R4-3, tests passing offline)

- `pytest -q` < 5 s and passes proxy-blocked.
- At least one thread `accepted` (the goal of this round).
- Bounces ≤ 5, all legitimate; fallbacks ≤ 3.
- No seller offer to a buyer exceeds that buyer's latest requested quantity by the end of
  the thread (report the per-thread quantity sequence).
- Seller floor never exceeds the best live buyer bid in other threads.
- `tests/helpers_events.py` passes on the recorded run.
- Report: run_id, duration, llm_calls, provider attempts vs moves per model (quota-file
  diff), every bounce reason, every fallback cause, each thread's offer sequence
  (price/qty/months per move), thread results, recommendation, checklist item by item.

---

## Round 3 (2026-09-12) — done, verified

Round 2 verified: tests offline (54 pass, 0.76 s, proxy-blocked), R2-2 floor correct in
`runs/run-e1cdc425.jsonl`, aliases have separate buckets, orchestrator seq race fixed.
Live run still had 17 bounces / 14 fallbacks / 0 accepted. Causes and fixes:

### R3-1. Allow numbers from public thread history (contract amended, team-approved)

`docs/EVENTS.md` ("Numbers in `message`") and `docs/AGENTS.md` §2 were amended: a
message number is valid if it equals one of the move's own structured fields **or** a
structured field (price / quantity / contract_months) of any offer already **delivered
earlier in the same thread** by either side. 8 of 17 bounces in `run-e1cdc425` were
messages quoting the counterparty's last price. Implement in
`_message_numbers_match_structured` (pass the thread's delivered offers) and update
`tests/helpers_events.py` to the amended rule. Tests: quoting counterparty's earlier price
→ delivered; a number from a *different* thread or never offered → bounced; bounced
(undelivered) offers don't count as history.

### R3-2. Leak check false positives

`_message_states_reservation` bounces any message number equal to the agent's current
(dynamic) floor/ceiling — so "24 months" bounced when the floor was $24.00 (9 of 17
bounces, reconstructed by the Round 2 agent). Per the amended contract, run the leak
check only on numbers **not** already allowed by R3-1. Test: floor 24.0 + message
"$26.00/t … 24 months" (contract_months=24) → delivered; message "our floor is 24.50"
with 24.50 not in own fields/history → bounced (caught as mismatch or leak, either reason).

### R3-3. Drop OpenRouter; run on Gemini ×3 + NVIDIA ×3 only

Team decision: OpenRouter causes too many failures (connection errors / timeouts in live
concurrent runs, upstream 429s on `:free` models) — **remove it from all chains**. Keep
the provider code (don't delete support), just don't use it. New chains for `.env` and
`.env.example`:

```
SELLER_LLM_CHAIN=gemini:gemini-3.1-flash-lite,gemini:gemini-3.5-flash,nvidia:deepseek-ai/deepseek-v4-flash-0731
BUYER_1_LLM_CHAIN=gemini_2:gemini-3.1-flash-lite,nvidia_2:deepseek-ai/deepseek-v4-flash-0731,gemini_2:gemini-3-flash-preview
BUYER_2_LLM_CHAIN=nvidia_3:deepseek-ai/deepseek-v4-flash-0731,gemini_3:gemini-3.1-flash-lite
BUYER_3_LLM_CHAIN=gemini_3:gemini-3.1-flash-lite,nvidia:deepseek-ai/deepseek-v4-flash-0731,gemini_3:gemini-3-flash-preview
```

Seller alone on key 1's Flash-Lite (15 RPM / 500 RPD). Buyer 2 is DeepSeek-first so two
vendors appear in the demo; its Gemini fallback covers NVIDIA stalls. Update
`.env.example` comments (OpenRouter marked "supported but unused by default") and
`docs/NEXT_STEPS.md` "Model and provider" to match.

### R3-4. Faster NVIDIA fallthrough

NVIDIA stalls > 40 s on roughly 3 of 7 calls. With the global 20 s timeout, a stalled
DeepSeek turn costs 20 s before Buyer 2 falls back. Add an optional per-base-provider
timeout (`NVIDIA_TIMEOUT_S`, default = global `LLM_TIMEOUT_S`) and set `NVIDIA_TIMEOUT_S=10`
in `.env` / `.env.example`. Aliases inherit it. A timeout advances the chain (next model)
before the deterministic fallback; only when the whole chain fails does a `fallback` event fire.
Test with a fake clock/mocked call — no network.

### Round 3 live-run checklist (one live run after R3-1…R3-4, tests passing offline)

- `pytest -q` < 5 s and passes proxy-blocked.
- No OpenRouter model appears in any event.
- Both `gemini*` and `nvidia*` models appear among successful moves.
- Validator bounces ≤ 5, and none are false positives (each bounce reason checked).
- Fallbacks ≤ 3.
- At least one thread `accepted`.
- Seller floor never exceeds the best live buyer bid in other threads.
- `tests/helpers_events.py` (amended) passes on the recorded run.
- Report the same fields as Round 2, plus: provider attempts vs successful moves per model
  (from `runs/.quota.json` diff before/after), and each bounce's reason.

---

## Round 2 (2026-09-12, after the agent's "all fixes applied" report) — done, verified

Round 1 items 1, 2, 5, 6, 7 look applied. But verification of the report found the
following; **R2-1 and R2-2 block everything else**. Do not commit Step 1 until R2-1…R2-4
are fixed and a new live run meets the checklist at the end of this section.

### R2-1. The test suite makes live API calls (critical)

`tests/test_negotiation_loop.py:41` uses `patch.dict("os.environ", {}, clear=False)` —
that changes nothing — and `run_thread` (`:53`) calls `negotiate(..., use_llm=True)`.
When a test's scripted fake moves run out, the loop calls the **real** providers with the
keys from `.env`. Observed: `test_buyer_offer_above_own_ceiling_bounced_no_seller_floor_leak`
alone ran > 90 s; the full suite > 3 min (was 0.2 s). Every suite run spends real quota —
this, plus the recorded runs, exhausted today's OpenRouter free quota (R2-4).

**Fix:**
- Tests must never touch the network. In `setUp`, blank every provider key and chain
  (`patch.dict(os.environ, {"GEMINI_API_KEY": "", "OPENROUTER_API_KEY": "", "NVIDIA_API_KEY": "",
  "OPENAI_API_KEY": "", "SELLER_LLM_CHAIN": "", "BUYER_1_LLM_CHAIN": "", ...})`) **and**
  patch the single LLM-call function (`_call_llm_for_move`) to raise
  `AssertionError("network call in test")`, so any leak fails loudly instead of silently
  calling out. Tests that need "LLM ran out" behavior should script it via fake moves.
- Note `load_dotenv()` at import does not override variables already set, so setting
  them to `""` in the patch is what wins — verify.
- Target: full suite < 5 s, identical results with Wi-Fi off.

### R2-2. Seller's dynamic floor includes its own asking prices (critical)

`_dynamic_floor` (`agents/negotiation/__init__.py:1174`) takes the best price among
**all** offers in other threads' `shared_state` — including the seller's own offers,
since every delivered offer is appended regardless of side (`:1202`, `:1242`). The seller
anchors at $28–29 in round 1, so its floor in every other thread jumps to $27.5–29.0,
above every buyer's ceiling (Shah 28, Crown 27, Seven Circle 26).

Evidence, `runs/run-f16a7291.jsonl`: 12 of 13 validator bounces are the seller being
refused below this inflated floor (`price 27.0 is below your floor 29.0`, etc.); 6 of the
10 fallbacks are the seller hitting 2 bounces in a row; **no thread reached `accepted`**.
So the "dynamic floor raised 27→29 demonstrating competing-offer influence" in the report
is this bug, not the intended behavior.

**Fix (this is the intended spec — `NEXT_STEPS.md` §1 wording clarified):** the floor
rises only from **buyer bids** in *other live* threads — offers whose `from_agent` is a
`buyer_agent:*`, from threads not yet `rejected`/`released`. Take the best such bid's net
value per tonne, convert to this buyer's route, `max()` with the static floor. Seller asks
never count. Add a test: seller anchors at $29 in thread A, buyer A bids $24 → thread B's
floor must be `max(22, 24 − 7 − 3.5 + 7 + 3.5) = 24`, not 29.

### R2-3. A thread can close `countered` below the seller's own floor

In the same run Shah closed `countered` at $26.50 while the seller's floor in that thread
was ≥ $27.50 — the orchestrator recommended a price the seller's own gate had been
refusing. **Fix:** at max rounds, the best open counter becomes `countered` only if it
passes the seller's move-legal floor at that moment; otherwise the thread is `rejected`
with that reason. (R2-2 removes most occurrences, but the rule must hold regardless.)
Add a test.

### R2-4. OpenRouter quota exhausted → OpenRouter buyers never participated

Direct check after the run: `429 Rate limit exceeded: free-models-per-day`,
`X-RateLimit-Remaining: 0` (resets 00:00 UTC = 05:30 IST). In `run-f16a7291` the only
models that answered were `gemini-3.1-flash-lite` (19) and NVIDIA DeepSeek (2); buyers 1–2
fell to the deterministic engine 4 times. The run did **not** demonstrate multi-vendor agents.

**Fix (code):**
- A 429 whose body says `free-models-per-day` / `limit_source: openrouter_free_tier_daily`
  means the whole `openrouter` bucket is done for the day: mark it exhausted (account-wide)
  and skip OpenRouter entries in every chain without calling, until the reset time in
  `X-RateLimit-Reset`.
- Persist RPD counts and exhausted-until timestamps to `runs/.quota.json` so separate
  processes (tests, CLI runs) share them — in-process counters reset every run.
- Fallback `cause` must be accurate: an exhausted/429 provider chain is `provider_error`
  with the real reason in `detail`, not `timeout` (EVENTS.md cause values). The current
  detail "provider call exceeded 20s or all chains failed" hides which one happened.

**Fix (human, not code):** the $10 OpenRouter credit purchase (`NEXT_STEPS.md`) — the
50/day tier can't support tests + rehearsals.

### R2-5. Report accuracy

The live run report should state what the run shows, including failures. For
`run-f16a7291`: 21 LLM calls, 13 bounces, 10 fallbacks, 0 accepted threads, OpenRouter
absent. Report those numbers alongside any claims, and say which acceptance criteria in
`NEXT_STEPS.md` §1 were met and which weren't.

### R2-6. Support multiple keys per provider (provider aliases)

The team now has extra keys: `OPENROUTER_2_API_KEY`, `OPENROUTER_3_API_KEY`,
`GEMINI_2_API_KEY` in `.env`. Support them as **aliases**: a provider name `<base>_<n>`
(e.g. `openrouter_2`, `gemini_2`) uses the base provider's URL and request quirks
(`reasoning_effort` for Gemini, etc.) but its **own** key (`<ALIAS>_API_KEY`), its own
pacing/quota bucket, and its own limits (`OPENROUTER_2_RPM`, `OPENROUTER_2_RPD`,
`GEMINI_2_RPM=model:limit,...`, `GEMINI_2_RPD=...`; if unset, inherit the base provider's
limit values — never share the base provider's *counter*). Chains then read e.g.
`BUYER_2_LLM_CHAIN=openrouter_2:nvidia/nemotron-3-super-120b-a12b:free,...`.
Record the alias in event `model` fields (`openrouter_2:<model>`). Add a parser test.
An alias whose key is empty is skipped in the chain without a call.

**Keys now in `.env` (all from different accounts → separate quota pools):**
`OPENROUTER_API_KEY` (today's 50/day exhausted), `OPENROUTER_2/3/4_API_KEY`;
`GEMINI_API_KEY`, `GEMINI_2/3_API_KEY`; `NVIDIA_API_KEY`, `NVIDIA_2/3_API_KEY`
(same DeepSeek model on each). Aliases are `openrouter_2..4`, `gemini_2..3`, `nvidia_2..3`.

**Target chains** (write to `.env` and, without secrets, `.env.example`):

```
SELLER_LLM_CHAIN=gemini:gemini-3.1-flash-lite,gemini_2:gemini-3.1-flash-lite,gemini:gemini-3.5-flash,nvidia:deepseek-ai/deepseek-v4-flash-0731
BUYER_1_LLM_CHAIN=openrouter_2:nex-agi/nex-n2.5-pro:free,openrouter_4:google/gemma-4-31b-it:free,nvidia_2:deepseek-ai/deepseek-v4-flash-0731
BUYER_2_LLM_CHAIN=openrouter_3:nvidia/nemotron-3-super-120b-a12b:free,openrouter_4:nex-agi/nex-n2.5-pro:free,nvidia_3:deepseek-ai/deepseek-v4-flash-0731
BUYER_3_LLM_CHAIN=gemini_3:gemini-3.1-flash-lite,gemini_3:gemini-3-flash-preview,openrouter:google/gemma-4-31b-it:free
```

Every buyer's primary is on a pool no other role uses first; `openrouter_4` is the shared
OpenRouter backup; NVIDIA stays last (it stalls). Alias limits inherit the base values
(Gemini per-model 15/500 Flash-Lite, 5/20 3.5 Flash and 3 Flash; OpenRouter 20 RPM / 50
RPD per key; NVIDIA 40 RPM per key) unless `<ALIAS>_RPM/_RPD` is set.

### Round 2 live-run checklist (after R2-1…R2-4, with OpenRouter quota available)

- `python -m pytest -q` < 5 s, passes with network disabled.
- One live run, top 3: every buyer's primary model answers at least once (check event
  `model` fields — Gemini, OpenRouter and NVIDIA should all appear if chains are hit).
- Fallbacks ≤ 3 and no `invalid_moves` fallback caused by the floor.
- At least one thread `accepted` (not only `countered`).
- Seller floor never exceeds the highest buyer bid seen in other live threads.
- `tests/helpers_events.py` checks pass on the new `runs/<id>.jsonl`.
- Report: run_id, llm_calls, bounces with reasons, fallbacks with causes, models used,
  thread results, and which §1 acceptance criteria are met.

---

## Round 1 (original review)

Hand-off for whoever continues Step 1 (`agents/negotiation/__init__.py`,
`agents/negotiation/prompts.py`, `orchestrator/__init__.py`). Reviewed 2026-09-12 against
`docs/AGENTS.md` §2, `docs/EVENTS.md` and `docs/NEXT_STEPS.md` §1. Line numbers refer to the
uncommitted working tree at review time.

**Do not change** the frozen contracts (`AGENTS.md` §2, `EVENTS.md`) or the `.env` /
`.env.example` formats described below — fix the code to match them. Commit the Step 1
work only after items 1–4 are fixed and the full suite passes.

---

## What already works (keep it working)

Verified offline (no API calls):

- `run_pipeline(use_llm=False, emit_event=events.append, top_n=3)` → 50 events that pass
  every check in `tests/test_events_sample.py` when pointed at the output; winner Shah
  Cement, $27/t × 65,000 t = $1,072,500 (same as the legacy path).
- Scripted moves via `negotiate(..., _fake_seller_moves=..., _fake_buyer_moves=...)`:
  below-own-floor offer bounced (`price 21.0 is below your floor 22.0`); message stating
  own floor bounced; two consecutive bounces → visible `fallback` event (`invalid_moves`);
  `accept_offer("shah_cement-o2")` closes with that offer's exact terms incl. 24 months.
- Existing 33 tests + 51 subtests pass; legacy deterministic path unchanged.

---

## Must fix

### 1. Pacing ignores the configured limits (Gemini is not paced at all)

`_get_provider_rpm` (`agents/negotiation/__init__.py:395`) does `float(os.getenv("GEMINI_RPM"))`.
The agreed `.env` format (see `.env.example`, "Pacing") is **per model** for Gemini because
AI Studio limits are per model:

```
GEMINI_RPM=gemini-3.1-flash-lite:15,gemini-3.5-flash:5,gemini-3-flash-preview:5
GEMINI_RPD=gemini-3.1-flash-lite:500,gemini-3.5-flash:20,gemini-3-flash-preview:20
OPENROUTER_RPM=20          # plain number = account-wide across all :free models
OPENROUTER_RPD=50
NVIDIA_RPM=40
```

`float("gemini-3.1-flash-lite:15,...")` raises → caught → returns `None` → **no pacing**.
Seller + buyer 3 both run on Flash-Lite (15 RPM shared), so a concurrent run will hit 429s
and spill onto fallbacks, including Gemini 3.5 Flash, which has only 20 requests/day.

**Fix:**
- Parse both forms: a plain number (provider-wide limit) or `model:limit,...` (per-model).
  Pace per `(provider, model)` when a per-model limit exists, else per provider.
  OpenRouter's limit is account-wide, so all OpenRouter models share one bucket.
- Implement `*_RPD`: count requests per `(provider, model)` per UTC day (in-process is
  fine; optionally persist to a small JSON file under `runs/`) and **skip to the next
  model in the chain** when a model is at ≥ its RPD, instead of calling and failing.
- A 429 from a provider should also advance the chain (verify this path).

### 2. Pacing serializes every thread

`_pace_provider` (`:409–420`) calls `time.sleep(wait)` **while holding the global
`_PACING_LOCK`**. While one Gemini call waits, OpenRouter/NVIDIA calls in other threads
block too — the concurrent threads effectively run one at a time.

**Fix:** use a lock per bucket, or reserve the next slot under the lock and sleep outside it:

```python
with _PACING_LOCK:
    now = time.monotonic()
    slot = max(now, _NEXT_SLOT.get(bucket, 0.0))
    _NEXT_SLOT[bucket] = slot + 60.0 / rpm
time.sleep(max(0.0, slot - now))
```

Use `time.monotonic()`, not `time.time()`, for intervals.

### 3. Number-word rule will bounce ordinary LLM prose

`_message_numbers_match_structured` (`:532`) bounces any message containing a number word
(`one`, `twelve`, …). Real models write "the best one we can do", "a twelve-month term",
"no one else" constantly. Expected effect in live runs: frequent bounces → fallbacks → the
demo mostly shows the deterministic engine instead of the agents.

**Fix:** only reject number words that express a **quantity/price/term that doesn't match
the structured fields** — e.g. convert number-word phrases to values (`twelve` → 12,
`sixty-five thousand` → 65000) and apply the same match-against-structured-fields check
as digits. Plain pronoun "one" must pass. Add the cases below as tests.

### 4. Add the Step 1 tests (required by `NEXT_STEPS.md` §1 "Testing")

The fake-move hooks exist but no tests use them. Add `tests/test_negotiation_loop.py`
(offline, no API calls) covering at least:

| Case | Expect |
|---|---|
| Seller offer below own floor | bounced, reason cites only the seller's floor |
| Buyer offer above own ceiling / `contract_months` outside own range | bounced, reason never contains the seller's floor |
| Message states own reservation value | bounced |
| Message number not in structured fields (digits and words, e.g. "twenty-nine") | bounced |
| Message with pronoun "one" / "twelve-month" matching `contract_months=12` | **delivered** |
| Two bounces in a row | `fallback` event with cause `invalid_moves`, then a deterministic move with `model: null` |
| `accept_offer` of a stale / own / unknown `offer_id` | rejected at the `deal_legal` gate |
| `accept_offer` of the counterparty's latest offer | `accepted`, result terms = that offer's terms |
| Seller "better offer" claim with no qualifying live offer | bounced — **also when the thread runs alone** (see item 5) |
| Full offline concurrent run (`use_llm=False`, `emit_event`) | passes the `tests/test_events_sample.py` checks — refactor those checks into a helper that takes an event list so both tests reuse them |
| Pacing: per-model RPM parse, provider-wide RPM parse, RPD exhaustion skips to next model | unit-test the parser/limiter with a fake clock |

---

## Should fix

### 5. Leverage check skipped when `shared_state is None`

`_validate_move_legal` (`:635`) only checks leverage claims when `shared_state` is passed.
The orchestrator always passes it, so concurrent runs are covered, but a direct
`negotiate(...)` call lets "We have a better offer elsewhere…" through (reproduced).
Treat `None` as "no other live threads" so the claim is bounced.

### 6. No way to run the new mode from the CLI / demo

`demo/render_log.py` and `orchestrator/run.py` still call the legacy path. Add a flag,
e.g. `python -m demo.render_log --live [--top-n 3]` and `orchestrator/run.py --live`,
that calls `run_pipeline(use_llm=True, emit_event=..., top_n=...)` and prints each event
as a readable line (seller/buyer message, validator ✓/✗, fallback) as it arrives. This is
also what Step 2's SSE endpoint will wrap, so keep the event → line formatting in one function.

### 7. Record every run

`NEXT_STEPS.md` §2 needs every run written to `runs/<run_id>.jsonl` as events stream
(and `runs/` gitignored). Cheapest to add now in the orchestrator's emit wrapper, so the
first live runs are captured for the demo safety net.

---

## Then: one live run, before committing

After 1–4: run one live concurrent run (`use_llm=True`, top 3) with the team `.env`.
Budget: ~36 LLM calls (~12 OpenRouter of the 50/day, ~25 Flash-Lite of 500/day). Report:

- total LLM calls, validator bounces, fallbacks (from `run_completed`)
- which models actually answered (from event `model` fields) vs chain fallthrough
- whether any 429s occurred (pacing works if none)
- wall time (expect ~2 min at Flash-Lite's 15 RPM)
- run the `tests/test_events_sample.py` checks on the recorded `runs/<id>.jsonl`

If fallbacks exceed a handful per run, look at the bounce reasons first — they point at
the validator rule or prompt that needs adjusting.

## Provider notes (from smoke tests, 2026-09-12)

- `gemini-3.5-flash` thinks by default and can exhaust `max_tokens` before answering →
  send `reasoning_effort` (`GEMINI_REASONING_EFFORT=low`). The code at `:799` does this;
  confirm it's sent for all Gemini models in the chain.
- NVIDIA DeepSeek needs `chat_template_kwargs.thinking=false` (`:814` does this). Its trial
  endpoint stalled > 40 s on 3 of 7 calls — fallback only, and rely on the 20 s timeout.
- All six primary/fallback models returned clean JSON in 1–3 s when not stalled.
