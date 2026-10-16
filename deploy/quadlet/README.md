# Mailroom on Podman Quadlet (sketch)

**Not deployed yet.** These units sketch the shape Mailroom will run in; the first staging deploy
turns them into a runbook. Broader context: [`docs/deploy.md`](../../docs/deploy.md).

| Unit | Role |
| --- | --- |
| `mailroom.pod` | Pod publishing **127.0.0.1:8080 → 8080** (nginx in `mailroom-web`, non-root). |
| `mailroom-api.container` | FastAPI + uvicorn on **8000** inside the pod, not published. Runs `alembic upgrade head` at startup (`MAILROOM_RUN_STARTUP_MIGRATIONS=1`, the image default). |
| `mailroom-web.container` | nginx serves the SPA and proxies `/api/` to `127.0.0.1:8000`. HTTP only; TLS terminates on the host's reverse proxy. |
| `mailroom-worker.container` | Background worker on the API image. **Placeholder**: the module arrives with the first background feature. Never migrates. |

Images are `localhost/mailroom-api` and `localhost/mailroom-web` (`mise run build-containers`), or
`registry.example.org/mailroom-api` / `mailroom-web` once pushed. **Never** `mailroom`: that name is
the MCP server's image (`registry.example.org/mailroom:production`), which runs on the production host today.

Postgres is provided outside the pod (the host's shared `postgres`): database and role `mailroom`.
Standalone mailroom-mcp uses `mailroom_mcp`; integrated, its tables live in schema `mcp` of
`mailroom`.
