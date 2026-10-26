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
  `Account`, a stoppable `monitor` and regex match timeouts.

## Client

- [ ] **Tier 1** — folders, message list, message view with the sandboxed HTML iframe and
      remote-content gate, search over IMAP.
- [ ] **Index and live updates** — schema, initial sync, IDLE-driven refresh, push to the browser;
      lists and search move to the index.
- [ ] **Tier 2** — triage actions and compose (reply, forward, drafts, attachments) through
      mailroom-core's send gate.

## MCP and suggestions

- [ ] **MCP server** ([`requirements/mcp.md`](requirements/mcp.md)), in this order:
  - [x] **Filters service layer** — rule-set and filter logic out of the routes, so the API and the
        MCP share it.
  - [ ] **Web SSO** — OIDC sign-in for existing users, linked sign-ins on the profile.
  - [ ] **MCP endpoint and tools** — `/mcp`, the `filters_*` groups, the envelope, the audit line.
  - [ ] **Built-in OAuth server** — DCR, CIMD, tokens, the `/connect` consent screen.
  - [ ] **External issuer** — JWTs from Authentik or Keycloak, mapped through linked sign-ins.
  - [ ] **Connected apps** — list and revoke grants on the profile.
  - [ ] **Mail tool groups** — once the client reaches parity with mailroom-mcp, turn on
        `mail_read`, `mail_organize`, `mail_draft` and `mail_send`.
- [ ] **Suggestions** — learn from manual moves, propose filters with evidence and dry run.

## Engineering and operations

- [ ] **First staging deploy** — database and role `mailroom` on the shared Postgres, backup
      line, Quadlet units for real, Woodpecker release and staging pipelines, Traefik route.
- [ ] **Theme and design pass** — palette, typography, light/dark switch, app shell.
