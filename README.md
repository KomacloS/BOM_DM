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
