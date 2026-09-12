"""
Generates data/buyers.json per docs/brief-dataset.md step 4:
  1. Hardcode real Bangladeshi cement companies (is_real_reference: true),
     accompanied by source notes for company profiles and port assumptions.
     The original research report was not committed; see docs/LIMITATIONS.md.
  2. Generate synthetic entries (is_real_reference: false) by varying
     demand/price/port within realistic bounds around those anchors.

Individual buyer demand/price figures are illustrative, anchored to
published aggregate trade data (see docs/DATA.md's own framing note) - no
company publicly discloses LD-slag-specific purchase volumes, so exact
figures here are modeled, not leaked/real contract terms. Demand is kept
within a plausible fraction of the seller's 100,000 t/year total supply
(data/seller.json), not scaled directly off each company's full cement
capacity (which would imply implausibly large single-buyer demand).

Re-run with `python3 data/generate_buyers.py` from the repo root to
regenerate data/buyers.json (deterministic - fixed random seed).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent / "buyers.json"

MATERIAL_ID = "ld_slag"

# ---------------------------------------------------------------------------
# Real-reference companies (see source_note below) - port preference and
# relative size are anchored to that research; exact LD-slag demand/price
# figures are illustrative (see module docstring).
# ---------------------------------------------------------------------------

REAL_BUYERS = [
    {
        "buyer_id": "shah_cement",
        "name": "Shah Cement Industries Ltd",
        "port_id": "chittagong",
        "annual_demand_tonnes": 65000,
        "max_acceptable_price_per_tonne_usd": 28,
        "min_quality_requirements": {"CaO_min_pct": 42},
        "source_note": "Real company (~6.0 MTPA, largest VRM in world) - shahcement.com; "
                        "Superbrands 2020-22; TBS News Jan 2026. LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "crown_cement",
        "name": "Crown Cement PLC",
        "port_id": "chittagong",
        "annual_demand_tonnes": 60000,
        "max_acceptable_price_per_tonne_usd": 27,
        "min_quality_requirements": {"CaO_min_pct": 40},
        "source_note": "Real company (5.7 MTPA, owns 2 ocean-going ships) - crowncement.com "
                        "Annual Report 2023-24. LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "seven_circle",
        "name": "Seven Circle Bangladesh Ltd",
        "port_id": "chittagong",
        "annual_demand_tonnes": 58000,
        "max_acceptable_price_per_tonne_usd": 26,
        "min_quality_requirements": {"CaO_min_pct": 40},
        "source_note": "Real company (8.4 MTPA, Shun Shing Group) - LinkedIn; Trademo. "
                        "LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "premier_cement",
        "name": "Premier Cement Mills PLC",
        "port_id": "chittagong",
        "annual_demand_tonnes": 52000,
        "max_acceptable_price_per_tonne_usd": 27,
        "min_quality_requirements": {"CaO_min_pct": 41},
        "source_note": "Real company (5.2 MTPA) - premiercement.com; bcma.com.bd; ~$48M "
                        "clinker imports. LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "unique_cement",
        "name": "Unique Cement Industries Ltd",
        "port_id": "chittagong",
        "annual_demand_tonnes": 48000,
        "max_acceptable_price_per_tonne_usd": 25,
        "min_quality_requirements": {"CaO_min_pct": 39},
        "source_note": "Real company (5.0 MTPA, Meghna Group) - bcma.com.bd; WCA. "
                        "LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "bashundhara_cement",
        "name": "Bashundhara Cement",
        "port_id": "mongla",
        "annual_demand_tonnes": 50000,
        "max_acceptable_price_per_tonne_usd": 29,
        "min_quality_requirements": {"CaO_min_pct": 42},
        "source_note": "Real company (5.05 MTPA, largest in Bangladesh, factory at Mongla "
                        "Port Industrial Area) - bashundharacement.com; bcma.com.bd. "
                        "LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "akij_cement",
        "name": "Akij Cement Company Ltd",
        "port_id": "chittagong",
        "annual_demand_tonnes": 40000,
        "max_acceptable_price_per_tonne_usd": 24,
        "min_quality_requirements": {"CaO_min_pct": 38},
        "source_note": "Real company (~2.0+ MTPA, PCC CEM-II/B-M S-L 72-79% clinker) - "
                        "MarketInside; Trademo; ~$80M imports May25-Apr26. "
                        "LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "heidelberg_bd",
        "name": "Heidelberg Materials Bangladesh PLC",
        "port_id": "chittagong",
        "annual_demand_tonnes": 30000,
        "max_acceptable_price_per_tonne_usd": 26,
        "min_quality_requirements": {"CaO_min_pct": 40},
        "source_note": "Real company (~1.5 MTPA, ScanCement/RubyCement brands, since 1998) - "
                        "Annual Report 2024. LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "diamond_cement",
        "name": "Diamond Cement Ltd",
        "port_id": "chittagong",
        "annual_demand_tonnes": 22000,
        "max_acceptable_price_per_tonne_usd": 23,
        "min_quality_requirements": {},
        "source_note": "Real company (1.35 MTPA) - bcma.com.bd; eximtradedata; imports "
                        "clinker from Indonesia. LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "nitol_cement",
        "name": "Nitol Cement Industries Ltd",
        "port_id": "mongla",
        "annual_demand_tonnes": 12000,
        "max_acceptable_price_per_tonne_usd": 22,
        "min_quality_requirements": {},
        "source_note": "Real company (0.13 MTPA, one of earliest cement companies in BD, "
                        "Jessore plant) - nitolniloy.com.bd. LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "metrocem_group",
        "name": "Metrocem Group",
        "port_id": "chittagong",
        "annual_demand_tonnes": 20000,
        "max_acceptable_price_per_tonne_usd": 24,
        "min_quality_requirements": {"CaO_min_pct": 38},
        "source_note": "Real company (owns vessels for clinker import, capacity not "
                        "disclosed in sources reviewed) - TBS News Jan 2026. Port assumed "
                        "Chittagong (92% of BD trade); LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "shamim_cement",
        "name": "Shamim Cement",
        "port_id": "chittagong",
        "annual_demand_tonnes": 15000,
        "max_acceptable_price_per_tonne_usd": 22,
        "min_quality_requirements": {},
        "source_note": "Real company, mentioned in industry sources reviewed; capacity/port "
                        "not disclosed there. Port assumed Chittagong (92% of BD trade); "
                        "LD-slag demand/price illustrative.",
    },
    {
        "buyer_id": "kds_cement",
        "name": "KDS Cement",
        "port_id": "chittagong",
        "annual_demand_tonnes": 15000,
        "max_acceptable_price_per_tonne_usd": 22,
        "min_quality_requirements": {},
        "source_note": "Real company, mentioned in industry sources reviewed; capacity/port "
                        "not disclosed there. Port assumed Chittagong (92% of BD trade); "
                        "LD-slag demand/price illustrative.",
    },
]

# ---------------------------------------------------------------------------
# Synthetic entries - clearly fictional company names, varied around the
# real anchors above (same demand/price/port/quality ranges).
# ---------------------------------------------------------------------------

SYNTHETIC_NAMES = [
    "Padma Valley Cement Ltd",
    "Meghna Delta Building Materials",
    "Sundarban Cement Works",
    "Jamuna Ridge Cement Co.",
    "Karnaphuli Industrial Cement",
    "Green Bengal Cement Ltd",
    "Sylhet Highland Cement",
    "Rupsha River Cement Mills",
    "Padma Bridge Cement Corp",
    "Barisal Coastal Cement Ltd",
    "Dhaka Metro Cement Industries",
    "Chattogram Bay Cement Ltd",
]

PORT_WEIGHTS = {"chittagong": 0.75, "mongla": 0.25}  # Chittagong handles ~92% of BD trade;
# skewed slightly less extreme here since Mongla-based Bashundhara alone accounts for a
# large share of that remainder in the real anchor set.


def generate_synthetic_buyers(count: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    synthetic = []
    ports = list(PORT_WEIGHTS.keys())
    weights = list(PORT_WEIGHTS.values())
    for i, name in enumerate(SYNTHETIC_NAMES[:count]):
        demand = rng.randint(8, 45) * 1000
        price = rng.randint(20, 32)
        port_id = rng.choices(ports, weights=weights, k=1)[0]
        has_requirement = rng.random() < 0.7
        requirements = {"CaO_min_pct": rng.randint(35, 44)} if has_requirement else {}
        synthetic.append({
            "buyer_id": f"synth_buyer_{i + 1}",
            "name": name,
            "port_id": port_id,
            "annual_demand_tonnes": demand,
            "max_acceptable_price_per_tonne_usd": price,
            "min_quality_requirements": requirements,
            "source_note": "synthetic, generated around real market anchors",
        })
    return synthetic


def contract_months_range(annual_demand_tonnes: int) -> tuple[int, int]:
    """
    Acceptable contract length (months) by demand tier - illustrative, not
    researched (see docs/LIMITATIONS.md). Larger buyers can commit to longer
    offtake; smaller ones want shorter exposure. Derived from demand rather
    than drawn from the seeded rng so existing generated values don't shift,
    and every tier contains 12 (the negotiation fallback's fixed term).
    """
    if annual_demand_tonnes >= 40000:
        return 12, 36
    if annual_demand_tonnes >= 15000:
        return 6, 24
    return 3, 12


def build_buyers() -> list[dict]:
    buyers = []
    for b in REAL_BUYERS:
        months_min, months_max = contract_months_range(b["annual_demand_tonnes"])
        buyers.append({
            "buyer_id": b["buyer_id"],
            "name": b["name"],
            "country": "Bangladesh",
            "port_id": b["port_id"],
            "material_required_id": MATERIAL_ID,
            "annual_demand_tonnes": b["annual_demand_tonnes"],
            "max_acceptable_price_per_tonne_usd": b["max_acceptable_price_per_tonne_usd"],
            "min_quality_requirements": b["min_quality_requirements"],
            "contract_months_min": months_min,
            "contract_months_max": months_max,
            "is_real_reference": True,
            "source_note": b["source_note"],
        })
    for s in generate_synthetic_buyers(count=len(SYNTHETIC_NAMES)):
        months_min, months_max = contract_months_range(s["annual_demand_tonnes"])
        buyers.append({
            "buyer_id": s["buyer_id"],
            "name": s["name"],
            "country": "Bangladesh",
            "port_id": s["port_id"],
            "material_required_id": MATERIAL_ID,
            "annual_demand_tonnes": s["annual_demand_tonnes"],
            "max_acceptable_price_per_tonne_usd": s["max_acceptable_price_per_tonne_usd"],
            "min_quality_requirements": s["min_quality_requirements"],
            "contract_months_min": months_min,
            "contract_months_max": months_max,
            "is_real_reference": False,
            "source_note": s["source_note"],
        })
    return buyers


def main() -> None:
    buyers = build_buyers()
    assert 20 <= len(buyers) <= 50, f"expected 20-50 buyers, got {len(buyers)}"
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(buyers, f, indent=2)
        f.write("\n")
    real_count = sum(1 for b in buyers if b["is_real_reference"])
    print(f"Wrote {len(buyers)} buyers ({real_count} real, {len(buyers) - real_count} synthetic) to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
