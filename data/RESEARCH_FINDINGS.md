# Track A — Research Findings & Data Anchors

> Generated from web research on 2026-09-12. All anchors sourced from public data.
> Use this to hardcode real entries into `buyers.json` and validate other data files.

---

## 1. Sea Distance Recommendation

> **⚠ Action item:** Replace `routes.json` distance values using a proper sea-route distance calculator (searates.com, ports.com, or shipdata.net) per DATA.md sourcing notes. Current values appear inflated.

Computed straight-line + estimated sea route distances from port coordinates:

| Route | Straight-line | Estimated Sea Route | Current routes.json | Issue |
|-------|--------------|-------------------|---------------------|-------|
| Dhamra → Chittagong | 530 km | ~690 km | 1350 km | ~2x too high |
| Dhamra → Mongla | 330 km | ~430 km | 1250 km | ~3x too high |
| Chittagong → Mongla | 230 km | ~345 km | 250 km | Reasonable |

The 1350/1250 values in routes.json may reflect inland waterway routing via rivers, or may simply be placeholders. Use searates.com for authoritative values.

---

## 2. Real Bangladeshi Cement Companies (buyer anchors)

### Confirmed companies with verified details:

| # | Company | Buyer ID | Location | Port | Capacity (MTPA) | Source Note |
|---|---------|----------|----------|------|-----------------|-------------|
| 1 | Shah Cement Industries Ltd | shah_cement | Muktarpur, Munshiganj | Chittagong | ~6.0 (largest VRM in world, 15K TPD) | shahcement.com; Superbrands 2020-22; TBS News Jan 2026 |
| 2 | Akij Cement Company Ltd | akij_cement | Kadamprasul, Narayanganj | Chittagong | ~2.0+ | MarketInside; Trademo; ~$80M imports May25-Apr26 |
| 3 | Crown Cement PLC | crown_cement | West Mukterpur, Munshiganj | Chittagong | 5.7 (19,040 TPD) | crowncement.com Annual Report 2023-24; owns 2 ocean-going ships |
| 4 | Seven Circle Bangladesh Ltd (Seven Rings Cement) | seven_circle | Gazipur + Chattogram | Chittagong | 8.4 | LinkedIn; Trademo; Shun Shing Group (HK); imports from Indonesia |
| 5 | Heidelberg Materials Bangladesh PLC | heidelberg_bd | Chittagong + Kanchpur (near Dhaka) | Chittagong | ~1.5 (0.75+0.75 grinding) | Annual Report 2024; ScanCement/RubyCement brands; since 1998 |
| 6 | Diamond Cement Ltd | diamond_cement | Ichanagar, Karnaphuli, Chittagong | Chittagong | 1.35 | bcma.com.bd; eximtradedata; imports clinker from Indonesia |
| 7 | Premier Cement Mills PLC | premier_cement | Narayanganj + Chittagong | Chittagong | 5.2 (19,040 TPD) | premiercement.com; bcma.com.bd; ~$48M imports |
| 8 | Unique Cement Industries Ltd | unique_cement | Meghnaghat, Sonargaon, Narayanganj | Chittagong | 5.0 | bcma.com.bd; WCA; Meghna Group; Fresh/Meghnacem brands |
| 9 | Bashundhara Cement | bashundhara_cement | Mongla + Madangonj | Mongla | 5.05 (largest in BD) | bashundharacement.com; bcma.com.bd; VRM Loesche Germany |

### Additional real companies (smaller, usable for synthetic generation anchors):

| Company | Buyer ID | Location | Port | Capacity | Source Note |
|---------|----------|----------|------|----------|-------------|
| Nitol Cement Industries Ltd | nitol_cement | Baniargati, Jessore | Mongla | 0.13 (130K MT grey + 20K MT white) | nitolniloy.com.bd; one of earliest cement companies in BD |
| Metrocem Group | metrokem_group | (owns vessels) | Chittagong | unknown | TBS News Jan 2026; owns own vessels for clinker import |
| Shamim Cement | shamim_cement | — | — | — | Mentioned in industry sources |
| KDS Cement | kds_cement | — | — | — | Mentioned in industry sources |

### Company-specific port preferences (important for buyer generation):
- **Chittagong port preference**: Shah, Crown, Seven Circle, Heidelberg, Diamond, Premier, Unique, Akij, Nitol (all have Chittagong operations/plants)
- **Mongla port preference**: Bashundhara (has a factory at Mongla Port Industrial Area)
- Chittagong handles 92% of BD import/export cargo; Mongla is secondary

---

## 3. Import & Pricing Data

### Clinker import prices — Bangladesh (annual trend)
| Year | Avg Import Price/Tonne | YoY Change | Import Volume | Import Value (USD) |
|------|----------------------|------------|---------------|-------------------|
| 2013 | $63 | — | — | — |
| 2020 | ~$40 | — | — | $530M |
| 2021 | ~$34 | declining | 22M tons (peak) | $746M |
| 2022 | ~$40 | +17% | — | $772M |
| 2023 | $38 | -3.9% | 13M tons (-34.2%) | $488M |
| 2024 | $38 | +3.1% | — | $939M (FY23-24) |
| 2025 (mid) | ~$53 | freight-hike | — | — |

> **Sources:** IndexBox, Cemnet, Financial Express BD, Bangladesh Bank monthly data (BDT mn)
> **Key drivers:** Middle East conflict (2025-2026) raised freight costs, pushing landed clinker price from ~$45 to ~$53/tonne
> **Monthly data available from:** Bangladesh Bank (CEIC) — imports reported monthly in BDT mn

### Import price by source country (2020-2023, USD million)
| Country | 2020 | 2021 | 2022 | 2023 | CAGR |
|---------|------|------|------|------|------|
| Indonesia | 72.5 | 147 | 162 | 122 | 18.9% |
| Vietnam | 52.9 | 105 | 149 | 102 | 24.5% |
| Hong Kong SAR | 67.5 | 82.2 | 96.8 | 79.2 | 5.5% |
| Thailand | 99.2 | 122 | 146 | 78.6 | -7.5% |
| UAE | 123 | 189 | 151 | 52.4 | -24.8% |
| Pakistan | 63.5 | 49.1 | 20.0 | 30.8 | -21.4% |
| Iran | 16.8 | 20.8 | 12.1 | 11.2 | -12.6% |

### Company-specific import data (from trade databases)
| Company | Import Value | Main Sources |
|---------|-------------|--------------|
| Akij Cement | $80.09M (May25-Apr26) | Indonesia, Thailand, Vietnam |
| Shah Cement | $108.74M (competitor data) | Indonesia, Thailand |
| Crown Cement | $99.20M | Indonesia, Thailand |
| Seven Circle | $35.58M | Indonesia, India, China |
| Premier Cement | $48.28M | Indonesia, Thailand |
| Unique Cement | $89.90M (competitor data) | Indonesia, Thailand, Vietnam |
| Diamond Cement | $2.185M per shipment | Indonesia |

### Freight cost benchmarks
| Route Type | Cost/Tonne | Source |
|------------|-----------|--------|
| India→Bangladesh coastal (Handysize) | $5-12/tonne | LinkedIn cost analysis Jan 2026 |
| Handysize 28K DWT | ~₹810/MT sea freight | LinkedIn cost analysis |
| Handymax 38K DWT | ~₹694/MT sea freight | LinkedIn cost analysis |
| Chittagong→Mongla (250nm) | ~$1.5/tonne (current routes.json) | Reasonable for short hop |
| Large mills with own vessels | ~$12/tonne | TBS News Jan 2026 |

---

## 4. Company-Specific Quality Requirements (cement composition)

### Academic study — Bangladeshi OPC brands (DOI: 10.3329/cerb.v12i0.1491)
Tested brands: Holcim, Shah, Crown, King Brand, Anwar Cement — all conforming to BS EN 197-1

| Element | BS Standard | Holcim | Shah | Crown | King | Anwar |
|---------|------------|--------|------|-------|------|-------|
| SiO2 % | 21-22 | 21.45 | 21.52 | 22.13 | 21.62 | 22.33 |
| Al2O3 % | 6.0 | 4.3 | 4.58 | 5.32 | 4.95 | 3.89 |
| Fe2O3 % | 3.5 | 3.28 | 3.38 | 3.34 | 3.44 | 3.45 |
| CaO % | 63-67 | 64.32 | 66.02 | 63.73 | 63.76 | 65.56 |
| MgO % | 0.7 | 1.18 | 1.26 | 1.89 | 1.8 | 1.42 |
| SO3 % | max 1.5 | 3.56 | 2.76 | 2.42 | 1.87 | 0.95 |
| IR % | max 1.5 | 0.35 | 0.45 | 0.5 | 0.55 | 0.65 |

### Per-company specifications from company websites:

**Shah Cement:** CEM-I 52.5N grade, no extra additive, <1% insoluble residue, free from unsoundness

**Crown Cement (PCC CEM-II/A-M):** 80-94% clinker, max 5% insoluble residue, max 0.10% chloride, max 0.60% total alkali, 42.5N compressive strength (≥10 MPa at 2 days, ≥42.5 MPa at 28 days)

**Akij Cement (PCC CEM-II/B-M S-L):** 42.5N, 72-79% clinker, 21-28% blast furnace slag & limestone, 0-5% gypsum, C3S ~50%, C2S ~20%, C3A ~10%, C4AF ~8%

### Relevance to LD slag as clinker substitute:
- LD slag composition: CaO 45%, SiO2 15%, Fe2O3 20%, MgO 8%
- LD slag is used as **clinker substitute** in cement manufacturing (replaces raw clinker)
- Buyers with higher CaO requirements (≥40% min per our schema) prefer slag with high CaO content
- LD slag's high Fe2O3 (20%) is beneficial for cement color and burnability
- Research shows optimal LD slag replacement ratio in concrete: **up to 20%** for strength and durability (Wiley 2020 study)

---

## 5. Port Coordinates (verified — matches current ports.json)

| Port ID | Name | Country | Lat | Lon | Notes |
|---------|------|---------|-----|-----|-------|
| dhamra | Dhamra Port | India | 20.79 | 86.98 | Nearest to seller (Tata Steel BSL, Angul) |
| chittagong | Chittagong Port | Bangladesh | 22.33 | 91.83 | Main seaport, handles 92% of BD trade |
| mongla | Mongla Port | Bangladesh | 22.49 | 89.60 | Second port, near Bashundhara factory |

---

## 6. Freight & Logistics Context

### Vessel types for clinker transport
- **Handysize** (25,000-40,000 DWT): Standard for cement/clinker, Bangladesh routes
- **Supramax** (50,000-60,000 DWT): Larger parcels, cost-efficient
- **Self-discharging cement carriers** (21-28K DWT): Used by major Indian cement firms
- **Large mills with own vessels** (Shah, Crown, Premier, Bashundhara): Can reduce freight to ~$12/tonne

### Key logistics facts
- Major Bangladeshi cement firms own ocean-going vessels (Shah, Crown, Premier, Bashundhara)
- Importers use outer anchorage at Chittagong; lighter vessels transfer cargo to shore
- Port congestion at Chittagong sometimes causes delays
- Middle East conflict (2025-2026) has driven freight costs up ~15-20%
- Coastal shipping in India is underdeveloped (only 2-3% of cement transport) vs road (66%) and rail (31%)
- Total Bangladesh cement production capacity: **86.707 Mt** (43 grinding plants, 1 integrated plant)
- Bangladesh cement is mostly import-dependent for raw materials (clinker)

---

## 7. Scenario Context (from PLAN.md)

- **Seller**: Tata Steel BSL (tata_steel_bsl), exports LD slag from Dhamra Port
- **Material**: LD (Linz-Donawitz) Steel Slag — composition CaO 45%, SiO2 15%, Fe2O3 20%, MgO 8%
- **Available quantity**: 100,000 tonnes/year
- **Seller price range**: $22/tonne min, $25/tonne preferred
- **Destination**: Cement manufacturers in Bangladesh
- **Application**: Cement clinker substitute (CaO_min_pct ≥ 40%)
- **Real anchor framing**: Individual buyer prices/quantities are illustrative, anchored to published aggregate trade data — NOT claimed as real leaked contract terms

---

## 8. CO2 Calculation Basis — Why It Matters & How to Compute It

### Why it's needed:
The Orchestrator output schema (per AGENTS.md) requires a `co2_avoided_tonnes_estimate` field. This quantifies the **circularity benefit** — that shipping LD slag from India to Bangladesh for use as clinker substitute avoids CO2 that would otherwise be emitted producing clinker.

### How to compute it:
- **Industry standard**: Producing 1 tonne of clinker emits ~800-900 kg CO2 (calcination of limestone + fuel)
- **LD slag as clinker substitute**: Avoids the calcination step entirely (slag is already a byproduct of steelmaking)
- **Typical avoidance rate**: ~0.8-0.9 tonnes CO2 avoided per tonne of clinker replaced by LD slag
- **Per academic research** (CSCM 2024, Springer 2025):
  - 30% LD slag replacement in cement → ~54 g CO2eq/kg mortar reduction in climate change impact
  - Ladle slag (similar to LD slag) at 30% replacement → 98% GWP reduction in geopolymer concrete (431.6 → 8.2 kg CO2 eq/m³)
  - Processed LFS-based binder → 4.05% higher compressive strength with 22% energy savings at 30% replacement
  - LD slag cement meets durability requirements at replacement ratios up to 20% (Wiley 2020)

### Formula for the demo:
```
co2_avoided_tonnes_estimate = quantity_tonnes_used_as_clinker_substitute × 0.85
```
(where 0.85 is the mid-range CO2 avoidance factor in tonnes CO2 per tonne of clinker replaced)

### For the scenario:
- If a buyer takes 40,000 tonnes of the 100,000-tonne LD slag shipment
- And uses it as clinker substitute: 40,000 × 0.85 = **~34,000 tonnes CO2 avoided**
- **Must be labeled as an estimate** per AGENTS.md — it's not a measured figure

---

## 9. Data Gaps / What Could Be Added

1. **Exact sea distances** from a proper calculator (searates.com/ports.com) — see Section 1 note
2. **Monthly clinker price data** — available from Bangladesh Bank via CEIC (monthly BDT mn, 260 observations from Jul 2004 to Feb 2026)
3. **Bangladesh GDP growth / construction sector growth data** — context for demand trends
4. **Exchange rate (BDT/USD)** — for any local cost calculations
5. **Company revenue/production data** — to anchor demand figures more realistically
6. **Additional companies** to reach 20+ real entries: Nitol Cement, Shamim Cement, KDS Cement
7. **Historical freight rate data** — to generate time-series variation in routes.json

---

## 10. Suggested next steps

1. **Write a Python script** to generate `buyers.json` with:
   - 6-9 real entries (is_real_reference: true) with source_note
   - 11-41 synthetic entries (is_real_reference: false) varying demand/price/port
   - Total: 20-50 entries
   - Use port preferences (Chittagong for most, Mongla for Bashundhara)
   - Use realistic demand ranges ($15K-$80K tonnes) anchored to real company data
   - Use realistic price ranges ($20-$30/tonne) anchored to the $38/ton import price trend
2. **Update `routes.json`** with recalculated sea distances from a proper calculator
3. **Validate** all 5 JSON files against DATA.md schema
4. **Add CO2 estimate** logic to the Orchestrator output
5. **Flag the data swap** to the team when ready (per brief-dataset.md step 6)

---

## Sources
- Cemnet (cemnet.com) — Bangladesh clinker import data, cement plant listings
- The Business Standard (tbsnews.net) — Freight costs, vessel ownership, large mill economics
- Financial Express Bangladesh (thefinancialexpress.com.bd) — Freight hikes, clinker price spikes
- IndexBox (indexbox.io) — Average import/export prices, country-by-country trade data
- Shah Cement (shahcement.com) — Company profile, CEM-I specifications
- Crown Cement (crowncement.com) — Annual Report 2023-24, PCC specifications
- Heidelberg Materials Bangladesh — Annual Report 2024
- Premier Cement (premiercement.com) — Company profile, BCMA
- Unique Cement (bcma.com.bd) — Company profile, WCA membership
- Bashundhara Cement (bashundharacement.com) — Company profile, factory locations
- Nitol Cement (nitolniloy.com.bd) — Company profile, Jessore plant
- MarketInside (marketinsidedata.com) — Trade data
- Trademo (trademo.com) — Supply chain data
- BCMA (bcma.com.bd) — Bangladesh Cement Manufacturers Association
- LinkedIn (Kocheril Shibu) — Coastal sea logistics cost analysis
- Hellenic Shipping News — CPA throughput data
- Bangladesh Bank / CEIC — Monthly import data
- Academic: DOI 10.3329/cerb.v12i0.1491 — Chemical composition of Bangladeshi cement brands
- Academic: CSCM 2024 (Václavík et al.) — LD slag as cement substitute, CO2 reduction
- Academic: Springer 2025 — Environmental LCA of steel slag-based cement
- Academic: Wiley 2020 — LD slag in concrete, durability and strength at up to 20% replacement
- SeaRates / SeaDistant — Port distance references
- DMS Consultancy — Sea distance calculator methodology
