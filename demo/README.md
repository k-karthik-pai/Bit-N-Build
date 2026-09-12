# Demo Output

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
   development stub, not actual commercial contracts.
2. Run the command. Point to the actual accepted, countered and rejected outcomes.
3. Read the selected buyer, quantity, price, route, freight, margin and total net
   value from the card. A recommendation marked COUNTERED still needs confirmation.
4. Describe CO2 avoided explicitly as an estimate, not a measured saving.

Before presenting, run the command once from the same terminal you will use for judging and confirm that live steps and the final card are both visible.

## Judge questions

**How does the negotiation agent decide the price?**

The current code proposes a price deterministically. It uses the seller's preferred
price when it lies between the effective seller floor and the buyer's ceiling;
otherwise it proposes the rounded midpoint of those bounds. The floor can be raised
by BATNA (the best alternative to a negotiated agreement), calculated by the
orchestrator from other buyers' baseline margins and this buyer's costs. A separate
validator checks price bounds. No overlap means rejection; a compromise, narrow
spread or thin margin produces a counteroffer. Quantity is capped by supply and
buyer demand. The current pipeline sets `use_llm=False`, so this run does not use
live LLM-generated dialogue. See `agents/negotiation/__init__.py` and
`orchestrator/__init__.py`.

**How is route cost calculated?**

The optimizer enumerates simple paths through the small port graph and sums each
path's stored `base_cost_per_tonne_usd` values from `data/routes.json`. It sorts by
cost then distance, keeps up to three routes and picks the first that meets the
deadline; the current fallback is the cheapest retained route if none meets it.
Transit time is distance divided by 444.5 km/day, plus 0.5 days per intermediate
stop, rounded to two decimals. These are fixed model inputs, not live freight
quotes. The implementation uses DFS path enumeration, despite the module's
introductory reference to Dijkstra. See `agents/logistics/__init__.py`.

## Integration limits to resolve before the final demo

- Live steps use an optional `on_progress` callback in `run_pipeline`; existing
  callers and the section 4 output are unchanged. Events are emitted synchronously
  at matching, route completion, baseline evaluation and each negotiation. The demo
  flushes each line immediately and shows actual quantity and contract term.
  Material-matched counts are not described as full quality-filter results.
  `render(result)` still supports a completed log for callers with a saved result.
- The pipeline still uses its internal simplified matcher. It ranks final deals
  by margin per tonne, and calculates total net value using buyer annual demand
  rather than negotiated quantity. Its selection can include counteroffers.
  Resolve these upstream before claiming fully integrated matching or maximum
  total net value. The demo displays the supplied result without correcting it.
- The CO2 estimate uses the pipeline's factor, whose referenced research file is
  missing on the reviewed main. Ask the owner to substantiate its application to
  this scenario. Replace the stub dataset before claiming researched buyer data.

## Verification

```sh
python -m unittest discover -s tests
```

Demo tests cover the frozen contract, optional logs, all negotiation statuses,
numeric fidelity, error output and a real pipeline run.
