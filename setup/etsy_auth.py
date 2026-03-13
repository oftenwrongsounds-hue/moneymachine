"""
Etsy OAuth2 setup — opens browser, captures token via local Flask callback.
Saves ETSY_ACCESS_TOKEN and ETSY_REFRESH_TOKEN to .env and GitHub secrets.
Run once during initial setup.
"""
import os
import sys
import json
import webbrowser
import threading
import subprocess
import secrets
import hashlib
import base64
from urllib.parse import urlencode
import requests
from flask import Flask, request
from dotenv import load_dotenv, set_key

load_dotenv()

ETSY_AUTH_URL = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
REDIRECT_URI = "http://localhost:8081/etsy/callback"
SCOPE = "transactions_r transactions_w listings_r listings_w shops_r shops_rw"

app = Flask(__name__)
auth_code_holder = {"code": None, "state": None, "done": threading.Event()}
_state = None
_code_verifier = None


def _generate_pkce() -> tuple:
    """Generate PKCE code verifier and challenge."""
    verifier = secrets.token_urlsafe(43)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@app.route("/etsy/callback")
def etsy_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        auth_code_holder["done"].set()
        return f"<h1>Error: {error}</h1><p>Close this tab.</p>"

    if code:
        auth_code_holder["code"] = code
        auth_code_holder["state"] = state
        auth_code_holder["done"].set()
        return "<h1>Etsy auth complete!</h1><p>You can close this tab.</p>"
    return "<h1>No code received</h1>", 400


def run_flask(port: int = 8081):
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(port=port, debug=False, use_reloader=False)


def get_tokens(api_key: str) -> dict:
    """Run the Etsy OAuth PKCE flow and return token data."""
    global _state, _code_verifier
    _state = secrets.token_urlsafe(16)
    _code_verifier, code_challenge = _generate_pkce()

    params = {
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "client_id": api_key,
        "state": _state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{ETSY_AUTH_URL}?{urlencode(params)}"

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print(f"\nOpening Etsy authorization page...")
    print(f"Click 'Allow Access' to authorize the app.\n")
    webbrowser.open(auth_url)

    auth_code_holder["done"].wait(timeout=300)
    code = auth_code_holder.get("code")

    if not code:
        raise RuntimeError("Etsy OAuth timed out or was denied.")

    # Exchange code for token
    data = {
        "grant_type": "authorization_code",
        "client_id": api_key,
        "redirect_uri": REDIRECT_URI,
        "code": code,
        "code_verifier": _code_verifier,
    }
    response = requests.post(ETSY_TOKEN_URL, data=data, timeout=30)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Etsy token exchange failed: {response.text}") from e

    return response.json()


def main():
    print("=" * 60)
    print("Etsy OAuth Setup")
    print("=" * 60)

    api_key = os.environ.get("ETSY_API_KEY")
    if not api_key:
        print("\nERROR: ETSY_API_KEY not set in environment.")
        print("Get it at: https://www.etsy.com/developers")
        sys.exit(1)

    try:
        token_data = get_tokens(api_key)
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
        set_key(env_path, "ETSY_ACCESS_TOKEN", access_token)
        if refresh_token:
            set_key(env_path, "ETSY_REFRESH_TOKEN", refresh_token)
        print(f"\nSaved tokens to {env_path}")

    repo = os.environ.get("GITHUB_REPO")
    if repo:
        try:
            subprocess.run(
                ["gh", "secret", "set", "ETSY_ACCESS_TOKEN", "--body", access_token, "--repo", repo],
                check=True, capture_output=True,
            )
            if refresh_token:
                subprocess.run(
                    ["gh", "secret", "set", "ETSY_REFRESH_TOKEN", "--body", refresh_token, "--repo", repo],
                    check=True, capture_output=True,
                )
            print("Saved tokens to GitHub secrets")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: Could not save to GitHub secrets: {e}")

    print(f"\nEtsy auth complete!")
    print(f"Token expires in: {expires_in // 3600} hours")
    if refresh_token:
        print("Refresh token saved — token_manager.py will auto-renew")


if __name__ == "__main__":
    main()
