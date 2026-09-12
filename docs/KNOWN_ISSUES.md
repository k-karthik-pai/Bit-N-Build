# Known issues

## Reliability review

Regression tests now cover requested-quantity and supply caps, fractional tonnes,
unknown requests, non-finite prices, negative costs, non-profitable deals, impossible
deadlines, and missing recommended routes. CLI errors are printed without tracebacks.
The deterministic pipeline and replay remain the no-network paths. Live mode uses the
Gemini/NVIDIA role chains and falls back visibly when a provider is unavailable; provider
availability and quota are external dependencies rather than hidden successes.
See `LIMITATIONS.md` for unresolved data provenance and model assumptions.

## RESOLVED — Orchestrator was picking highest per-tonne margin, not highest total deal value

**Status: fixed.** `run_pipeline`'s buyer-selection loop now compares
`margin * deal["quantity_tonnes"]` (total value) across buyers, not raw
per-tonne margin — matching what `"maximize_net_value"` actually implies and
what `total_net_value_usd` already displayed.

**Original problem:** the selection loop compared per-tonne margin only,
so with the real 25-buyer dataset it picked a synthetic buyer at $29/t for
24,000t (total $444,000) over Shah Cement at $27/t for 65,000t (total
$1,072,500) — a much worse outcome overall, purely because $29 > $27
per-tonne. The old 5-entry stub never exposed this because all 5 buyers
happened to rank the same way under both metrics.

**One thing deliberately NOT changed:** BATNA's own walk-away floor
(`agents/negotiation`'s `batna_price_per_tonne_usd`, computed in the
baseline pass) still compares per-tonne rates across buyers, not total
value. An earlier attempt to make BATNA total-value-aware too caused
per-tonne floors to blow up unrealistically for smaller buyers (up to
$115/t on a material that normally trades at $22-30/t) — a tiny buyer would
need an absurd rate to numerically match a large buyer's total dollar
contribution, which isn't how a real walk-away threshold should work. BATNA
answers "what rate can I get elsewhere," which is legitimately a per-tonne
question; only the final "which buyer do we actually pick" comparison
needed to be total-value-based. If this distinction ever needs revisiting,
see `orchestrator/__init__.py`'s Step 3a/3b comments for the reasoning.

**Verified:** full test suite passes; live pipeline run now selects Shah
Cement Industries Ltd (65,000t @ $27/t, total net value $1,072,500) instead
of the smaller higher-rate buyer, with BATNA floors staying in the normal
$25-26/t range across the board.
