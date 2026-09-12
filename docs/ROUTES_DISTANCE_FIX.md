# Action item: fix distance_km in data/routes.json

## The problem

`data/routes.json` currently has:

```json
[
  {"from_port_id": "dhamra", "to_port_id": "chittagong", "distance_km": 1350, "base_cost_per_tonne_usd": 7},
  {"from_port_id": "dhamra", "to_port_id": "mongla", "distance_km": 1250, "base_cost_per_tonne_usd": 6},
  {"from_port_id": "mongla", "to_port_id": "chittagong", "distance_km": 250, "base_cost_per_tonne_usd": 1.5},
  {"from_port_id": "chittagong", "to_port_id": "mongla", "distance_km": 250, "base_cost_per_tonne_usd": 1.5}
]
```

The `1350` and `1250` values are almost certainly wrong. Confirmed via the
straight-line (haversine) distance between the same coordinates already in
`data/ports.json`:

| Route | Straight-line (verified) | Current routes.json | Ratio |
|---|---|---|---|
| Dhamra → Chittagong | 530 km | 1350 km | ~2.5x |
| Dhamra → Mongla | 330 km | 1250 km | ~3.8x |
| Chittagong ↔ Mongla | 230 km | 250 km | ~1.1x (fine, not flagged) |

A real sea route is never shorter than the straight line, but 2.5-4x is an
implausible amount of detour for two ports on the same coastline with no
major landmass to route around. The 1350/1250 numbers are most likely
placeholders, or accidentally a land/river-routing distance instead of a
sea-route distance. Only `distance_km` is suspect — the `base_cost_per_tonne_usd`
values (7, 6, 1.5) look independently reasonable against real freight
benchmarks and don't need to change.

Only `distance_km` for the two Dhamra legs needs fixing; leave everything
else in the file alone.

## Why this wasn't just fixed already

Per `docs/DATA.md`'s own sourcing note: "use a sea-route distance calculator
(searates.com, ports.com) — do not estimate by hand." I tried to get an
authoritative number and hit a wall specific to my environment:

- Direct fetches to `ports.com`, `searates.com`, and `breezada.com`'s
  route-distance pages all failed (403 / unreachable) from this sandbox.
- I tried to back into a number by finding *known* real Bay of Bengal sea
  routes and computing an empirical straight-line-to-real-route multiplier:
  - Chittagong ↔ Kolkata: real route 190 NM vs straight-line 196 NM → ratio ≈ 0.97x
  - Kolkata ↔ Visakhapatnam: real route 576 NM vs straight-line 408 NM → ratio ≈ 1.41x

  Those two disagree by 45%, meaning port-specific approach-channel geometry
  (river mouths, sandbars, pilot stations — e.g. Mongla port sits ~71 NM up
  the Possur river from open sea) dominates and there's no reliable generic
  multiplier to apply here. Publishing a number from that method would just
  be a differently-flawed guess, not the calculator-sourced value DATA.md
  asks for.

This needs someone with normal (non-sandboxed) internet access to actually
run the calculator.

## What to do

1. Go to [searates.com/distance-time](https://www.searates.com/distance-time/)
   or [ports.com](http://ports.com/sea-route/).
2. Look up:
   - Dhamra Port, India → Chittagong Port, Bangladesh
   - Dhamra Port, India → Mongla Port, Bangladesh
3. Take the nautical-mile figure, convert to km (`nm * 1.852`), and update
   `distance_km` for those two rows in `data/routes.json` only.
4. Leave `base_cost_per_tonne_usd` as-is unless you have a reason to change it.
5. Sanity-check the result: it should land somewhere at or above the
   straight-line floor (530 km / 330 km) — if the calculator gives you
   something close to the current 1350/1250, double check you didn't
   accidentally select a land-route or a different pair of ports.
6. Flag the change to the team (same as any data swap, per `docs/brief-dataset.md`).

## Reference

Full research context (company anchors, pricing, CO2 methodology, etc.) is
in `data/RESEARCH_FINDINGS.md` §1 — this doc only covers the distance
action item in more depth since it's the one thing that couldn't be
resolved automatically.
