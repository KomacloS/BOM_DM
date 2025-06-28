# root: README.md
## BOM Platform – bootstrap

This repository is the starting point for an on-premise BOM-centric test-engineering platform.

**Stack**:
- Python 3.11 (virtual-env recommended)
- FastAPI + Uvicorn backend
- PostgreSQL (will run locally during dev)
- Pytest for TDD

### Three-layer model

1. **Core API** – FastAPI application with all business logic and data storage
2. **Control Center GUI** – Tk desktop application communicating with the API
3. **CLI / Scripts** – helper scripts for headless install and automation

### Quick start
```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Create a BOM item:
```bash
curl -X POST http://localhost:8000/bom/items \
     -H "Content-Type: application/json" \
     -d '{"part_number":"ABC-123","description":"10 \u00b5F Cap","quantity":2}'
```

Retrieve a single item:
```bash
curl http://localhost:8000/bom/items/1
```

Update an item completely:
```bash
curl -X PUT http://localhost:8000/bom/items/1 \
     -H "Content-Type: application/json" \
     -d '{"part_number":"ABC-123","description":"Cap 22uF","quantity":3}'
```

Patch an item:
```bash
curl -X PATCH http://localhost:8000/bom/items/1 \
     -H "Content-Type: application/json" \
     -d '{"quantity":5}'
```

Delete an item:
```bash
curl -X DELETE http://localhost:8000/bom/items/1
```

List items with search and pagination:
```bash
TOKEN=<token>
curl -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/bom/items?search=Cap&min_qty=1&max_qty=10&skip=0&limit=20"
```

Import a BOM file:
```bash
curl -F file=@sample.pdf http://localhost:8000/bom/import
curl -F file=@bom.csv http://localhost:8000/bom/import
```
CSV columns may include `manufacturer`, `mpn`, `footprint` and `unit_cost`.

### Customers & Projects

Group BOM items by customer and project. Example:

```bash
curl -X POST http://localhost:8000/customers \
     -H "Content-Type: application/json" \
     -d '{"name":"Acme"}'
curl -X POST http://localhost:8000/projects \
     -H "Content-Type: application/json" \
     -d '{"customer_id":1,"name":"Widget"}'
curl -F file=@sample.csv \
     http://localhost:8000/bom/import?project_id=1
```

### Quote

Get a quick time and cost estimate for the current BOM:

```bash
curl http://localhost:8000/bom/quote
```
Get a quote for a specific project:
```bash
TOKEN=<token>
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/projects/1/quote
```
Labor cost is calculated using `BOM_HOURLY_USD` (default `$25/hr`).
Parts marked as `dnp` (do-not-populate) are ignored in quotes.
Fetch latest pricing with `POST /bom/items/{id}/fetch_price` and body `{"source":"octopart"}`.
Set `OCTOPART_TOKEN` for live lookups or rely on built-in mock data.
The default currency is set via `BOM_DEFAULT_CURRENCY` (defaults to USD).

### Purchase orders

Generate a simple purchase order PDF for a project:

```bash
TOKEN=<token>
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -o po.pdf http://localhost:8000/projects/1/po.pdf
```
Inventory levels are adjusted automatically.

### Inventory

Track stock levels via the `/inventory` endpoints:

```bash
curl http://localhost:8000/inventory
```
Inline edits are available under `/ui/inventory`.

Each Part can have one-or-more "Test Macros" attached (fixtures, Python tests, 3-D models, etc.).
Use `/parts/{id}/testmacros` to manage these links.

### Test results

Log a new flying-probe test result:

```bash
curl -X POST http://localhost:8000/testresults \
     -H "Content-Type: application/json" \
     -d '{"serial_number":"SN123","result":true}'
```

List saved results:

```bash
curl http://localhost:8000/testresults?skip=0&limit=10
```

### BOMItem fields
- **part_number**: unique identifier for the part (string, required; deduplicated via the Parts catalogue)
- **description**: human-friendly description (string, required)
- **quantity**: number of parts required (integer, min 1, default 1)
- **reference**: optional reference designator or notes
- **manufacturer**: optional manufacturer name
- **mpn**: optional manufacturer part number
- **footprint**: optional package footprint
- **unit_cost**: optional unit price (numeric with 4 decimals)

### Health check

Run the server and open `http://localhost:8000/health` to verify both the API
and the database connection. The endpoint returns:

```json
{"api": "ok", "db": "ok"}
```

### Authentication

Obtain a token using the default admin account (created on first startup):

```bash
curl -X POST http://localhost:8000/auth/token \
     -d "username=admin&password=change_me"
```

Use the returned token to access protected routes:

```bash
TOKEN=<token-from-login>
curl -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"part_number":"ABC-1","description":"Cap","quantity":1}' \
     http://localhost:8000/bom/items
```

The same token is required when **listing** BOM items or retrieving a
project's BOM.
Users with the **operator** role can only view data; any write attempts return 403.

### Traceability

Identify which boards failed because of a component:

```bash
curl http://localhost:8000/traceability/component/ABC-123
```

See the fail status of each BOM item for a board:

```bash
curl http://localhost:8000/traceability/board/SN123
```

### Data export & backups

Admins can download the current BOM and all test results:

```bash
TOKEN=<admin-token>
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/export/bom.csv
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/export/testresults.xlsx
```

Nightly backups of these exports are saved under the `backups/` directory.

### Browser access

Open `http://localhost:8000/ui/` in a browser to use the built-in web UI.
A new step-by-step workflow is available at `http://localhost:8000/ui/workflow`.
Switch between SQLite and Postgres under **Configuration**. Saving
changes rewrites `~/.bom_platform/settings.toml` and reloads the database
without restarting Python.
Run `python -m app.migrate` after upgrading to create new tables or columns.

Uploading a PDF datasheet for an item:
```bash
curl -X POST -F file=@datasheet.pdf \
     -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/bom/items/1/datasheet
```

Inline edits in the workflow automatically save changes and show a little
confirmation toast. Uploaded datasheets turn the button into a 📎 link.

### Editing an existing project

Saved BOM items can be retrieved for further editing:

```bash
TOKEN=<token>
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/projects/1/bom
```

Export the project BOM to CSV:

```bash
curl -L -o widget.csv http://localhost:8000/projects/1/export.csv
```

Use `?comma=false` for semicolon-separated files.

The datasheet upload size limit defaults to 10 MB. Set `BOM_MAX_DS_MB` to
override this cap.
`FX_CACHE_HOURS` controls how long exchange rates are cached (default 24).

### Test Assets

Upload       Endpoint                       Accepted/limit
-----------  -----------------------------  ----------------------
3-D Model    POST /testmacros/{id}/upload_glb    .glb ≤ 10 MB
EDA Bundle   POST /complexes/{id}/upload_eda     .zip ≤ 20 MB
Python Test  POST /pythontests/{id}/upload_file  .py ≤ 1 MB

Download files via `/assets/{sha}/{name}`.

Workflow inline editing with the clip icon is shown in the documentation.


### 📋 Server Control Center GUI

Launch the graphical control center from a virtual environment on a session that can open windows (RDP or local login):

```bash
bom-gui               # or:  python -m gui.control_center
```

This window lets you start and stop the API server, run tests, trigger backups and download exports without using the terminal.

![GUI screenshot](docs/gui_screenshot.png)

### One-click install

Windows:
```bat
scripts\setup.bat
```

Linux/macOS:
```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
```

The script creates a virtual environment, installs all optional dependencies and launches the Control Center.
It now detects the project root and directly invokes the Python inside `.venv`,
so it works even if `python` isn't on your PATH.
