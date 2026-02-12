#!/usr/bin/env python3
"""
Agent Platform OBO Simulation
=============================
Simulates the Agent Platform → MCP Server → Downstream APIs architecture:

  Step 1: User login (auth code flow) → token with aud = Agent Platform's app
  Step 2: (Skipped) Scope discovery via /.well-known/oauth-protected-resource
  Step 3: OBO exchange → swap user token for MCP-server-scoped token
  Step 4: Call MCP server with the OBO token
  Step 5: (Server-side) MCP server does its own OBO for downstream APIs

Prereqs:
  pip install msal requests python-dotenv

Config:
  Set values in .env — see CONFIG section below.
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
CLIENT_ID     = os.getenv("CLIENT_ID",     "<YOUR_CLIENT_ID>")       # Agent Platform's app registration
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "<YOUR_CLIENT_SECRET>")
REDIRECT_URI  = os.getenv("REDIRECT_URI",  "http://localhost:53682/callback")

# Scope for the initial user login (aud = Agent Platform's own app)
SCOPE         = os.getenv("SCOPE",         "<CLIENT_APP_ID>/user_impersonation")

# Scope for the OBO exchange to the downstream MCP server (optional)
# If not set, the script will auto-discover it from the MCP server's
# /.well-known/oauth-protected-resource metadata endpoint
MCP_SCOPE     = os.getenv("MCP_SCOPE",     "")

# MCP server endpoint
RESOURCE_HOST = os.getenv("RESOURCE_HOST", "https://<your-mcp-host>")
# ==========================

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
AUTHORIZE_URL = f"{AUTHORITY}/oauth2/v2.0/authorize"

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
        "scope": SCOPE,
        "state": state
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}"

def parse_sse_or_json(resp):
    """Parse a response that may be plain JSON or SSE (event: message / data: {...})."""
    text = resp.text.strip()
    # Try plain JSON first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Parse SSE: look for lines starting with 'data:' and extract the JSON payload
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            try:
                return json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue
    # Fallback: return None so callers know parsing failed
    return None


def main():
    # Basic sanity checks
    for key, val in [("TENANT_ID", TENANT_ID), ("CLIENT_ID", CLIENT_ID),
                     ("CLIENT_SECRET", CLIENT_SECRET), ("REDIRECT_URI", REDIRECT_URI),
                     ("SCOPE", SCOPE), ("RESOURCE_HOST", RESOURCE_HOST)]:
        if not val or val.startswith("<"):
            print(f"!!! Set {key} first")
            sys.exit(1)

    # ── Step 1: User login (interactive auth code flow) ──────────────
    print("\n" + "=" * 60)
    print("STEP 1: User login (auth code flow)")
    print("=" * 60)

    # Start local listener (in a thread) to capture the code
    t = threading.Thread(target=start_http_listener, daemon=True)
    t.start()

    state = "agent-platform-obo-sim"
    auth_url = build_auth_request_url(state)
    print("\n[+] Open this URL in a browser and sign in:\n")
    print(auth_url + "\n")

    print("[*] Waiting for authorization code...")
    _code_received.wait(timeout=300)
    if _auth_code_bucket.get("error"):
        print(f"[-] Authorization error: {_auth_code_bucket['error']}")
        sys.exit(1)
    code = _auth_code_bucket.get("code")
    if not code:
        print("[-] Did not receive an authorization code (timeout?).")
        sys.exit(1)

    print("[+] Got authorization code. Exchanging for tokens...")

    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET
    )

    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=[s for s in SCOPE.split() if s not in ("openid", "profile", "offline_access")],
        redirect_uri=REDIRECT_URI
    )

    if "access_token" not in result:
        error = result.get("error", "unknown_error")
        error_desc = result.get("error_description", "No description")
        print(f"[-] Token acquisition failed: {error} — {error_desc}")
        sys.exit(1)

    user_token = result["access_token"]
    print(f"[+] Got user token (aud = Agent Platform app)")
    print(f"    Token preview: {user_token[:40]}...")

    # ── Step 2: Scope discovery ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 2: Scope discovery")
    print("=" * 60)

    mcp_scope = MCP_SCOPE
    if not mcp_scope:
        # Auto-discover from /.well-known/oauth-protected-resource
        base_host = RESOURCE_HOST.split("/runtime/")[0] if "/runtime/" in RESOURCE_HOST else RESOURCE_HOST.rstrip("/")
        metadata_url = f"{base_host}/.well-known/oauth-protected-resource"
        print(f"[*] Fetching metadata from {metadata_url} ...")
        try:
            meta_resp = requests.get(metadata_url)
            if meta_resp.status_code == 200:
                metadata = meta_resp.json()
                print(json.dumps(metadata, indent=2))
                # Extract the first scope from scopes_supported
                scopes = metadata.get("scopes_supported", [])
                # Filter out generic OIDC scopes
                api_scopes = [s for s in scopes if s not in ("openid", "profile", "email", "offline_access")]
                if api_scopes:
                    mcp_scope = api_scopes[0]
                    print(f"[+] Discovered MCP scope: {mcp_scope}")
                else:
                    print("[-] No API scopes found in metadata.")
                    sys.exit(1)
            else:
                print(f"[-] Metadata endpoint returned {meta_resp.status_code}")
                print(meta_resp.text)
                sys.exit(1)
        except Exception as e:
            print(f"[-] Failed to fetch metadata: {e}")
            sys.exit(1)
    else:
        print(f"[*] Using MCP_SCOPE from .env: {mcp_scope}")

    # ── Step 3: OBO exchange — swap user token for MCP-scoped token ──
    print("\n" + "=" * 60)
    print("STEP 3: OBO exchange (user token → MCP-scoped token)")
    print("=" * 60)

    obo_result = app.acquire_token_on_behalf_of(
        user_assertion=user_token,
        scopes=[mcp_scope]
    )

    if "access_token" not in obo_result:
        error = obo_result.get("error", "unknown_error")
        error_desc = obo_result.get("error_description", "No description")
        print(f"[-] OBO exchange failed: {error} — {error_desc}")
        sys.exit(1)

    mcp_token = obo_result["access_token"]
    print(f"[+] OBO exchange succeeded!")
    print(f"    MCP token preview: {mcp_token[:40]}...")

    # ── Step 4: Call MCP server with the OBO token ───────────────────
    print("\n" + "=" * 60)
    print("STEP 4: Call MCP server with OBO token")
    print("=" * 60)

    mcp_url = RESOURCE_HOST.rstrip('/')
    print(f"[+] MCP server: {mcp_url}")

    headers = {
        "Authorization": f"Bearer {mcp_token}",
        "Content-Type": "application/json"
    }

    # Initialize MCP session
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agent-platform-obo-sim", "version": "0.1.0"}
        }
    }
    resp = requests.post(mcp_url, json=init_payload, headers=headers)
    print(f"[+] initialize → {resp.status_code}")
    init_result = parse_sse_or_json(resp)
    if init_result:
        print(json.dumps(init_result, indent=2))
    else:
        print(resp.text)

    if resp.status_code != 200:
        sys.exit(1)

    # List available tools
    list_tools_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    resp2 = requests.post(mcp_url, json=list_tools_payload, headers=headers)
    print(f"\n[+] tools/list → {resp2.status_code}")
    tools = []
    tools_result = parse_sse_or_json(resp2)
    if tools_result:
        print(json.dumps(tools_result, indent=2))
        tools = tools_result.get("result", {}).get("tools", [])
    else:
        print(resp2.text)

    if not tools:
        print("[-] No tools available.")
        sys.exit(0)

    # Build a lookup of tool names for quick access
    tool_names = [t["name"] for t in tools]

    # Interactive tool-calling loop
    call_id = 3
    while True:
        print("\n" + "=" * 50)
        print("Available tools:")
        for i, name in enumerate(tool_names, 1):
            desc = next((t.get("description", "") for t in tools if t["name"] == name), "")
            print(f"  {i}. {name} — {desc}")
        print()
        choice = input("Enter tool name (or 'quit' to exit): ").strip()
        if choice.lower() in ("quit", "exit", "q", ""):
            print("[+] Done.")
            break

        # Allow selecting by number
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(tool_names):
                choice = tool_names[idx]
            else:
                print(f"[-] Invalid number. Choose 1-{len(tool_names)}.")
                continue

        if choice not in tool_names:
            print(f"[-] Unknown tool '{choice}'. Pick one from the list.")
            continue

        # Find the tool's input schema to prompt for arguments
        tool_def = next(t for t in tools if t["name"] == choice)
        input_schema = tool_def.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))

        arguments = {}
        if properties:
            print(f"\n[*] Tool '{choice}' expects the following parameters:")
            for param_name, param_info in properties.items():
                param_type = param_info.get("type", "string")
                param_desc = param_info.get("description", "")
                req_label = " (required)" if param_name in required else " (optional)"
                prompt_text = f"  {param_name} [{param_type}]{req_label}"
                if param_desc:
                    prompt_text += f" — {param_desc}"
                prompt_text += ": "
                val = input(prompt_text).strip()
                if val == "" and param_name not in required:
                    continue
                # Try to parse JSON values for non-string types
                if param_type in ("integer", "number"):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif param_type == "boolean":
                    val = val.lower() in ("true", "1", "yes")
                elif param_type in ("object", "array"):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        print(f"  [!] Could not parse as JSON, sending as string.")
                arguments[param_name] = val

        # Call the tool
        call_payload = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": choice,
                "arguments": arguments
            }
        }
        call_id += 1

        print(f"\n[+] Calling tool '{choice}'...")
        resp3 = requests.post(mcp_url, json=call_payload, headers=headers)
        print(f"[+] tools/call → {resp3.status_code}")
        call_result = parse_sse_or_json(resp3)
        if call_result:
            print(json.dumps(call_result, indent=2))
        else:
            print(resp3.text)


if __name__ == "__main__":
    main()
