# HANDOFF_INTEGRATION.md — remaining fixes after merging Steps 1–3

Status as of 2026-09-12 (`main` @ `6a44cbe`): Step 1 (multi-agent LLM negotiation), Step 2
(live streaming dashboard) and Step 3 (knowledge-graph matching) are merged. **88 tests
pass** (network-blocked). Three fixes remain before the dashboard can show a live
negotiation. Each is small and independent; pick one, put your name next to it, and open a
PR (or push to `main` if that's what the team is doing — tell the others either way).

| # | Fix | Size | Suggested owner | Blocks |
|---|---|---|---|---|
| 1 | Event timestamps → milliseconds | ~1 line + test | anyone | dashboard live mode |
| 2 | `orchestrator.run_multi_agent` adapter + single recording | ~15 lines + test | Step 2 owner (knows the coordinator) | dashboard live mode |
| 3 | Seller fallback must not retract a concession | ~20 lines + tests | Step 1 owner | demo credibility |

Do **1 before 2** (2's test will fail on timestamps otherwise). 3 is independent.

---

## Setup (once)

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
cp .env.example .env        # then paste your own keys; never commit .env
.venv/bin/python -m pytest -q
```

- Tests are fully offline — they blank provider keys and fail loudly on any network call.
  Keep it that way: suite < 5 s, must pass with
  `HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9`.
- `requirements.txt` pins `openai<2.0.0`; Step 1 was verified working with 1.109 (real call).
- Frozen contracts: `docs/AGENTS.md` §2 (negotiation) and `docs/EVENTS.md` (event
  schema). Fix code to match them; don't change them without telling the team.
- Never paste API keys into code, docs, tests or chat. `runs/` (recordings,
  `runs/.quota.json`) is gitignored.

Useful commands:

```sh
.venv/bin/python -m orchestrator.run --live --top-n 3     # live multi-agent run, events to stderr (spends API quota)
.venv/bin/python -m demo.web --replay demo/sample_run.jsonl  # dashboard replay at http://127.0.0.1:8000
.venv/bin/python -m demo.web                                 # dashboard; "Live" needs fixes 1 + 2
```

---

## Fix 1 — Event timestamps must have exactly 3 millisecond digits

**Problem.** `orchestrator/__init__.py` `_now_iso()` produces microseconds
(`2026-09-12T16:06:41.107134Z`). `docs/EVENTS.md` requires milliseconds
(`2026-09-12T10:15:07.300Z`). The dashboard's validator (`demo/events.py`) correctly rejects
every Step 1 event: `ts must contain exactly three millisecond digits`.

**Verified:** with timestamps normalized to milliseconds, a real recorded live run and an
offline concurrent run both pass `demo.events.validate_run`. This is the only
incompatibility found between Step 1 and the dashboard.

**Do:**
- `datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")`
  in `_now_iso()`; search for any other place that builds a `ts`.
- Test: every `ts` from `run_pipeline(use_llm=False, emit_event=..., top_n=3)` matches
  `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$`, and the same event list passes
  `demo.events.validate_run`. Add the regex check to `tests/helpers_events.py` too.

**Done when:** the test above passes; full suite green.

---

## Fix 2 — Wire the dashboard's live mode to the negotiation engine

**Problem.** Dashboard live mode (`demo/coordinator.py` `_resolve_live_runner`) calls
`orchestrator.run_multi_agent(*, run_id, top_n, emit)`. That function doesn't exist, so live
mode ends in `run_failed` ("use Replay Sample until Part 1 exposes
orchestrator.run_multi_agent"). The coordinator also requires every emitted event's
`run_id` to equal the one it passed (`_append`).

Step 1 already has what's needed: `run_pipeline(use_llm=True, top_n=..., run_id=...,
emit_event=...)` accepts a `run_id` and emits `docs/EVENTS.md` envelopes.

**Second problem — double recording.** `run_pipeline` writes `runs/<run_id>.jsonl` itself,
and the dashboard's `RunStore` writes `<RUNS_DIR>/<run_id>.jsonl`. With the same `run_id`
and `RUNS_DIR=runs` (check the default in `demo/web.py`), both append to the **same file**
→ every event twice.

**Do:**
- Add a `record: bool = True` parameter to `run_pipeline`; when `False`, skip its own
  `runs/<id>.jsonl` writes (keep the `runs/.quota.json` bookkeeping).
- Add and export (`__all__`) in `orchestrator/__init__.py`:

  ```python
  def run_multi_agent(*, run_id: str, top_n: int, emit: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
      """Entry point for the dashboard's live mode (demo/coordinator.py)."""
      return run_pipeline(use_llm=True, top_n=top_n, run_id=run_id, emit_event=emit, record=False)
  ```

- Test (offline, in `tests/test_live_dashboard.py` style with FastAPI's `TestClient`):
  start a live run with the provider keys blanked (every move falls back to the
  deterministic engine, no network) → the stream ends in `run_completed`, passes
  `validate_run`, and the recording has each `seq` exactly once.
- Manual check (spends ~25 API calls): `python -m demo.web`, click Live, watch three
  negotiation threads fill in, and a recommendation card appear.

**Done when:** offline test passes; one manual live run in the dashboard completes.

---

## Fix 3 — Seller fallback must not take back a concession

**Problem.** When the seller LLM makes 2 invalid moves in a row, a deterministic fallback
makes its move. In live run `run-e32f3cd7` (Crown and Seven Circle threads) the seller had
already offered **$26.50**, then two LLM offers bounced (below its $26.50 floor), then
the fallback offered **$27.00** — raising its price after conceding. The spec says the
fallback "holds the last valid offer", and $26.50 was still valid. A judge can spot this.

**Do** (seller branch of `_deterministic_fallback_offer` in
`agents/negotiation/__init__.py`):
- If the seller has a delivered offer in this thread that still passes the move-legal
  gate at the current floor → re-offer exactly those terms (price, quantity, months).
- If the dynamic floor has since risen above it → offer at the current floor, never above
  `max(last_delivered_price, current_floor)`.
- No prior seller offer → keep the current opening behavior.
- Same principle for the buyer fallback: never below its own last delivered bid.

**Tests (offline):** (a) last delivered $26.50, floor $26.50, two bounces → fallback
$26.50 with same qty/months; (b) floor rose to $27.00 after the $26.50 offer → fallback
$27.00; (c) across a thread, a seller fallback is never above its previous delivered price
unless the floor forced it; a buyer fallback never below its previous bid.

**Done when:** tests pass; no live run needed.

---

## Context worth knowing

- **Models:** Gemini ×3 keys + NVIDIA DeepSeek ×3 keys, as provider aliases (`gemini_2`,
  `nvidia_3`, …) configured in `.env` — see `.env.example`. OpenRouter is supported but
  unused (too many failures on free tiers). Gemini 3.5 Flash / 3 Flash have only 20
  requests/day per key — backups only; Flash-Lite (500/day) does the work.
- **NVIDIA DeepSeek** stalled on 3/3 attempts in the last live run and handed over to
  Gemini, so the demo currently shows one vendor. Team decision whether that matters.
- **Latest live run** (`run-e32f3cd7`, before these fixes): 58 s, Shah accepted at
  $26.90/t × 65,000 t (net $1,066,000), Crown accepted then released, Seven Circle
  rejected (its $26 ceiling was below the floor set by Shah's live bid — correct).
- Full review history: `docs/STEP1_FIXES.md` (Rounds 1–5).
