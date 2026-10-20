# Backend Foundation (Layers 1–6)

Status: complete and tested (33 tests green). Deferred by design: AI assistant
NLU, WhatsApp ingestion, OCR/AI receipt extraction, advanced CRM analytics.

## Layers

### 1. Core: config, DB, RLS, request hygiene
- `app/core/config.py` — pydantic-settings; `.env` is looked up in the common
  run locations (repo root or `backend/`). Keys: `DATABASE_URL`,
  `MIGRATION_DATABASE_URL` (optional, owner/migration role), `REDIS_URL`,
  JWT settings, `PII_ENCRYPTION_KEY` (Fernet), `PII_HMAC_KEY` (hex).
- `app/core/db.py` — async engine + `SessionLocal`.
- `app/core/rls.py` — single source of truth for tenancy: GUC names,
  `bind_session_context(session, org_id=..., user_id=...)` (binds via
  `set_config(..., is_local => true)`), the policy SQL shared by the migration
  and the RLS tests, and `enable_audit_purge` (sanctioned escape hatch,
  `app.allow_audit_purge='on'`, used by test/org teardown only).
- `app/core/middleware.py` — request-id propagation; `app/core/context.py` —
  contextvars for org/user; `app/core/logging_filter.py` — PII redaction in
  logs.

### 2. Auth & sessions
- `app/services/auth_service.py` + `app/api/auth.py`:
  `/api/v1/auth/register | login | refresh | switch-org | logout | me`.
- JWT access tokens (`app/core/auth.py`); refresh rotation with jti tracking;
  session kill-switch (user-level revocation) via Redis
  (`app/core/sessions.py`; tests use fakeredis). Password hashing in
  `app/core/passwords.py`.
- Register creates the organization + owner membership; login supports org
  selection; switch-org re-issues tokens bound to another membership.

### 3. CRM
- Models: `Customer` (encrypted name/phone/email columns + HMAC blind-index
  columns; soft delete via `deleted_at`) and `Touchpoint`.
- `app/services/customer_service.py`: dedup on the blind index (partial unique
  indexes ignore soft-deleted rows), merge moves touchpoints, explicit
  `session.refresh` after UPDATE (MissingGreenlet guard).
- Endpoints under `/api/v1/customers`, incl. `/{id}/merge` and
  `/{id}/touchpoints`.

### 4. Dashboard
- `app/services/dashboard_service.py` + `GET /api/v1/dashboard/metrics`:
  org-scoped aggregation under RLS.

### 5. Proposals & audit
- `Proposal` model (status pending/approved/rejected/expired, spend amount,
  `idempotency_hash`); partial unique index on
  `(org_id, idempotency_hash) WHERE status='pending'`; create is idempotent
  (savepoint + conflict lookup).
- `app/services/proposal_service.py`: 24h expiry sweep
  (`POST /api/v1/proposals/expire`), role allow-list + per-role spend caps
  (`app/core/rbac.py`), decisions written to the audit log.
- `app/models/audit_log.py` is append-only via a statement trigger; writes go
  through `app/services/audit_service.py`.

### 6. Migration, roles, tests
- `backend/migrations/versions/001_initial_schema.py`: all tables, RLS
  (`ENABLE` + `FORCE` + policies generated from `rls.py`), the append-only
  audit trigger, `CREATE ROLE finex_app` (NOSUPERUSER, NOBYPASSRLS, NOLOGIN)
  and its grants (audit_logs gets INSERT/SELECT only).
- `alembic check` is clean (models ↔ migration zero drift).
- `backend/tests` (33 tests): auth flows, refresh rotation/revocation,
  customers CRUD/dedup/merge/permissions, proposal idempotency/caps/expiry,
  dashboard metrics, RBAC matrix, crypto (encrypt/blind index), RLS
  enabled+forced and cross-tenant isolation — including behavioral checks that
  run as `SET ROLE finex_app`.

## Run / test

```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head              # DB must be up: docker compose up -d
uvicorn app.main:app --reload     # http://127.0.0.1:8000/health

pytest -q                         # Postgres required; Redis is faked (fakeredis)
```

## Security invariants (enforced, not aspirational)
- Every tenant table has RLS enabled + forced with the org policy; the app
  role cannot bypass it (NOBYPASSRLS, plus behavioral `SET ROLE` tests).
- PII columns hold ciphertext; lookups go through the HMAC blind index.
- No raw PII in logs (logging filter).
- Soft delete only (`deleted_at`); queries and unique indexes honor it.
- Sessions: refresh tokens rotate; logout/revocation kills sessions
  server-side via Redis.
- Proposals: 24h expiry, idempotency hash, role allow-list, spend caps,
  immutable audit trail.

## Windows/Docker test-reliability note
Docker Desktop's Windows-side port proxy on this machine intermittently wedges:
connects to published ports fail in waves with `WinError 52` /
`WSAEADDRINUSE (10048)` because Windows' ephemeral-port allocator keeps picking
a local port that is already in `TIME_WAIT` (classically `49168`, the first
port of the dynamic range). `tests/conftest.py` works around this by pinning
every DB connection to a rotating explicit local port (pool `30100–30599`,
retrying on 10048/10049); `FINEX_DISABLE_PORT_PINNING=1` opts out.

If other tools show the same symptom (curl, psql, browsers), give them an
explicit source port (e.g. `curl --local-port 30801 ...`) or run
`docker compose restart postgres`. A machine-wide cure needs an elevated
prompt: widen the dynamic range with
`netsh int ipv4 set dynamicport tcp start=10000 num=55535` and consider
lowering `TcpTimedWaitDelay` (registry, min 30s). Restarting the machine also
clears the tide.

## Hardening for production
- Set a real `MIGRATION_DATABASE_URL` (owner/migration role) and grant
  `finex_app` `LOGIN` (it is NOLOGIN by default) with a password; the runtime
  `DATABASE_URL` should use `finex_app`, not the owner role.
- Replace local JWT/PII keys; store them in a secret manager.
- Rate limiting and audit retention policies per `docs/security-charter.md`.
