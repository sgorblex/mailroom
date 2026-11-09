# Deploy

**Runs in production since 0.2.0** (28 October 2026), on the operator's host, from the Quadlet units
below adapted to it. There is no staging yet. A release tag builds, pushes and deploys both images
([`requirements/ci-cd.md`](requirements/ci-cd.md) — *Release and deploy*).

## Images

| Image | Built from | Runs |
| --- | --- | --- |
| `mailroom-api` | `backend/Containerfile` (context: repository root) | uvicorn on 8000, non-root, migrates at startup; `/healthz` for the healthcheck. Also the worker (`python -m mailroom.worker`, one instance: it runs the filters and watches folders over IMAP IDLE), which never migrates |
| `mailroom-web` | `web/Containerfile` (context: `web/`) | nginx on 8080, non-root, serves the SPA (`/connect` included), proxies `/api/`, `/mcp`, `/oauth/`, `/.well-known/oauth-` and `/.well-known/openid-configuration` to `127.0.0.1:8000`, sets the SPA's CSP |

**Never name an image `mailroom`**: `registry.example.org/mailroom:production` is the MCP server, running
on the production host.

```bash
mise run build-containers            # localhost/mailroom-{api,web}:{<version>,latest,production}
mise run build-containers -- staging # :staging and :staging-<branch>-<sha>
```

`scripts/container-build.sh` picks Podman (or Docker; `MAILROOM_CONTAINER_ENGINE`), stamps OCI
labels (version from `version.txt`, git SHA and branch), and with `MAILROOM_REGISTRY` set also tags
and **pushes** `$MAILROOM_REGISTRY/mailroom-{api,web}` — a remote action, run only when asked;
releases do it from CI. `build-containers` (`scripts/containers-both.sh`) pushes only once both
images have built, the moving tags last.

## Runtime

Quadlet sketch: [`../deploy/quadlet/README.md`](../deploy/quadlet/README.md) — one pod (api, web,
worker), Postgres outside it, TLS on the host's reverse proxy, which must append the client to
`X-Forwarded-For` (login throttling keys on it). The web container's nginx logs paths without
their query string, which can hold mail data (a folder name, a search the SPA keeps in its URL);
the host's proxy must log the same way, or not at all. The web container's nginx allows a body of
30 MB on `/api/v1/mail/drafts` alone, for a draft's attachments; the host's proxy must allow the
same there, or it refuses them first. Its read timeout must also cover the whole upload: at 60 s,
30 MB needs about 4 Mbit/s, and a slower client gets the proxy's 502 or 504 partway through.
Operator environment, `MAILROOM_SECRETS_KEY` included:
[`../deploy/quadlet/mailroom-api.env.example`](../deploy/quadlet/mailroom-api.env.example).

## MCP server and OAuth

Specification: [`requirements/mcp.md`](requirements/mcp.md). Variables:
[`../deploy/quadlet/mailroom-api.env.example`](../deploy/quadlet/mailroom-api.env.example).

1. Set `MAILROOM_PUBLIC_URL` to the origin users reach (`https://mail.example.com`; a `:443` is
   dropped), with `mcp` in `MAILROOM_FEATURES` (the default when unset). Without it, or when it is
   not a plain origin with a valid host (the log says why), the MCP server and SSO are off.
2. The host's reverse proxy sends every path to the pod (the web container's nginx routes `/mcp`,
   `/oauth/`, `/.well-known/oauth-` and `/.well-known/openid-configuration` to the API) and keeps the request's `Host`, as Traefik does
   by default: `/mcp` answers only `MAILROOM_PUBLIC_URL`'s host (421 otherwise).
3. Check: `curl https://mail.example.com/.well-known/oauth-protected-resource/mcp` names the
   authorization server, and `curl -X POST https://mail.example.com/mcp` answers 401 with a
   `WWW-Authenticate` naming that metadata.
4. In claude.ai, add a custom connector named **Mailroom app** with the URL
   `https://mail.example.com/mcp` (the standalone mailroom-mcp keeps its own connector).

**Built-in mode** (`MAILROOM_MCP_AUTH=builtin`, the default) needs nothing else: claude.ai
registers itself (DCR, or its client ID metadata document), and the user signs in and approves the
client on Mailroom's `/connect` screen, with SSO there too when it is set up. Its `/oauth/register`
and `/oauth/authorize` throttles key on the client address from `X-Forwarded-For` too: a proxy that
does not append it puts every client behind one limit.

### Authentik

One OAuth2/OpenID application serves both web SSO and, if wanted, external mode, so a user's
linked sign-in counts for both.

1. In Authentik, create an **OAuth2/OpenID provider** and an **application** for it (slug
   `mailroom`):
   - Client type **Confidential**.
   - **Signing key: an RS256 certificate. Mandatory**: without one Authentik signs with HS256,
     which Mailroom always refuses.
   - Redirect URIs (strict):
     - `https://mail.example.com/api/v1/auth/oidc/callback` (web SSO)
     - `https://claude.ai/api/mcp/auth_callback` (external mode)
     - `https://claude.com/api/mcp/auth_callback` (external mode)
   - Scopes `openid`, `profile` and `email`.
2. Mailroom's environment, from the provider's page (the issuer is per application and ends in a
   slash):

   ```bash
   MAILROOM_OIDC_ISSUER=https://auth.example.com/application/o/mailroom/
   MAILROOM_OIDC_CLIENT_ID=<client id>
   MAILROOM_OIDC_CLIENT_SECRET=<client secret>
   MAILROOM_OIDC_NAME=Authentik
   ```

   Restart the API: the sign-in screen shows "Sign in with Authentik". Users must exist in
   Mailroom already; each links their sign-in on their *Profile* page (or set
   `MAILROOM_OIDC_LINK_CLAIM=preferred_username`, [`requirements/mcp.md`](requirements/mcp.md) —
   *Web SSO*: only when Authentik's users cannot choose or change their usernames, since a first
   sign-in whose username equals a Mailroom username, exactly, becomes that user's).
3. **External mode** (optional: Authentik issues the MCP tokens instead of Mailroom):

   ```bash
   MAILROOM_MCP_AUTH=external
   MAILROOM_MCP_OIDC_AUDIENCES=<client id>   # Authentik's aud is the client id
   # MAILROOM_MCP_OIDC_ISSUER defaults to MAILROOM_OIDC_ISSUER
   ```

   On the provider, for external mode:
   - **Scopes: add `authentik default OAuth Mapping: OpenID 'offline_access'`.** MCP clients ask for
     the scopes the protected-resource metadata advertises, `openid offline_access`; without the
     mapping Authentik issues no refresh token, and the user has to reconnect each time the access
     token expires. If `MAILROOM_MCP_OIDC_REQUIRED_SCOPE` is set, add a scope mapping with that
     scope name too.
   - **Advanced protocol settings → Access token validity** is how long one token lasts before
     the connector refreshes it. Keep it short (minutes): Mailroom checks these tokens itself, so
     revoking one in Authentik takes effect only when it expires (unlinking the user's sign-in in
     Mailroom stops it at once). **Refresh token validity** is how long a connector may go unused
     before its user has to reconnect.

   The built-in `/oauth/*` endpoints are then off. In the claude.ai connector's **advanced
   settings**, enter the same client ID and client secret (Authentik offers no dynamic
   registration). Only a user who has linked their Authentik sign-in in Mailroom gets in; anyone
   else's token is a 401. What is still to be checked about external mode is in
   [`requirements/mcp.md` — *Open questions*](requirements/mcp.md#open-questions).
