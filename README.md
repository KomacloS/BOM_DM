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

Each BOM row now includes mode-aware test metadata resolved by the unified
service layer:

```json
{
  "id": 7,
  "reference": "Q1",
  "part_number": "PMIC-123",
  "test_method": "Macro",
  "test_detail": "PMIC POWER-UP",
  "test_method_powered": "Macro",
  "test_detail_powered": "PMIC POWER-UP",
  "test_resolution_source": "mapping"
}
```

When an assembly's `test_mode` is `powered` and the part is active, the payload
includes the powered-only fields so the UI can render the additional columns
without recomputing per-row state.

Download a BOM template (CSV or XLSX accepted):

```bash
curl http://localhost:8000/bom/template
```

## Part test methods & local assets

Assign a test method to a part to create local automation scaffolding. The
backend stores the mapping in the new `part_test_assignment` table and manages
files under the repository's `data/` directory:

```
data/
  python/<PN>/            # Python automation assets per part number
  QuickTest/<PN>.txt      # Plain-text quick test scripts
```

Use the API to manage assignments and assets:

- `POST /tests/assign` – set a part's method to `macro`, `python`, or
  `quick_test`. Assigning `python` creates `data/python/<PN>/`, while
  `quick_test` ensures `data/QuickTest/<PN>.txt` exists.
- `GET /tests/{pn}/detail` – return metadata about the current assignment
  including relative and absolute paths to on-disk assets.
- `GET /tests/{pn}/python/zip` – download the `data/python/<PN>/` folder as a
  zip archive (useful in the browser-only build).
- `POST /tests/{pn}/quicktest/read` – read or lazily create the quick test
  `.txt` file.
- `POST /tests/{pn}/quicktest/write` – persist quick test edits back to disk.

All filesystem helpers reject unsafe part numbers (only `A-Z`, `a-z`, `0-9`,
`-`, `_`, and `.` are allowed) to prevent path traversal. The React components
in `frontend/src/components/TestMethodSelector.tsx` and
`frontend/src/components/QuickTestEditor.tsx` demonstrate how to integrate the
API, providing desktop-only folder reveal actions plus web-friendly download
flows.

### Schema drift on SQLite (dev)

During development the SQLite schema can drift. To inspect and apply safe
migrations run:

```bash
python -m app.tools.db doctor
python -m app.tools.db migrate
```

If you do not need existing data you can also delete the SQLite database file.

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

### Powered vs. unpowered boards

The Projects Terminal exposes a **Powered / Not powered** toggle for each
assembly. Switching to *Powered* automatically resolves active-part tests using
the powered mapping and reveals two additional columns in the BOM grid:
**Test method (powered)** and **Test detail (powered)**. Passive parts retain the
original unpowered tests, and the extra columns collapse automatically when the
board is marked *Not powered*.

The primary **Test method** and **Test detail** columns always display the
resolver's effective values for the assembly's current mode. Double-clicking a
cell still opens the assignment editors so you can stage or apply overrides
without losing sight of the resolved test plan.

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

## Complex Editor bridge communication

The GUI's Complex Editor integrations perform a preflight readiness handshake
before any other API calls. The handshake uses a shared proxy-bypassing HTTP
session, always attaches the configured bearer token, and polls `/health`
until the bridge reports `ready == true`. If the bridge takes too long, the
helper surfaces the latest reason reported by that endpoint.

See [docs/ce_activation.md](docs/ce_activation.md) for a detailed narrative of
the activation flow and its integration points across the application.

### CE Bridge reference

* API specifics: [docs/ce_bridge_api.md](docs/ce_bridge_api.md)
* Integration walkthrough: [docs/bom_db_ce_integration.md](docs/bom_db_ce_integration.md)

Quickstart for the CE Ops-Kit gates shipped with BOM_DB:

```bash
# run CE ops gate from BOM_DB checkout
cd comm/ce_ops_kit
source ./ce_env
./ce_gate.sh
```

### BOM to VIVA ➜ Complex Editor export

Triggering **Export for VIVA** now also runs the BOM→CE workflow implemented in
`app/services/bom_to_ce_export.py`. The exporter gathers all fitted BOM lines
with the **Complex** test method, resolves Complex IDs via `ComplexLink` (and
optionally `ce_component_map`), and then calls the CE Bridge to build a Microsoft
Access `.mdb` plus a CSV report of skipped rows.

Artifacts are written next to the VIVA files under a dedicated `CE/` folder. The
summary surfaced in the GUI mirrors the service response:

- `SUCCESS` – all requested complexes were exported. Message shows the MDB path
  (click **Open CE Folder** to jump to it).
- `PARTIAL_SUCCESS` – some rows were skipped (`not_linked_in_CE`,
  `not_found_in_CE`, or `unlinked_data_in_CE`). The GUI prompts to open the
  report CSV.
- `FAILED_INPUT` / `FAILED_BACKEND` – failure details are displayed and any
  generated report can be opened directly.
- `RETRY_LATER` / `RETRY_WITH_BACKOFF` – the bridge is temporarily unavailable;
  retry after the reported condition clears.

Each run includes a `trace_id`; operators can fetch correlated CE Bridge logs
via `GET /admin/logs/{trace_id}` when authentication is configured.

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
