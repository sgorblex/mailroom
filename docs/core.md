# mailroom-core

The mail layer under the app's web client and MCP mail tools, and the filter engine under its
filters: accounts and their sealed secrets, IMAP and SMTP, the send gate, rendering, the audit
items and filters, in `backend/core` (package `mailroom-core`, module `mailroom_core`). It never imports `mailroom`
([`CONTEXT.md`](CONTEXT.md) — *The component family*). Its tests run on their own:
`mise run test-core`, or `mise run test-core-mail` with throwaway Dovecot and Mailpits.

It started as the Python port of mailroom-mcp's TypeScript server, released as mailroom-core 0.1.0
to 0.1.3 on PyPI, with the same behaviour, so that the server and the app could share it. Where it
differs from that server, the differences are deliberate, and each section below lists them. Core
has no tool layer: it holds the logic (refs, pages, rendering, the envelope, the gate) and the text
the tools answer with, and the app's MCP writes thin tools over it. The filter engine
(*Filters*) was imap-thingy, a library on PyPI, and joined with its history on 2026-11-11.

## Stack

| Topic | Decision |
|---|---|
| Language | Python 3.14, a member of the backend's uv workspace. Ruff, mypy (strict) and pytest. |
| IMAP | `imapclient` (BSD-3, over stdlib `imaplib`), synchronous; a host calls it from threads of its own and bounds the waiting itself (the app:
`mailroom.mail.pool`). Per account `pool_size` slots (1 by default), each a lazily opened connection owned by a lock; one selected mailbox per connection. A connection is dropped on any socket error or timeout (the account's other idle ones too when the server is unreachable), and after 5 minutes unused, with a single retry, always on a newly opened connection and never for a bug in core. A cancelled await does not stop the thread: a host whose deadline passes calls `interrupt(account)`, which shuts down the sockets of the connections in use, so the blocked calls fail at once with `unreachable` and are not retried. Login is AUTHENTICATE PLAIN (UTF-8) when offered, else LOGIN for ASCII credentials; login errors are logged by type only. `reachability` never raises, and a busy account is not reported down; its probe waits a second for a connection and `Timeouts.probe` (10 s) for each connect and reply, with no retry. Reads use EXAMINE and `BODY.PEEK[part]<0.N>`, decoded here (no BINARY: imapclient cannot parse literal8). COPYUID and APPENDUID come from `imaplib`'s response codes. `mailroom_core.mail.MailConnections` takes callables that look up an account and its password, so a host keys one instance per user; `close(account)` makes it log in afresh after a change. Folder roles come from the special-use flags, else from well-known names, in English and Italian only. `IdleWatch` watches one folder through IDLE on a connection of its own, outside the slots (which close after 5 minutes unused), opened with the same TLS and login and selected with EXAMINE. Its thread waits in IDLE in 1 s slices, so `stop()` only sets an event. Each IDLE ends on a response or after 25 minutes; the watch then sends a NOOP and hands each batch of responses, the NOOP's included, to `on_change` as an `IdleChange` (`new_mail` on an EXISTS). `on_connect` runs on every connect; callbacks run on the watch's thread and their errors are logged by type. Reconnects back off from 5 s to 300 s; the watch ends with `failure` when its first connect fails, or on `auth_failed`, `mailbox_missing`, `invalid_argument` or `unsupported` (no IDLE). `test_login` logs in and out on such a connection, bounded by `Timeouts.probe`. |
| SMTP | Stdlib `smtplib` (`SMTP_SSL`, or `SMTP` and `starttls`) in a worker thread, always with an explicit verifying SSL context; one connection per send; partially refused recipients from `sendmail`'s result. |
| HTML to text | Our own port of the `html-to-text` subset the TypeScript server used, with an iterative walk and chunked output, over `lxml.html` (BSD-3) parsed from UTF-8 bytes with `huge_tree`. Not an HTML5 tree builder: lexbor, html5ever and html5lib are quadratic on deeply nested markup, lxml is linear and bounds the depth. |
| Quotes | A vendored port of `email-reply-parser` 2.3.9 (crisp-oss, MIT), with its multi-language quote headers and signatures. Not PyPI's `email_reply_parser`, which knows English only. |
| HTML for display | `nh3` (MIT, ammonia) over markup normalised by lxml, for the app's message view: tag, attribute and CSS-property allowlists, `url()` dropped from styles, remote `src` removed, `cid:` images inlined as `data:`. The app shows the result in a sandboxed iframe under its own CSP. |
| Crypto | `cryptography`: AES-256-GCM, HKDF. |
| Validation | pydantic v2, for the config and the settings; TOML through `tomllib`. |
| Store | Postgres through SQLAlchemy 2 (Core, synchronous) on psycopg 3, and Alembic. |
| Filters | `mail-parser` parses the sources the engine fetches through `mail` for its predicates, until they read messages through core ([`TODO.md`](TODO.md) — *Engine parsing on core's MIME*); `regex` for user patterns, which can time out. |

## Modules

| Module | Holds |
|---|---|
| `config` | The settings document: accounts, identities, send modes, limits (`CoreConfig`) |
| `crypto` | Sealed secrets |
| `store` | `AccountStore` and core's migrations, in schema `core` |
| `render` | Bodies to text, quotes, the untrusted-content envelope, `safe_html` |
| `mail` | Connections (`mail.imap`), operations on a selected folder (`mail.selected`), IDLE (`mail.idle`), refs, read, organize, compose, drafts, the tools' text, audit items |
| `send` | SMTP and the send gate |
| `filters` | The filter engine: criteria, actions, rules as data (specs), runs, the runner and IDLE monitor, reports |
| `testing` | A pytest plugin (no-network guard, store and mail fixtures) the app's tests load too |

**Public API.** What each subpackage exports in `__all__`; everything else is how it is done.
Rules, the first three kept by checks and the fourth by a test:

- every public name has a docstring, in the style the code already uses (ruff's `D1` rules on
  `core/src`; magic methods excepted, since their operator says what they do);
- the app imports a subpackage, never one of its modules (the app's
  `tests/test_component_deps.py`);
- inside core, `filters` may use `mail`, never the other way round, and nothing imports `mailroom`
  (`core/tests/test_boundaries.py`);
- core works with no app, database or server for filtering: accounts from plain config, rules,
  runs, an IDLE monitor and their reports (`core/tests/filters/test_engine_mail.py`, which builds
  everything from `connections_from` and a settings dict; `test_boundaries.py` keeps `filters`
  and `mail` from importing `store`);
- no semver while core is unpublished: a breaking change fixes its callers in the same pull
  request and is marked `!` with scope `core`.

**Integration points.** Core takes its host's database and its users: the host runs core's
`migrate` (the app's `migrate` task runs it before the app's own), and binds an `AccountStore` to
one user. Every row in schema `core` carries its `user_id`; `STANDALONE_USER` (`owner`) is the
user rows written by mailroom-mcp belong to. The tools' text takes the host's tool names
(`ToolNames`), so the app's MCP points the model at its own tools.

**Plain config.** Without the app, `mail.connections_from` builds the connections from the
settings the app stores, `[[account]]` tables and an optional `[limits]`, checked by
`core_settings_or_issues` (`ConfigError` lists every problem), and each account's password by id;
`smtp` and an identity are required as in the app, and the passwords stay out of the file.
`MailConnections.from_accounts` does the same from `AccountConfig`s.

```python
import os
import tomllib

from mailroom_core.mail import connections_from

with open("accounts.toml", "rb") as file:
    settings = tomllib.load(file)
connections = connections_from(settings, {"personal": os.environ["PERSONAL_PASSWORD"]})
```

## Store

Schema `core`, with its own Alembic history whose version table lives in that schema, so it
shares a database with the app's (`mailroom_core.store`, `AccountStore`):

- **`settings`**: the settings document, one row per revision, the current settings being the
  highest. Revisions are numbered, and revisions and account ids are unique per user (migration `core_0002`,
  which gave the rows stored before it to `owner`). A save waits up to 5 s for another save of the
  same user's settings (a per-user advisory lock, so users never wait on each other), then fails
  with `SettingsBusy`.
- **`account_secrets`**: the sealed passwords.
- **`sends`**: the send log. `claim_send` writes a row before SMTP, counting and inserting under a
  per-user lock, so concurrent sends admit exactly the limit. A row with a `key` (`core_0003`,
  unique per user) is kept at least 30 days, the others at least one, pruned on the user's next
  send, and a retry of its key can't deliver twice: `sent` replays, `sending` answers the same id,
  `failed` claims again, and `unknown`, which is also what `sending` reads as past 360 s, claims
  again only with `confirm_resend`. Each attempt counts towards the limit. The claim locks the
  key's row, so an outcome lands either before its read, and replays, or after it, and misses the
  reclaimed row.

`forget_user` (or `AccountStore.forget`) deletes one user's rows, under the lock a save of their
settings takes, in the host's transaction. Times are `timestamptz` and JSON is `jsonb`. The tests
copy a migrated template database on the server `TEST_DATABASE_URL` names
(`mailroom_core.testing`).

## Render

`mailroom_core.render` matches the TypeScript server's output byte for byte, checked against
outputs it produced (`backend/core/tests/fixtures/render-typescript-outputs.json`). Lengths and
offsets (`max_chars`, `offset`, `next_offset`, the header caps) stay in UTF-16 units, so a window or
a cap falls where the TypeScript's does; the regular expressions keep JavaScript's `\s`, `\w`, `.`,
`^` and `$`. Where lxml and htmlparser2 build different trees from misnested markup (an `<a>`
inside an `<a>`, a block closing the inline element around it, content outside `<body>`, nesting
past libxml2's 2048 levels) the text can differ. Deliberate differences, all stricter or bounded:

- **Quote detection is bounded**, since Python's matcher holds the GIL and the library's patterns
  backtrack polynomially, so one hostile mail would stall a multi-user host. A line over 256
  characters is never a quote header or a signature; only the 2000 last distinct lines are tested;
  a header wrapped over two lines is joined only within the last 500 lines, and over two lines at
  most. The Outlook header search is linear. Real replies fall well inside these bounds, and a
  2 MB hostile body collapses in about a second.
- **The envelope rewrites every lookalike of its tag:** the plain spellings are rewritten first,
  and whenever the folded result still spells the tag name, the content is passed on folded and
  rewritten again, also when a plain tag is present too. Deciding by presence after the rewrite,
  not by comparing counts, keeps a plain tag that folding merges away (a combining mark after it)
  from sparing a fullwidth one.
- **The quote marker is a parameter** (`marker=` of `render_body` and `collapse_quotes`, `None`
  for none); `RenderedBody.quoted_lines` and `signature_lines` say what was collapsed, so a host
  can word it its own way.

`render.safe_html` is the app's (see *Stack*, HTML for display).

## Read

`mailroom_core.mail` holds what the read tools do, minus the tool plumbing: `list_messages`,
`search_messages` (with `SearchQuery`), `thread_page`, `read_message`, `read_attachment` and
`html_view` return typed results (`Page` of `MessageSummary`, `MessageView`, `Attachment`), and
`mailroom_core.mail.text` writes the tools' text from them. Refs and cursors are the TypeScript
server's bytes: a ref (`mailroom_core.mail.ref`) is base64url of `[1, account, folder,
uidValidity, uid]`, and one whose folder's UIDVALIDITY has changed fails with `stale_reference`,
never naming another message. Refs are not signed: the host checks the account is the caller's. Header text comes decoded and flattened. RFC 2047 words, RFC 2231 parameters,
transfer encodings (fetched in windows with `BODY.PEEK[part]<offset.n>` up to the cap), charsets
and `format=flowed` are decoded. Pages read the sources of different accounts in threads of their
own, and fetch previews in one FETCH per body part number rather than one per message. A page item
carries the arrival time as well as the Date header, and the size; a view carries `has_html` and,
when the read asks for `remote_check` (off by default, as it costs the HTML's download and a
sanitising pass), `has_remote_content`. A ref's error names no tool. Deliberate choices:

- **Inline parts are not attachments:** a part of a multipart/related with a Content-ID and no
  attachment disposition is in `inline_parts` (what `html_view` inlines), not in `attachments`, and
  does not set `has_attachments`. An attachment call still returns it.
- **One size per part:** a part's size is its decoded size, estimated from BODYSTRUCTURE's encoded
  one in listings (base64 at 57 bytes per 78, quoted-printable as encoded) and exact once
  downloaded (`Attachment.size`). An attachment whose estimate is clearly past the cap is refused
  before any download, and downloads come in windows of at most 1 MiB.
- **Search text is never syntax:** SEARCH arguments are written here, not by imapclient, which
  sends `(`, `)` or `{5}` unquoted: text is an atom only when it is one, else a quoted string, or a
  literal under `CHARSET UTF-8` when not ASCII. Text with CR, LF or NUL is refused
  (`invalid_argument`), and so is non-ASCII text opening a parenthesised group; one closing it
  gets `ALL` before the parenthesis, which cannot follow a literal. The arguments still go out
  through imapclient's private sender (`_raw_command_untagged`, which writes the literals), so core
  pins imapclient below 4.2.
- **A charset is a text encoding:** a sender's charset naming one of Python's other codecs (`hex`,
  `rot13`, `zlib`, `idna`…) counts as unknown, and decoding never raises.
- **Provider quirks by capability**, never by hostname: Gmail is `X-GM-EXT-1` in CAPABILITY.
- **Gmail attachments by X-GM-RAW:** Gmail ignores SEARCH HEADER, so on a Gmail account
  `has_attachment` becomes Gmail's own `has:attachment` (X-GM-RAW), grouped with any `gmail_query`
  into that one criterion.

## Organize

`mailroom_core.mail.organize`: `move_messages`, `trash_messages`, `archive_messages` (to the folder
with role `archive`) and `set_flags` take up to 50 refs from any folders, check every ref and its
account before writing anything, and act per folder, a failing folder leaving the others done.
They return an `OrganizeResult`: per folder a `GroupResult` (count, new refs from COPYUID, Junk
training, flags read back, or the `MailError`), and per message an `AuditItem`
(`mailroom_core.mail.audit`, the sender's address and a hash of the subject) with the call's
`outcome`. `organize_text` writes the tools' text; a host raises a lone folder's error itself.

They are built on `mailroom_core.mail.selected`, as `reply.mark_answered` and
`drafts.remove_draft` are: operations by UID on the folder a `with_mailbox` session holds
(`search_uids`, `fetch_sources` with `BODY.PEEK[]`, `fetch_envelopes`, `move_uids`,
`store_flags`, `delete_uids`, `listed_folders`), with the refusals of a move
(`move_target`). Whatever acts inside a session of its own uses them, folders included,
since a second connection of the account would wait for the one it holds. `store_flags` sets only
`\Seen`, `\Flagged` and `\Answered`; `delete_uids` needs UIDPLUS and serves only a draft
Mailroom wrote (*Safety*) and a filter's `delete`. The filter engine runs on them too (*Filters*).

imapclient's `move()` needs MOVE, so without it core issues UID COPY, STORE `\Deleted` and UID
EXPUNGE of exactly those UIDs itself, which only UIDPLUS allows; a server with neither is refused.
When that COPY succeeds and the removal fails, the group fails but keeps the copies' new refs, its
error says they were copied but may not have been removed, so the move must not be repeated, and
names the new refs, so a host raising it alone still shows them; its audit items carry `copied`.

## Compose

`mailroom_core.mail.compose` (with `recipients`, `identity`, `reply`, `attachments` and
`outgoing`, the caps of the tools' input) builds outgoing messages with the standard library.
`compose` takes the Message-ID, so a draft's versions can share one. The `email` package builds
only the MIME body: its header classes parse their input, which would decode an encoded word in a
subject or a name back into the header, CR/LF included, and would turn a long Message-ID into an
encoded word, so the message's own headers are written by hand. Deliberate choices:

- `X-Mailroom-Draft` is `1`, or `1; answers=<ref>` when sending the draft is to set `\Answered` on
  the message it answers. Only those two forms count as Mailroom's mark.
- The `wire` copy never carries that header, and `compose` checks every recipient with
  `deliverable_address`, so a draft addressed to an address from IMAP that no send would accept is
  refused when saved, not when sent.
- Domains are punycoded with the standard library's IDNA 2003, which would turn `ß` and `ς` into
  other letters, hence another domain: an address with them in its domain is refused.
- Message-IDs must be printable ASCII and at most 900 characters; others are dropped from the
  threading headers.
- A `multipart/*` or `message/*` attachment goes as `application/octet-stream`, base64, byte for
  byte: a multipart needs a boundary, and `message/*` may not be base64 (RFC 2046, 5.2.1).
- `=?` in an attachment's filename is written `= ?`, because the `email` package would decode it as
  an encoded word when it parses its own header.
- `deliverable_address` refuses an address over 254 characters once punycoded, the most SMTP can
  carry: a header word is never split, so a longer one could pass the limit of a line.
- `attachments_from` takes attachment and calendar parts, not what the HTML shows in place, which
  core does not list as attachments.

**`from` resolution:** an explicit `from` must be one of the account's identities, else the call
fails and lists the valid ones. A reply or forward without one matches the original's `To`, then
`Cc`, then `X-Original-To` / `Delivered-To`, against the identities; otherwise the account's
default identity. The chosen identity is returned, and the app's MCP tools always echo it.

## Drafts

`mailroom_core.mail.drafts`: `save_draft` is `check_removable`, the APPEND, then removing the old
version. A `compose_id` gives every version of a draft the same Message-ID,
`<compose_id@domain>`, so `find_drafts` can recover a save whose answer was lost. Deliberate
choices:

- The old version goes only once the new one has a ref. Without one (no APPENDUID, and a search
  that found nothing) the save answers no ref and both versions stay. A save whose removal fails
  stands, answering `replaced = "failed"`, since the client needs the new ref either way.
- The search that stands in for APPENDUID looks only at UIDs from the folder's UIDNEXT before the
  APPEND on: the older versions share the Message-ID, and an index lagging behind could otherwise
  return one of them.
- `find_drafts` searches by `<compose_id@`, so a change of identity between versions does not hide
  any, and checks each match's ENVELOPE: a server's search can go by words (Dovecot's does).
- Each save answers `autosave`, false without UIDPLUS, where every version would land in Trash.
- `lossy` also covers a draft with an HTML alternative, inline images or an invite with no name,
  none of which a save keeps. A named `.ics` is an attachment, and lists as one.
  `Draft.answers` is dropped when it names another account: the header is only as trustworthy as
  whoever wrote the draft.
- `remove_draft` tags a failed removal `removed: "failed"`, for an expunge and for a move to Trash,
  however it failed, a refusal before either included. `try_remove_draft` answers `failed` instead
  of raising, for a save that went through or a message delivered.

## Send gate

`mailroom_core.send`, on smtplib, with one connection per send and a verifying TLS context.
`send_through_gate` runs three phases a host can also call apart, to answer before the message has
left: `admit` (`Admitted`, or `Drafted`, or `Earlier` for a retry), `deliver` and `file_sent`. Only
`admit` makes an `Admitted`, and each is good for one delivery. `prepare_draft` reads a draft for
sending, and `settle_draft` and `mark_original` do what follows a sent draft or reply.

- **`send_mode` limits agents.** `admit` takes `by`, `user` or `agent`: `off` refuses both, and
  `drafts` and `approval` apply to agents only, so a click in the app sends. The hourly limit and
  the recipient cap bind both. An agent's draft the mode would only draft again is refused. The
  audit item carries `by`, and `delivery` can be `repeated`.
- **The attempt is written down before SMTP** (`claim_send`), and ends `sent`, `failed` or
  `unknown`. A `key`, the draft's `compose_id` in the web client, makes a retry safe: `sent`
  replays the result, `sending` answers the same id, `failed` is claimed again, and `unknown` is
  refused with `delivery_unknown` unless a person confirms the resend. Without a key, as for an MCP
  send, a retry is a new message.
- **`failed` means the server said no, or nothing left.** A send fails if it broke before DATA was
  entered, or on a 4xx or 5xx reply to DATA or to the message. Any other end after entering DATA is
  `unknown`, the message having possibly been taken and the reply lost: a timeout, a lost
  connection, a reply that is not one, or a code that is neither. smtplib makes up codes of its own
  (500 for a long line, -1 for garbage), which do not count as replies. An exception nobody
  classified is `unknown` as well.
- **After delivery, nothing is a failure.** Filing in Sent, capabilities included, can only end in
  `filed = false`, and the `sent` item is audited either way; removing the draft and setting
  `\Answered` can only add a note, a `SendNote` with a stable `code` beside its English `text`, so a
  host can word it itself. If the store cannot be reached to record the outcome, the row stays
  `sending` and reads `unknown` past the ceiling, and the send's result stands.
- **A draft that was not filed stays.** It is then the only copy in the account, so `settle_draft`
  takes `filed`, still sets `\Answered`, and notes `draft_kept`. The send's key keeps a retry from
  delivering it again while its row is kept, at least 30 days.
- **One write after delivery.** `deliver` writes the row, and what followed goes in once, by
  `send_through_gate` or by the host, when all of it is over; `file_sent` only answers. A host that
  stops waiting for a step can write its result without that step overwriting it later.
- **Timeouts:** 15 s for the connection, the greeting and all it takes to have TLS up, 60 s for
  each reply after that, 60 s plus the size at 128 KiB/s for DATA, and a watchdog at 360 s for the
  whole send, the store's `SEND_CEILING`, against a server dripping bytes under the others.
  `Transfer.abort` lets a host do the same; it shuts down a duplicate of the TCP socket, which
  works while another thread is inside TLS. A row reads `unknown` from its claim plus the ceiling,
  so that is when `deliver` ends the attempt, whenever it started: one queued past it is failed
  rather than sent, and one still running is aborted. Abort and row meet only within what it takes
  a socket to shut down, and a resend still takes a person confirming `unknown`.
- **One login attempt,** PLAIN when offered, else LOGIN, in UTF-8. smtplib's `login` tries every
  mechanism on a refusal, each counting against the server's limit, and speaks only ASCII. EHLO
  gives `[127.0.0.1]`, which keeps the machine's name to itself and costs no DNS lookup.
- The SMTP log line has the error's type and reply code, not its text.
- A prepared message goes to the recipients of the `MessageInput` `admit` checked, not to whatever
  envelope it came with, and each recipient is named once. Every line goes out ending in CRLF,
  which a draft stored by another client may not. `prepare_draft` fixes them before it strips Bcc
  and the draft mark, so that the strip and the wire agree on what a line is.
- A bare CR in a stored draft's headers is refused, `invalid_argument`. ENVELOPE reads it as data
  and the wire as a line break, so a From, Message-ID or Cc behind one would be checked in one
  reading and go out in the other. RFC 5322 forbids it and Mailroom never writes one, and saving
  the draft rebuilds its headers, so refusing closes every reader at once.
- A stored draft whose From is anything but one address in one field is refused the same way: only
  that address is checked against the account's identities, and the field goes out as stored.
- A draft with no Message-ID, which Mailroom never writes, gets one when sent: it could not be
  found again in Sent, and would audit as nothing.
- A stored draft may reach 1.5 times `attachment_max_mb` plus 2 MiB, not `attachment_max_mb`, or a
  draft with attachments near the cap, which base64 grows by 37%, could be saved and never sent.

## Filters

The filter engine, `mailroom_core.filters`: rules that sort mail on an IMAP server, built in Python
or loaded from their data form. Its tests are in `tests/filters`.

- **A filter** is a criterion and an action: `Filter(criterion, action)` or
  `If(criterion).then(action)`.
- **A criterion** has two halves that must agree: an IMAP SEARCH query (`IMAPQuery`, run by the
  server) and a predicate on the fetched `Message`. `is_efficient` says the query alone is exact,
  so nothing is fetched; a looser query (`SUBJECT` matches substrings, pattern criteria search
  wider) is only a prefilter, and the predicate decides. `&`, `|` and `~` combine both halves,
  efficient only if every operand is. `IMAPQuery.Deferred` builds its query at search time, for
  criteria relative to the run (`OlderThanDays`).
- **User patterns** compile when the criterion is built (a bad one is a `SpecError` on load) and
  match with `regex`, never `re`, under the process-wide match timeout (1 s for each match by
  default; past it, `MatchTimeoutError`).
- **An action** acts on a set of UIDs of the selected folder (`Selected`: the run's
  `MailboxContext` and the server's delimiter); `+` or `.then()` chains actions. They are core's
  operations (*Organize*): `move_to` and `trash` are the client's moves (`move_target`, then
  `move_uids`, with its COPY and `UID EXPUNGE` fallback under UIDPLUS), so a folder that is not
  there is `mailbox_missing` and Drafts or Sent are refused, while a move into the folder being
  filtered (Trash from Trash too) is skipped; `trash` without a folder takes the special-use
  Trash (`unsupported` without one). Flag actions are `store_flags`. `delete` removes for good
  (`delete_uids`, UIDPLUS only), which a filter may do because the user wrote it, saw its dry run
  and enabled it ([`requirements/filters.md`](requirements/filters.md)).
- **Specs** are the rules as data, the form the app stores (`spec`): leaves
  `{"type", "args"}`, the combinators `and`, `or`, `not`, `seq` for actions, and a filter
  `{"criterion", "action"}`. `load_filter` rebuilds an equal filter, `to_spec()` gives it back and
  `json_schema()` describes the format. A new criterion or action needs its `spec_type`, an entry
  in `spec/schema.json`, its name in the facade's `__all__` and a case in
  `tests/filters/test_spec.py` (whose coverage test, over that `__all__`, fails otherwise). **A change to an existing spec ships with a migration of the stored filters**, in
  the same pull request.
- **A run** (`Folder.run`, `run_into`) is one `with_mailbox` session for all the filters given,
  in order, each seeing what the earlier ones did: search, fetch only what the query cannot decide
  (`fetch_sources`, which never sets `\Seen`, parsed by mail-parser; cached for the run, dropped
  once acted on), act, or in a dry run only report, with the folder selected read-only (a dry run
  that lost its connection is retried once, its report started afresh). The folder's name is
  joined with the server's delimiter, asked before the session. Every search ANDs `UNDELETED`, so
  an original a move copied but could not expunge never matches again. `newest=n` (at least 1)
  looks the folder's newest `n` messages up once (`exists-n+1:exists`), ANDs their UID range into
  every search, so an earlier filter's expunge does not shift it, and sets the report's
  `limited_to_newest`. Its `FolderReport` gives per filter what it matched (from one `ENVELOPE`
  fetch) and did; `run_into` fills it as filters finish, so a failure keeps what was done. Mail
  failures are `MailError`s; anything else (a pattern's `MatchTimeoutError`, a bug) leaves the
  session as itself, never as a `server_error`.
- **Searches**: `IMAPQuery.criteria()` writes a query as core's criteria, an AND flat, an AND
  inside OR or NOT as a group, and `search_uids` sends them (`CHARSET UTF-8` only for non-ASCII
  text; a line break in a value is `invalid_argument`).
- **The runner**: `Rules` pairs folders with their filters; `run_rules` runs them in turn, or one
  thread per folder with every failure raised together (`RunRulesError`); `starred_only` limits
  rules to starred messages, unstarring them first; `monitor` keeps an `IdleWatch` per folder
  until stopped and runs the folder's filters when it connects and on new mail, on a pooled
  connection, never on the watch's own; a folder whose name cannot be looked up is logged by
  code and skipped, the others watched. A run holds one of its account's connections throughout,
  so folders of one account run in parallel only as far as the connections' `pool_size`.
- **Connections** are the host's `MailConnections`: `Account(connections, account_id)` names one
  of their accounts, so the engine has the client's TLS (STARTTLS too), logins, pool, retries and
  `interrupt`. Its loggers sit under `mailroom_core.filters`.

## Safety

The main adversary is the content of an email: an instruction in a message a model reads. Core's
share of the defence:

- **The untrusted-content envelope:** mail-derived text in the tools' answers is wrapped in
  `<untrusted_email nonce="…">` … `</untrusted_email nonce="…">`, with a short warning before and
  after. The nonce is random per answer, and the tag name inside the content is rewritten (see
  *Render*), so a message can neither close the envelope nor open a fake one. Homoglyphs from other
  scripts pass: this is a mitigation, not a guarantee.
- **Hidden characters go:** every `\p{Default_Ignorable_Code_Point}` and format character
  (`\p{Cf}`) is removed from bodies and headers, and C0/C1 controls other than tab and newline; the
  count is reported, since hidden characters are themselves a phishing signal. Headers are
  flattened to one line (CR, LF, U+2028/9 become spaces) and capped at 998 characters.
- **No destructive deletes:** trash moves to `\Trash`. Nothing is expunged but what a move has
  copied elsewhere on a server without MOVE (*Organize*), and a draft Mailroom wrote itself
  (`X-Mailroom-Draft`) when it is replaced or deleted, always by `UID EXPUNGE` of exactly those UIDs
  (UIDPLUS required); a draft written anywhere else is moved to Trash. A filter's `delete`, the
  user's own rule, is the one exception, and expunges exactly its UIDs too: without UIDPLUS it is
  `unsupported`.
- **Audit items:** each write and send, refused ones included, yields items (which the app does
  not keep yet: [`TODO.md`](TODO.md)) naming the account,
  folder, UID, Message-ID, sender and recipients in clear, and the subject only as a SHA-256.
- **Errors** are `MailError` codes with fixed messages, never the server's or a library's wording.
  An `unreachable` caused by a certificate that did not verify (itself, or wrapped in a login
  error) ends with the fixed `CERTIFICATE_DETAIL`, which a host may recognise.
  Secrets never reach a log line, an error or a tool's text.

## Compatibility

**Sealed secrets** stay byte-compatible with mailroom-mcp's, so its accounts import unchanged:
HKDF info `mailroom account secrets`, AAD `mailroom/v1/<account>/<purpose>`,
`format ‖ nonce ‖ ciphertext ‖ tag`. The AAD names no user: account ids are unique per user only,
so a blob copied to another user's account of the same id would open. Doing that takes write access
to the database, which can replace the settings anyway.
