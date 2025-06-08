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
curl "http://localhost:8000/bom/items?search=Cap&min_qty=1&max_qty=10&skip=0&limit=20"
```

Import a BOM PDF:
```bash
curl -F file=@sample.pdf http://localhost:8000/bom/import
```
The parser assumes table columns are separated by multiple spaces or tabs. Real
world PDFs may require tweaks.

### Quote

Get a quick time and cost estimate for the current BOM:

```bash
curl http://localhost:8000/bom/quote
```

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
- **part_number**: unique identifier for the part (string, required)
- **description**: human-friendly description (string, required)
- **quantity**: number of parts required (integer, min 1, default 1)
- **reference**: optional reference designator or notes

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
Switch between SQLite and Postgres under **Configuration**. Saving
changes rewrites `~/.bom_platform/settings.toml` and reloads the database
without restarting Python.


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
