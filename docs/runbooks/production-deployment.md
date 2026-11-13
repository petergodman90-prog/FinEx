# Production deployment runbook

This runbook is the operational checklist for exposing FinEx to real users. It
assumes a single-region Linux deployment with managed PostgreSQL and Redis. A
different hosting model is fine, but every item below still needs an owner.

The application and API must share one HTTPS origin. The browser uses
`HttpOnly`, `SameSite=Lax` cookies, so do not split the UI and API across
origins and do not add a wildcard to `CORS_ORIGINS`.

## 1. Producing the environment file

Create the production environment file on the host, owned by the deploy user
and mode `0600`, and load it through the process supervisor—not from a shell
history, chat message, or repository file.

```bash
# Generate values once and store them in the secret manager.
openssl rand -hex 32                       # JWT_SECRET (>= 32 bytes)
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
openssl rand -hex 32                       # PII_HMAC_KEY (>= 32 bytes)
```

Required settings:

```dotenv
APP_ENV=production
APP_NAME=FINEX
SERVE_FRONTEND=true
CORS_ORIGINS=

# Runtime role only: NOBYPASSRLS, no DDL, no ownership.
DATABASE_URL=postgresql+asyncpg://finex_app:...@db.internal:5432/finex?ssl=require
# Owner/migration role, used only by the migration job.
MIGRATION_DATABASE_URL=postgresql+asyncpg://finex_owner:...@db.internal:5432/finex?ssl=require

# Managed Redis with TLS/auth and persistence enabled.
REDIS_URL=rediss://:...@redis.internal:6379/0

JWT_SECRET=<generated>
PII_ENCRYPTION_KEY=<generated Fernet key>
PII_HMAC_KEY=<generated hex key>

# Keep the built-in limiter on; the reverse proxy must forward the real IP.
AUTH_RATE_LIMIT_ENABLED=true
```

`APP_ENV=production` forces `Secure` cookies and makes the app reject weak JWT
secrets, missing PII keys, and malformed keys at startup. Do not disable that
validation to get a deployment running.

### Secret separation

- Keep `PII_ENCRYPTION_KEY`/`PII_HMAC_KEY` in a different secret store and with
  different access than database backups. A dump plus the PII keys is a full
  data breach.
- Keep `MIGRATION_DATABASE_URL` available only to the migration job, never to
  the long-running web process.
- Rotate the JWT secret by invalidating sessions and requiring users to sign in
  again. Rotating PII keys requires a re-encryption migration; plan it
  separately.
- Never commit `.env`, dumps, certificates, or private keys. They are ignored
  by `.gitignore`; keep it that way.

## 2. Database roles and migrations

Use two PostgreSQL roles:

| Role | Used by | Privileges |
| --- | --- | --- |
| `finex_owner` | Alembic migration job | schema owner; can run DDL |
| `finex_app` | Uvicorn web process | `NOBYPASSRLS`; runtime DML/read only |

The app checks the runtime role at startup and refuses to run as a role that
can bypass RLS. Create the roles with the bootstrap SQL in
[`db/migrations/0001_person_a_financial_core.sql`](../../db/migrations/0001_person_a_financial_core.sql),
then run migrations as a one-off job:

```bash
cd /opt/finex/backend
set -a; . /etc/finex/finex.env; set +a
.venv/bin/alembic current
.venv/bin/alembic upgrade head
```

Never apply migration SQL files directly. Take a verified backup before every
migration and follow [backup-and-restore.md](backup-and-restore.md).

## 3. Process supervisor

Run Uvicorn behind the reverse proxy on loopback only. Example systemd unit:

```ini
[Unit]
Description=FinEx API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=finex
Group=finex
WorkingDirectory=/opt/finex/backend
EnvironmentFile=/etc/finex/finex.env
ExecStart=/opt/finex/backend/.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 \
  --proxy-headers --forwarded-allow-ips=127.0.0.1 \
  --workers 2
Restart=always
RestartSec=3
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/finex/backend
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

If the proxy runs in another container, set `--forwarded-allow-ips` to that
proxy's network CIDR, not `*`. Forwarding is needed so the auth rate limiter
sees the real client address instead of one shared proxy address.

Use a process manager or orchestrator that sends `SIGTERM`, waits for in-flight
requests, and restarts on failure. Keep the previous release directory for a
fast rollback.

## 4. TLS and reverse proxy

Terminate TLS at the proxy, redirect all HTTP to HTTPS, and keep the app port
unreachable from the public internet. Minimum TLS 1.2 (prefer 1.3), modern
ciphers, and automatic certificate renewal.

### Caddy example

```caddyfile
finex.example.com {
    encode zstd gzip
    request_body {
        max_size 2MB
    }
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        Content-Security-Policy "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        -Server
    }
    reverse_proxy 127.0.0.1:8000
}
```

The CSP keeps the existing inline theme/bootstrap scripts working while
blocking third-party script origins. Migrate those inline scripts to nonces
before tightening `script-src` further.

### nginx example

```nginx
server {
    listen 443 ssl http2;
    server_name finex.example.com;

    ssl_certificate     /etc/letsencrypt/live/finex.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/finex.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    client_max_body_size 2m;
    server_tokens off;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy no-referrer always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}

server {
    listen 80;
    server_name finex.example.com;
    return 301 https://$host$request_uri;
}
```

Do not enable HSTS permanently until the domain and certificate renewal are
verified. Start with a short `max-age` in staging.

## 5. Health checks and monitoring

The app exposes two probes:

| Endpoint | Purpose | Expected |
| --- | --- | --- |
| `GET /health` | liveness; the process is serving | `200 {"status":"ok"}` |
| `GET /ready` | readiness; PostgreSQL and Redis are reachable | `200 {"status":"ready"}`, else `503` |

Use `/health` for container/process restarts and `/ready` for load-balancer
traffic. Do not route traffic to a process that fails `/ready`: auth fails
closed when Redis is unavailable.

Minimum production monitoring:

- External uptime checks against `/health` and `/ready` every 30 seconds from
  at least two regions.
- Alert on `/ready` failures, 5xx rate above 1% for five minutes, p95 API
  latency above 1 second, and TLS certificate expiry under 14 days.
- PostgreSQL: connection saturation, slow queries, replication/PITR status, and
  backup age. Alert before disk or connection pools are exhausted.
- Redis: availability, memory pressure, evictions, and persistence errors. Use
  `maxmemory-policy noeviction`; sessions/rate limits must not be silently
  evicted. Losing Redis signs users out and fails auth closed, but must page.
- Collect application and proxy logs centrally. The app already filters PII and
  adds request IDs; never log passwords, tokens, authorization headers, or
  reset links. Alert on repeated 401/403/429 spikes and on audit-write errors.
- Add a synthetic check that signs in with a dedicated monitoring account and
  reads a ledger-derived page, then signs out. Do not use a real owner account.

The application does not emit Prometheus metrics yet. Use the provider metrics
plus proxy/access logs for launch, and add application metrics as a follow-up
if the chosen monitoring stack requires them.

## 6. Backups and restore drills

Follow [backup-and-restore.md](backup-and-restore.md). Production minimum:

- Managed PostgreSQL with automated daily backups and point-in-time recovery;
  target RPO <= 15 minutes and a tested RTO.
- Encrypted, immutable, off-site copies in a different region/account.
- A quarterly restore drill into a scratch database, including `pytest -q` and
  a cross-tenant RLS spot check.
- Backup alerts that page when the latest successful backup is older than the
  agreed RPO.
- PII keys backed up separately from database dumps, with access logging.

A backup that has never been restored is not a backup.

## 7. Network and host hardening

- Public ingress: TCP 443 only. Redirect 80 to 443.
- App binds `127.0.0.1:8000`; PostgreSQL and Redis are private-only.
- Firewall egress to package registries, the managed database/Redis, the
  monitoring backend, and the email provider only.
- Enable automatic OS security updates, host intrusion detection, and disk
  encryption.
- Run the app as a non-root user with no shell login. Do not run migrations from
  the web process.
- Restrict SSH to a bastion/VPN and require keys, not passwords.

## 8. Release and rollback

Before a release:

1. CI is green for both `backend` and `browser` workflows.
2. A verified backup exists and the migration was reviewed.
3. Staging ran `alembic upgrade head`, the backend suite, and the browser E2E.
4. The previous release artifact is retained.

Deploy order: install dependencies/build artifact, run migrations as
`finex_owner`, restart the app, wait for `/ready`, then run the external smoke
check. On failure, stop traffic, restore the previous release, and restore the
database only if a forward migration cannot be rolled back safely. Financial
records are append-only; prefer a forward fix over destructive downgrades.

## 9. Launch blockers and residual gaps

These are intentionally not silently marked as done:

- **Password reset is not implemented.** The login screen still shows
  "coming soon". Shipping it requires an email provider/SMTP decision, a
  single-use token store, session revocation, and a reset UI.
- **Email invitations for new users are deferred.** The current staff flow only
  invites existing FinEx accounts.
- **No production monitoring account or alert routing has been provisioned**
  until a hosting target and monitoring provider are chosen.
- **No managed backup/PITR provider has been selected.** The backup runbook is
  ready, but the production backup configuration and drill date must be owned.
- **No third-party penetration test or dependency-audit gate is configured.**
  Run `pip-audit` (or the chosen SCA tool) before launch and after dependency
  changes.
- **CSP still permits inline scripts** because the static theme bootstrap uses
  them. Tighten this after moving the bootstrap to a nonced or external file.

## Go-live checklist

- [ ] DNS and certificate renewal verified; HTTP redirects to HTTPS.
- [ ] `APP_ENV=production`, strong secrets, restricted runtime DB role.
- [ ] `alembic upgrade head` run as owner; app starts as `finex_app`.
- [ ] `/health` and `/ready` monitored externally; alert routing tested.
- [ ] Backups, PITR, off-site immutability, and a restore drill date confirmed.
- [ ] Central logs and PII-redaction review completed.
- [ ] Firewall, private DB/Redis, and host hardening verified.
- [ ] Release rollback rehearsed; previous artifact retained.
- [ ] Residual gaps above accepted by the product/security owner before launch.
