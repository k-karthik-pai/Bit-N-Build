# CIRCUIT — autonomous circular supply-chain agents

CIRCUIT turns an industrial by-product into a closed deal. Given a seller's waste
material, it finds buyers who can reuse it, prices the shipping route, and lets **LLM
agents negotiate the deal live** — a seller agent bargaining with several buyer agents at
once — while a deterministic validator makes sure no agent can invent a price, leak a
secret, or bluff about a competing offer.

**Demo scenario:** Tata Steel BSL (Angul, India) has 100,000 t/year of LD (steelmaking)
slag. Cement makers in Bangladesh can use it as a clinker substitute. CIRCUIT matches the
slag to compatible cement buyers, routes it from Dhamra port, negotiates with the top three
buyers simultaneously, and recommends the deal with the highest total net value — with an
estimated CO₂ saving.

---

## How it works

```mermaid
flowchart LR
    M["Circularity agent<br/>knowledge-graph matching"] --> O["Orchestrator"]
    L["Logistics optimizer<br/>port graph, cost, ETA"] --> O
    O --> T1["Seller ⇄ Buyer 1"]
    O --> T2["Seller ⇄ Buyer 2"]
    O --> T3["Seller ⇄ Buyer 3"]
    T1 & T2 & T3 --> V{"Validator<br/>checks every move"}
    V --> R["Recommendation<br/>best total net value"]
    O -. live events .-> D["Dashboard"]
```

1. **Match** — a knowledge graph links *material → application → buyer*. A buyer qualifies
   only if the slag's composition meets its quality thresholds; each match comes with a
   readable reasoning path (`LD slag → cement clinker substitute → Shah Cement`).
2. **Route** — the logistics optimizer finds the cheapest sea route that meets the
   deadline and returns distance, transit days and freight per tonne.
3. **Negotiate** — one seller agent and three buyer agents, each a separate LLM context
   that knows only its own limits (floor price, ceiling, demand, contract range). Agents
   make moves as structured JSON: offer, accept, reject, or ask for information.
4. **Validate** — every move passes a deterministic gate before the other side sees it:
   an agent may only offer within its *own* limits, may only quote numbers that are
   grounded in the conversation, may not reveal its own reservation price, and the seller
   may not claim a "better offer elsewhere" unless one really exists in another live
   negotiation. Invalid moves bounce back to the agent to retry; repeated failures hand the
   move to a deterministic fallback — visibly, never silently.
5. **Compete** — the negotiations influence each other: a real bid in one thread raises
   the seller's floor in the others.
6. **Decide** — the orchestrator picks the agreed deal with the highest total net value
   (`(price − freight − handling − processing) × quantity`) and releases the rest.

The dashboard streams all of this live: buyer cards, one chat thread per negotiation,
private agent reasoning, validator bounces, a price-convergence chart and the final
recommendation card.

---

## Quick start

Requires Python 3.11+ (tested on 3.13 and 3.14).

**Linux / macOS**

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m demo.web --replay demo/sample_run.jsonl
```

**Windows (PowerShell)**

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m demo.web --replay demo/sample_run.jsonl
```

Open <http://127.0.0.1:8000>. Replay mode needs no API keys and no network — it plays a
recorded negotiation through the same dashboard.

> Always run through the virtual environment's Python (`.venv/bin/python` or
> `.\.venv\Scripts\python`), or activate it first. The system `python` won't have the
> dependencies.

### Live negotiation (needs API keys)

```sh
cp .env.example .env        # then paste your API keys into .env
.venv/bin/python -m demo.web
```

Click **Live** in the dashboard to start a real multi-agent negotiation (about one to two
minutes). Keys and model chains are configured in `.env` — see `.env.example` for every
option. The default setup uses Google Gemini (3.1 Flash-Lite) for all agents with NVIDIA
DeepSeek as a last-resort backup; OpenRouter is also supported. A different env file can
be passed with `--env-file path`.

`.env` is gitignored. Never commit API keys.

---

## Other ways to run it

| Command | What it does |
|---|---|
| `python -m demo.web [--replay FILE] [--port 8000] [--host 127.0.0.1]` | Dashboard (live or replay) |
| `python -m orchestrator.run --live --top-n 3` | Live negotiation in the terminal, prints the recommendation JSON |
| `python -m orchestrator.run` | Deterministic pipeline, no API keys needed |
| `python -m demo.render_log [--live]` | Readable terminal log plus a recommendation card |
| `python -m agents.circularity --material-id ld_slag --quantity-tonnes 100000 --seller-id tata_steel_bsl` | Buyer matching only |

`--top-n` accepts 3–5 concurrent buyers. Every live run is recorded to `runs/<run_id>.jsonl`
(gitignored) and can be replayed with `--replay`.

Run the tests (fully offline, a few seconds):

```sh
.venv/bin/python -m unittest discover -s tests
```

### Deploying

`render.yaml` deploys the dashboard to [Render](https://render.com). Set API keys as
Render environment variables, not in the repository. Render's disk is ephemeral; attach a
disk and set `RUNS_DIR=/var/data/runs` to keep recordings.

---

## Project layout

```
agents/
  circularity/     buyer matching (knowledge graph) — agent.py, knowledge_graph.py
  logistics/       port-graph routing, freight and transit time
  negotiation/     LLM seller/buyer agents, validators, fallback engine, prompts.py
  validation.py    shared numeric validation
orchestrator/      pipeline, concurrent negotiations, run_multi_agent() for the dashboard
demo/              FastAPI dashboard (web.py, static/), event validation, replay, terminal log
data/              seller, buyers, materials, ports, routes + research notes
tests/             offline test suite
```

---

## Data and limitations

This is a hackathon prototype on a fixed data snapshot — no buyer is contacted, no ship is
booked, and an "accepted" outcome is a simulation, not a contract.

- **Buyers:** 13 real Bangladeshi cement companies (names, capacity and ports from public
  sources) plus 12 synthetic buyers. Individual prices, demand and contract terms are
  **illustrative**, anchored to published aggregate trade data (`data/RESEARCH_FINDINGS.md`),
  not real quotes.
- **Routes:** Dhamra → Chittagong / Mongla distances come from sea-route calculators;
  transit time is modeled sailing time and excludes loading, customs and weather.
- **CO₂ avoided** is an **estimate** (0.85 t CO₂ per tonne of clinker displaced, 1:1
  substitution), not a measured or lifecycle-verified figure. It is only shown for cement
  buyers.
- **One deal per run:** the seller closes with a single buyer; splitting supply across
  several buyers is out of scope.
- **Live mode depends on external LLM providers** and their free-tier quotas. If a provider
  fails, the deterministic fallback keeps the run going and says so in the event stream;
  replay mode is the no-network backup.

---

## License

See [LICENSE](LICENSE).
