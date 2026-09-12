# CIRCUIT — Bit N Build

Python 3.11+ hackathon prototype for matching LD slag to buyers, pricing routes,
validating negotiations, and selecting the highest total-value candidate deal.

From the repository root:

```sh
python -m demo.render_log
python -m orchestrator.run
python -m unittest discover -s tests -v
```

The default demo is offline. Optional negotiation dialogue requires
`pip install -r requirements.txt` and `OPENROUTER_API_KEY` in your environment
or a local `.env` file. `NEGOTIATION_LLM_MODEL` overrides the configured model.
Direct `agents.negotiation.negotiate(..., use_llm=True)` enables that path;
the orchestrator intentionally runs deterministic negotiations.

Use `python -m orchestrator.run --scenario path/to/scenario.json` for a JSON
request with `seller_id`, `material_id`, `quantity_tonnes`, and
`objective: "maximize_net_value"`. Quantity must be positive and is capped
by seller supply and buyer demand. The pipeline chooses one deal; it does
not split remaining supply across buyers. Counteroffers require confirmation.

The snapshot contains 13 real-reference company names and 12 synthetic buyers.
Prices, demand, quality thresholds, freight rates and CO2 savings are modeled
inputs, not verified contracts or measured results. See
[limitations](docs/LIMITATIONS.md) and [demo guidance](demo/README.md).
