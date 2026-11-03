# Norvale Sales Assistant

A command-line AI agent that supports a water-treatment equipment salesperson during a customer call. It can:

1. **Answer product questions and recommend a product**, grounded in product brochures.
2. **Estimate the cost of a field service visit** from 50 historical technician reports.
3. **Assess a customer's reverse-osmosis (RO) plant readings** against six months of hourly data from a reference plant.

For each message it decides which of these to do, whether it needs a clarifying question first, and when to say "I don't know".

> **All data is synthetic.** Norvale Water Technologies, its products, customers and reports are fictional,
> generated for this project (`scripts/generate_data.py`). The messiness is deliberate: mixed currencies,
> missing costs, truncated tables, VAT, weekend rates, ambiguous dates, a copy-paste error, and two
> brochure editions that contradict each other.

## Architecture

```
user message
    │
    ▼
Claude (claude-opus-5-5) + 6 tools ── manual tool-use loop (aquaassist/agent.py)
    │
    ├─ list_products / search_product_docs ─► BM25 over brochure pages ─────► cites [doc p.N]
    ├─ estimate_service_cost / find_service_reports / get_service_report
    │        └─► deterministic estimator over structured records ───────────► cites FSR IDs
    └─ analyze_process_readings ─► pandas over the hourly plant log ─────────► percentiles, drivers
```

**The key design choice is that the LLM routes and explains, but code does the numbers.**

| Stage | Who | Why |
|---|---|---|
| Routing: which tool, or ask, or answer | Claude, guided by tool descriptions and the system prompt | Free-form requests need judgement |
| Reading messy reports into fields (offline, once) | Claude with a strict JSON schema (`aquaassist/extraction.py`) | Parsing varied free text is what LLMs do well |
| Checking the extraction | Python rules: line math, totals vs. VAT, copy-paste detection | Catches LLM and technician errors |
| Cost arithmetic and ranges | Python (`aquaassist/cost.py`) | Repeatable, auditable, no invented numbers |
| Process statistics | pandas (`aquaassist/process.py`) | Computed from data, not general knowledge |

### How a cost estimate is built
The estimate is assembled from components, each taken from the most relevant evidence:
- **On-site hours** come from the same equipment and task, using a linear fit on quantity (e.g. 2.9 h + 0.31 h per membrane element).
- **Travel hours** are what the user says, else the median for visits in the same country.
- **Hourly rates** come from itemized reports in the same country, else from all countries, with a caveat.
- **Part prices** are unit prices from itemized reports of the same task. Customer-supplied (zero-cost) parts are excluded.

It also shows what similar jobs actually cost in total, as a sanity check. Every output lists the report IDs used and any caveats.

### Scaling notes
- **Brochures:** swap BM25 for embeddings plus a vector store (ideally hybrid search) behind the same `search()` interface.
- **Reports:** extraction is incremental (it only re-extracts changed files) and could move to the Batch API. The records belong in a database instead of a JSON file.
- **Process data:** the same functions work over a warehouse table. The like-for-like matching becomes a filtered query.

## Run it

Requires Python 3.9+ and an Anthropic API key.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # never commit this

python scripts/generate_data.py        # (optional) regenerate the synthetic data
python -m aquaassist.ingest            # extract the 50 reports into index/service_records.json (one-time, ~50 API calls)
python -m aquaassist.cli --trace       # chat; --trace shows tool calls and reasoning summaries
```

### HTTP API

```bash
uvicorn aquaassist.api:app --host 0.0.0.0 --port 8000   # interactive docs at http://localhost:8000/docs
```

- `POST /chat` takes `{"session_id": "abc", "message": "..."}` and returns `{"session_id", "reply", "sources", "tools_used"}`.
  - `sources` lists the brochure pages and service reports cited in the reply. Each has `verified: true` only if a tool actually returned it during that turn.
  - Invalid input returns 422 `{"error": "invalid_request", "message", "details": [{"field", "message"}]}`.
  - LLM failures return 429, 502 or 503 with the same shape, never echoing upstream error text.
- `GET /health` is for container health probes. It never calls the LLM and reports whether the service-record index is present.
- Conversation state is kept per session **in memory** (`aquaassist/sessions.py`). That works for one container, but production needs a shared store such as Redis or Cosmos DB, since sessions are lost on restart and aren't shared between replicas.

Other commands:

```bash
pytest -q                              # tools, validation and agent loop tests (no API key needed)
python eval/eval_extraction.py         # field-level accuracy of extraction vs. ground truth
```

Try the prompts in [`eval/demo_prompts.md`](eval/demo_prompts.md).

Configuration comes from environment variables, all listed with placeholder values in [`.env.example`](.env.example): `ANTHROPIC_API_KEY` (required), `AQUAASSIST_MODEL` (default `claude-opus-5-5`), `AQUAASSIST_EFFORT` (default `medium`), `AQUAASSIST_EXTRACTION_EFFORT` (default `low`). For local use, copy it to `.env` (gitignored), fill in your key, and load it with `set -a; source .env; set +a`.

## Project layout

```
aquaassist/
  agent.py        system prompt + tool-use loop
  tools.py        tool schemas (the routing surface) + dispatcher
  retrieval.py    brochure chunking + BM25
  extraction.py   report -> JSON record (Claude) + validation rules (Python)
  cost.py         service cost estimator
  process.py      RO process data analysis
  llm.py          SDK wrapper (model, refusal fallback, safe history replay)
  api.py          FastAPI service: /chat and /health
  sessions.py     in-memory per-session agents
  citations.py    extract and verify the sources cited in a reply
  cli.py, ingest.py, config.py
data/             synthetic brochures, service reports, hourly plant CSV
scripts/          data generator
eval/             ground truth, extraction eval, demo prompts
tests/            offline tests
```

## Limitations
- The cost estimates are rough planning figures. FX rates are fixed assumptions, and sparse history means wide ranges.
- The process comparison uses a single reference plant, so it shows correlation, not cause.
- There is no UI, authentication or persistence beyond a single CLI session, by design.
