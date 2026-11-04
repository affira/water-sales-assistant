# Screenshots

✅ = already captured. Save the remaining screenshots here with these file names. Then replace each "Screenshot placeholder" block in the main README with an image link, for example `![CLI trace](docs/screenshots/cli-trace.png)`.

| File | What to capture |
|---|---|
| `cli-trace.png` ✅ | `python -m aquaassist.cli --trace` answering the Chile membrane cost question, with tool calls visible |
| `api-docs.png` ✅ | Swagger UI at `http://localhost:8000/docs` showing a `/chat` response with `sources` marked `verified: true` |
| `github-actions.png` ✅ | A passing GitHub Actions run (Lint and Test jobs) |
| `azure-container-app.png` | The Container App overview in the Azure portal (application URL, revision, scale to zero) |
| `app-insights.png` | Application Insights: request latency, plus the `aquaassist.llm.tokens` metric |
| `azure-devops-pipeline.png` | An Azure DevOps run showing the Lint, Test, Build and Deploy stages |

Before saving, check that no screenshot shows keys, connection strings or the contents of your `.env`.
