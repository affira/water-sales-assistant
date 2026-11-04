# CLAUDE.md

Guidance for working on this repository: a command-line AI sales assistant built on the
Claude API (Anthropic SDK). Current goal: make it deployable on Azure without breaking
anything that works today.

## Commands

```bash
source .venv/bin/activate
pytest -q                           # offline test suite, must pass before every commit
ruff check .                        # lint; CI (GitHub Actions and Azure DevOps) fails on any finding
python scripts/generate_data.py     # regenerate synthetic data (deterministic)
python -m aquaassist.ingest         # extract reports into index/service_records.json (calls the API)
python -m aquaassist.cli --trace    # interactive agent (calls the API)
uvicorn aquaassist.api:app --port 8000   # HTTP API: POST /chat, GET /health, docs at /docs
docker build -t water-sales-assistant .  # production image (runtime deps from requirements.txt only)
pip install -r requirements-dev.txt      # local dev: runtime deps + pytest/httpx
python eval/eval_extraction.py      # extraction accuracy vs. eval/ground_truth_service_records.json
```

## Where things live

- `aquaassist/agent.py`: system prompt and the manual tool-use loop (`Agent.ask`)
- `aquaassist/api.py`: FastAPI service (`/chat`, `/health`); `sessions.py` holds per-session agents
  in memory; `citations.py` extracts and verifies cited sources
- `aquaassist/tools.py`: tool schemas (the routing surface) and the `run_tool` dispatcher
- `aquaassist/providers/`: the only place LLM SDK clients are created and called. `LLM_PROVIDER`
  selects `anthropic` (default), `azure_openai` or `foundry_claude`; `base.py` defines the internal
  message/tool-call format and `convert.py` is the single place that translates it to each SDK
- `aquaassist/runtime.py`: start-up (`bootstrap()`): `secret_store.py` loads API keys from Key Vault
  when `KEY_VAULT_URL` is set, then `telemetry.py` enables Azure Monitor when
  `APPLICATIONINSIGHTS_CONNECTION_STRING` is set
- `aquaassist/config.py`: environment variables, paths, FX rates, enums
- `aquaassist/cost.py`, `process.py`, `retrieval.py`: deterministic engines behind the tools
- `aquaassist/extraction.py`: LLM report extraction and the rule-based `validate()`
- `tests/`: offline tests; `conftest.py` provides ground-truth records as a fixture

## Rules

### Code style
- Python with type hints on every function signature. Keep functions small and single-purpose.
- Every change ships with pytest tests that cover it. Run `pytest -q` and see it pass before committing.

### LLM vs. code
- The LLM handles routing and explanation; deterministic Python computes every number.
- Never move calculations into prompts. Costs, rates, percentiles, unit conversions and
  totals belong in `cost.py` / `process.py` (or new deterministic modules), exposed through tools.
- LLM extraction output is always checked by deterministic validation before it is used.

### Tests
- Tests run offline with the mocked LLM and never call a real API.
- Use the fake SDK clients in `tests/fakes.py` (`scripted_provider(kind, ...)`), and test agent
  behaviour on every provider (`PROVIDERS`). Never require a real key to run tests; an autouse
  fixture in `tests/conftest.py` blocks all network access.
- Tool and engine tests use the ground-truth records fixture, not `index/`.

### Secrets and configuration
- No secrets in code, logs or git: no API keys, connection strings or tokens in source,
  test fixtures, commit messages or log/trace output.
- Configuration comes from environment variables. When you add or rename a variable, update
  `.env.example` in the same change (placeholder values only) and document it in the README.
- `.env` stays gitignored.
- API keys may come from Key Vault (`secret_store.py`); log variable names and sources only.
- Telemetry records names, counts, durations, token counts and error codes only: never message or
  reply text, tool inputs, session ids or exception messages. Keep `test_telemetry.py`'s privacy
  test passing when adding new attributes.

### Data
- All company, product, customer and report data is synthetic (Norvale Water Technologies is
  fictional). Never describe it as a real company or as real customer data in code, docs,
  prompts or commit messages.
