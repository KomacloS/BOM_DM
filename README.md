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
