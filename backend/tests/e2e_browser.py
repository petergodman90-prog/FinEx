"""End-to-end browser smoke test for the live FinEx UI.

The test starts its own API server against PostgreSQL (with fakeredis), seeds a
small tenant, launches headless Chrome, and verifies the security-sensitive UI
states introduced by the proposal/credit-note hardening:

* a manager-created proposal never shows an Approve button;
* the dashboard says "Another approver required";
* the credit-note dialog only allows restocking once a receipt is linked;
* a 403 is shown in-page rather than treated as an expired session.

Run from ``backend/`` with PostgreSQL available::

    python tests/e2e_browser.py

CI runs the same command in the ``browser`` workflow. Chrome/Chromium must be
on PATH. The script uses only test dependencies already declared by the backend.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import websockets

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PASSWORD = "password123"
TEST_FERNET_KEY = "IlhSkVWXA6mKWXGy2rLEPXojxdbYigobmmWiYCPQLxk="
TEST_HMAC_KEY = "f7b830813a497f11c82c214aba395ea4bb2d48528ebe004a30dd282511471fc9"


def _load_dotenv_defaults(env: dict[str, str]) -> None:
    """Load simple ``KEY=value`` lines from the repo .env without overriding."""
    for candidate in (REPO / ".env", BACKEND / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip())


def _server_env() -> dict[str, str]:
    env = os.environ.copy()
    _load_dotenv_defaults(env)
    if not env.get("DATABASE_URL"):
        raise SystemExit(
            "DATABASE_URL is required (for example the docker-compose Postgres)"
        )
    if not env.get("MIGRATION_DATABASE_URL"):
        env["MIGRATION_DATABASE_URL"] = env["DATABASE_URL"]
    if not env.get("JWT_SECRET"):
        env["JWT_SECRET"] = "e2e-only-not-a-production-secret"
    if not env.get("PII_ENCRYPTION_KEY"):
        env["PII_ENCRYPTION_KEY"] = TEST_FERNET_KEY
    if not env.get("PII_HMAC_KEY"):
        env["PII_HMAC_KEY"] = TEST_HMAC_KEY
    if not env.get("REDIS_URL"):
        env["REDIS_URL"] = "redis://127.0.0.1:6379/0"
    env["APP_ENV"] = "local"
    env["SERVE_FRONTEND"] = "true"
    env["COOKIE_SECURE"] = "false"
    env["AUTH_RATE_LIMIT_ENABLED"] = "false"
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(BACKEND), env.get("PYTHONPATH", "")) if part
    )
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def _migrate(env: dict[str, str]) -> None:
    last = None
    for attempt in range(4):
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND,
            env=env,
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0:
            print("[e2e] database at head")
            return
        last = proc
        time.sleep(2)
    assert last is not None
    raise RuntimeError(
        "alembic upgrade failed:\n" + (last.stdout or "") + (last.stderr or "")
    )


def _start_server(env: dict[str, str], log_path: Path) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e_server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        cwd=BACKEND,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            log.flush()
            raise RuntimeError(
                f"API server exited early ({proc.returncode}):\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            )
        try:
            response = httpx.get(f"{base}/ready", timeout=2)
            if response.status_code == 200:
                print(f"[e2e] API listening on {base}")
                return proc, base
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("API server did not become healthy within 60s")


def _token(
    client: httpx.Client,
    email: str,
    *,
    org_id: str | None = None,
) -> dict:
    payload = {"email": email, "password": PASSWORD}
    if org_id:
        payload["org_id"] = org_id
    response = client.post("/api/v1/auth/token", json=payload)
    _check(response, 200, "token")
    return response.json()


def _check(response: httpx.Response, expected: int, label: str) -> httpx.Response:
    if response.status_code != expected:
        raise AssertionError(
            f"{label}: expected {expected}, got {response.status_code}: "
            f"{response.text[:1200]}"
        )
    return response


def _headers(token: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['access_token']}"}


def _short_id(value: str) -> str:
    return value.replace("-", "")[:8].upper()


def seed_data(client: httpx.Client) -> dict:
    stamp = uuid.uuid4().hex[:12]
    owner_email = f"e2e-owner-{stamp}@example.com"
    manager_email = f"e2e-manager-{stamp}@example.com"
    now = datetime.now(timezone.utc)
    effective_at = now.isoformat()

    register = {
        "password": PASSWORD,
        "full_name": "E2E Owner",
    }
    response = client.post(
        "/api/v1/auth/register",
        json={
            **register,
            "org_name": "E2E Security Demo",
            "email": owner_email,
        },
    )
    _check(response, 201, "register owner")
    owner_org = response.json()["active_org_id"]
    owner = _token(client, owner_email)
    owner_h = _headers(owner)

    response = client.post(
        "/api/v1/auth/register",
        json={
            **register,
            "full_name": "E2E Manager",
            "org_name": "E2E Manager Personal",
            "email": manager_email,
        },
    )
    _check(response, 201, "register manager")
    manager_personal = _token(client, manager_email)

    response = client.post(
        "/api/v1/staff",
        json={"email": manager_email, "role": "manager"},
        headers=owner_h,
    )
    _check(response, 201, "invite manager")
    membership_id = response.json()["id"]

    response = client.post(
        f"/api/v1/auth/invitations/{membership_id}/accept",
        headers=_headers(manager_personal),
    )
    _check(response, 200, "accept invitation")
    manager = _token(client, manager_email, org_id=owner_org)
    manager_h = _headers(manager)
    assert manager["active_org_id"] == owner_org

    response = client.post(
        "/api/v1/products",
        json={
            "sku": f"E2E-{stamp}",
            "name": "E2E Security Widget",
            "unit": "each",
            "sale_price": "25.50",
            "cost": "10.00",
            "reorder_point": "2",
        },
        headers=owner_h,
    )
    _check(response, 201, "create product")
    product = response.json()

    response = client.post(
        "/api/v1/customers",
        json={"name": f"E2E Customer {stamp}"},
        headers=owner_h,
    )
    _check(response, 201, "create customer")
    customer = response.json()

    response = client.post(
        "/api/v1/inventory/opening-stock",
        json={
            "effective_at": effective_at,
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "10",
                    "unit_cost": "10.00",
                }
            ],
        },
        headers=owner_h,
    )
    _check(response, 200, "opening stock")
    response = client.post(
        f"/api/v1/proposals/{response.json()['proposal']['id']}/approve",
        json={},
        headers=owner_h,
    )
    _check(response, 200, "approve opening stock")

    response = client.post(
        "/api/v1/sales",
        json={
            "customer_id": customer["id"],
            "currency": "NGN",
            "effective_at": effective_at,
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "2",
                    "unit_price": "25.50",
                }
            ],
            "payment": {"method": "cash", "issue_receipt": True},
        },
        headers=owner_h,
    )
    _check(response, 200, "create owner sale")
    owner_sale = response.json()["sale"]
    response = client.post(
        f"/api/v1/proposals/{owner_sale['id']}/approve",
        json={},
        headers=owner_h,
    )
    _check(response, 200, "approve owner sale")
    response = client.get(f"/api/v1/sales/{owner_sale['id']}", headers=owner_h)
    _check(response, 200, "read owner sale")
    receipt_id = response.json().get("receipt_id")
    assert receipt_id, "cash sale did not produce a receipt"

    response = client.post(
        "/api/v1/sales",
        json={
            "customer_id": customer["id"],
            "currency": "NGN",
            "effective_at": effective_at,
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "1",
                    "unit_price": "25.50",
                }
            ],
            "payment": {
                "method": "receivable",
                "due_on": (date.today() + timedelta(days=30)).isoformat(),
            },
        },
        headers=manager_h,
    )
    _check(response, 200, "create manager sale")
    manager_sale = response.json()["sale"]

    response = client.post(
        "/api/v1/sales/credit-notes",
        json={
            "customer_id": customer["id"],
            "currency": "NGN",
            "effective_at": effective_at,
            "restock": False,
            "lines": [{"quantity": "1", "unit_price": "5.00"}],
        },
        headers=manager_h,
    )
    _check(response, 200, "create manager credit note")
    manager_credit = response.json()["credit_note"]

    # Domain types may not be staged through the generic proposal endpoint.
    response = client.post(
        "/api/v1/proposals",
        json={"proposal_type": "sale", "title": "Bypass attempt"},
        headers=manager_h,
    )
    _check(response, 422, "generic sale proposal rejection")

    # Unlinked credit notes are service-only and cannot restock inventory.
    response = client.post(
        "/api/v1/sales/credit-notes",
        json={
            "customer_id": customer["id"],
            "currency": "NGN",
            "effective_at": effective_at,
            "restock": True,
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "1",
                    "unit_price": "5.00",
                }
            ],
        },
        headers=manager_h,
    )
    _check(response, 422, "unlinked restock rejection")

    # The manager who created the sale cannot approve it.
    response = client.post(
        f"/api/v1/proposals/{manager_sale['id']}/approve",
        json={},
        headers=manager_h,
    )
    _check(response, 403, "manager self-approval rejection")
    assert response.json()["code"] == "permission_denied"
    assert "cannot approve proposals they created" in response.json()["detail"]

    return {
        "base": str(client.base_url).rstrip("/"),
        "owner_id": owner["user"]["id"],
        "manager_id": manager["user"]["id"],
        "owner_org": owner_org,
        "receipt_id": receipt_id,
        "manager_sale_id": manager_sale["id"],
        "manager_credit_id": manager_credit["id"],
        "manager_access_token": manager["access_token"],
        "manager_refresh_token": manager["refresh_token"],
    }


def _chrome_binary() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("Chrome/Chromium is required for the browser E2E test")


def _start_chrome(log_path: Path) -> tuple[subprocess.Popen, int, Path]:
    binary = _chrome_binary()
    port = _free_port()
    profile = Path(tempfile.mkdtemp(prefix="finex-e2e-chrome-"))
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            binary,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            log.flush()
            raise RuntimeError(
                "Chrome exited early:\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            )
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2
            ) as response:
                json.load(response)
            print(f"[e2e] Chrome CDP on {port}")
            return proc, port, profile
        except (OSError, ValueError):
            time.sleep(0.25)
    raise RuntimeError("Chrome did not expose a CDP endpoint within 30s")


def _targets(port: int) -> list[dict]:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/json/list", timeout=5
    ) as response:
        return json.load(response)


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        ident = self.seq
        await self.ws.send(
            json.dumps({"id": ident, "method": method, "params": params or {}})
        )
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
            message = json.loads(raw)
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def js(self, expression: str):
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        if "exceptionDetails" in result:
            raise RuntimeError(str(result["exceptionDetails"]))
        return result["result"].get("value")

    async def wait_for(self, expression: str, label: str, timeout: float = 20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = await self.js(expression)
            if value:
                return value
            await asyncio.sleep(0.25)
        raise AssertionError(f"timed out waiting for {label}")

    async def goto(self, url: str, wait_expression: str, label: str) -> None:
        await self.send("Page.navigate", {"url": url})
        await self.wait_for(
            "document.readyState === 'complete'", f"{label} load", timeout=20
        )
        await self.wait_for(wait_expression, label, timeout=25)


async def _browser_checks(state: dict, cdp_port: int) -> None:
    pages = [target for target in _targets(cdp_port) if target.get("type") == "page"]
    if not pages:
        raise RuntimeError("Chrome has no page target")
    ws = await websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=2**24)
    cdp = CDP(ws)
    base = state["base"]
    try:
        await cdp.send("Page.enable")
        await cdp.send("Runtime.enable")
        await cdp.send("Network.enable")
        for name, value in (
            ("finex_access", state["manager_access_token"]),
            ("finex_refresh", state["manager_refresh_token"]),
        ):
            result = await cdp.send(
                "Network.setCookie",
                {
                    "name": name,
                    "value": value,
                    "url": base,
                    "path": "/",
                    "httpOnly": True,
                    "sameSite": "Lax",
                },
            )
            assert result.get("success"), f"could not set {name} cookie"

        await cdp.goto(
            f"{base}/pages/dashboard.html",
            "document.querySelectorAll('#proposals-body tr').length > 0",
            "dashboard proposals",
        )
        rows = await cdp.js(
            """
            (() => [...document.querySelectorAll('#proposals-body tr')].map((tr) => ({
              text: tr.innerText.replace(/\\s+/g, ' ').trim(),
              buttons: [...tr.querySelectorAll('button')].map((b) => b.textContent.trim()),
            })))()
            """
        )
        pending_sale = next(
            (
                row
                for row in rows
                if "Sale - NGN 25.50" in row["text"] and "Pending" in row["text"]
            ),
            None,
        )
        assert pending_sale, f"manager sale missing from dashboard: {rows}"
        assert "Another approver required" in pending_sale["text"], pending_sale
        assert not pending_sale["buttons"], pending_sale
        assert not any(
            "Approve" in row["buttons"]
            for row in rows
            if "Pending" in row["text"]
        ), rows
        assert sum("Another approver required" in row["text"] for row in rows) >= 2, rows
        print("[e2e] dashboard suppresses manager self-approval")

        await cdp.goto(
            f"{base}/pages/sales.html",
            "document.querySelectorAll('#sales-body tr').length > 0",
            "sales rows",
        )
        sales_rows = await cdp.js(
            """
            (() => [...document.querySelectorAll('#sales-body tr')].map((tr) => ({
              text: tr.innerText.replace(/\\s+/g, ' ').trim(),
              buttons: [...tr.querySelectorAll('button')].map((b) => b.textContent.trim()),
            })))()
            """
        )
        sale_row = next(
            (
                row
                for row in sales_rows
                if _short_id_in(row["text"], state["manager_sale_id"])
            ),
            None,
        )
        assert sale_row, f"manager sale row missing: {sales_rows}"
        assert "Awaiting another approver" in sale_row["text"], sale_row
        assert not sale_row["buttons"], sale_row
        print("[e2e] sales workspace suppresses manager self-approval")

        await cdp.goto(
            f"{base}/pages/credit-notes.html",
            "document.querySelectorAll('#credit-notes-body tr').length > 0",
            "credit-note rows",
        )
        note_rows = await cdp.js(
            """
            (() => [...document.querySelectorAll('#credit-notes-body tr')].map((tr) => ({
              text: tr.innerText.replace(/\\s+/g, ' ').trim(),
              buttons: [...tr.querySelectorAll('button')].map((b) => b.textContent.trim()),
            })))()
            """
        )
        note_row = next(
            (
                row
                for row in note_rows
                if _short_id_in(row["text"], state["manager_credit_id"])
            ),
            None,
        )
        assert note_row, f"manager credit-note row missing: {note_rows}"
        assert "Awaiting another approver" in note_row["text"], note_row
        assert not note_row["buttons"], note_row
        print("[e2e] credit-note workspace suppresses manager self-approval")

        await cdp.js(
            "document.getElementById('credit-note-add-open').click()"
        )
        await cdp.wait_for(
            "document.getElementById('credit-note-dialog').open",
            "credit-note dialog",
        )
        unlinked = await cdp.js(
            """
            (() => {
              const restock = document.getElementById('credit-note-restock');
              return {
                disabled: restock.disabled,
                checked: restock.checked,
                hint: document.getElementById('credit-note-restock-hint').textContent.trim(),
              };
            })()
            """
        )
        assert unlinked == {
            "disabled": True,
            "checked": False,
            "hint": "Restocking requires a linked original receipt.",
        }, unlinked

        await cdp.js(
            """
            (() => {
              const input = document.getElementById('credit-note-receipt-id');
              input.value = %s;
              input.dispatchEvent(new Event('input', { bubbles: true }));
            })()
            """
            % json.dumps(state["receipt_id"])
        )
        linked = await cdp.js(
            """
            (() => {
              const restock = document.getElementById('credit-note-restock');
              return {
                disabled: restock.disabled,
                checked: restock.checked,
                hint: document.getElementById('credit-note-restock-hint').textContent.trim(),
              };
            })()
            """
        )
        assert linked["disabled"] is False, linked
        assert linked["checked"] is True, linked
        assert "original sale" in linked["hint"], linked

        await cdp.js(
            """
            (() => {
              const input = document.getElementById('credit-note-receipt-id');
              input.value = '';
              input.dispatchEvent(new Event('input', { bubbles: true }));
            })()
            """
        )
        cleared = await cdp.js(
            """
            (() => {
              const restock = document.getElementById('credit-note-restock');
              return {
                disabled: restock.disabled,
                checked: restock.checked,
                hint: document.getElementById('credit-note-restock-hint').textContent.trim(),
              };
            })()
            """
        )
        assert cleared == unlinked, cleared
        print("[e2e] credit-note restock toggle follows receipt linkage")

        auth = await cdp.js(
            """
            (() => {
              const Fx = window.FinexFinance;
              return {
                forbiddenIsAuthError: Fx.handleAuthError({ status: 403 }),
                canApproveOwn: Fx.canApproveProposal({ created_by_id: %s }),
                canApproveOther: Fx.canApproveProposal({ created_by_id: %s }),
                path: location.pathname,
              };
            })()
            """
            % (
                json.dumps(state["manager_id"]),
                json.dumps(state["owner_id"]),
            )
        )
        assert auth == {
            "forbiddenIsAuthError": False,
            "canApproveOwn": False,
            "canApproveOther": True,
            "path": "/pages/credit-notes.html",
        }, auth
        print("[e2e] 403 stays in-page and self-approval is denied client-side")
    finally:
        await ws.close()


def _short_id_in(text: str, value: str) -> bool:
    return _short_id(value) in text.upper()


def main() -> int:
    env = _server_env()
    _migrate(env)

    temp_dir = Path(tempfile.mkdtemp(prefix="finex-e2e-"))
    server_log = temp_dir / "server.log"
    chrome_log = temp_dir / "chrome.log"
    server = None
    chrome = None
    chrome_profile: Path | None = None
    try:
        server, base = _start_server(env, server_log)
        with httpx.Client(base_url=base, timeout=30) as client:
            state = seed_data(client)
        print("[e2e] seeded tenant and API guards")

        chrome, cdp_port, chrome_profile = _start_chrome(chrome_log)
        asyncio.run(_browser_checks(state, cdp_port))
        print("[e2e] PASS")
        return 0
    except Exception as exc:  # noqa: BLE001 - print diagnostics before exiting
        print(f"[e2e] FAIL: {exc}", file=sys.stderr)
        if server_log.exists():
            print("[e2e] server log tail:", file=sys.stderr)
            print(
                server_log.read_text(encoding="utf-8", errors="replace")[-6000:],
                file=sys.stderr,
            )
        if chrome_log.exists():
            print("[e2e] chrome log tail:", file=sys.stderr)
            print(
                chrome_log.read_text(encoding="utf-8", errors="replace")[-4000:],
                file=sys.stderr,
            )
        return 1
    finally:
        _stop_process(chrome)
        _stop_process(server)
        if chrome_profile is not None:
            shutil.rmtree(chrome_profile, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
