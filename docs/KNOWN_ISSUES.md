# Known issues

## Orchestrator picks highest per-tonne margin, not highest total deal value

**Where:** `orchestrator/__init__.py` — the buyer-selection loop (~line 297-311)
compares `net_value = price - logistics_cost - handling - processing`
(a **per-tonne** figure) across buyers and keeps whichever is highest:

```python
net_value = _net_value(deal, logistics_cost)   # per-tonne margin
...
if net_value is not None and net_value > best_net_value:
    best_net_value = net_value
    best_deal = {...}
```

`total_net_value_usd` (per-tonne margin × quantity) is only computed
afterward, for display — it never influences which buyer gets picked.

**Why this matters:** `run_pipeline`'s `objective` parameter is literally
named `"maximize_net_value"` (see `FIXED_SCENARIO` and `AGENTS.md` section 4),
which reads as "maximize total deal value," but the current implementation
maximizes *per-tonne* margin regardless of quantity. Those aren't the same
thing, and it's easy to accidentally pick a worse overall deal.

**Concrete example, seen once the real 25-buyer dataset went in:** the
pipeline picked a synthetic buyer at $29/t for 24,000t (total net value
$444,000) over Shah Cement at $27/t for 65,000t (which would have been a
much larger total net value) — purely because $29/t beat $27/t per-tonne,
even though the smaller deal is worth far less overall. With the old
5-entry stub this never surfaced because all 5 buyers happened to rank the
same way under both metrics — the real dataset is what exposed it.

**Why it wasn't just fixed:** this is existing orchestrator logic, not
something introduced by the dataset swap or the BATNA/circularity work —
changing buyer-selection semantics is a judgment call for whoever owns the
Orchestrator, not something to silently change while doing other work.

**What to do:** decide whether `"maximize_net_value"` should mean per-tonne
margin (current behavior — favors margin quality) or total deal value
(`margin * deal["quantity_tonnes"]`, matching what's actually displayed as
`total_net_value_usd`). If it's the latter, swap the comparison at line
~309 to compare `margin * deal["quantity_tonnes"]` instead of `net_value`.
Worth resolving before the demo — `PLAN.md`'s own judging notes warn
"numbers must be internally consistent under questioning," and "why didn't
you pick the bigger buyer" is a very likely question given the real dataset
now makes this visible.
