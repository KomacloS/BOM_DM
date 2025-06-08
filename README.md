# root: README.md
## BOM Platform – bootstrap

This repository is the starting point for an on-premise BOM-centric test-engineering platform.

**Stack**:  
- Python 3.11 (virtual-env recommended)  
- FastAPI + Uvicorn backend  
- PostgreSQL (will run locally during dev)  
- Pytest for TDD

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

Import a BOM PDF:
```bash
curl -F file=@sample.pdf http://localhost:8000/bom/import
```
The parser assumes table columns are separated by multiple spaces or tabs. Real
world PDFs may require tweaks.

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
