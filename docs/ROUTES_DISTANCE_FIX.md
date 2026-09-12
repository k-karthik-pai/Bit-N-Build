# routes.json distance fix — DONE

Both flagged distances in `data/routes.json` are now real, calculator-sourced
values (searoutesnav.com), replacing the original placeholders:

| Route | Was | Now | Source |
|---|---|---|---|
| Dhamra → Chittagong | 1350 km | **540.8 km** | searoutesnav.com: 292 nm |
| Dhamra → Mongla | 1250 km | **439.5 km** | searoutesnav.com: 237.3 nm (23h44m @ 10.0 kn) |
| Chittagong ↔ Mongla | 250 km | 250 km (unchanged) | already reasonable, not flagged |

Both new values sit comfortably above the independently-verified
straight-line (haversine) floors (530 km and 330 km respectively), which is
what a real sea route should do. `base_cost_per_tonne_usd` was left
untouched throughout — only `distance_km` was ever wrong.

The original `data/RESEARCH_FINDINGS.md` was not committed. See git history for the
investigation (direct calculator fetches were blocked from the
assistant's sandbox for most sites; searoutesnav.com worked and gave both
final numbers once a teammate ran the lookups manually).
