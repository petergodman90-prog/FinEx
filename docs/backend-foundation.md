# Backend Foundation (Layers 1–6)

Status: complete and tested (105 tests green). Deferred by design: AI assistant
NLU, WhatsApp ingestion, OCR/AI receipt extraction, advanced CRM analytics.

## Layers

### 1. Core: config, DB, RLS, request hygiene
- `app/core/config.py` — pydantic-settings; `.env` is looked up in the common
  run locations (repo root or `backend/`). Keys: `DATABASE_URL`,
  `MIGRATION_DATABASE_URL` (optional, owner/migration role), `REDIS_URL`,
  JWT settings, `PII_ENCRYPTION_KEY` (Fernet), `PII_HMAC_KEY` (hex).
- `app/core/db.py` — async engine + `SessionLocal`; in production,
  `assert_runtime_role_is_restricted` refuses to start if the connection role
  is `SUPERUSER` or has `BYPASSRLS`.
- `app/main.py` — CORS is opt-in via `CORS_ORIGINS` and never a wildcard;
  production serves the UI and API from one origin. With `SERVE_FRONTEND=true`
  the backend serves only `index.html` and the public `assets/`, `css/`, `js/`
  and `pages/` directories — never the repository root. Browser sessions use
  `HttpOnly`, `SameSite=Lax` cookies (`app/core/cookies.py`); browser login,
  register, refresh and switch-org responses contain no tokens. Non-browser
  clients explicitly opt into bearer tokens via `/api/v1/auth/token` and
  `/api/v1/auth/token/refresh`.
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
  `/api/v1/auth/register | login | refresh | switch-org | logout | me` for
  browsers, plus explicit `/api/v1/auth/token | token/refresh | token/switch-org`
  endpoints for non-browser API clients.
- JWT access tokens (`app/core/auth.py`); refresh rotation is atomic via
  Redis `SET NX` with jti tracking. Reusing a consumed refresh token revokes
  the whole token family. Session kill-switch (user-level revocation) via
  Redis (`app/core/sessions.py`; tests use fakeredis). Password hashing in
  `app/core/passwords.py`.
- Login/register/refresh are rate limited per client IP (and login additionally
  per account) through Redis (`app/core/rate_limit.py`). The limiter fails
  closed if Redis is unavailable.
- Production startup rejects weak/placeholder JWT secrets, missing or invalid
  PII keys, wildcard CORS, and unsafe JWT algorithms
  (`app/core/config.py`).
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
- `backend/migrations/versions/002_person_a_financial_core.py`: executes
  Person A's canonical ledger SQL, adds RLS to every `ledger` table
  (`receipt_lines` via its receipt), sets `security_invoker=true` on the read
  views, rejects posting rows that do not match the transaction's org context,
  and grants `finex_app` SELECT + posting-function EXECUTE only.
- `backend/migrations/versions/005_membership_rls_hardening.py`: splits the
  memberships policy into read-own/read-org SELECT plus owner/admin-only
  INSERT/UPDATE/DELETE, blocking staff self-promotion.
- `alembic check` is clean (models ↔ migration zero drift).
- `backend/tests` (105 tests): auth flows, refresh rotation/revocation,
  customers CRUD/dedup/merge/permissions, proposal idempotency/caps/expiry,
  dashboard metrics, RBAC matrix, crypto (encrypt/blind index), RLS
  enabled+forced and cross-tenant isolation — including behavioral checks that
  run as `SET ROLE finex_app`, ledger posting/receipt isolation, safe views,
  cross-tenant posting rejection, and posting idempotency.

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
  Membership writes are further restricted to the active org's owner/admin.
- PII columns hold ciphertext; lookups go through the HMAC blind index.
- No raw PII in logs (logging filter).
- Soft delete only (`deleted_at`); queries and unique indexes honor it.
- Sessions: refresh tokens rotate atomically; replay kills the token family;
  logout/revocation kills sessions server-side via Redis. Browser responses
  never expose bearer tokens.
- Auth endpoints are Redis-rate-limited; production startup validates JWT and
  PII key material.
- Proposals: 24h expiry, idempotency hash, role allow-list, spend caps,
  immutable audit trail.
- Ledger: every table RLS-enabled and forced; `receipt_lines` isolated through
  its parent receipt; read views are `security_invoker`; `finex_app` has no
  direct ledger writes; posting functions reject a foreign org context.

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
  `DATABASE_URL` should use `finex_app`, not the owner role. With
  `APP_ENV=production`, startup fails if the runtime role is `SUPERUSER` or has
  `BYPASSRLS`.
- Leave `CORS_ORIGINS` empty and serve the UI plus `/api` from one HTTPS origin
  (reverse proxy), with HSTS, CSP, `X-Content-Type-Options` and
  `Referrer-Policy`. Either keep `SERVE_FRONTEND=true` so the backend serves the
  public UI, or set it false and let the proxy serve only those public files.
  Never mount the repository root in a static server.
- Cookies are forced `Secure` when `APP_ENV=production`; set
  `COOKIE_SECURE=true` for any other HTTPS deployment. Keep `HttpOnly` and
  `SameSite=Lax` — do not move tokens into `localStorage`.
- Replace local JWT/PII keys; store them in a secret manager.
- Configure automated encrypted backups with point-in-time recovery and test a
  restore before launch — see
  [runbooks/backup-and-restore.md](runbooks/backup-and-restore.md).
- Auth rate limiting is Redis-backed and fail-closed. Plan audit retention
  policies per `docs/security-charter.md`.
