# Demo Output

## Live dashboard

Install dependencies and start the presentation-safe replay from the repository root:

```sh
pip install -r requirements.txt
python -m demo.web --replay demo/sample_run.jsonl
```

Open `http://127.0.0.1:8000`. The page shows ranked buyers, independent negotiation
threads, validator bounces, private rationale, price convergence, route updates, and
the final recommendation. Every streamed event is validated against `docs/EVENTS.md`
and recorded to the gitignored `runs/` directory.

For normal startup, use `python -m demo.web`. Replay can then be started from the UI.
Host, port, recording directory, replay speed, and logging can be configured with
`HOST`, `PORT`, `RUNS_DIR`, `DEFAULT_REPLAY_SPEED`, and `LOG_LEVEL`.

The included `render.yaml` deploys the same FastAPI application to Render. Secrets are
configured in Render rather than committed. Render's default filesystem is ephemeral;
for persistent recordings, attach `/var/data` and set `RUNS_DIR=/var/data/runs`.

Live mode calls the asynchronous integration contract below and does not duplicate
orchestrator logic in the API layer:

```python
def run_multi_agent(*, run_id: str, top_n: int, emit: Callable[[dict], None]) -> dict:
    ...
```

The adapter runs the validator-gated negotiation engine with its own recorder disabled;
the dashboard validates and records each event exactly once. Start it with
`python -m demo.web --env-file env` when the team credential file is named `env`.
The sample replay remains the no-network presentation fallback.

## Terminal output

From the repository root, run:

```sh
python -m demo.render_log
```

The command calls the real orchestrator once and displays live progress
and its recommendation card. The current default run works without an API key;
the renderer uses only the Python standard library. Currency is USD and `t` means
metric tonnes. Display formatting adds separators and pads currency to at least two
decimal places without rounding away returned precision.

`render(result)` also accepts exactly the recommendation object in
`docs/AGENTS.md` section 4. `pipeline_log` is optional. Without it, the card is
shown without inventing negotiation history or acceptance status. Counteroffers
remain explicitly pending confirmation. Expected run/data errors produce an error
message and a nonzero exit code instead of a success card.

## Short rehearsal

1. Introduce the fixed LD slag export scenario. The current buyer dataset is a
   snapshot of 13 real-reference companies and 12 synthetic buyers, with illustrative
   commercial terms rather than actual contracts.
2. Run the command. Point to the actual accepted, countered and rejected outcomes.
3. Read the selected buyer, quantity, price, route, freight, margin and total net
   value from the card. A recommendation marked COUNTERED still needs confirmation.
4. Describe CO2 avoided explicitly as an estimate, not a measured saving.

## Judge questions

**How does the negotiation agent decide the price?**

In live mode, separate seller and buyer LLM contexts propose structured moves. A
deterministic validator checks the proposing side's bounds, message grounding and
leverage claims, and re-checks both sides before accepting a deal. Invalid moves are
bounced for correction; repeated invalid moves or provider failures produce a visible
deterministic fallback. The seller's live floor can rise from real buyer bids in other
threads, while final selection remains deterministic by total net value. Replay shows
the same event path without spending provider quota. See `agents/negotiation/__init__.py`
and `orchestrator/__init__.py`.

**How is route cost calculated?**

The optimizer enumerates simple paths through the small port graph and sums each
path's stored `base_cost_per_tonne_usd` values from `data/routes.json`. It sorts by
cost then distance, checks all paths against the deadline, and retains up to three
options including the cheapest feasible route. It raises an error if none meets it.
Transit time is distance divided by 444.5 km/day, plus 0.5 days per intermediate
stop, rounded to two decimals. `distance_km` values are real sea-route lookups
(searoutesnav.com), not hand estimates — see `docs/ROUTES_DISTANCE_FIX.md` for the
investigation. The implementation uses DFS path enumeration rather than Dijkstra,
which is fine at this graph's size (a handful of ports). See
`agents/logistics/__init__.py`.

**How does buyer matching/ranking work?**

The Circularity Agent (`agents/circularity`) decides which buyers qualify (material
match AND the material's composition actually meets the buyer's stated quality
requirements) and how they rank (`compatibility_score`, based on demand coverage).
The orchestrator calls it directly and only attaches each ranked candidate's full
record (port, price ceiling, etc.) for the downstream steps — it doesn't do any of
its own filtering or scoring. `pipeline_log` entries carry each buyer's
`circularity_application` and `circularity_compatibility_score` alongside the
negotiation outcome, so "why was this buyer even tried" is traceable straight from
the demo log.

## Verification

```sh
python -m unittest discover -s tests
```

Demo tests cover the frozen contract, optional logs, all negotiation statuses,
numeric fidelity, error output and a real pipeline run.
