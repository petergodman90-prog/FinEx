# FINEX

Multi-tenant business platform: dashboard, AI assistant, WhatsApp, CRM.

## Stack
- Backend: Python + FastAPI
- AI service: Python + FastAPI
- Workers: Celery/Arq + Redis
- DB: PostgreSQL 15 (RLS)
- Frontend: Next.js + TypeScript

## Local setup
1. `cp .env.example .env`
2. `docker compose up -d`
3. Read `docs/security-charter.md` before writing code.

## Backend (Layers 1–6 complete)

Architecture, security invariants, and deferred scope: `docs/backend-foundation.md`.

```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head              # requires: docker compose up -d
uvicorn app.main:app --reload     # http://127.0.0.1:8000/health

pytest -q                         # 33 tests; Postgres required, Redis is faked
```

Note (Windows/Docker): if connects to published ports fail in waves with
`WinError 52` / `WSAEADDRINUSE`, the test suite already works around it — see
the port-pinning note in `docs/backend-foundation.md` for other tools.