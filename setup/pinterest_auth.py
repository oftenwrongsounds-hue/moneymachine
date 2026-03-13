"""
Pinterest OAuth2 setup — opens browser, captures token via local Flask callback.
Saves PINTEREST_ACCESS_TOKEN to .env and GitHub secrets.
Run once during initial setup.
"""
import os
import sys
import json
import webbrowser
import threading
import subprocess
import secrets
from urllib.parse import urlencode
import requests
from flask import Flask, request
from dotenv import load_dotenv, set_key

load_dotenv()

PINTEREST_AUTH_URL = "https://www.pinterest.com/oauth/"
PINTEREST_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
REDIRECT_URI = "http://localhost:8082/pinterest/callback"
SCOPE = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"

app = Flask(__name__)
auth_holder = {"code": None, "done": threading.Event()}
_state = None


@app.route("/pinterest/callback")
def pinterest_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        auth_holder["done"].set()
        return f"<h1>Error: {error}</h1><p>Close this tab.</p>"

    if code:
        auth_holder["code"] = code
        auth_holder["done"].set()
        return "<h1>Pinterest auth complete!</h1><p>You can close this tab.</p>"

    return "<h1>No code received</h1>", 400


def run_flask(port: int = 8082):
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(port=port, debug=False, use_reloader=False)


def get_tokens(app_id: str, app_secret: str) -> dict:
    """Run the Pinterest OAuth flow and return token data."""
    global _state
    _state = secrets.token_urlsafe(16)

    params = {
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "state": _state,
    }
    auth_url = f"{PINTEREST_AUTH_URL}?{urlencode(params)}"

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print(f"\nOpening Pinterest authorization page...")
    print("Click 'Allow' to authorize the app.\n")
    webbrowser.open(auth_url)

    auth_holder["done"].wait(timeout=300)
    code = auth_holder.get("code")

    if not code:
        raise RuntimeError("Pinterest OAuth timed out or was denied.")

    # Exchange code for token
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    response = requests.post(
        PINTEREST_TOKEN_URL,
        data=data,
        auth=(app_id, app_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Pinterest token exchange failed: {response.text}") from e

    return response.json()


def main():
    print("=" * 60)
    print("Pinterest OAuth Setup")
    print("=" * 60)

    app_id = os.environ.get("PINTEREST_APP_ID")
    app_secret = os.environ.get("PINTEREST_APP_SECRET")

    if not all([app_id, app_secret]):
        print("\nERROR: Missing Pinterest credentials.")
        print("Required: PINTEREST_APP_ID, PINTEREST_APP_SECRET")
        print("Get them at: https://developers.pinterest.com")
        sys.exit(1)

    try:
        token_data = get_tokens(app_id, app_secret)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 0)

    if not access_token:
        print("ERROR: No access token in response.")
        print("Response:", json.dumps(token_data, indent=2))
        sys.exit(1)

    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        set_key(env_path, "PINTEREST_ACCESS_TOKEN", access_token)
        if refresh_token:
            set_key(env_path, "PINTEREST_REFRESH_TOKEN", refresh_token)
        print(f"\nSaved tokens to {env_path}")

    repo = os.environ.get("GITHUB_REPO")
    if repo:
        try:
            subprocess.run(
                ["gh", "secret", "set", "PINTEREST_ACCESS_TOKEN", "--body", access_token, "--repo", repo],
                check=True, capture_output=True,
            )
            if refresh_token:
                subprocess.run(
                    ["gh", "secret", "set", "PINTEREST_REFRESH_TOKEN", "--body", refresh_token, "--repo", repo],
                    check=True, capture_output=True,
                )
            print("Saved tokens to GitHub secrets")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: Could not save to GitHub secrets: {e}")

    print(f"\nPinterest auth complete!")
    print(f"Token expires in: {expires_in // 3600} hours")
    if refresh_token:
        print("Refresh token saved — token_manager.py will auto-renew")


if __name__ == "__main__":
    main()
