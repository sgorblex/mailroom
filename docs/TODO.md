# TODO

The next phases, roughly in order. Not a requirements document — see
[`REQUIREMENTS.md`](REQUIREMENTS.md) for scope and maturity, and [`IDEAS.md`](IDEAS.md) for
exploratory work. Once an item is done, remove it.

## Across the family

Mailroom is built on standalone projects, each with its own repository and checklist. As of
2026-10-23:

- **mailroom-mcp** (`../mailroom-mcp`, GitHub `sgorblex/mailroom-mcp`): the 0.3 Python port is
  under way; `crypto` (sealed secrets, byte-compatible), `config` and the per-user `store` are
  ported and published as **mailroom-core** 0.1.0 on PyPI. Its own
  [`docs/TODO.md`](https://github.com/sgorblex/mailroom-mcp/blob/main/docs/TODO.md) *0.3* section
  is the checklist, in dependency order, with the TypeScript tests as the parity list. The
  TypeScript server keeps running on the production host until the cutover described there.
- **imap-thingy** (`../imap-thingy`, GitHub `sgorblex/imap-thingy`): rules as data (`to_spec()`,
  `imap_thingy.spec`) since 0.5.0, the runner (`imap_thingy.runner`: `run_rules`, `starred_only`,
  the IDLE `monitor`) since 0.6.0; 0.7.0 adds run reports (what each filter matched and did, also
  for a dry run), the `older_than_days` criterion, SSL and timeout options and `list_folders()` on
  `Account`, a stoppable `monitor` and regex match timeouts; 0.8.0 names every built-in action in
  plain words (`star`, `mark as read`), as run reports show them.

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
- [ ] **Tier 2 compose** (slice C, [`requirements/mail-client.md`](requirements/mail-client.md)) —
      compose, reply, reply all, forward, drafts and attachments through mailroom-core's send
      gate: compose, drafts and send in mailroom-core 0.1.3, the REST routes,
      the web composer and the `mail_draft` and `mail_send` MCP groups. Needs the host proxy's
      30 MB limit on `/api/v1/mail/drafts` before the deploy. Then to try by hand: a send to
      oneself from the web, and a Gmail account (it may keep old draft versions in All Mail or
      Trash).

## MCP and suggestions

- [ ] **MCP server** ([`requirements/mcp.md`](requirements/mcp.md)), in this order:
  - [x] **Filters service layer** — rule-set and filter logic out of the routes, so the API and the
        MCP share it.
  - [x] **Web SSO** — OIDC sign-in for existing users, linked sign-ins on the profile.
  - [x] **MCP endpoint and tools** — `/mcp`, the `filters_*` groups, the envelope, the audit line.
  - [x] **Built-in OAuth server** — DCR, CIMD, tokens, the `/connect` consent screen.
  - [x] **External issuer** — JWTs from Authentik or Keycloak, mapped through linked sign-ins.
  - [x] **Connected apps** — list and revoke grants on the profile.
  - [ ] **Mail tool groups** — each with its client slice: `mail_draft` and `mail_send`
        with slice C (`mail_read` and `mail_organize` are on).
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
