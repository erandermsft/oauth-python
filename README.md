# oauth-python — Agent Platform OBO Simulation

> **WARNING: This is a development/test utility only.**
> It stores secrets in plain text, runs an insecure local HTTP server, and prints tokens to the terminal.
> **Do NOT use this in production, staging, or any environment beyond local development.**

## What it does

A Python script that simulates an **Agent Platform → MCP Server → Downstream APIs** architecture using **OAuth 2.0 On-Behalf-Of (OBO)** with [MSAL for Python](https://github.com/AzureAD/microsoft-authentication-library-for-python).

The script walks through the full token chain:

1. **User login** — Starts a local HTTP server, opens an auth code flow URL. The user signs in and the script captures the authorization code, exchanging it for a token scoped to the Agent Platform's own app.
2. **Scope discovery** — Fetches `/.well-known/oauth-protected-resource` from the MCP server to discover the required scope (served automatically by [Azure App Service authentication for MCP](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp)). Falls back to `MCP_SCOPE` from `.env` if set.
3. **OBO exchange** — Exchanges the user's token for one scoped to the MCP server using the OBO grant.
4. **MCP tool calls** — Calls the MCP server with the OBO token, lists available tools, and provides an interactive prompt to invoke them.

For the full architecture details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Prerequisites

- **Python 3.12+**
- **[UV](https://docs.astral.sh/uv/)** (recommended) or pip
- **Two Microsoft Entra ID app registrations**:
  - **Agent Platform (client app)** — with a client secret, a Web redirect URI (`http://localhost:53682/callback`), and an exposed `user_impersonation` scope
  - **MCP Server (downstream API)** — with an exposed `user_impersonation` scope and the Agent Platform's client ID pre-authorized

## Setup

1. Clone this repo and `cd` into it.

2. Create a `.env` file with your values:

   | Variable | Description |
   |---|---|
   | `TENANT_ID` | Your Entra ID (Azure AD) tenant ID |
   | `CLIENT_ID` | Agent Platform app registration (client) ID |
   | `CLIENT_SECRET` | A client secret from the Agent Platform app registration |
   | `REDIRECT_URI` | Must match the redirect URI in the app registration (default: `http://localhost:53682/callback`) |
   | `SCOPE` | Scope for the initial user login, targeting the Agent Platform's own app (e.g. `<CLIENT_ID>/user_impersonation`) |
   | `RESOURCE_HOST` | The MCP server endpoint, e.g. `https://my-func.azurewebsites.net/runtime/webhooks/mcp` |
   | `MCP_SCOPE` | *(optional)* Scope for the OBO exchange to the MCP server. If not set, auto-discovered from the server's `/.well-known/oauth-protected-resource` metadata. |

3. Install dependencies:

   ```bash
   uv sync
   ```

## Usage

```bash
uv run python authorization_code_flow.py
```

1. The script prints an authorization URL — open it in your browser.
2. Sign in with your Microsoft account.
3. The script captures the auth code, exchanges it for a user token, performs the OBO exchange, and connects to the MCP server.
4. Available tools are listed. Enter a tool name or number to call it, or type `quit` to exit.

## Security notes

- **Secrets in `.env`** — never commit this file. It is listed in `.gitignore`.
- **Tokens printed to stdout** — for debugging only. Do not share terminal output.
- **HTTP (not HTTPS) redirect** — acceptable only for localhost development callbacks.
- **No PKCE** — this uses a confidential client with a client secret. For public clients, use PKCE instead.
