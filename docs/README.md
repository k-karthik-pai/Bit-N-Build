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
- `docs/briefs/` — one file per buildable unit: purpose, contract, acceptance criteria, dependencies

## Setup

(fill in once stack is chosen — placeholder)

```bash
# python venv / install deps
pip install -r requirements.txt

# run orchestrator against the fixed demo scenario
python orchestrator/run.py --scenario data/scenario_tata_steel_bsl.json
```
