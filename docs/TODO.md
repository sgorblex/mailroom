# TODO

The next phases, roughly in order. Not a requirements document — see
[`REQUIREMENTS.md`](REQUIREMENTS.md) for scope and maturity, and [`IDEAS.md`](IDEAS.md) for
exploratory work. Once an item is done, remove it.

## Foundations

- [ ] **Owner login** — single-owner session (password, Argon2id; another project's session-cookie pattern),
      every non-health route behind it, CSRF stance for the cookie SPA.
- [ ] **Component dependencies** — add mailroom-core, mailroom-mcp and imap-thingy as
      `git+…@<tag>` sources, plus a mise task that switches them to local editable paths for
      cross-repo work. Waits for the mailroom-mcp Python port's packages and imap-thingy's rule
      spec and runner.
- [ ] **Accounts** — `AccountStore` over schema `app`, sealed secrets as mailroom-core seals them,
      settings screens; dev seeding of the dev Dovecot from `mailroom_core.testing`.
- [ ] **Worker scaffold** — procrastinate (as another project) with `dev-worker` in `mise run dev`, the Quadlet
      worker unit made real.

## Client

- [ ] **Tier 1** — folders, message list, message view with the sandboxed HTML iframe and
      remote-content gate, search over IMAP.
- [ ] **Index and live updates** — schema, initial sync, IDLE-driven refresh, push to the browser;
      lists and search move to the index.
- [ ] **Tier 2** — triage actions and compose (reply, forward, drafts, attachments) through
      mailroom-core's send gate.

## Filters and MCP

- [ ] **Filters UI** — list, edit, dry run, enable; worker runs them only for mailboxes
      filterd has released.
- [ ] **MCP host** — mount mailroom-mcp at `/mcp/<profile>` with the app's integration points.
- [ ] **Suggestions** — learn from manual moves, propose filters with evidence and dry run.

## Engineering and operations

- [ ] **First staging deploy** — database and role `mailroom` on the shared Postgres, backup
      line, Quadlet units for real, Woodpecker release and staging pipelines, Traefik route.
- [ ] **Theme and design pass** — palette, typography, light/dark switch, app shell.
