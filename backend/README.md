# FinEx backend (Person B)

FastAPI + async SQLAlchemy platform API: auth/session management, tenant
isolation (RLS), CRM, dashboard aggregation, proposals, and the audit trail.
Alembic revision `002_financial_core` also installs Person A's `ledger` schema
with tenant policies, safe read views, and execute-only posting grants.

See the root [README](../README.md) for the whole-system layout and the
[backend foundation](../docs/backend-foundation.md) for architecture,
security invariants, and deferred scope.

## Run

```powershell
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload        # http://127.0.0.1:8000/health
pytest -q                            # 52 tests
```

Requires PostgreSQL (see `../docker-compose.yml`); Redis is faked in tests.
