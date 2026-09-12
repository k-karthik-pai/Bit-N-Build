# Action item: fix distance_km in data/routes.json

## Status: Dhamra→Mongla fixed. Dhamra→Chittagong still open.

`data/routes.json` currently has:

```json
[
  {"from_port_id": "dhamra", "to_port_id": "chittagong", "distance_km": 1350, "base_cost_per_tonne_usd": 7},
  {"from_port_id": "dhamra", "to_port_id": "mongla", "distance_km": 439.5, "base_cost_per_tonne_usd": 6},
  {"from_port_id": "mongla", "to_port_id": "chittagong", "distance_km": 250, "base_cost_per_tonne_usd": 1.5},
  {"from_port_id": "chittagong", "to_port_id": "mongla", "distance_km": 250, "base_cost_per_tonne_usd": 1.5}
]
```

Dhamra→Mongla was fixed using a real lookup from
[searoutesnav.com](https://searoutesnav.com/route/dhamra-in-anchorage-27093/to/mongla-bdmgl-11063):
237.3 nm (23h 44m at 10.0 knots) = 439.5 km. Worth noting: that duration
estimate (10.0 knots) matches `agents/logistics/__init__.py`'s own
`CARGO_SHIP_SPEED_KM_PER_DAY` constant (444.5 km/day = 10.0 knots) almost
exactly — good sign the transit-time model was already well-calibrated.

**Dhamra→Chittagong (currently 1350 km) is still wrong** and still needs a
real number. Confirmed via the straight-line (haversine) distance between
the same coordinates already in `data/ports.json`:

| Route | Straight-line (verified) | Real (searoutesnav.com) | Current routes.json | Status |
|---|---|---|---|---|
| Dhamra → Chittagong | 530 km | *not yet found* | 1350 km | **still needs fixing** |
| Dhamra → Mongla | 330 km | 439.5 km | 439.5 km ✅ | fixed |
| Chittagong ↔ Mongla | 230 km | — | 250 km | fine, not flagged |

A real sea route is never shorter than the straight line, but 1350 km is
2.5x the 530 km floor — an implausible amount of detour for two ports on
the same coastline with no major landmass to route around. Only
`distance_km` is suspect — `base_cost_per_tonne_usd` values look
independently reasonable against real freight benchmarks and don't need
to change.

## Why this wasn't just fixed already

Per `docs/DATA.md`'s own sourcing note: "use a sea-route distance calculator
(searates.com, ports.com) — do not estimate by hand." I tried to get an
authoritative number and hit a wall specific to my environment:

- Direct fetches to `ports.com`, `searates.com`, and `breezada.com`'s
  route-distance pages all failed (403 / unreachable) from this sandbox.
- `searoutesnav.com` **is** reachable — that's what produced the working
  Dhamra→Mongla number above. But its port search is JS-driven (autocomplete,
  not a guessable URL), so I couldn't self-serve a Dhamra→Chittagong lookup
  the same way — I could only read a route page whose exact URL was already
  known (the one that gave us Mongla).
- I tried to back into a number by finding *known* real Bay of Bengal sea
  routes and computing an empirical straight-line-to-real-route multiplier
  instead, but two different real routes gave conflicting ratios (0.97x and
  1.41x) — not reliable enough to hand-estimate Dhamra→Chittagong from.

## What to do

1. Go to [searoutesnav.com](https://searoutesnav.com) (already proven to
   work for this project — use its route search, origin "Dhamra", destination
   "Chittagong" or "Chattogram") — or `searates.com/distance-time` /
   `ports.com/sea-route` if you prefer.
2. Look up: Dhamra Port, India → Chittagong Port, Bangladesh.
3. Take the nautical-mile figure, convert to km (`nm * 1.852`), and update
   `distance_km` for that one row in `data/routes.json`.
4. Leave `base_cost_per_tonne_usd` as-is unless you have a reason to change it.
5. Sanity-check the result: it should land at or above the straight-line
   floor (530 km) — if you get something close to the current 1350 km,
   double check you didn't select a land-route or the wrong port pair.
6. Flag the change to the team (same as any data swap, per `docs/brief-dataset.md`).

## Reference

Full research context (company anchors, pricing, CO2 methodology, etc.) is
in `data/RESEARCH_FINDINGS.md` §1 — this doc only covers the distance
action item in more depth since it's the one thing that couldn't be
resolved automatically.
