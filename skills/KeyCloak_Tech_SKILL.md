# Keycloak Implementation Reference

> **Source**: `a2a_orchestrator` (Project_Guru) — a multi-agent system using the A2A protocol.
> **Purpose**: A blueprint for implementing the same Keycloak-based authentication pattern in other applications. Read this top-to-bottom, then map each section onto your target app's structure.

---

## 1. The Pattern at a Glance

This application secures **service-to-service** (agent-to-agent) traffic with Keycloak using the **OAuth 2.0 client_credentials grant** and **JWT bearer tokens verified via JWKS**.

A second, parallel middleware handles **human-facing API endpoints** using a different identity provider (Accenture IAM short-tokens). The two coexist on the same app via path-based routing.

### Two-Middleware Architecture

| Middleware | Class | Purpose | Validates |
|---|---|---|---|
| **Inbound (humans)** | `IAMAuthMiddleware` | Protects explicit HTTP API endpoints (e.g. `/file_upload`) | IAM short-tokens |
| **Inbound (services)** | `KeycloakA2AMiddleware` | Protects A2A/MCP protocol mounts | Keycloak JWTs (via JWKS) |
| **Outbound (services)** | `KeycloakA2AAuth` (`httpx.Auth`) | Injects bearer token on outbound A2A calls | n/a — issues tokens |

The same files are present in **every agent** (mcp_server, rag_agent, eval_agent, host_agent, requirement_extractor, userstory_generator, excel_generator, deployment_agent, business_specification_agent, rai_agent, etc.):

```
<agent>/
├── a2a_auth.py        ← Keycloak: middleware + httpx auth handler
├── auth_utils.py      ← IAM: middleware + token validator
└── config.ini         ← [auth.keycloak] + [auth] + [iam] blocks
```

---

## 2. Configuration Block (`config.ini`)

Every agent reads the same block from its `config.ini`:

```ini
[auth.keycloak]
isAuthEnabled = true
domain        = {MyWizardWebAPIBaseUrl}
client_id     = {MyWizardGenWizardKeyCloakAPIClientID}
client_secret = {MyWizardGenWizardKeyCloakAPIClientSecret}
url           = {MyWizardGenWizardKeycloakBaseUrl}
realm         = {MyWizardGenWizardKeyCloakrealmurl}
```

| Key | Meaning |
|---|---|
| `isAuthEnabled` | Master switch — set `false` to bypass all Keycloak checks (dev only) |
| `url` | Keycloak base URL (e.g. `https://keycloak.myapp.com`) |
| `realm` | Realm name (e.g. `myapp`) |
| `client_id` | The client this agent authenticates **as** when calling others |
| `client_secret` | Paired with `client_id` for `client_credentials` grant |
| `domain` | The agent's own public base URL (used by OAuthProxy where applicable) |

**Placeholder substitution**: values wrapped in `{...}` are resolved at deploy time by an encrypted config loader (`file_encryptor_GCM`). In a new app, replace these with env vars or a secrets-manager fetch.

---

## 3. Inbound Verification — `KeycloakA2AMiddleware`

A Starlette `BaseHTTPMiddleware` that verifies the `Authorization: Bearer <JWT>` header on every incoming request.

### 3.1 Key design choices

- **JWKS-based local verification** (not token introspection) — fast, scalable, offline after first JWKS fetch
- **Lazy JWKS client** — `PyJWKClient` is created once and caches public keys internally
- **Path-aware** — `skip_paths` defers explicit API routes to the IAM middleware
- **Exempt well-known paths** — `.well-known/agent.json` is unauthenticated so agents can discover each other
- **Decoded claims stashed at `request.state.auth_claims`** for downstream handlers

### 3.2 Core code (from `a2a_auth.py`)

```python
from jwt import PyJWKClient
import jwt as pyjwt

_jwks_client = None

def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None and KEYCLOAK_URL and KEYCLOAK_REALM:
        jwks_uri = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
        _jwks_client = PyJWKClient(jwks_uri)
    return _jwks_client


def verify_keycloak_jwt(token: str) -> dict | None:
    client = _get_jwks_client()
    if not client:
        return None
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS384", "RS512"],
            options={"verify_aud": False, "verify_iss": False},  # ← see Hardening
        )
        return claims
    except pyjwt.ExpiredSignatureError:
        logger.warning("A2A auth: token expired")
    except pyjwt.InvalidTokenError as exc:
        logger.warning("A2A auth: invalid token — %s", exc)
    return None


_EXEMPT_PATHS = frozenset({
    "/.well-known/agent.json",
    "/.well-known/agent-card.json",
})


class KeycloakA2AMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, skip_paths: list[str] | None = None):
        super().__init__(app)
        self._skip_paths = tuple(skip_paths or [])

    async def dispatch(self, request, call_next):
        if not A2A_AUTH_ENABLED or not all([KEYCLOAK_URL, KEYCLOAK_REALM]):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        if self._skip_paths and any(
            request.url.path.startswith(p) for p in self._skip_paths
        ):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or invalid Authorization header"},
                status_code=401,
            )

        token = auth_header[len("Bearer "):]
        claims = verify_keycloak_jwt(token)
        if claims is None:
            return JSONResponse(
                {"error": "Invalid or expired Keycloak token"},
                status_code=401,
            )

        request.state.auth_claims = claims
        return await call_next(request)
```

### 3.3 What happens on a request

```
Request arrives at agent
  ↓
Is auth disabled?                     → pass through
Is it OPTIONS (CORS preflight)?       → pass through
Is path in _EXEMPT_PATHS?             → pass through
Does path match skip_paths prefix?    → pass through (IAM handles it)
Missing/malformed Authorization?      → 401
verify_keycloak_jwt() returns None?   → 401
                                      ↓
Stash claims on request.state, call_next()
```

---

## 4. Outbound Auth — `KeycloakA2AAuth` (`httpx.Auth` handler)

Outbound calls automatically pick up a cached service token. **Application code never manages tokens directly.**

### 4.1 Core code

```python
import time, httpx

_cached_token: str | None = None
_token_expiry: float = 0


async def get_a2a_service_token_async() -> str | None:
    global _cached_token, _token_expiry
    if not A2A_AUTH_ENABLED:
        return None
    if _cached_token and time.time() < _token_expiry:
        return _cached_token                                # ← cache hit

    token_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(token_url, data={
            "grant_type":    "client_credentials",
            "client_id":     KEYCLOAK_CLIENT_ID,
            "client_secret": KEYCLOAK_CLIENT_SECRET,
        })
    if resp.status_code == 200:
        data = resp.json()
        _cached_token = data["access_token"]
        _token_expiry = time.time() + data.get("expires_in", 300) - 30  # ← 30s safety margin
        return _cached_token
    return None


class KeycloakA2AAuth(httpx.Auth):
    """httpx Auth handler — auto-injects Keycloak Bearer token on every request."""

    def auth_flow(self, request):
        token = get_a2a_service_token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request

    async def async_auth_flow(self, request):
        token = await get_a2a_service_token_async()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request
```

### 4.2 Usage in caller code

```python
from a2a_auth import KeycloakA2AAuth

self._client = httpx.AsyncClient(timeout=1000, auth=KeycloakA2AAuth())
self._a2a_client = A2AClient(self._client, agent_card, url=agent_url)
```

**That's the entire integration.** Every request `self._client` makes carries a fresh, cached Keycloak token without further code.

### 4.3 Caching behaviour

- Module-global `_cached_token` and `_token_expiry`
- Token refreshed when `time.time() > _token_expiry`
- 30-second safety margin before actual expiry — prevents using a token that expires mid-flight
- **Caveat**: per-process cache. In a multi-replica deployment, each replica fetches its own token.

---

## 5. Wiring the Middlewares (Order Matters)

From an agent's `main.py`:

```python
from auth_utils import IAMAuthMiddleware
from a2a_auth   import KeycloakA2AMiddleware

# Human/API traffic → IAM. Machine/A2A traffic → Keycloak.
http_app.add_middleware(IAMAuthMiddleware, api_paths=["/file_upload"])
http_app.add_middleware(KeycloakA2AMiddleware, skip_paths=["/file_upload"])
```

### 5.1 How the routing works

| Request path | IAM middleware | Keycloak middleware |
|---|---|---|
| `/file_upload/...` | ✅ validates IAM token | ⏭ skipped via `skip_paths` |
| `/adlc_agentcreator/...` (MCP mount) | ⏭ only validates listed `api_paths` | ✅ validates Keycloak JWT |
| `/.well-known/agent.json` | ⏭ skipped | ⏭ exempt |
| `/health`, `/docs`, `/openapi.json` | ⏭ default-excluded | ⏭ exempt |

Both middlewares are *defensive*: if their config is incomplete or `isAuthEnabled = false`, they fall through to `call_next()` — they never crash the app.

---

## 6. End-to-End Flow

```
┌──────────────────┐  POST /token            ┌──────────────────┐
│  Host Agent      │  grant=client_creds     │   Keycloak       │
│  (httpx +        │ ──────────────────────▶ │   /realms/myapp  │
│   KeycloakAuth)  │ ◀── access_token ────── │                  │
└────────┬─────────┘   (cached ~5 min)       └────────┬─────────┘
         │                                            │
         │ POST /rag_agent/                           │ GET /certs
         │ Authorization: Bearer <JWT>                │ (once, cached)
         ▼                                            ▼
┌──────────────────────────┐  verify locally  ┌──────────────────┐
│  RAG Agent               │  with public key │  JWKS endpoint   │
│  KeycloakA2AMiddleware   │ ◀──────────────  │                  │
│  → handler               │                  └──────────────────┘
└──────────────────────────┘
```

**Key insight**: Keycloak is **not in the request hot path**. After the initial token fetch (caller side, ~5 min TTL) and JWKS fetch (callee side, once at startup), every agent-to-agent call is verified purely via local cryptography.

---

## 7. Per-Agent Replication

Every agent in this codebase contains the *same* `a2a_auth.py` and `auth_utils.py` (copy-paste pattern). New agents follow the same recipe:

1. **Drop in** `a2a_auth.py` and `auth_utils.py` from a sibling agent
2. **Add the `[auth.keycloak]` block** to the agent's `config.ini` (same keys, same placeholders)
3. **Wire the middlewares** in the agent's `main.py` / `__main__.py`:
   ```python
   http_app.add_middleware(IAMAuthMiddleware, api_paths=[...])
   http_app.add_middleware(KeycloakA2AMiddleware, skip_paths=[...])
   ```
4. **For outbound calls** to other agents, wrap the httpx client:
   ```python
   client = httpx.AsyncClient(auth=KeycloakA2AAuth())
   ```

> ⚠ **Known weakness of this approach**: every agent has its own copy. Bug fixes and improvements must be propagated to all copies manually. **Recommendation for new apps** → extract these modules into a shared internal package (see §10).

---

## 8. The MCP Server Variant — `CombinedAuthProvider`

The `mcp_server` (and `new_host_agent`) uses an additional pattern via FastMCP's `OAuthProxy` to support **both** browser-based OAuth flow (for tools like Copilot Chat) **and** direct client_credentials JWTs (for service-to-service).

From `new_host_agent/utils/auth.py`:

```python
class CombinedAuthProvider(AuthProvider):
    """
    Token verification order:
      1. OAuthProxy (FastMCP JWT from browser OAuth flow)
      2. Fallback: JWTVerifier (direct Keycloak client_credentials token)
    """
    def __init__(self, oauth_proxy: OAuthProxy, jwt_verifier: JWTVerifier):
        ...
    async def verify_token(self, token):
        result = await self._oauth_proxy.verify_token(token)
        if result is not None:
            return result
        return await self._jwt_verifier.verify_token(token)
```

Use this variant only if the new app also needs to serve browser-OAuth clients. For pure backend service mesh, the simpler `KeycloakA2AMiddleware` is enough.

---

## 9. Reusing This Pattern in a New Application

### 9.1 Checklist

- [ ] Run Keycloak (`quay.io/keycloak/keycloak`) — dev with `start-dev`, prod with Postgres + HA
- [ ] Create one **realm** for the new app
- [ ] Create one **client per service** (Client authentication: ON, Service accounts roles: ON, Standard flow: OFF)
- [ ] Copy each client's `client_id` + `client_secret` into the service's config
- [ ] Drop `a2a_auth.py` (and optionally `auth_utils.py`) into each service — adjust imports
- [ ] Add `[auth.keycloak]` block to each service's config
- [ ] Wire `KeycloakA2AMiddleware` into the Starlette/FastAPI app
- [ ] Wrap outbound `httpx.AsyncClient` with `auth=KeycloakA2AAuth()`
- [ ] (Recommended) Apply hardening fixes — see §10

### 9.2 Adaptation by stack

| New app stack | Inbound | Outbound |
|---|---|---|
| **Starlette / FastAPI** (Python) | Use `KeycloakA2AMiddleware` as-is | Use `KeycloakA2AAuth` as-is |
| **Flask** (Python) | Port middleware → `@app.before_request` hook with same JWKS verification | Use `requests` session + `auth_callback` |
| **Spring Boot** (Java) | Use `spring-boot-starter-oauth2-resource-server` with `issuer-uri` | Use `OAuth2AuthorizedClientManager` |
| **Node.js / Express** | Use `express-jwt` + `jwks-rsa` | Use `axios` interceptor + `client-credentials` flow |
| **Go** | Use `github.com/coreos/go-oidc` for JWKS verification | `golang.org/x/oauth2/clientcredentials` |
| **.NET** | `Microsoft.AspNetCore.Authentication.JwtBearer` with `Authority` | `IHttpClientFactory` + `ClientCredentialsTokenManagementService` |

The **pattern is portable**: JWKS verification + cached client_credentials tokens + transparent auth handler on outbound calls.

---

## 10. Recommended Hardening for New Applications

The current implementation works but has gaps you should close in a new app:

| Current behaviour | Why it's risky | Fix for new app |
|---|---|---|
| `verify_aud=False, verify_iss=False` | A token issued for service A could be replayed against service B | Set `audience=<my_client_id>` and `issuer=<realm_url>` in `pyjwt.decode()` |
| `httpx.AsyncClient(verify=False)` in IAM path | MITM-able in non-dev environments | Always verify TLS in non-dev |
| Per-process token cache | N replicas → N tokens fetched from Keycloak | Use Redis-backed cache for multi-replica deployments |
| No role/scope check in middleware | Any authenticated client can call any endpoint | Define realm roles, check `claims["realm_access"]["roles"]` |
| Secrets in `config.ini` (even with placeholders) | Risk of leaking via repo or logs | Inject via env vars from secrets manager (Vault, AWS SM, Azure KV) |
| Copy-paste of `a2a_auth.py` per agent | Drift between agents over time | Extract into a shared internal package (`myapp-auth`) |
| `print()` statements in auth path | Floods stdout in prod | Use `logger.debug` / structured logs |
| No thundering-herd protection on token fetch | Cold start may fire N concurrent token requests | Wrap `_fetch_token` in `asyncio.Lock` |
| No retry on 401 from downstream | Stale token after key rotation → request fails | In `async_auth_flow`, invalidate cache and retry once if response is 401 |
| No auth tests visible | Bugs in auth logic ship unnoticed | Add tests: expired, wrong-audience, wrong-issuer, missing-role, key-rotation |

---

## 11. Quick Reference — File Map

| File | Role | Lines worth reading |
|---|---|---|
| `<agent>/a2a_auth.py` | Keycloak middleware + outbound auth handler | All ~280 lines |
| `<agent>/auth_utils.py` | IAM middleware (parallel for human traffic) | Class `IAMAuthMiddleware`, function `validate_iam_token` |
| `<agent>/config.ini` | `[auth.keycloak]`, `[auth]`, `[iam]` blocks | §36–60 in `mcp_server/config.ini` |
| `<agent>/main.py` or `__main__.py` | Middleware wiring | The two `add_middleware(...)` calls |
| `new_host_agent/host_agent/remote_agent_connection.py` | Outbound usage example | Line 50 (`auth=KeycloakA2AAuth()`) |
| `new_host_agent/utils/auth.py` | Combined OAuthProxy + JWT for MCP browser flow | Class `CombinedAuthProvider` |

---

## 12. TL;DR — What to Copy, What to Improve

### Copy as-is

- The **two-middleware pattern** (IAM for humans, Keycloak for services)
- **JWKS-based local verification** (not token introspection)
- The **`httpx.Auth` handler pattern** — keeps tokens invisible to caller code
- **Module-global token cache** with safety-margin expiry
- **Exempt well-known paths** so service discovery works without auth
- **Path-based routing** between middlewares via `api_paths` / `skip_paths`

### Improve before shipping to a new app

- Extract auth into a **shared library** — no copy-paste
- **Enable `aud` and `iss` verification**
- Add **role-based authz** in the middleware
- Move secrets to a **vault** + inject via env vars
- Add **Redis token cache** for multi-replica deployments
- Add **retry-on-401** in the outbound `httpx.Auth` handler
- Add **`asyncio.Lock`** to prevent thundering-herd on cold start
- Write **auth tests** (expired, wrong aud, wrong iss, missing role, key rotation)
- Replace `print()` with **structured logging + metrics**

---

*This document describes the implementation as observed in the `a2a_orchestrator` codebase. Refer to the source files for the canonical behaviour, and treat the hardening recommendations in §10 as a baseline for any new deployment.*
