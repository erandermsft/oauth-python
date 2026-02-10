# oauth-python — OAuth Authorization Code Flow Test Script

> **WARNING: This is a development/test utility only.**
> It stores secrets in plain text, runs an insecure local HTTP server, and prints tokens to the terminal.
> **Do NOT use this in production, staging, or any environment beyond local development.**

## What it does

A simple Python script that walks through the full **OAuth 2.0 Authorization Code Flow** (confidential client) using [MSAL for Python](https://github.com/AzureAD/microsoft-authentication-library-for-python). It is designed to test that a token issued by Microsoft Entra ID (Azure AD) can successfully authenticate against an API protected by EasyAuth or similar (e.g., an MCP server on Azure Functions).

The script:

1. Starts a temporary local HTTP server to capture the redirect callback.
2. Prints an authorization URL — you open it in a browser and sign in.
3. Receives the authorization code via the redirect.
4. Exchanges the code for an access token using MSAL (confidential client with client secret).
5. Calls your target API (e.g., an MCP server) with the Bearer token and prints the response.

## Prerequisites

- **Python 3.12+**
- **[UV](https://docs.astral.sh/uv/)** (recommended) or pip
- A **Microsoft Entra ID app registration** with:
  - A **client secret** configured
  - A **Web** redirect URI set to `http://localhost:53682/callback`
  - An API scope exposed (e.g., `user_impersonation`) or use `.default`

## Setup

1. Clone this repo and `cd` into it.

2. Copy the example environment variables and fill in your values:

   ```bash
   cp .env.example .env   # or create .env manually
   ```

   Required `.env` values:

   | Variable | Description |
   |---|---|
   | `TENANT_ID` | Your Entra ID (Azure AD) tenant ID |
   | `CLIENT_ID` | App registration Application (client) ID |
   | `CLIENT_SECRET` | A client secret from the app registration |
   | `REDIRECT_URI` | Must match the redirect URI in the app registration (default: `http://localhost:53682/callback`) |
   | `SCOPE` | The API scope to request, e.g. `api://<APP_ID>/user_impersonation` or `api://<APP_ID>/.default` |
   | `RESOURCE_HOST` | The full URL of the API endpoint to test against, e.g. `https://my-func.azurewebsites.net/runtime/webhooks/mcp` |

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
3. After sign-in, the browser redirects to `localhost:53682/callback` and the script captures the auth code.
4. The script exchanges the code for an access token and calls your target API.
5. The response (including any MCP tool listings) is printed to the terminal.

## Security notes

- **Secrets in `.env`** — never commit this file. It is listed in `.gitignore`.
- **Tokens printed to stdout** — for debugging only. Do not share terminal output.
- **HTTP (not HTTPS) redirect** — acceptable only for localhost development callbacks.
- **No PKCE** — this uses a confidential client with a client secret. For public clients, use PKCE instead.
