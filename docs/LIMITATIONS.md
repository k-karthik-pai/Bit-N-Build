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
- BATNA compares modeled alternative margins per tonne. Final selection compares
  total values among the resulting eligible offers. This is not a global multi-buyer
  allocation optimizer; pending counteroffers can still be recommended.
- Optional LLM dialogue is wording only. Numeric placeholders are validated before
  local substitution; prose is not a contractual commitment. Provider failures fall
  back to deterministic dialogue. Live provider availability is an external dependency.
