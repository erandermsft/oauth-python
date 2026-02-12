# Agent Platform → MCP Server → Downstream APIs: OBO Architecture

## Overview

This project simulates the end-to-end OAuth 2.0 On-Behalf-Of (OBO) flow used when
an Agent Platform connects to MCP servers that in turn call downstream APIs — all on behalf
of the signed-in user, with no additional consent prompts at runtime.

```
┌──────────┐       ┌──────────────┐       ┌────────────┐       ┌──────────────┐
│          │  (1)  │              │  (3)   │            │  (5)  │              │
│   User   ├──────►│   Agent      ├───────►│ MCP Server ├──────►│  Downstream  │
│ (Browser)│ Login │  Platform    │  OBO   │ (Functions)│  OBO  │  APIs (Graph │
│          │       │              │ token  │            │ token │  AI, etc.)   │
└──────────┘       └──────────────┘        └────────────┘       └──────────────┘
```

## Step-by-Step Flow

| Step | Who | What | Interactive? |
|------|-----|------|:------------:|
| **1. User login** | User → Agent Platform | User authenticates via Entra ID. The resulting token has `aud` = Agent Platform's app ID. | Yes (once) |
| **2. Scope discovery** | Agent Platform → MCP Server | Agent Platform fetches `/.well-known/oauth-protected-resource` from each MCP server (served automatically by EasyAuth). Learns the required scope, e.g. `api://func-api-.../user_impersonation`. The script does this automatically when `MCP_SCOPE` is not set in `.env`. | No |
| **3. OBO exchange** | Agent Platform backend | Agent Platform calls Entra's token endpoint with the OBO grant, exchanging the user's token for one scoped to the specific MCP server. MSAL caches the result. | No (silent) |
| **4. MCP tool call** | Agent Platform → MCP Server | Agent Platform calls the MCP server with `Authorization: Bearer <mcp-scoped-token>`. EasyAuth validates the token. The MCP extension forwards headers into `ToolInvocationContext.Transport`. | No |
| **5. Downstream OBO** | MCP Server → Graph/etc. | `OboTokenService` extracts the bearer token from `HttpTransport.Headers`, creates `OnBehalfOfCredential`, and calls downstream APIs (Graph, Azure AI, etc.) on behalf of the original user. | No (silent) |

## Token Flow Diagram

```
User signs in (auth code flow)
        │
        ▼
┌─────────────────────────────┐
│  Token A                    │
│  aud = Agent Platform app ID│
│  scope = <app>/user_impers. │
└──────────────┬──────────────┘
               │ OBO exchange (Step 3)
               ▼
┌─────────────────────────────┐
│  Token B                    │
│  aud = MCP Server app ID    │
│  scope = api://<mcp>/...    │
└──────────────┬──────────────┘
               │ Sent to MCP Server (Step 4)
               │ MCP Server does OBO (Step 5)
               ▼
┌─────────────────────────────┐
│  Token C                    │
│  aud = downstream API       │
│  scope = e.g. Graph .default│
└─────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| **EasyAuth** (Azure App Service Auth) | Token validation, `/.well-known/oauth-protected-resource` metadata document — no custom code needed |
| **Entra ID app registration** (MCP server) | Exposes `user_impersonation` scope, has API permissions for downstream services, pre-authorizes Agent Platform's client ID |
| **OboTokenService** (your code in the MCP server) | Extracts bearer token from `ToolInvocationContext.Transport` headers, creates `OnBehalfOfCredential` for downstream API calls |
| **Admin consent** (one-time setup) | Agent Platform's app gets admin consent for each MCP server's scope. Each MCP server's app gets admin consent for downstream APIs. Eliminates all user consent prompts at runtime. |

## Entra ID App Registrations

You need **at minimum two** app registrations:

### 1. Agent Platform (Client App)

- **App ID**: The `CLIENT_ID` in `.env`
- **Client secret**: The `CLIENT_SECRET` in `.env`
- **Redirect URI**: `http://localhost:53682/callback` (for local dev)
- **API permissions**: Must have delegated permission to each MCP server's `user_impersonation` scope
- **Expose an API**: Exposes its own `user_impersonation` scope so the initial auth code flow can target itself (using the GUID-based identifier: `<CLIENT_ID>/user_impersonation`)

### 2. MCP Server (Downstream API)

- **App ID URI**: e.g. `api://func-api-...`
- **Expose an API**: Exposes `user_impersonation` scope
- **Pre-authorized clients**: Add Agent Platform's client ID to skip user consent
- **API permissions**: Delegated permissions for any downstream APIs (e.g. Microsoft Graph `User.Read`)
- **Admin consent**: Granted for all downstream API permissions

## Scaling to 20–30 MCP Servers

| Concern | How it's handled |
|---------|-----------------|
| **User experience** | User logs in once to the Agent Platform. Never sees another consent prompt. |
| **Token acquisition** | Agent Platform discovers each MCP server's required scope via its metadata document. Performs silent OBO for each. MSAL caches tokens per-user, per-resource. |
| **MCP server setup** | Each MCP server pre-authorizes Agent Platform's client ID (one-time admin setup). Uses `OboTokenService` for its own downstream calls. |
| **Consent** | Handled entirely by admin consent at deployment time, not at runtime. |

## Configuration (`.env`)

```env
# Entra ID tenant
TENANT_ID="<your-tenant-id>"

# Agent Platform app registration
CLIENT_ID="<agent-platform-client-id>"
CLIENT_SECRET="<librechat-client-secret>"
REDIRECT_URI="http://localhost:53682/callback"

# Scope for initial user login (targets LibreChat's own app using GUID)
SCOPE="<CLIENT_ID>/user_impersonation"

# MCP server endpoint
RESOURCE_HOST="https://func-api-xxx.azurewebsites.net/runtime/webhooks/mcp"

# Scope for OBO exchange to the MCP server
# Leave unset to auto-discover from /.well-known/oauth-protected-resource
# MCP_SCOPE="api://func-api-xxx/user_impersonation"
```

> **Note:** The initial `SCOPE` uses the raw GUID (no `api://` prefix) because Entra ID
> requires the GUID-based identifier when an app requests a token for itself
> (error AADSTS90009).

## Running the Simulation

```bash
uv run python authorization_code_flow.py
```

1. The script opens a browser for user sign-in (Step 1)
2. Exchanges the auth code for a token scoped to LibreChat's app
3. Performs the OBO exchange to get a token scoped to the MCP server (Step 3)
4. Calls the MCP server's JSON-RPC endpoint with the OBO token (Step 4)
5. Lists available tools and lets you interactively call them
