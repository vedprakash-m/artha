#!/usr/bin/env python3
"""
scripts/setup_plaid.py — Interactive Plaid financial connector setup wizard.

Plaid Link flow:
  1. Creates a link_token via POST /link/token/create
  2. Opens a browser to a hosted local Plaid Link page (127.0.0.1:7777)
  3. User completes bank login in browser
  4. Plaid redirects to callback with public_token
  5. Wizard exchanges public_token for access_token via /item/public_token/exchange
  6. Stores credentials in system keyring

Stores in keyring:
  artha-plaid-client-id  / value
  artha-plaid-secret     / value
  artha-plaid-access-token / value
  artha-plaid-environment / value

Usage:
  python scripts/setup_plaid.py              # full interactive setup
  python scripts/setup_plaid.py --verify-only # check existing credentials
  python scripts/setup_plaid.py --reset       # clear all stored credentials + re-run
  python scripts/setup_plaid.py --env sandbox # force sandbox environment
"""
from __future__ import annotations

import argparse
import getpass
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_PLAID_URLS = {
    "sandbox":     "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production":  "https://production.plaid.com",
}
_LINK_HOST = "127.0.0.1"
_LINK_PORT = 7777
_LINK_TIMEOUT = 300  # 5 minutes

_KEYRING_CLIENT_ID    = ("artha-plaid-client-id",    "value")
_KEYRING_SECRET       = ("artha-plaid-secret",       "value")
_KEYRING_ACCESS_TOKEN = ("artha-plaid-access-token", "value")
_KEYRING_ENVIRONMENT  = ("artha-plaid-environment",  "value")


# ---------------------------------------------------------------------------
# Keyring helpers
# ---------------------------------------------------------------------------

def _kr_get(service: str, username: str) -> Optional[str]:
    try:
        import keyring  # type: ignore[import]
        val = keyring.get_password(service, username)
        return val if val else None
    except Exception:
        return None


def _kr_set(service: str, username: str, value: str) -> bool:
    try:
        import keyring  # type: ignore[import]
        keyring.set_password(service, username, value)
        return True
    except Exception:
        return False


def _kr_del(service: str, username: str) -> None:
    try:
        import keyring  # type: ignore[import]
        keyring.delete_password(service, username)
    except Exception:
        pass


def _load_stored() -> tuple[str, str, str, str]:
    client_id    = _kr_get(*_KEYRING_CLIENT_ID)    or os.environ.get("ARTHA_PLAID_CLIENT_ID", "")
    secret       = _kr_get(*_KEYRING_SECRET)       or os.environ.get("ARTHA_PLAID_SECRET", "")
    access_token = _kr_get(*_KEYRING_ACCESS_TOKEN) or os.environ.get("ARTHA_PLAID_ACCESS_TOKEN", "")
    environment  = _kr_get(*_KEYRING_ENVIRONMENT)  or os.environ.get("ARTHA_PLAID_ENVIRONMENT", "sandbox")
    return client_id, secret, access_token, environment


def _clear_all() -> None:
    for svc, usr in (_KEYRING_CLIENT_ID, _KEYRING_SECRET, _KEYRING_ACCESS_TOKEN, _KEYRING_ENVIRONMENT):
        _kr_del(svc, usr)


# ---------------------------------------------------------------------------
# Plaid API helpers
# ---------------------------------------------------------------------------

def _plaid_post(base_url: str, endpoint: str, payload: dict) -> dict:
    url = f"{base_url}/{endpoint.lstrip('/')}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Artha/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode()
        err = json.loads(err_body) if err_body.startswith("{") else {}
        raise RuntimeError(
            f"Plaid error [{err.get('error_code', exc.code)}]: {err.get('error_message', err_body)}"
        ) from exc


def _create_link_token(
    base_url: str, client_id: str, secret: str, redirect_uri: str
) -> str:
    """Create a Plaid link_token for the Link flow."""
    resp = _plaid_post(base_url, "link/token/create", {
        "client_id": client_id,
        "secret": secret,
        "user": {"client_user_id": "artha-user"},
        "client_name": "Artha Personal Intelligence OS",
        "products": ["transactions"],
        "country_codes": ["US"],
        "language": "en",
        "redirect_uri": redirect_uri,
    })
    link_token = resp.get("link_token", "")
    if not link_token:
        raise RuntimeError(f"link_token missing from Plaid response: {resp}")
    return link_token


def _exchange_public_token(
    base_url: str, client_id: str, secret: str, public_token: str
) -> str:
    """Exchange a public_token for a permanent access_token."""
    resp = _plaid_post(base_url, "item/public_token/exchange", {
        "client_id": client_id,
        "secret": secret,
        "public_token": public_token,
    })
    access_token = resp.get("access_token", "")
    if not access_token:
        raise RuntimeError("access_token missing from Plaid exchange response")
    return access_token


def _verify_access(base_url: str, client_id: str, secret: str, access_token: str) -> str:
    """Verify access_token by calling /item/get. Returns institution name."""
    resp = _plaid_post(base_url, "item/get", {
        "client_id": client_id,
        "secret": secret,
        "access_token": access_token,
    })
    item = resp.get("item", {})
    inst_id = item.get("institution_id", "unknown")
    return inst_id


# ---------------------------------------------------------------------------
# Local Plaid Link server
# ---------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler to receive Plaid Link public_token via redirect."""

    public_token: Optional[str] = None
    csrf_nonce: str = ""

    def log_message(self, format: str, *args: Any) -> None:  # type: ignore[override]
        pass  # suppress access log

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/plaid-callback":
            received_nonce = params.get("state", [""])[0]
            if received_nonce != _CallbackHandler.csrf_nonce:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>Invalid state parameter</h1>")
                return

            public_token = params.get("public_token", [""])[0]
            if public_token:
                _CallbackHandler.public_token = public_token
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Artha: Bank linked!</h1>"
                    b"<p>You can close this window and return to the terminal.</p>"
                )
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>Missing public_token</h1>")
            return

        # Serve the Plaid Link JS page
        if parsed.path in ("/", "/plaid-link"):
            link_token_param = params.get("link_token", [""])[0]
            html = self._build_link_page(link_token_param)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())
            return

        self.send_response(404)
        self.end_headers()

    def _build_link_page(self, link_token: str) -> str:
        """Build the HTML page that runs Plaid Link JS."""
        state = _CallbackHandler.csrf_nonce
        callback_url = f"http://{_LINK_HOST}:{_LINK_PORT}/plaid-callback"
        return f"""<!DOCTYPE html>
<html><head><title>Artha × Plaid Link</title></head>
<body>
<h2>Connecting your bank to Artha…</h2>
<p>A Plaid Link dialog will open automatically. Complete the login in the popup.</p>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
var handler = Plaid.create({{
  token: "{link_token}",
  onSuccess: function(public_token, metadata) {{
    window.location.href = "{callback_url}?public_token=" + encodeURIComponent(public_token) + "&state={state}";
  }},
  onExit: function(err, metadata) {{
    document.body.innerHTML = "<h2>Cancelled or error.</h2><p>" + (err ? err.display_message : "No error.") + "</p><p>Close this window and re-run setup.</p>";
  }},
}});
handler.open();
</script>
</body></html>"""


from typing import Any  # noqa: E402 (re-import for handler class)


def _run_link_flow(
    base_url: str,
    client_id: str,
    secret: str,
    environment: str,
) -> str:
    """Run the full Plaid Link flow; returns public_token."""
    csrf_nonce = secrets.token_hex(16)
    _CallbackHandler.csrf_nonce = csrf_nonce
    _CallbackHandler.public_token = None

    redirect_uri = f"http://{_LINK_HOST}:{_LINK_PORT}/plaid-callback"

    print("  Creating Plaid link token…")
    link_token = _create_link_token(base_url, client_id, secret, redirect_uri)

    link_url = f"http://{_LINK_HOST}:{_LINK_PORT}/plaid-link?link_token={link_token}"

    # Start local HTTP server
    server = http.server.HTTPServer((_LINK_HOST, _LINK_PORT), _CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print(f"\n  Opening browser for Plaid Link: {link_url}")
    print("  Complete the bank login in your browser…")
    webbrowser.open(link_url)

    # Wait for callback
    deadline = time.time() + _LINK_TIMEOUT
    while time.time() < deadline:
        if _CallbackHandler.public_token:
            server.shutdown()
            return _CallbackHandler.public_token
        time.sleep(0.5)

    server.shutdown()
    raise TimeoutError("Plaid Link timed out waiting for browser callback.")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def _print_banner() -> None:
    print()
    print("=" * 60)
    print("  Artha × Plaid Financial Connector Setup")
    print("=" * 60)
    print()


def _print_instructions(environment: str) -> None:
    print(f"Environment: {environment.upper()}")
    print()
    print("You will need your Plaid API credentials from:")
    print("  https://dashboard.plaid.com/developers/keys")
    print()
    print("Privacy reminder:")
    print("  Artha NEVER stores raw transaction data.")
    print("  Only aggregated category summaries are persisted.")
    print()


def run_verify_only() -> int:
    client_id, secret, access_token, environment = _load_stored()
    if not all((client_id, secret, access_token)):
        print("[ERROR] Plaid credentials not fully configured.")
        return 1
    base_url = _PLAID_URLS.get(environment, _PLAID_URLS["sandbox"])
    print(f"Verifying Plaid access token ({environment})…")
    try:
        inst = _verify_access(base_url, client_id, secret, access_token)
        print(f"  [OK] Institution ID: {inst}")
        return 0
    except Exception as exc:
        print(f"  [FAIL] {exc}")
        return 1


def run_setup(environment: str = "sandbox", reset: bool = False) -> int:
    _print_banner()

    if reset:
        print("Clearing all stored Plaid credentials…")
        _clear_all()
        print("  Done.")
        print()

    existing_client_id, existing_secret, existing_access, existing_env = _load_stored()
    if all((existing_client_id, existing_secret, existing_access)) and not reset:
        environment = existing_env
        base_url = _PLAID_URLS.get(environment, _PLAID_URLS["sandbox"])
        print("Plaid credentials already stored. Verifying…")
        try:
            inst = _verify_access(base_url, existing_client_id, existing_secret, existing_access)
            print(f"  [OK] Institution ID: {inst} ({environment})")
            print()
            answer = input("Credentials valid. Re-configure anyway? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print("Setup skipped.")
                return 0
        except Exception as exc:
            print(f"  [FAIL] {exc}")
            print("  Proceeding to re-configure…")

    _print_instructions(environment)
    base_url = _PLAID_URLS.get(environment, _PLAID_URLS["sandbox"])

    # Collect client_id and secret
    client_id = getpass.getpass("Plaid client_id: ").strip()
    if not client_id:
        print("[ERROR] client_id is required.")
        return 1

    secret = getpass.getpass(f"Plaid secret ({environment}): ").strip()
    if not secret:
        print("[ERROR] secret is required.")
        return 1

    # Run Plaid Link flow to get access_token
    print()
    print("Starting Plaid Link flow…")
    try:
        public_token = _run_link_flow(base_url, client_id, secret, environment)
    except TimeoutError as exc:
        print(f"[ERROR] {exc}")
        return 1
    except Exception as exc:
        print(f"[ERROR] Plaid Link failed: {exc}")
        return 1

    print("  Exchanging public_token for access_token…")
    try:
        access_token = _exchange_public_token(base_url, client_id, secret, public_token)
    except Exception as exc:
        print(f"[ERROR] Token exchange failed: {exc}")
        return 1

    # Verify
    try:
        inst = _verify_access(base_url, client_id, secret, access_token)
        print(f"  [OK] Access token verified. Institution: {inst}")
    except Exception as exc:
        print(f"  [WARN] Verification failed: {exc}")

    # Store credentials
    ok1 = _kr_set(*_KEYRING_CLIENT_ID,    client_id)
    ok2 = _kr_set(*_KEYRING_SECRET,       secret)
    ok3 = _kr_set(*_KEYRING_ACCESS_TOKEN, access_token)
    ok4 = _kr_set(*_KEYRING_ENVIRONMENT,  environment)

    if all((ok1, ok2, ok3, ok4)):
        print("  Credentials stored securely in system keyring.")
    else:
        print("  [WARN] keyring unavailable — set env vars manually:")
        print("    ARTHA_PLAID_CLIENT_ID, ARTHA_PLAID_SECRET, ARTHA_PLAID_ACCESS_TOKEN")

    print()
    print("═" * 60)
    print("  Plaid integration configured!")
    print()
    print("  Next steps:")
    print("    1. In config/connectors.yaml, set plaid.enabled: true")
    print("    2. Run: python artha.py --connector plaid --dry-run")
    print()
    print("  Privacy reminder: Only aggregated summaries are ever stored.")
    print("═" * 60)
    print()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Plaid financial connector for Artha.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify existing credentials without re-configuring",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear all stored credentials and re-run full setup",
    )
    parser.add_argument(
        "--env",
        choices=["sandbox", "development", "production"],
        default=None,
        help="Plaid environment (default: sandbox)",
    )
    args = parser.parse_args()

    if args.verify_only:
        sys.exit(run_verify_only())
    else:
        env = args.env or "sandbox"
        sys.exit(run_setup(environment=env, reset=args.reset))


if __name__ == "__main__":
    main()
