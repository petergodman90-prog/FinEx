# Database migration runbook

## Purpose

A migration is a versioned, forward-only database change. Migrations make it possible for every developer, test environment, and production environment to reach the same schema reliably.

## Rules

1. Never edit a migration that has been merged into `main` or applied outside your local machine.
2. Make a new migration for every later change.
3. Use ordered names: `YYYYMMDDHHMM_owner_short-description.sql`.
4. Keep a migration focused on one logical change.
5. Add a test proving the new behaviour and a test proving an unsafe operation is rejected.
6. Document any required backfill, deployment order, or reversal procedure in the pull request.

## Current baseline

`db/migrations/0001_person_a_financial_core.sql` is the initial local Person A financial-core migration. Before the next shared migration is created, the team should agree on the timestamp-based naming convention above.

## Financial-schema safeguards

Person A migrations must preserve these rules:

- posted financial and stock records are append-only;
- corrections use reversal records;
- callers receive only execute permission on approved posting functions;
- client applications and integrations have no direct write access to ledger or stock tables.

