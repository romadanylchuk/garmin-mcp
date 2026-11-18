"""Authentication: one-time interactive login and a lazily created client.

The password is only ever typed into `garmin-mcp-login` in your own terminal.
The MCP server itself never sees it: it only loads the token file that the
login command writes (``~/.garminconnect/garmin_tokens.json``, mode 0600).
"""

from __future__ import annotations

import getpass
import os
import sys
import threading
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

TOKEN_DIR = Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser()

_client: Garmin | None = None
_lock = threading.Lock()


class NotLoggedInError(RuntimeError):
    pass


def get_client() -> Garmin:
    """Return a logged-in client built from saved tokens (no password)."""
    global _client
    with _lock:
        if _client is not None:
            return _client
        if not TOKEN_DIR.exists():
            raise NotLoggedInError(
                f"No Garmin tokens in {TOKEN_DIR}. Run `garmin-mcp-login` once in a terminal."
            )
        client = Garmin()
        try:
            client.login(str(TOKEN_DIR))
        except GarminConnectAuthenticationError as e:
            raise NotLoggedInError(
                f"Saved Garmin tokens were rejected ({e}). Run `garmin-mcp-login` again."
            ) from e
        _client = client
        return client


def reset_client() -> None:
    global _client
    with _lock:
        _client = None


def login_cli() -> None:
    """Interactive one-time login. Supports MFA. Writes the token file."""
    print("Garmin Connect login (password is used once and never stored).")
    email = os.getenv("GARMIN_EMAIL") or input("Email: ").strip()
    password = getpass.getpass("Password: ")

    def prompt_mfa() -> str:
        return input("MFA code from Garmin (email/app): ").strip()

    TOKEN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    try:
        client.login(str(TOKEN_DIR))
    except GarminConnectAuthenticationError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)
    # login() dumps tokens when a tokenstore path is given; make sure anyway.
    try:
        client.client.dump(str(TOKEN_DIR))
    except Exception:
        pass
    name = getattr(client, "full_name", None) or getattr(client, "display_name", "")
    print(f"OK, logged in as {name}. Tokens saved to {TOKEN_DIR}")
    print("Treat that folder like a password. Revoke access in Garmin account security settings if leaked.")


if __name__ == "__main__":
    login_cli()
