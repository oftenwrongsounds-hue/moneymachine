"""
eBay OAuth2 setup — opens browser, captures token via local Flask callback.
Saves EBAY_USER_TOKEN to .env and optionally to GitHub secrets.
Run once during initial setup.
"""
import os
import sys
import json
import webbrowser
import threading
import subprocess
from urllib.parse import urlencode, urlparse, parse_qs
import requests
from flask import Flask, request, redirect
from dotenv import load_dotenv, set_key

load_dotenv()

EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
REDIRECT_URI = "http://localhost:8080/ebay/callback"
SCOPE = "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.fulfillment"

app = Flask(__name__)
auth_code_holder = {"code": None, "done": threading.Event()}


@app.route("/ebay/callback")
def ebay_callback():
    code = request.args.get("code")
    error = request.args.get("error")
    if error:
        auth_code_holder["code"] = None
        auth_code_holder["done"].set()
        return f"<h1>Error: {error}</h1><p>Close this tab and check the terminal.</p>"
    if code:
        auth_code_holder["code"] = code
        auth_code_holder["done"].set()
        return "<h1>eBay auth complete!</h1><p>You can close this tab.</p>"
    return "<h1>No code received</h1>", 400


def run_flask(port: int = 8080):
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(port=port, debug=False, use_reloader=False)


def get_user_token(app_id: str, cert_id: str, dev_id: str) -> dict:
    """Run the OAuth flow and return token data."""
    params = {
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "prompt": "login",
    }
    auth_url = f"{EBAY_AUTH_URL}?{urlencode(params)}"

    # Start Flask in background
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print(f"\nOpening eBay authorization page...")
    print(f"URL: {auth_url}")
    print("Click 'I agree' to authorize the app.\n")
    webbrowser.open(auth_url)

    # Wait for callback
    auth_code_holder["done"].wait(timeout=300)
    code = auth_code_holder.get("code")

    if not code:
        raise RuntimeError("eBay OAuth timed out or was denied.")

    # Exchange code for token
    import base64
    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    response = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=30)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"eBay token exchange failed: {response.text}") from e

    token_data = response.json()
    return token_data


def main():
    print("=" * 60)
    print("eBay OAuth Setup")
    print("=" * 60)

    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    dev_id = os.environ.get("EBAY_DEV_ID")

    if not all([app_id, cert_id, dev_id]):
        print("\nERROR: Missing eBay credentials in environment.")
        print("Required: EBAY_APP_ID, EBAY_CERT_ID, EBAY_DEV_ID")
        print("Get these at: https://developer.ebay.com")
        sys.exit(1)

    try:
        token_data = get_user_token(app_id, cert_id, dev_id)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    user_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 0)

    if not user_token:
        print("ERROR: No access token in response.")
        print("Response:", json.dumps(token_data, indent=2))
        sys.exit(1)

    # Save to .env
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        set_key(env_path, "EBAY_USER_TOKEN", user_token)
        if refresh_token:
            set_key(env_path, "EBAY_REFRESH_TOKEN", refresh_token)
        print(f"\nSaved EBAY_USER_TOKEN to {env_path}")

    # Save to GitHub secrets if gh CLI available
    repo = os.environ.get("GITHUB_REPO")
    if repo:
        try:
            subprocess.run(
                ["gh", "secret", "set", "EBAY_USER_TOKEN", "--body", user_token, "--repo", repo],
                check=True, capture_output=True,
            )
            print("Saved EBAY_USER_TOKEN to GitHub secrets")
            if refresh_token:
                subprocess.run(
                    ["gh", "secret", "set", "EBAY_REFRESH_TOKEN", "--body", refresh_token, "--repo", repo],
                    check=True, capture_output=True,
                )
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not save to GitHub secrets: {e.stderr.decode()}")
        except FileNotFoundError:
            print("Warning: gh CLI not found — token saved to .env only")

    print(f"\neBay auth complete!")
    print(f"Token expires in: {expires_in // 3600} hours")
    if refresh_token:
        print("Refresh token saved — token_manager.py will auto-renew")


if __name__ == "__main__":
    main()
