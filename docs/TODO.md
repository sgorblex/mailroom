# TODO

The next phases, roughly in order. Not a requirements document — see
[`REQUIREMENTS.md`](REQUIREMENTS.md) for scope and maturity, and [`IDEAS.md`](IDEAS.md) for
exploratory work. Once an item is done, remove it.

## Across the family

Mailroom is built on projects with their own repositories and checklists. As of 2026-11-12:

- **mailroom-mcp** (`../mailroom-mcp`, GitHub `sgorblex/mailroom-mcp`): retired on 2026-11-10 and
  archived; its Python port's core lives in `backend/core`.
- **imap-thingy** (`../imap-thingy`, GitHub `sgorblex/imap-thingy`): folded into mailroom-core
  as `mailroom_core.filters` on 2026-11-11; its last PyPI release, 0.8.1, points here, and its
  repository is archived.

## Family roadmap

In order. The reasons are in [`CONTEXT.md`](CONTEXT.md) — *Where the family is heading*.

- [x] **Fold mailroom-core into this repository** as a workspace package, with its history.
- [x] **Editions:** features chosen in config (mail client, MCP, filters) over an always-present
      base.
- [x] **Retire mailroom-mcp** (2026-11-10): the app's MCP covers its use, the audit trail
      included; its deployment is gone and its repository archived.
- [x] **Go public:** the whole history audited and rewritten where it named private things, and
      the repository public since 2026-11-10 (the earlier one stays private as
      `mailroom-archive`, with its pull requests).
- [ ] **One library under one app**, in this order:
  - [x] **Fold imap-thingy into mailroom-core** (2026-11-11): as `mailroom_core.filters`, with its
        history, its tests among core's and the app's imports moved over; the helpers only
        scripts used gone. Its last PyPI release, 0.8.1, says where it went; its repository is
        archived, and its Woodpecker repository and PyPI trusted publisher are gone.
  - [x] **Core's public API** (2026-11-11): ruff's `D1` rules on core's source, with the missing
        docstrings written; tests that the app imports only the subpackages' facades, that
        `mail` never imports `filters` and that core never imports the app.
  - [x] **One IMAP stack** (2026-11-12): the engine on core's connections, its searches, moves
        and flags core's, the client's move checks included; IDLE in `mailroom_core.mail`; the
        worker's watches and runs and the API's dry run on the app's mail pools, and
        `mailroom.accounts.mail` gone, so STARTTLS accounts filter too. Connections from plain
        config (`connections_from`), and `core/tests/filters/test_engine_mail.py` running
        filters with no app or database.
  - [x] **Engine parsing on core's MIME** (2026-11-13): the engine's criteria read the messages
        they fetch through `mailroom_core.mail.parsed_message` instead of mail-parser, which left
        the dependencies; an HTML-only body is matched as its visible text.
  - [x] **Tidy-ups** (2026-11-13): mailroom-mcp's leftovers out of core (`STANDALONE_USER`,
        its config.toml load path, the docs that named it as running); `mailroom.mail.service`
        and `mailroom.api.routes.mail` split by concern into packages, the OpenAPI unchanged.
- [ ] **Command-line tools:**
  - [ ] a client for a running instance, after personal API tokens;
  - [ ] `run`, for filters with no server, over mailroom-core; it settles whether core keeps the
        database ([`CONTEXT.md`](CONTEXT.md) — *Where the family is heading*).

## Client

- [ ] **Tier 1** (slice A, [`requirements/mail-client.md`](requirements/mail-client.md)):
  - [x] **Core mail read** — `render` and `mail` (with `mail.imap`) in mailroom-core 0.1.1, the
        app's pin.
  - [x] **REST** — folders, message list and search, message view, HTML document, part download.
  - [x] **Web** — `/mail`: folders with counts, list with load more, message view with the
        sandboxed HTML iframe and remote-content gate.
  - [x] **MCP** — the `mail_read` group.
- [x] **Tier 2 triage** (slice B, [`requirements/mail-client.md`](requirements/mail-client.md)) —
      move, archive, trash and flag over REST, the web triage (multi-select, toolbar, shortcuts,
      mark read on open) and the `mail_organize` MCP group.
- [x] **Tier 2 compose** (slice C, [`requirements/mail-client.md`](requirements/mail-client.md)) —
      compose, reply, reply all, forward, drafts and attachments through mailroom-core's send
      gate: compose, drafts and send in mailroom-core 0.1.3, the REST routes,
      the web composer and the `mail_draft` and `mail_send` MCP groups.
- [x] **Host proxy limit for drafts** — the production proxy sets no body limit, and its read
      timeout is now 300 s, so a 30 MB draft has time to arrive.

## Next: milestones and batches

In order. A batch is one stack, its items one branch each, in the order listed; a milestone is
released and checked in production before the next one starts. Batch 15 is the roadmap's:
*Suggestions* and the command-line tools.

### Milestone A: Urgent and accounts

#### 1. Urgent

- [x] **A folder with its subfolders** (urgent) — a folder's messages listed and searched together
      with all its subfolders' (an Inbox kept in subfolders, say), newest first: a toggle on the
      folder in the web, an option on `messages_list` and `messages_search` over MCP. Until the
      index, a bounded merge of the folders' own listings.
- [x] **Time zone per user** (urgent) — dates in the user's time zone, set on the profile (the
      browser's at first sign-in), in the web and over MCP.

#### 2. Accounts

- [x] **Account display names** — an editable name per account, one per account of a user,
      shown wherever the web names an account (a removed account shows as *Removed account*).
- [x] **Numbered account ids** — the id the filters, the audit trail, refs and MCP use is a
      number the instance gives, never shown in the web; accounts named by a slug before keep it
      as their name, unless another of the user's accounts already has that name.
- [x] **Account colours** — so the accounts stay apart in unified lists: a dot beside the
      account's name in the web.
- [x] **Account autoconfiguration** — on setup, look the address up in the provider's
      autoconfig, Thunderbird's ISPDB and DNS SRV records (RFC 6186); when nothing answers,
      offer presets (Gmail with an app password, iCloud, Fastmail, Yahoo).
- [x] **Mailbox quota** — each account's usage, from the server's IMAP `QUOTA`: a bar per
      account on the *Accounts* page.

### Milestone B: Acting on mail, reading it, filtering from it

#### 3. Actions

- [x] **Shared action registry and context menu** — one list of message actions behind the
      toolbar, the shortcuts and a right-click menu on messages and folders.
- [x] **Shortcut sheet** — `?` lists the keyboard shortcuts, read from the action registry.
- [x] **Undo** — a toast with Undo for a few seconds after a move, archive or trash.
- [x] **Drag and drop** — messages onto a folder, of the same account or another one.
- [x] **Select all matching** — after selecting all, act on every message the list or search
      matches, not only those loaded, with the count to confirm.
- [x] **Mark folder as read** — every message in a folder at once, with the count to confirm.
- [x] **Command palette** — Ctrl/⌘+K: go to an account, a folder or a settings page, and run the
      registry's actions on the open or selected messages; recents on an empty query.

#### 4. Reading a message

- [x] **Sender mismatch warning** — when a sender's display name is or looks like an address other
      than the one it sends from, show the real one and flag it.
- [x] **Sender authentication** — a badge for the SPF, DKIM and DMARC results, and a warning when
      they fail.
- [x] **Link safety** — a link's real address on hover, and a warning when its text names
      another site.
- [x] **"Sent to" address** — which of the user's addresses or aliases a message arrived at.
- [x] **Recipient summary** — "to you and 12 others", expanded on click.
- [x] **Mailing list badge** — the list's name, with its archive and unsubscribe at hand.
- [x] **Unsubscribe** — `List-Unsubscribe` one-click (RFC 8058) where the list supports it, else
      its link or address; never automatic, and with the offer of a filter instead.
- [x] **Replied indicator** — a mark on the rows the user answered or forwarded.
- [x] **Remote content per sender** — the remote-content gate remembers the senders the user
      trusts.
- [x] **Tracker count** — how many trackers the remote-content gate blocked, beside it.
- [x] **Print and download** — a message printed with its headers and sanitised body, remote
      images left out, or saved as its `.eml`.

#### 5. Filters from the client

- [x] **A filter from a message** — the filter editor pre-filled from a message (its list, its
      sender, the sender's domain unless a public provider's; never the subject).
- [x] **A filter from a search** — the current search turned into a filter's conditions.
- [x] **Report spam** — the message to Junk and, if the user wants, a filter that keeps
      sending its sender's mail to Junk.
- [x] **Mute a thread** — later replies arrive read
      ([`filters.md`](requirements/filters.md#muted-threads)).
- [x] **Match counts per filter** — how often each filter matched lately, so dead ones stand out.
- [x] **Rules for all accounts** — one rule set applied to every account of the user
      ([`filters.md`](requirements/filters.md)).
- [ ] **Filters that move to another account** — the action, its checks and the loop-guard
      ledger are in, and the filter editor's account and folder picker
      ([`filters.md`](requirements/filters.md#moves-to-another-account)); left: the rule set's
      `disabled_reason` in the web.

### Milestone C: The index

#### 6. Index and what it unlocks

- [x] **Index and live updates** — schema, initial sync, IDLE-driven refresh, push to the browser;
      lists and search move to the index.
- [ ] **Unified folders and saved searches** (after *Index and live updates*) — a folder made of
      any folders of any of the user's accounts (*All inboxes* first), and a search kept as a
      folder; one mechanism for both.
- [x] **Unread only and quick filters** — one-click views of the list (unread, flagged, with
      attachments), which are preset searches: both, the same mechanism.
- [x] **Search syntax** — `from:`, `to:`, `has:attachment`, `is:unread`, `before:` and the like
      in the search box, with chips; MCP's `messages_search` takes it as `query`.
- [x] **Snippets** (after *Index and live updates*) — the opening of each message's text under its
      subject in the list, now stored with the index.
- [ ] **Conversations in the list** — messages grouped by thread, not only on open.
- [ ] **Large folders** — a virtualised list, so a folder of tens of thousands of messages stays
      smooth.
- [x] **Desktop notifications** (after *Index and live updates*) — new mail, per folder.

### Milestone D: Writing and sending

#### 7. Compose

- [ ] **Reply from the right address** — a reply goes from the identity the message arrived at.
- [ ] **Signatures** — one per identity, added in compose.
- [ ] **Quote the selection** — Reply with text selected quotes only that.
- [ ] **Paste images inline** — a pasted image becomes an inline part of the message.
- [ ] **Address autocomplete** — in compose, from the addresses the user wrote to.
- [ ] **Rich text compose** — formatting (Markdown or a rich editor) sent as HTML with a plain
      text part; to be expanded.

#### 8. Scheduled by the worker

- [ ] **Undo send** — a sent message is held for a few seconds, with Undo, before it goes to the
      mail server.
- [ ] **Send later and snooze** — a time to send a message or bring one back, run by the worker.
- [ ] **Vacation auto-reply** — on and off, with dates, once per sender, through the send gate.

#### 9. HTML and templated mail

- [ ] **HTML and templated mail** (to specify) — two parts, sharing the send path:
  - **Designed HTML mail:** a ready-built message (HTML, its plain-text part, inline `cid:`
    images, attachments) taken in as a draft, maybe later authored here from MJML. Previewed
    at phone and desktop widths, light and dark; checked before sending (images without
    `alt`, a `cid:` with no image or the reverse, over Gmail's 102 KB clip); a test send to
    the user; sent from the account, so it lands in Sent and its thread.
  - **Templates with variants per recipient:** logic-free `{{ placeholders }}`, each value
    HTML-escaped, from the recipient (a contact) or typed per recipient (a table, a CSV); a
    missing value holds back that recipient only. Rendered once on the server, so the
    preview cannot drift from what is sent; sent one by one by the worker, each recipient
    pending, sending (recorded before the server is called), then sent or failed, so none
    gets it twice. A real sandboxed engine with autoescape only if conditions are ever
    needed.

### Milestone E: Profile, design, agents, integrations, operations

#### 10. Profile, security and data

- [ ] **Sessions** — the user's signed-in sessions on the profile, each one endable.
- [ ] **Personal API tokens** — made and revoked on the profile, for scripts and the command-line
      client (*Command-line tools*).
- [ ] **Export** — the user's rule sets as JSON and their audit trail as CSV.
- [ ] **Audit retention** — how long the audit trail is kept, a setting.

#### 11. Design

- [ ] **Theme and design pass** — palette, typography, light/dark switch, app shell.
- [ ] **Home page** — a real start page after sign-in instead of landing straight in a mail
      list, its content configurable (unread across accounts, flagged, recent filter runs, say).
- [ ] **Every message action in the reading pane** — each action on one message (move, archive,
      junk, To sort, mute, filter like this, print, `.eml`, reply and the rest) offered in the
      pane beside the message, not only some of them there and the rest elsewhere.
- [ ] **Layout and density** — the reading pane on the right, below the list or off; compact or
      comfortable rows.
- [ ] **Dark mode for HTML mail** — the sandboxed frame's colours adapted to a dark theme, off
      for a message with one click.
- [ ] **Calendar invites** — an `.ics` invite shown as a card, with Accept, Tentative and Decline.
- [ ] **Mobile and installable** — a layout pass for phones, and a web app manifest (with
      *Theme and design pass*).
- [ ] **Swipe actions** — on phones, archive and trash by swiping, the actions configurable.
- [ ] **Accessibility pass** — screen readers, keyboard focus and contrast across the app, with
      automated checks in CI.

#### 12. MCP extras

- [ ] **Attachments from agents** — `draft_*` and the send tools take new files as content
      (size-capped, type checked against the content) and rename attachments kept from
      messages. Lifts the rule that attachments are named by `ref` and `part` only, so its
      reasons are weighed first: what an agent may attach unasked, and tool calls that size.
- [ ] **Per-app limits** (maybe) — a connected app sees only the accounts the user chooses,
      and may send less than the account allows (drafts only, say).
- [ ] **Activity per app** (maybe) — the audit trail of one connected app, linked from Connected
      apps.

#### 13. Integrations

- [ ] **Microsoft accounts over OAuth2** — Outlook.com and Microsoft 365 refuse a password over
      IMAP: sign-in through Microsoft, tokens stored and refreshed like passwords are kept.
- [ ] **Contacts over CardDAV** — the user's address book for autocomplete and sender names.
- [ ] **Filter import** — Sieve scripts and Thunderbird filters turned into rule sets.
- [ ] **Remote image proxy** (maybe) — allowed remote images fetched through the server, so the
      sender never sees the user's address or when they read. Its security comes first: no
      request to private or loopback addresses (SSRF), images only, size and time limits, never
      an open proxy.

#### 14. Operations

- [ ] **Staging pipeline** — a staging instance, deployed from CI.
- [ ] **Admin page** — users, invites and instance settings in the web, not only on the command
      line.
- [ ] **Instance health** — the worker's last run, its queue and the failing accounts, for the
      admin.

## Folders and moves across accounts

In this order, after *One IMAP stack* (*Family roadmap*): each builds on the one before.

- [x] **Folder management** — create, rename (moving it in the hierarchy) and delete an account's
      folders, from the web and MCP. A rename updates the rule sets that name the folder. The
      first change that deletes permanently also brings the safeguards every later one keeps:
      the docs' "never deletes permanently" becomes "deletes only deliberately" (the rule was
      lifted on 2026-11-10), a confirmation on the web, a destructive-marked MCP tool, never a
      side effect, refusal tests and audit records.
- [x] **Move messages across accounts** (2026-11-14) — `mailroom_core.mail.move_to_account`:
      copied with flags and date, confirmed by `Message-ID` (Gmail's own search there), then
      the original to Trash or, when asked, deleted for good; never copied twice; a removal
      that fails leaves the message in both and says so; at most the user's `move_max_mb`.
      *Move to another account…* on the web, the `mail_transfer` MCP group (preview), recorded
      in both accounts.

## MCP and suggestions

- [ ] **MCP server** ([`requirements/mcp.md`](requirements/mcp.md)), in this order:
  - [x] **Filters service layer** — rule-set and filter logic out of the routes, so the API and the
        MCP share it.
  - [x] **Web SSO** — OIDC sign-in for existing users, linked sign-ins on the profile.
  - [x] **MCP endpoint and tools** — `/mcp`, the `filters_*` groups, the envelope, the audit line.
  - [x] **Built-in OAuth server** — DCR, CIMD, tokens, the `/connect` consent screen.
  - [x] **External issuer** — JWTs from Authentik or Keycloak, mapped through linked sign-ins.
  - [x] **Sign-in through SSO** (2026-11-13) — `MAILROOM_MCP_AUTH=oidc`: the built-in server,
        each connection signing in through web SSO before the consent screen.
  - [x] **Connected apps** — list and revoke grants on the profile.
  - [x] **Mail tool groups** — each with its client slice: `mail_read`, `mail_organize`,
        `mail_draft`, `mail_send` and `mail_folders` are on.
  - [x] **Consent per tool group** (2026-11-14) — the consent screen offers the served tool
        groups as checkboxes (those that send or delete for good unchecked), the grant records
        the choice, and a client sees and calls only those groups; a group served later waits
        for the user's approval under Connected apps, where an app's groups can be changed.
        Grants approved before count as approving the groups that were `stable`.
- [ ] **Suggestions** — learn from manual moves, propose filters with evidence and dry run.

## Engineering and operations

- [x] **First deploy** — production since 0.2.0: database and role `mailroom` on the shared
      Postgres, backup line, Quadlet units, Traefik route.
- [x] **Release pipeline** — a release tag builds and pushes both images; the host's config
      repository deploys them.
