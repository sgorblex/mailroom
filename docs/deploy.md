# Deploy

**Not deployed yet.** This records the image layout so the first deploy has nothing to invent.

## Images

| Image | Built from | Runs |
| --- | --- | --- |
| `mailroom-api` | `backend/Containerfile` (context: repository root) | uvicorn on 8000, non-root, migrates at startup; `/healthz` for the healthcheck. Also the worker (`python -m mailroom.worker`), which never migrates |
| `mailroom-web` | `web/Containerfile` (context: `web/`) | nginx on 8080, non-root, serves the SPA, proxies `/api/` to `127.0.0.1:8000`, sets the CSP |

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
`X-Forwarded-For` (login throttling keys on it). Operator environment:
[`../deploy/quadlet/mailroom-api.env.example`](../deploy/quadlet/mailroom-api.env.example).
