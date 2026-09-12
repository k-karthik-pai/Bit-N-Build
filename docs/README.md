# CIRCUIT — Autonomous Circular Supply Chain Agent

Hackathon project for the "Supply Chain Circularity & Industrial Symbiosis" problem statement.

Demo scenario: an industrial byproduct producer (modeled on Tata Steel BSL's LD slag exports) needs to
find a buyer, negotiate terms, and route a shipment — autonomously.

## Repo layout

```
/data/          buyers.json, ports.json, materials.json, seller.json
/agents/
  circularity/
  negotiation/
  logistics/
/orchestrator/
/demo/
/docs/          PLAN.md, AGENTS.md, DATA.md, briefs/
```

## Docs

- `docs/PLAN.md` — scope, timeline, ownership, what we are NOT building
- `docs/AGENTS.md` — every agent's input/output contract in one place (read this before touching any agent code)
- `docs/DATA.md` — schema + sourcing notes for everything in `/data/`
- `docs/brief-*.md` — one file per buildable unit: purpose, contract, acceptance criteria, dependencies

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m orchestrator.run
.\.venv\Scripts\python -m demo.web --replay demo/sample_run.jsonl
```

For a quota-consuming live run, use `python -m demo.web --env-file env` with the
team's untracked credential file. See `demo/README.md` for the presentation flow.
