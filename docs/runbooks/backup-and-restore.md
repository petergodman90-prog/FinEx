# Backup and restore runbook

## Why this exists

RLS and encryption protect data in use. Backups protect it against accidental
deletion, failed migrations, disk loss, and ransomware. A backup is only real
once a restore has been tested.

## Local development

`docker-compose.yml` stores PostgreSQL data in the named `pgdata` volume:

- `docker compose down` keeps data.
- `docker compose down -v` **deletes** the database volume. Never run it on a
  database you care about.

Take a compressed backup before any risky migration or destructive test:

```powershell
New-Item -ItemType Directory -Force .\backups | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
docker exec finex_postgres pg_dump -U finex -d finex -Fc -f /tmp/finex-$stamp.dump
docker cp finex_postgres:/tmp/finex-$stamp.dump .\backups\finex-$stamp.dump
docker exec finex_postgres rm /tmp/finex-$stamp.dump
```

`backups/` is gitignored: a dump contains customer records and must never be
committed or attached to a pull request.

## Restore drill (recommended before every release)

Restore into a scratch database rather than overwriting the live one:

```powershell
$dump = ".\backups\finex-YYYYMMDD-HHMMSS.dump"
docker exec finex_postgres createdb -U finex finex_restore_test
docker cp $dump finex_postgres:/tmp/restore.dump
docker exec finex_postgres pg_restore -U finex -d finex_restore_test --no-owner /tmp/restore.dump
docker exec finex_postgres psql -U finex -d finex_restore_test -c "select version_num from alembic_version;"
docker exec finex_postgres psql -U finex -d finex_restore_test -c "select count(*) from ledger.journal_entries;"
```

Point a temporary `DATABASE_URL` at `finex_restore_test` and run `pytest -q` if
you need proof that the restored schema still enforces RLS. Drop the scratch
database afterwards:

```powershell
docker exec finex_postgres dropdb -U finex finex_restore_test
```

## Production

Local `pg_dump` is not a production strategy. Use:

1. A managed PostgreSQL service with automated daily backups **and**
   point-in-time recovery (WAL archiving). Target RPO ≤ 15 minutes and test the
   chosen RTO.
2. Encrypted, off-site copies in a different region/account, with immutability
   (object lock) for ransomware resistance.
3. A restore drill every quarter, including the application test suite and a
   spot check that RLS blocks a cross-tenant read.
4. Restricted access to dumps and to the PII keys. A dump plus the keys is a
   full data breach; keep them in separate secret stores.
5. Retention that matches the privacy policy, then secure deletion. Financial
   records remain append-only in the database; backups expire on schedule.

## Before applying a migration

1. Confirm the latest automated backup completed.
2. Run `alembic current` and record the revision.
3. Apply `alembic upgrade head` (transactional DDL).
4. Run `pytest -q` and the health check.
5. If anything fails, stop writes and restore into a new database; do not run a
   destructive downgrade on production.
