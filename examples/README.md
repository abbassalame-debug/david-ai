# examples/

Everything here is **synthetic**.

| Path | What it is |
|---|---|
| `demo_vault/` | five short notes in three vaults (`System`, `Business`, `Music`). Point `DAVID_VAULTS` here to try retrieval without any private data. |
| `demo_bistro/` | the restaurant vertical's sample tree: `app/backup/<date>/menu_items.json` (a POS menu export), `09-2026/22-09-2026/` (a weights workbook) and `draft_package.demo.json` (a draft package produced by the pipeline from scripted readings). |
| `design_file_scan_demo.json` | the shape of a metadata scan of a designer's folders (file years, fonts, swatches, colour profiles), with made-up numbers. It seeds `design_preferences.example.json`. |
| `templates/` | where the registered Illustrator templates would live (`agents/jobs/02_brand_marketing/reference/templates.json` points here). The `.ai` files themselves are not included. |
| `academy_docs/`, `sample_libraries/` | empty placeholders named in `config/ingest_paths.example.json` |

## Try it (no GPU, no cloud)

```bash
# 1. install Ollama and pull the two small models
ollama pull qwen3-embedding:0.6b
ollama pull qwen3.5:9b

# 2. index the demo vault into a scratch data folder
DAVID_DATA=/tmp/david-demo DAVID_VAULTS=examples/demo_vault python turbo_ingest.py --vaults-only

# 3. ask a question that the notes answer
DAVID_DATA=/tmp/david-demo DAVID_VAULTS=examples/demo_vault python -c "import rag; print(rag.retrieve('analyst', 'Which supplier delivers beef trimmings?', top_k=2))"
```
