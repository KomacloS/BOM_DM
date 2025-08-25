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

Download a CSV template:

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
