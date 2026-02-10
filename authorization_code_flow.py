#!/usr/bin/env python3
"""
Auth Code Flow (Confidential Client) with MSAL + client secret,
then call an EasyAuth-protected endpoint (/.auth/me or your API route).

Prereqs:
  pip install msal requests

Config:
  Fill the CONFIG section or use environment variables.
"""

import os
import sys
import json
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import msal
from dotenv import load_dotenv

load_dotenv(override=True)


# ========= CONFIG =========
TENANT_ID     = os.getenv("TENANT_ID",     "<YOUR_TENANT_ID>")
CLIENT_ID     = os.getenv("CLIENT_ID",     "<YOUR_CLIENT_ID>")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "<YOUR_CLIENT_SECRET>")
# Redirect URI must be present in your app registration (type: Web)
REDIRECT_URI  = os.getenv("REDIRECT_URI",  "http://localhost:53682/callback")

# Target API audience (Application ID URI) and scope(s)
# EITHER an explicit scope like 'api://<APP_ID>/user_impersonation'
# OR '.default' to use granted app permissions: 'api://<APP_ID>/.default'
SCOPE         = os.getenv("SCOPE",         "api://<APP_ID>/user_impersonation")

# Your EasyAuth-protected host (App Service / ACA / Functions with built-in auth)
RESOURCE_HOST = os.getenv("RESOURCE_HOST", "https://<your-host>")
# Endpoint to validate identity; use /.auth/me or your MCP route
TEST_PATH     = os.getenv("TEST_PATH",     "/.auth/me")
# ==========================

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
AUTHORIZE_URL = f"{AUTHORITY}/oauth2/v2.0/authorize"

# In-memory place to store the "code" captured by the local HTTP handler
_auth_code_bucket = {"code": None, "state": None, "error": None}
_code_received = threading.Event()

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != urllib.parse.urlparse(REDIRECT_URI).path:
                self.send_response(404); self.end_headers()
                self.wfile.write(b"Not Found")
                return
            params = urllib.parse.parse_qs(parsed.query)
            if "error" in params:
                _auth_code_bucket["error"] = params.get("error_description", ["Unknown error"])[0]
            else:
                _auth_code_bucket["code"] = params.get("code", [None])[0]
                _auth_code_bucket["state"] = params.get("state", [None])[0]

            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"You may close this tab and return to the terminal.")
            _code_received.set()
        except Exception as e:
            self.send_response(500)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(f"Callback error: {e}".encode("utf-8"))

def start_http_listener():
    url = urllib.parse.urlparse(REDIRECT_URI)
    host = url.hostname or "localhost"
    port = url.port or 53682  # default to 53682 instead of 80
    httpd = HTTPServer((host, port), CallbackHandler)
    print(f"[+] Listening for auth code on {host}:{port}{url.path} ...")
    httpd.handle_request()  # handle a single request then return

def build_auth_request_url(state: str):
    query = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "response_mode": "query",
        "scope": SCOPE if ".default" in SCOPE else f"openid profile offline_access {SCOPE}",
        "state": state
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}"

def main():
    # Basic sanity checks
    for key, val in [("TENANT_ID", TENANT_ID), ("CLIENT_ID", CLIENT_ID),
                     ("CLIENT_SECRET", CLIENT_SECRET), ("REDIRECT_URI", REDIRECT_URI),
                     ("SCOPE", SCOPE), ("RESOURCE_HOST", RESOURCE_HOST)]:
        if not val or val.startswith("<"):
            print(f"!!! Set {key} first")
            sys.exit(1)

    # Start local listener (in a thread) to capture the code
    t = threading.Thread(target=start_http_listener, daemon=True)
    t.start()

    # Open/print the authorize URL for interactive sign-in
    state = "msal-demo-state"
    auth_url = build_auth_request_url(state)
    print("\n[+] Open this URL in a browser and sign in:\n")
    print(auth_url + "\n")

    # Wait for the callback to populate _auth_code_bucket
    print("[*] Waiting for authorization code...")
    _code_received.wait(timeout=300)  # up to 5 minutes
    if _auth_code_bucket.get("error"):
        print(f"[-] Authorization error: {_auth_code_bucket['error']}")
        sys.exit(1)
    code = _auth_code_bucket.get("code")
    if not code:
        print("[-] Did not receive an authorization code (timeout?).")
        sys.exit(1)

    print("[+] Got authorization code. Exchanging for tokens...")

    # Create a confidential client and redeem the code
    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET
    )

    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=[s for s in SCOPE.split() if s not in ("openid", "profile", "offline_access")],
        redirect_uri=REDIRECT_URI
        # Note: If you use PKCE with a confidential client, pass code_verifier=...
    )

    if "access_token" not in result:
        error = result.get("error", "unknown_error")
        error_desc = result.get("error_description", "No description")
        print(f"[-] Token acquisition failed: {error} — {error_desc}")
        sys.exit(1)

    access_token = result["access_token"]

    # Test the token by calling the MCP server (JSON-RPC over HTTP)
    mcp_url = RESOURCE_HOST.rstrip('/')
    print(f"[+] Calling MCP server at {RESOURCE_HOST} ...")

    # Initialize the MCP session
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "oauth-test-client", "version": "0.1.0"}
        }
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    resp = requests.post(mcp_url, json=init_payload, headers=headers)
    print(f"[+] initialize → {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(resp.text)

    if resp.status_code == 200:
        # List available tools
        list_tools_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        resp2 = requests.post(mcp_url, json=list_tools_payload, headers=headers)
        print(f"\n[+] tools/list → {resp2.status_code}")
        try:
            print(json.dumps(resp2.json(), indent=2))
        except Exception:
            print(resp2.text)


if __name__ == "__main__":
    main()
