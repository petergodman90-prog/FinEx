"""Liveness and readiness probe behaviour.

`/health` must stay cheap and independent of dependencies so an orchestrator
does not restart a healthy process during a transient database blip. `/ready`
combines PostgreSQL and Redis because auth fails closed without the shared
revocation store.
"""

from __future__ import annotations

from redis.exceptions import RedisError

from app.core import sessions


async def test_health_is_liveness_only(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_checks_postgres_and_redis(client) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_ready_fails_closed_when_redis_is_down(client, monkeypatch) -> None:
    class BrokenRedis:
        async def ping(self):
            raise RedisError("redis unavailable")

    monkeypatch.setattr(sessions, "_get_redis", lambda: BrokenRedis())

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "not ready"}
