# Database migration runbook

## Purpose

A migration is a versioned, forward-only database change. Migrations make it
possible for every developer, test environment, and production environment to
reach the same schema reliably.

## Single runner

Alembic in `backend/migrations/` is the only migration runner. A fresh database
needs one command:

```powershell
cd backend
alembic upgrade head
```

| Revision | Contents |
| --- | --- |
| `001_initial` | Platform tenancy, CRM, proposals, audit + RLS policies |
| `002_financial_core` | Executes `db/migrations/0001_person_a_financial_core.sql`, then adds ledger RLS, `security_invoker` views, posting-context guards and app-role grants |

Do **not** apply the financial-core SQL file separately. Alembic records the
schema version; a database with a manually created `ledger` schema cannot be
upgraded cleanly (revision 002 fails with an explicit message).

## Rules

1. Never edit a migration that has been merged into `main` or applied outside
   your local machine.
2. Make a new Alembic revision for every later change. Do not edit revision 002
   or append to the financial-core SQL file once merged.
3. Use descriptive revision ids, for example `003_ledger_receipts_index`.
4. Keep a migration focused on one logical change.
5. Add a test proving the new behaviour and a test proving an unsafe operation
   is rejected.
6. Document any required backfill, deployment order, or reversal procedure in
   the pull request.
7. Run migrations as the schema owner (`MIGRATION_DATABASE_URL` when set), never
   as the runtime `finex_app` role.

## Routine commands

```powershell
alembic current            # revision applied to the database
alembic history            # ordered revisions
alembic upgrade head       # apply everything pending
alembic check              # fail if models and schema drift
```

`alembic downgrade -1` exists for local recovery but is **destructive** for
revision 002: it drops the entire `ledger` schema, including posted financial
history. Take a backup first (see
[backup-and-restore.md](backup-and-restore.md)) and never downgrade a production
database without an approved recovery plan.

## Financial-schema safeguards

Person A migrations must preserve these rules:

- posted financial and stock records are append-only;
- corrections use reversal records;
- callers receive only execute permission on approved posting functions;
- client applications and integrations have no direct write access to ledger or
  stock tables;
- every `ledger` table remains RLS-enabled and forced, and read views use
  `security_invoker = true`;
- the runtime role is not the schema owner and does not have `SUPERUSER` or
  `BYPASSRLS`.

## Verification after `alembic upgrade head`

```powershell
cd backend
pytest -q                  # includes ledger RLS, view, grant and cross-tenant tests
alembic current            # should print 002_financial_core (head)
```
