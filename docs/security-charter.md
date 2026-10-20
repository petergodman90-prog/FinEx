# FINEX — Security & Tenancy Charter

## 1. Tenancy
- Every tenant-owned table has `org_id NOT NULL`.
- PostgreSQL Row Level Security (RLS) enabled on every tenant table.
- App DB user CANNOT bypass RLS. Migrations use a separate DB user.

## 2. Auth
- Every request carries: `user_id`, `active_org_id`, `role`.
- Sessions revocable instantly.
- Org switching rotates DB session context.

## 3. AI
- AI reads only from ledger-derived views. No recalculation.
- AI NEVER writes. It creates proposals only.
- Proposals: 24h expiry, idempotency hash, audit log, role allow-list, spend cap.

## 4. WhatsApp
- Verify Meta webhook signature.
- Deduplicate by message ID.
- High-impact replies go through approval buffer.
- Rate limit inbound.

## 5. PII
- Phone/email encrypted at rest.
- Blind index (HMAC) for lookup.
- No raw PII in logs, AI prompts, analytics, or errors.

## 6. Lifecycle
- Soft delete 30 days → purge or crypto-shred.
- Export pipeline for user data requests.

## 7. Secrets
- Never in Git or VSCode settings.
- Local: `.env` (git-ignored).
- Prod: secret manager.

## 8. Rules for all services
- No direct cross-service DB access. Use APIs or events.
- RLS tests required for every tenant table.
- Production data never in dev.