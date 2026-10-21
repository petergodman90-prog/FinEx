# FinEx

FinEx is a multi-tenant business-management application for small businesses:
CRM, dashboard, proposals, WhatsApp and AI-assisted workflows, built around a
financial posting service that is the single source of financial and stock truth.

## Repository layout

| Path | Owner | Contents |
| --- | --- | --- |
| `backend/` | Person B | FastAPI platform API: auth/sessions, tenant isolation (RLS), CRM, dashboard, proposals, audit |
| `db/migrations/` | Person A | Canonical SQL for the financial core (`ledger` schema, posting functions, immutable records); executed by Alembic revision 002 |
| `assets/`, `css/`, `js/`, `pages/`, `index.html` | Person C | Static HTML/CSS/JS interface (no build step) |
| `docs/` | All | Architecture, contracts, security charter, ADRs, runbooks |
| `docker-compose.yml` | Person B | Local PostgreSQL 15 + Redis |

## Team working agreement

Financial and inventory changes must pass through the posting service.
Organisation isolation, integrations and AI proposals are separate from that
service; the UI displays backend-provided figures without recalculating them.

- [Ownership and boundaries](docs/architecture/ownership-and-boundaries.md)
- [Posting-service contract](docs/architecture/posting-service-contract.md)
- [Contribution guide](CONTRIBUTING.md)
- [Security charter](docs/security-charter.md)

## Local setup

1. `cp .env.example .env` (fill the PII keys — never commit real keys)
2. `docker compose up -d` (PostgreSQL on 5432, Redis on 6379)
3. Backend:

   ```powershell
   cd backend
   python -m venv .venv; .\.venv\Scripts\activate
   pip install -r requirements.txt -r requirements-dev.txt
   alembic upgrade head
   uvicorn app.main:app --reload        # UI + API: http://127.0.0.1:8000/
   pytest -q                            # 63 tests; Postgres required, Redis faked
   ```

4. Open <http://127.0.0.1:8000/>. The backend serves `index.html` and only the
   public `assets/`, `css/`, `js/` and `pages/` directories from the same origin
   as `/api/v1`. Browser sessions use `HttpOnly`, `SameSite=Lax` cookies, so no
   tokens are stored in JavaScript and no CORS setup is needed.

   Never serve the repository root with a generic static server: that would
   expose `.env`, `backend/` and `db/`. Set `SERVE_FRONTEND=false` only when a
   reverse proxy serves the public frontend directories. In production, keep
   the UI and `/api` on one HTTPS origin and leave `CORS_ORIGINS` empty.

## Database migrations

Alembic is the single migration runner (one command for a fresh database):

```powershell
cd backend
alembic upgrade head
```

- `001_initial` — platform tables: organisations, users, memberships,
  customers, touchpoints, proposals, audit logs, and their RLS policies.
- `002_financial_core` — executes
  [`db/migrations/0001_person_a_financial_core.sql`](db/migrations/0001_person_a_financial_core.sql),
  then adds RLS policies to every `ledger` table, `security_invoker` read views,
  posting-context guards, and read/execute-only grants for `finex_app`.

Never apply the SQL file separately; Alembic owns the schema version. Run
migrations as the schema owner (`MIGRATION_DATABASE_URL` if set), not as the
runtime `finex_app` role. See
[docs/runbooks/database-migrations.md](docs/runbooks/database-migrations.md).

## Documentation

- [Backend foundation](docs/backend-foundation.md) — layers, invariants, Windows/Docker notes
- [Database migration runbook](docs/runbooks/database-migrations.md)
- [Backup and restore runbook](docs/runbooks/backup-and-restore.md)
- [ADR 0001: financial records are append-only](docs/adr/0001-financial-records-are-append-only.md)

## Windows/Docker note

If connections to published ports fail in waves with `WinError 52` /
`WSAEADDRINUSE`, the test suite already works around it. See the port-pinning
note in [docs/backend-foundation.md](docs/backend-foundation.md).
