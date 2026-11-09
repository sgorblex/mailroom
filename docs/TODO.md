# TODO

The next phases, roughly in order. Not a requirements document — see
[`REQUIREMENTS.md`](REQUIREMENTS.md) for scope and maturity, and [`IDEAS.md`](IDEAS.md) for
exploratory work. Once an item is done, remove it.

## Across the family

Mailroom is built on projects with their own repositories and checklists. As of 2026-11-09:

- **mailroom-mcp** (`../mailroom-mcp`, GitHub `sgorblex/mailroom-mcp`) holds **mailroom-core**,
  0.1.3 on PyPI, which the app pins. Its Python port of the server stopped at core: the
  TypeScript server keeps running on the production host until it is retired (*Family roadmap* below).
- **imap-thingy** (`../imap-thingy`, GitHub `sgorblex/imap-thingy`): rules as data (`to_spec()`,
  `imap_thingy.spec`) since 0.5.0, the runner (`imap_thingy.runner`: `run_rules`, `starred_only`,
  the IDLE `monitor`) since 0.6.0; 0.7.0 adds run reports (what each filter matched and did, also
  for a dry run), the `older_than_days` criterion, SSL and timeout options and `list_folders()` on
  `Account`, a stoppable `monitor` and regex match timeouts; 0.8.0 names every built-in action in
  plain words (`star`, `mark as read`), as run reports show them.

## Family roadmap

In order. The reasons are in [`CONTEXT.md`](CONTEXT.md) — *Where the family is heading*.

- [ ] **Fold mailroom-core into this repository** as a workspace package, with its history.
- [ ] **Editions:** features chosen in config (mail client, MCP, filters) over an always-present
      base.
- [ ] **Retire mailroom-mcp** once the app's MCP covers its use, per-connector profiles included.
      Then stop its deployment and archive the repository.
- [ ] **Go public:**
  - [ ] audit the whole history (secrets, infrastructure names);
  - [ ] make the repository public;
  - [ ] fold in imap-thingy, keeping its history and PyPI name, and archive its old repository
        with a pointer.
- [ ] **Command-line tools:**
  - [ ] a client for a running instance, after personal API tokens;
  - [ ] `imap-thingy run`, for filters with no server.

## Client

- [ ] **Tier 1** (slice A, [`requirements/mail-client.md`](requirements/mail-client.md)):
  - [x] **Core mail read** — `render` and `mail` (with `mail.imap`) in mailroom-core 0.1.1, the
        app's pin.
  - [x] **REST** — folders, message list and search, message view, HTML document, part download.
  - [x] **Web** — `/mail`: folders with counts, list with load more, message view with the
        sandboxed HTML iframe and remote-content gate.
  - [x] **MCP** — the `mail_read` group.
- [ ] **Index and live updates** — schema, initial sync, IDLE-driven refresh, push to the browser;
      lists and search move to the index.
- [x] **Tier 2 triage** (slice B, [`requirements/mail-client.md`](requirements/mail-client.md)) —
      move, archive, trash and flag over REST, the web triage (multi-select, toolbar, shortcuts,
      mark read on open) and the `mail_organize` MCP group.
- [x] **Tier 2 compose** (slice C, [`requirements/mail-client.md`](requirements/mail-client.md)) —
      compose, reply, reply all, forward, drafts and attachments through mailroom-core's send
      gate: compose, drafts and send in mailroom-core 0.1.3, the REST routes,
      the web composer and the `mail_draft` and `mail_send` MCP groups.
- [x] **Host proxy limit for drafts** — the production host's proxy sets no body limit, and its read timeout is now
      300 s, so a 30 MB draft has time to arrive.
- [ ] **Try compose by hand** — a send to oneself from the web, then a Gmail account (it may keep
      old draft versions in All Mail or Trash, which no test here can show), and a 25 MB
      attachment through the host's proxy.

## MCP and suggestions

- [ ] **MCP server** ([`requirements/mcp.md`](requirements/mcp.md)), in this order:
  - [x] **Filters service layer** — rule-set and filter logic out of the routes, so the API and the
        MCP share it.
  - [x] **Web SSO** — OIDC sign-in for existing users, linked sign-ins on the profile.
  - [x] **MCP endpoint and tools** — `/mcp`, the `filters_*` groups, the envelope, the audit line.
  - [x] **Built-in OAuth server** — DCR, CIMD, tokens, the `/connect` consent screen.
  - [x] **External issuer** — JWTs from Authentik or Keycloak, mapped through linked sign-ins.
  - [x] **Connected apps** — list and revoke grants on the profile.
  - [x] **Mail tool groups** — each with its client slice: `mail_read`, `mail_organize`,
        `mail_draft` and `mail_send` are on.
  - [ ] **Consent per tool group** — record the tool groups each grant was approved for, and ask
        for fresh consent when a grant's client calls a group added after it (a grant approved
        before `mail_read`, `mail_organize`, `mail_draft` or `mail_send` reads, moves and, within the
        send mode, sends mail without asking again); before other users join.
- [ ] **Suggestions** — learn from manual moves, propose filters with evidence and dry run.
- [ ] **Organize and send audit trail** — keep mailroom-core's audit item per moved, trashed or
      flagged message and per draft saved or removed and message sent (REST and MCP; the sends'
      recipients and a draft's `removed` tag included), per user, where the user can review it;
      the app logs counts and codes only today
      ([`requirements/mail-client.md`](requirements/mail-client.md), *MCP*).

## Engineering and operations

- [x] **First deploy** — production since 0.2.0: database and role `mailroom` on the shared
      Postgres, backup line, Quadlet units, Traefik route.
- [x] **Release pipeline** — a release tag builds, pushes and deploys both images.
- [ ] **Staging pipeline** — a staging instance, deployed from CI.
- [ ] **Theme and design pass** — palette, typography, light/dark switch, app shell.
