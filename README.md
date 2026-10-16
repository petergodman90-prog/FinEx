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