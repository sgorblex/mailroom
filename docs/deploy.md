# Deploy

**Not deployed yet.** This records the image layout so the first deploy has nothing to invent.

## Images

| Image | Built from | Runs |
| --- | --- | --- |
| `mailroom-api` | `backend/Containerfile` (context: repository root) | uvicorn on 8000, non-root, migrates at startup; `/healthz` for the healthcheck. Also the worker (`python -m mailroom.worker`, one instance: it runs the filters and watches folders over IMAP IDLE), which never migrates |
| `mailroom-web` | `web/Containerfile` (context: `web/`) | nginx on 8080, non-root, serves the SPA (`/connect` included), proxies `/api/`, `/mcp`, `/oauth/` and `/.well-known/oauth-` to `127.0.0.1:8000`, sets the SPA's CSP |

**Never name an image `mailroom`**: `registry.example.org/mailroom:production` is the MCP server, running
on the production host.

```bash
mise run build-containers            # localhost/mailroom-{api,web}:{<version>,latest,production}
mise run build-containers -- staging # :staging and :staging-<branch>-<sha>
```

`scripts/container-build.sh` picks Podman (or Docker; `MAILROOM_CONTAINER_ENGINE`), stamps OCI
labels (version from `version.txt`, git SHA and branch), and with `MAILROOM_REGISTRY` set also tags
and **pushes** `$MAILROOM_REGISTRY/mailroom-{api,web}` — a remote action, run only when asked.

## Runtime

Quadlet sketch: [`../deploy/quadlet/README.md`](../deploy/quadlet/README.md) — one pod (api, web,
worker), Postgres outside it, TLS on the host's reverse proxy, which must append the client to
`X-Forwarded-For` (login throttling keys on it). Operator environment, `MAILROOM_SECRETS_KEY` included:
[`../deploy/quadlet/mailroom-api.env.example`](../deploy/quadlet/mailroom-api.env.example).

## MCP server and OAuth

Specification: [`requirements/mcp.md`](requirements/mcp.md). Variables:
[`../deploy/quadlet/mailroom-api.env.example`](../deploy/quadlet/mailroom-api.env.example).

1. Set `MAILROOM_PUBLIC_URL` to the origin users reach (`https://mail.example.com`; a `:443` is
   dropped). Without it, or when it is not a plain origin with a valid host (the log says why),
   the MCP server and SSO are off.
2. The host's reverse proxy sends every path to the pod (the web container's nginx routes `/mcp`,
   `/oauth/` and `/.well-known/oauth-` to the API) and keeps the request's `Host`, as Traefik does
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

1. In Authentik, create an **OAuth2/OpenID provider** and an **application** for it (slug
   `mailroom`):
   - Client type **Confidential**.
   - **Signing key: an RS256 certificate. Mandatory**: without one Authentik signs with HS256,
     which Mailroom always refuses.
   - Redirect URI (strict): `https://mail.example.com/api/v1/auth/oidc/callback`.
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
