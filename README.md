# CIRCUIT — Bit N Build

Python 3.11+ hackathon prototype for matching LD slag to buyers, pricing routes,
validating negotiations, and selecting the highest total-value candidate deal.

Create an isolated environment from the repository root (Windows PowerShell):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m demo.web --replay demo/sample_run.jsonl
```

Open `http://127.0.0.1:8000`. Replay mode is deterministic and presentation-safe.
For a live top-three negotiation using the team's untracked credential file:

```powershell
.\.venv\Scripts\python -m demo.web --env-file env
```

Live mode calls `orchestrator.run_multi_agent(...)`, streams validator-gated
Gemini/NVIDIA negotiations, and records each event once through the dashboard's
run store. OpenRouter remains supported but is not in the default provider chains.
Never commit `env`, `.env`, API keys, or generated `runs/` recordings.

Use `python -m orchestrator.run --scenario path/to/scenario.json` for a JSON
request with `seller_id`, `material_id`, `quantity_tonnes`, and
`objective: "maximize_net_value"`. Quantity must be positive and is capped
by seller supply and buyer demand. The pipeline chooses one deal; it does
not split remaining supply across buyers. Counteroffers require confirmation.
Add `--live --top-n 3 --env-file env` for the live CLI path; use `--no-record`
only when another component owns event recording.

The snapshot contains 13 real-reference company names and 12 synthetic buyers.
Prices, demand, quality thresholds, freight rates and CO2 savings are modeled
inputs, not verified contracts or measured results. See
[limitations](docs/LIMITATIONS.md) and [demo guidance](demo/README.md).
