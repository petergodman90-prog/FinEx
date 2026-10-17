# Contributing to FinEx

## Shared ownership

- **Person A** owns financial posting, ledger, inventory movements, receipts, and ledger-derived reports.
- **Person B** owns tenant isolation, session/integration security, WhatsApp, and AI proposal generation.
- **Person C** owns user-facing screens, client state, and approval flows.

Use the ownership boundaries in [`docs/architecture/ownership-and-boundaries.md`](docs/architecture/ownership-and-boundaries.md) before changing a cross-domain feature.

## Everyday Git workflow

1. Update your local `main` branch: `git switch main` then `git pull --ff-only origin main`.
2. Create a short-lived branch from `main`, such as `person-a/a-002-opening-balances`.
3. Make one focused change and add tests and documentation as needed.
4. Check `git status` and review your diff with `git diff`.
5. Commit with a clear message, for example `feat(ledger): add opening balance posting`.
6. Push the branch and open a pull request into `main`.

Do not commit directly to `main`, rewrite shared history, or use force-push on a shared branch.

## Pull-request checklist

- The change stays within the stated owner boundary, or the affected owners have reviewed it.
- Database migrations are forward-only and have a unique ordered filename.
- Financial and stock writes use the posting service; no UI, AI, importer, or integration writes those tables directly.
- Automated tests cover the success case and a relevant failure case.
- Any public contract or operational behaviour is documented.

