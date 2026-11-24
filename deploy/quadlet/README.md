# Mailroom on Podman Quadlet (sketch)

These units sketch the shape Mailroom runs in; the production units, on the operator's host, add its
networks and reverse-proxy labels and set the non-secret variables, such as `MAILROOM_PUBLIC_URL`, in
the unit. Broader context: [`docs/deploy.md`](../../docs/deploy.md).

| Unit | Role |
| --- | --- |
| `mailroom.pod` | Pod publishing **127.0.0.1:8080 → 8080** (nginx in `mailroom-web`, non-root). |
| `mailroom-api.container` | FastAPI + uvicorn on **8000** inside the pod, not published. Migrates at startup (mailroom-core's schema `core`, then `alembic upgrade head`) (`MAILROOM_RUN_STARTUP_MIGRATIONS=1`, the image default). |
| `mailroom-web.container` | nginx serves the SPA and proxies `/api/`, `/mcp`, `/oauth/`, `/.well-known/oauth-` and `/.well-known/openid-configuration` to `127.0.0.1:8000`. HTTP only; TLS terminates on the host's reverse proxy, which must append the client to `X-Forwarded-For` (login throttling keys on it) and pass the live updates stream (`/api/v1/mail/events`) unbuffered, with a read timeout above 25 s, and allow the body sizes and the answer time of `/api/v1/mail/drafts` and `/mcp` ([`docs/deploy.md`](../../docs/deploy.md)). |
| `mailroom-worker.container` | procrastinate worker on the API image (`python -m mailroom.worker`), same environment file. Never migrates: it waits for the API to bring the database to head, then runs jobs (filter runs, the mail index's sync) and watches the folders of new-mail rule sets over IMAP IDLE, so it reaches the mail servers. One instance only (a second exits with status 1). Only the mail client and filters need it: with neither in `MAILROOM_FEATURES` it exits 0 at once, and the unit may be left out. `Restart=on-failure`, so that exit is final while a failure still restarts: after adding `mail` or `filters`, start the unit (`systemctl start mailroom-worker`), or install it if it was left out. |

Images are `localhost/mailroom-api` and `localhost/mailroom-web` (`mise run build-containers`), or
`<registry>/mailroom-api` / `mailroom-web` once pushed. **Never** `mailroom`: in the production
registry that name holds the retired standalone MCP server's images.
In production the host's config pins both as `<version>@sha256:…`, and Renovate proposes each new
release there ([`docs/requirements/ci-cd.md`](../../docs/requirements/ci-cd.md) — *Release and
deploy*); the units here take `:latest`.

The first user, once the API has migrated the database: `podman exec -it systemd-mailroom-api
.venv/bin/python -m mailroom.dev.create_user <name> --admin` (the password is prompted for). Until the
app can manage users, every user is made this way.

The environment file needs `MAILROOM_SECRETS_KEY` (`openssl rand -hex 32`): mailbox passwords are
sealed under it, and a lost or changed key means entering every one again.

The MCP server and SSO need `MAILROOM_PUBLIC_URL`, and the reverse proxy must pass the request's
`Host` through: setup, and the Authentik runbook, in [`docs/deploy.md`](../../docs/deploy.md) —
*MCP server and OAuth*.

Postgres is provided outside the pod (the host's shared `postgres`): database and role `mailroom`. The
role needs `CREATE` on the database, for the `pg_trgm` extension its migrations add.
