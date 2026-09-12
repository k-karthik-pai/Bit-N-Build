# Data and model limitations

This repository is a fixed-snapshot hackathon prototype. No buyer is contacted,
no shipment is booked, and an accepted simulation outcome is not a signed contract.

- `is_real_reference` identifies a real-reference company name, not verified
  willingness to buy LD slag. Individual prices and quantities are illustrative.
- Source notes exist in `buyers.json`. The research document previously referenced
  as `data/RESEARCH_FINDINGS.md` was not committed. Aggregate price derivation and
  freight benchmarks cannot be independently reconstructed from this repo.
- Dhamra route distances have recorded calculator lookups in
  `ROUTES_DISTANCE_FIX.md`; the 250 km inter-port links lack equivalent evidence.
- Transit time is modeled sailing time plus intermediate-port processing. It excludes
  loading at origin, discharge at destination, queues, customs and weather delays.
- Compatibility checks only the supplied composition thresholds. It does not certify
  material safety, regulatory compliance, or suitability for a particular process.
- CO2 output preserves the existing illustrative 0.85 t/t factor. It assumes 1:1
  clinker displacement for the cement scenario and excludes treatment/transport.
  It is not a validated LD-slag lifecycle factor and is not applicable to road aggregate.
- Contract-length ranges (`contract_months_min/max` in `buyers.json` and `seller.json`)
  are illustrative: buyer ranges are derived from demand tier, not researched terms.
- Concurrent negotiations close at most one deal per run: threads reach agreed-pending,
  the orchestrator selects one by total net value and releases the rest. Splitting the
  seller's supply across several buyers (multi-buyer allocation) is out of scope.
- BATNA compares modeled alternative margins per tonne. Final selection compares
  total values among the resulting eligible offers. This is not a global multi-buyer
  allocation optimizer; pending counteroffers can still be recommended.
- LLM agents propose moves as validated JSON; a deterministic validator gates every
  `make_offer` (move-legal: own floor/ceiling, quantity, contract months) and final
  `accept_offer` (deal-legal: both sides' bounds, latest counterparty offer). Messages
  stating own reservation (floor/ceiling/BATNA), numbers not matching structured fields,
  or false seller leverage claims are bounced for retry (2 bounces → deterministic
  fallback, visible `fallback` event; per-call 20 s timeout also falls back). The
  deterministic engine remains the offline test path and fallback. Live provider
  availability is an external dependency; recorded replay is the demo safety net.
