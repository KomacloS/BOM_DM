# BOM Platform – bootstrap

Minimal FastAPI service for importing Bills of Materials (BOMs).
A temporary admin account is created on startup so everything is reachable during testing.

## Quickstart

```bash
make install
make test  # run unit tests
make dev
```

API runs at http://localhost:8000. Log in with **admin / admin**:

```bash
curl -X POST -F "username=admin" -F "password=admin" http://localhost:8000/auth/token
```

Use the returned token for authorized requests:

```bash
TOKEN=<token-from-previous-step>
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/auth/me
```

### Import a BOM

Create a customer, project, and assembly, then upload a CSV BOM:

```bash
curl -H "Authorization: Bearer $TOKEN" -X POST \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme"}' http://localhost:8000/customers
# create project and assembly similarly...

curl -H "Authorization: Bearer $TOKEN" \
  -F file=@tests/fixtures/sample_bom.csv \
  http://localhost:8000/assemblies/1/bom/import
```

List items and tasks:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/assemblies/1/bom/items
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/projects/1/tasks
```

Download a BOM template (CSV or XLSX accepted):

```bash
curl http://localhost:8000/bom/template
```

### Schema drift on SQLite (dev)

During development the SQLite schema can drift. To inspect and apply safe
migrations run:

```bash
python -m app.tools.db doctor
python -m app.tools.db migrate
```

If you do not need existing data you can also delete the SQLite database file.
To run the versioned schema upgrade CLI manually use:

```bash
python -m app.cli.migrate --db path/to/app.db
```
`


To force-run only the versioned schema upgrades (e.g., adding new columns) use:

```bash
python -m app.cli.migrate --db path/to/app.db
```

## GUI-first Projects Terminal

A lightweight desktop application written with **PyQt6** allows working with
customers, projects and assemblies without the HTTP API.  It talks to the same
database and reuses the service layer directly.

```bash
make gui
```

Steps:

1. Each pane now shows a header so you always know where you are.
2. Create a customer, project and assembly using the forms on the left or the
   **New Project (Workflow)** button for a guided wizard.
3. Select an assembly and upload a BOM CSV.
4. Review the import report, BOM items and any tasks created for unknown parts.

Right-click or use the Delete buttons to remove Customers, Projects or Assemblies. If
a Customer or Project has children, the GUI will prompt to cascade-delete.

Use **Import BOM** on an Assembly to load a CSV with the strict header. The dialog
reports matched/unmatched counts and any created task IDs.

## Debug GUI

Launch the optional Qt-based debug GUI to explore the API locally. The
repository provides a helper script that creates a virtual environment,
installs dependencies, and starts the GUI:

```bash
./scripts/setup.sh
```

Run the script from the project root on a system with Python 3.10+ and Bash.
If you prefer to perform the steps manually, use:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .[full]
python -m gui.control_center
```

This opens a simple interface for authentication, importing BOMs, and other
debug actions.

## Datasheet Search & AI Rerank

The GUI can search the web for datasheet PDFs and optionally use an AI model
to pick the best URL from search results.

- Search providers: set one of the following environment variables and the
  app will auto-detect in this order: `BING_SEARCH_KEY`, `GOOGLE_API_KEY` +
  `GOOGLE_CSE_ID`, `SERPAPI_KEY`, or `BRAVE_API_KEY`.

- AI reranker: configure an OpenAI-compatible chat completions endpoint via
  environment variables:
  - `AI_CHAT_URL` (default: `https://api.openai.com/v1/chat/completions`)
  - `AI_CHAT_MODEL` (default: `gpt-4o-mini`)
  - `AI_CHAT_API_KEY` (fallbacks: `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
    `AZURE_OPENAI_API_KEY`)
  - `AI_CHAT_AUTH_HEADER` (default: `Authorization`)
  - `AI_CHAT_AUTH_SCHEME` (default: `Bearer`)

Examples:

```bash
# OpenAI
export AI_CHAT_URL=https://api.openai.com/v1/chat/completions
export AI_CHAT_MODEL=gpt-4o-mini
export AI_CHAT_API_KEY=sk-...

# OpenRouter
export AI_CHAT_URL=https://openrouter.ai/api/v1/chat/completions
export AI_CHAT_MODEL=google/gemini-1.5-flash
export AI_CHAT_API_KEY=or-...

# LM Studio (no auth)
export AI_CHAT_URL=http://localhost:1234/v1/chat/completions
unset AI_CHAT_API_KEY

# Azure OpenAI
export AI_CHAT_URL="https://<resource>.openai.azure.com/openai/deployments/<deploy>/chat/completions?api-version=2024-02-15-preview"
export AI_CHAT_MODEL=<ignored-by-azure>
export AI_CHAT_API_KEY=<azure-key>
export AI_CHAT_AUTH_HEADER=api-key
export AI_CHAT_AUTH_SCHEME=""  # not used when header != Authorization
```

On Windows PowerShell, replace `export VAR=value` with `$env:VAR = "value"`.