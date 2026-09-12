# Circularity Agent

Run the agent from the repository root:

```powershell
python -m agents.circularity --material-id ld_slag --quantity-tonnes 100000 --seller-id tata_steel_bsl
```

The command reads `data/materials.json` and `data/buyers.json` by default and writes the
JSON output contract from `docs/AGENTS.md` to standard output. Use `--materials` and
`--buyers` to provide alternate schema-compatible datasets.

Import `find_candidates` for in-memory data or `run_from_files` for file-backed data:

```python
from agents.circularity import run_from_files

result = run_from_files(
    {
        "material_id": "ld_slag",
        "quantity_tonnes": 100000,
        "seller_id": "tata_steel_bsl",
    }
)
```
