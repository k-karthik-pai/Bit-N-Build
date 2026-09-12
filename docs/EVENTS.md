# EVENTS.md

Frozen in Step 0 of `NEXT_STEPS.md`. This is the contract between the negotiation runtime
(producer) and the live UI / recorder / replay (consumers). Changing it after Step 1 and
Step 2 start must be flagged to everyone — same rule as `AGENTS.md`.

Transport: one JSON object per event. Live runs stream events over SSE; every run is also
written line-by-line to `runs/<run_id>.jsonl`; `--replay` streams a recorded file through
the same endpoint. `demo/sample_run.jsonl` is the hand-written reference run the UI is
built against, and `tests/test_events_sample.py` checks it against this document and the
real data files.

## Envelope

```json
{
  "seq": 12,
  "ts": "2026-09-12T10:15:07.300Z",
  "run_id": "sample-0001",
  "deal_id": "shah_cement",
  "type": "offer",
  "from_agent": "seller_agent",
  "to_agent": "buyer_agent:shah_cement",
  "model": "gemini:flash",
  "payload": {},
  "validator": {"ok": true, "gate": "move_legal", "reason": "within your bounds"},
  "delivered": true
}
```

| Field | Rule |
|---|---|
| `seq` | Integer, starts at 1, strictly increasing by 1 within a run. Ordering key — never sort by `ts`. |
| `ts` | ISO-8601 UTC with milliseconds. Non-decreasing. Replay uses the gaps for timing. |
| `run_id` | Same for every event in a run. |
| `deal_id` | The buyer_id of the negotiation thread, or `null` for run-level events. |
| `type` | One of the types below. |
| `from_agent` / `to_agent` | `seller_agent`, `buyer_agent:<buyer_id>`, `logistics_agent`, `circularity_agent`, `orchestrator`, `validator`, `runtime`, or `null` (to_agent only). |
| `model` | `"provider:model"` for events produced by an LLM call, else `null`. The UI badges chat bubbles with it. |
| `payload` | Type-specific, below. |
| `validator` | Present on every agent move (`offer`, `accept`, `reject`, `info_request`); `null` otherwise. `gate` is `move_legal` or `deal_legal`. |
| `delivered` | Agent moves only: `false` means the validator bounced the move back to its author — the counterparty never saw it. `null` for non-moves. |

## Event types

| type | from → to | payload |
|---|---|---|
| `run_started` | orchestrator → null | `{seller_id, material_id, quantity_tonnes, objective, top_n}` |
| `match` | circularity_agent → orchestrator | `{matched_count, selected: [{buyer_id, buyer_name, application, compatibility_score, port_id}]}` |
| `route` | logistics_agent → orchestrator | `{origin_port, destination_port, route_id, distance_km, transit_days, cost_per_tonne_usd}` |
| `thread_started` | orchestrator → null | `{buyer_id, buyer_name, seller_model, buyer_model}` |
| `offer` | agent → counterparty | `{offer_id, round, price_per_tonne_usd, quantity_tonnes, contract_months, message, rationale}` |
| `accept` | agent → counterparty | `{offer_id, round, message, rationale}` |
| `reject` | agent → counterparty | `{round, reason, message, rationale}` |
| `info_request` | agent → logistics_agent / orchestrator | `{round, topic, rationale}` — topic `route_cost` or `best_competing_offer` (seller only) |
| `info_response` | logistics_agent / orchestrator → agent | `{round, topic, data}` |
| `fallback` | runtime → agent | `{round, agent, cause, detail}` — cause `timeout`, `provider_error`, `invalid_moves`; the deterministic engine made that agent's move and the move follows as its own event with `model: null` |
| `thread_result` | orchestrator → null | `{status, price_per_tonne_usd, quantity_tonnes, contract_months, rounds, final_offer_id, total_net_value_usd, agreed_pending}` — `status` is `accepted` / `countered` / `rejected` (AGENTS.md §2) |
| `released` | orchestrator → buyer_agent | `{reason}` — sent to every `accepted` / `countered` thread that was not selected |
| `deal_closed` | orchestrator → null | `{buyer_id, offer_id, total_net_value_usd}` |
| `recommendation` | orchestrator → null | the unchanged `AGENTS.md` §4 object |
| `run_completed` | orchestrator → null | `{duration_ms, llm_calls, validator_bounces, fallbacks}` |
| `run_failed` | orchestrator → null | `{error}` — terminal; the UI shows it instead of a card |

## Rules the producer must follow

- **Private rationale.** `rationale` is the agent's private reasoning. It is shown in the
  UI (collapsible "thinking" line) and recorded, but it is **never** included in the
  counterparty's prompt. Only `message` and the structured terms cross to the other side.
- **Offer ids** are assigned by the runtime as `<deal_id>-o<n>`, counting delivered offers
  per thread. Bounced offers get no id (`offer_id: null`).
- **Bounced moves** (`delivered: false`) go back to the author with `validator.reason`.
  The reason may reference only the author's own constraints (move-legal gate) — never the
  counterparty's bound. The author's next event is its retry.
- **Rounds.** A round is one delivered move by one agent. `info_request`/`info_response`
  and bounced moves share the round number of the move they precede. Max 6 rounds; at the
  limit the best open counter becomes `countered`.
- **Numbers in `message`** must match either the move's own structured fields (price,
  quantity, contract months) or a structured field of an offer already **delivered earlier
  in the same thread** by either side (so "you offered $24.50" is fine). Anything else is a
  bounced move. *(Amended 2026-09-12, team-approved: quoting public history is grounded, not
  invented.)* Also allowed: the thread's route freight `cost_per_tonne_usd` from the
  logistics agent's `route` / `info_response` data — it is public to both sides.
  *(Amended 2026-09-12, team-approved.)*
- **Own-reservation leak** is checked only against numbers **not** covered by the rule
  above — a number equal to the move's own terms or to public thread history is never a
  leak, even if it coincides with the agent's floor/ceiling.
- **Leverage claims** by the seller: "other interest" needs at least one other live thread;
  "a better offer" needs a live buyer offer elsewhere with higher total net value. Checked
  against actual thread state at that moment.
- **Net value** everywhere is `(price − freight − 2.00 handling − 1.50 processing) ×
  quantity`, the same formula as the orchestrator.
- **Exactly one terminal event**: `run_completed` or `run_failed`, last.
