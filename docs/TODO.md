# TODO

The next phases, roughly in order. Not a requirements document — see
[`REQUIREMENTS.md`](REQUIREMENTS.md) for scope and maturity, and [`IDEAS.md`](IDEAS.md) for
exploratory work. Once an item is done, remove it.

## Across the family

Mailroom is built on standalone projects, each with its own repository and checklist. As of
2026-10-20:

- **mailroom-mcp** (`../mailroom-mcp`, GitHub `sgorblex/mailroom-mcp`): the 0.3 Python port is
  under way; `crypto` (sealed secrets, byte-compatible), `config` and the `store` are ported, and
  the store is being keyed by user (in progress). Its own
  [`docs/TODO.md`](https://github.com/sgorblex/mailroom-mcp/blob/main/docs/TODO.md) *0.3* section
  is the checklist, in dependency order, with the TypeScript tests as the parity list. The
  TypeScript server keeps running on the production host until the cutover described there.
- **imap-thingy** (`../imap-thingy`, GitHub `sgorblex/imap-thingy`): rules as data (`to_spec()`,
  `imap_thingy.spec`) are released in 0.5.0; the runner (`imap_thingy.runner`: `run_rules`,
  `starred_only`, the IDLE `monitor`) is released in 0.6.0. Its next release adds run reports
  (what each filter matched and did, also for a dry run), the `older_than_days` criterion, an SSL
  context and `list_folders()` on `Account`, and a stoppable `monitor`.

**The first goal is the Filters UI.** It needs all of *Foundations*, so its critical path runs
through the mailroom-mcp port up to a tagged `mailroom-core`: the Worker
scaffold can start now, while **Component dependencies**, and **Accounts** after it, wait for
that tag.

## Foundations

- [ ] **Component dependencies** — add mailroom-core, mailroom-mcp and imap-thingy as
      `git+…@<tag>` sources, plus a mise task that switches them to local editable paths for
      cross-repo work. Waits for the mailroom-mcp Python port's packages.
- [ ] **Accounts** — mailroom-core's `AccountStore` over its schema `core` in the app's database, sealed secrets as mailroom-core seals them,
      settings screens; dev seeding of the dev Dovecot from `mailroom_core.testing`.
- [ ] **Worker scaffold** — procrastinate with `dev-worker` in `mise run dev`, the Quadlet
      worker unit made real.

## Filters

- [ ] **Filters UI** — rule sets, filters, dry run, run now and history
      ([`requirements/filters.md`](requirements/filters.md)); the worker runs them for accounts
      with *Run filters* on.

## Client

- [ ] **Tier 1** — folders, message list, message view with the sandboxed HTML iframe and
      remote-content gate, search over IMAP.
- [ ] **Index and live updates** — schema, initial sync, IDLE-driven refresh, push to the browser;
      lists and search move to the index.
- [ ] **Tier 2** — triage actions and compose (reply, forward, drafts, attachments) through
      mailroom-core's send gate.

## MCP and suggestions

- [ ] **MCP host** — mount mailroom-mcp at `/mcp/<profile>` with the app's integration points.
      Waits for mailroom-mcp to serve several users (decided for its 0.4: `AccountStore` and
      `OwnerAuth` carry the user), which its design settles before `auth` is ported.
- [ ] **Suggestions** — learn from manual moves, propose filters with evidence and dry run.

## Engineering and operations

- [ ] **First staging deploy** — database and role `mailroom` on the shared Postgres, backup
      line, Quadlet units for real, Woodpecker release and staging pipelines, Traefik route.
- [ ] **Theme and design pass** — palette, typography, light/dark switch, app shell.
