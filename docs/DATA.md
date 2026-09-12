# DATA.md

Everything under `/data/`. Schema frozen at project start — see PLAN.md for why changing
it late is costly.

## seller.json (single object)
```json
{
  "seller_id": "string",
  "name": "string",
  "location": {"place": "string", "lat": 0.0, "lon": 0.0},
  "nearest_port_id": "string",
  "material_id": "string",
  "available_quantity_tonnes_per_year": 100000,
  "min_acceptable_price_per_tonne_usd": 22,
  "preferred_price_per_tonne_usd": 25,
  "current_disposal_cost_per_tonne_usd": 0
}
```

## materials.json (list)
```json
{
  "material_id": "string",
  "name": "string",
  "composition_pct": {"CaO": 0, "SiO2": 0, "Fe2O3": 0, "MgO": 0},
  "applications": [
    {"application": "string", "min_quality_requirements": {}}
  ]
}
```

## buyers.json (list, 20–50 entries)
```json
{
  "buyer_id": "string",
  "name": "string",
  "country": "string",
  "port_id": "string",
  "material_required_id": "string",
  "annual_demand_tonnes": 0,
  "max_acceptable_price_per_tonne_usd": 0,
  "min_quality_requirements": {},
  "is_real_reference": true,
  "source_note": "string — cite where the name/anchor number came from, or 'synthetic, generated around real market anchors' if not"
}
```

## ports.json (list)
```json
{
  "port_id": "string",
  "name": "string",
  "country": "string",
  "lat": 0.0,
  "lon": 0.0
}
```

## routes.json (optional — can be computed at runtime instead)
```json
{
  "from_port_id": "string",
  "to_port_id": "string",
  "distance_km": 0,
  "base_cost_per_tonne_usd": 0
}
```

---

## Sourcing notes (fixed snapshot, not a live feed — no scraping needed)

**Real anchors to use, not invent:**
- Real Bangladeshi cement importers/manufacturers to draw buyer names from: Shah Cement,
  Akij Cement, Crown Cement, Seven Circle, Lafarge Surma (Holcim), HeidelbergCement
  Bangladesh, Diamond Cement — most importing clinker/slag through Chittagong or Mongla.
- Aggregate market data to derive a defensible average price-per-tonne anchor: published
  clinker + raw-material import value/volume figures (e.g. clinker import value and
  Chittagong Port Authority annual clinker tonnage). Average price = value / volume.
- Port coordinates and distances: use a sea-route distance calculator (searates.com,
  ports.com) for Dhamra ↔ Chittagong / Mongla — do not estimate by hand.
- Freight cost basis: anchor to a public bulk freight rate benchmark rather than an
  invented number.

**How the full 20–50 buyer entries get built:**
1. Research the real anchors above (~45–60 min, one person, Track A in PLAN.md).
2. Hardcode the 6–8 real companies as `is_real_reference: true` entries with a `source_note`.
3. Run a short generation script that creates the remaining synthetic entries by varying
   demand/price/port within realistic bounds around the real anchors — mark these
   `is_real_reference: false`.

**Framing for the demo:** state plainly that individual buyer prices/quantities are
illustrative and anchored to published aggregate trade data, not claimed as real leaked
contract terms. This is more credible under questioning than presenting synthetic numbers
as if they were confidential deal data.
