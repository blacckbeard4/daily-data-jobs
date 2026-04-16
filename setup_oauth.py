"""
One-time LinkedIn OAuth 2.0 setup script.

Run this manually once to authorise the app and save encrypted tokens:
    python setup_oauth.py

What it does:
1. Builds the LinkedIn OAuth consent URL
2. Opens your browser to the consent page
3. Starts a local HTTP server on port 8000 to catch the callback
4. Exchanges the auth code for access + refresh tokens
5. Encrypts and saves them to storage/tokens.json
6. Prints expiry dates so you know when to re-run

Required .env variables:
    LINKEDIN_CLIENT_ID
    LINKEDIN_CLIENT_SECRET
    TOKEN_ENCRYPTION_KEY   (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
"""

import base64
import json
import os
import secrets
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from config.settings import (
    OAUTH_CALLBACK_PORT,
    OAUTH_CALLBACK_URI,
    OAUTH_SCOPES,
    LINKEDIN_AUTH_URL,
    LINKEDIN_TOKEN_REFRESH_URL,
    TOKENS_PATH,
)

load_dotenv()


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def generate_auth_url(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
) -> str:
    """Build the LinkedIn OAuth 2.0 authorization URL."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
    }
    return f"{LINKEDIN_AUTH_URL}?{urllib.parse.urlencode(params)}"


class _CallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that captures the OAuth callback once."""

    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = dict(urllib.parse.parse_qsl(parsed.query))
            _CallbackHandler.captured = params
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorisation successful!</h2>"
                b"<p>You can close this tab and return to your terminal.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: Any) -> None:  # suppress access log noise
        pass


def run_local_callback_server(port: int = OAUTH_CALLBACK_PORT) -> dict[str, str]:
    """
    Start a one-shot local HTTP server.
    Blocks until /callback is hit, then shuts down.
    Returns the parsed query parameters (code, state).
    """
    server = HTTPServer(("localhost", port), _CallbackHandler)
    print(f"[setup_oauth] Listening for OAuth callback on http://localhost:{port}/callback …")
    while not _CallbackHandler.captured:
        server.handle_request()
    server.server_close()
    return _CallbackHandler.captured


def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Exchange the auth code for access + refresh tokens via LinkedIn API."""
    resp = requests.post(
        LINKEDIN_TOKEN_REFRESH_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def compute_expiry_timestamps(token_response: dict) -> dict:
    """
    Compute absolute expiry datetimes from the token response.

    LinkedIn returns:
        expires_in               — access token TTL in seconds (~5184000 = 60 days)
        refresh_token_expires_in — refresh token TTL in seconds (~31536000 = 365 days)

    Note: refresh_token is only returned when offline_access scope is granted.
    If absent, refresh_expires_at is set to match access_expires_at.
    """
    now = datetime.now(timezone.utc)
    access_ttl = int(token_response.get("expires_in", 5184000))
    refresh_ttl = int(token_response.get("refresh_token_expires_in", access_ttl))
    refresh_token = token_response.get("refresh_token") or token_response["access_token"]
    return {
        "access_token": token_response["access_token"],
        "refresh_token": refresh_token,
        "access_expires_at": (now + timedelta(seconds=access_ttl)).isoformat(),
        "refresh_expires_at": (now + timedelta(seconds=refresh_ttl)).isoformat(),
    }


# ---------------------------------------------------------------------------
# Encryption helpers (also imported by linkedin_auth.py)
# ---------------------------------------------------------------------------

def _get_fernet(encryption_key: str) -> Fernet:
    """
    Return a Fernet instance from the provided key string.
    Accepts both raw Fernet keys (44-char base64) and plain strings
    (will be padded/truncated to 32 bytes and base64-encoded).
    """
    key_bytes = encryption_key.encode()
    # If it's already a valid Fernet key (44 chars of URL-safe base64), use as-is
    try:
        return Fernet(key_bytes)
    except Exception:
        # Derive a 32-byte key from whatever was provided
        padded = key_bytes[:32].ljust(32, b"\0")
        fernet_key = base64.urlsafe_b64encode(padded)
        return Fernet(fernet_key)


def encrypt_and_save_tokens(
    token_data: dict,
    encryption_key: str,
    path: str = TOKENS_PATH,
) -> None:
    """Encrypt token_data with Fernet and save to path as JSON."""
    fernet = _get_fernet(encryption_key)
    plaintext = json.dumps(token_data).encode()
    encrypted = fernet.encrypt(plaintext).decode()
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"encrypted": encrypted}, f, indent=2)


def decrypt_tokens(encryption_key: str, path: str = TOKENS_PATH) -> dict:
    """Load and decrypt tokens from path. Raises FileNotFoundError if missing."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Tokens file not found at '{path}'. Run python setup_oauth.py first."
        )
    with open(path) as f:
        wrapper = json.load(f)

    if not wrapper:
        raise ValueError(f"Tokens file at '{path}' is empty. Run python setup_oauth.py.")

    encrypted = wrapper.get("encrypted")
    if not encrypted:
        raise ValueError(f"Tokens file at '{path}' has unexpected format.")

    fernet = _get_fernet(encryption_key)
    plaintext = fernet.decrypt(encrypted.encode())
    return json.loads(plaintext)


# ---------------------------------------------------------------------------
# Person URN fetch + .env writer
# ---------------------------------------------------------------------------

def fetch_person_urn(access_token: str) -> str:
    """
    Call the LinkedIn /v2/userinfo endpoint (OpenID Connect) to retrieve
    the authenticated user's ID and return it as: urn:li:person:{sub}
    """
    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    person_id = data.get("sub")
    if not person_id:
        raise ValueError(f"LinkedIn /v2/userinfo did not return a 'sub' field. Response: {data}")
    return f"urn:li:person:{person_id}"


def write_env_value(env_path: str, key: str, value: str) -> None:
    """
    Update a single KEY=value line in the .env file in-place.
    If the key already has a value it is overwritten.
    If the key is missing entirely it is appended.
    """
    if not os.path.exists(env_path):
        with open(env_path, "w") as f:
            f.write(f"{key}={value}\n")
        return

    with open(env_path) as f:
        lines = f.readlines()

    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main() -> None:
    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "")
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
    encryption_key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

    # Locate .env relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")

    if not client_id or not client_secret:
        print(
            "[setup_oauth] ERROR: LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET "
            "must be set in your .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not encryption_key:
        print(
            "[setup_oauth] ERROR: TOKEN_ENCRYPTION_KEY must be set in your .env file.\n"
            "Generate one with:\n"
            "    python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"",
            file=sys.stderr,
        )
        sys.exit(1)

    state = secrets.token_urlsafe(16)
    auth_url = generate_auth_url(client_id, OAUTH_CALLBACK_URI, OAUTH_SCOPES, state)

    print(f"\n[setup_oauth] Opening browser to LinkedIn OAuth consent page …")
    print(f"[setup_oauth] If the browser doesn't open, visit:\n    {auth_url}\n")
    webbrowser.open(auth_url)

    callback_params = run_local_callback_server(OAUTH_CALLBACK_PORT)

    # Security: verify state parameter
    if callback_params.get("state") != state:
        print(
            "[setup_oauth] ERROR: State mismatch — possible CSRF. Aborting.",
            file=sys.stderr,
        )
        sys.exit(1)

    code = callback_params.get("code")
    if not code:
        print(
            f"[setup_oauth] ERROR: No auth code in callback. Params: {callback_params}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("[setup_oauth] Exchanging auth code for tokens …")
    raw_tokens = exchange_code_for_tokens(code, OAUTH_CALLBACK_URI, client_id, client_secret)
    token_data = compute_expiry_timestamps(raw_tokens)

    print("[setup_oauth] Fetching LinkedIn person URN …")
    person_urn = fetch_person_urn(token_data["access_token"])
    print(f"[setup_oauth] Person URN: {person_urn}")

    print(f"[setup_oauth] Writing LINKEDIN_PERSON_URN to {env_path} …")
    write_env_value(env_path, "LINKEDIN_PERSON_URN", person_urn)

    print(f"[setup_oauth] Encrypting and saving tokens to {TOKENS_PATH} …")
    encrypt_and_save_tokens(token_data, encryption_key, TOKENS_PATH)

    access_exp = token_data["access_expires_at"]
    refresh_exp = token_data["refresh_expires_at"]
    print(
        f"\n[setup_oauth] ✓ Setup complete!\n"
        f"    Person URN:            {person_urn}\n"
        f"    Access token expires:  {access_exp}\n"
        f"    Refresh token expires: {refresh_exp}\n"
        f"\nLINKEDIN_PERSON_URN has been written to your .env file automatically.\n"
        f"The pipeline will auto-refresh the access token every 55 days.\n"
        f"The refresh token expires in 365 days — you will receive an alert "
        f"email 15 days before expiry.\n"
    )


if __name__ == "__main__":
    main()
