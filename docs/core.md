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
`mailroom.mail.pool`). Per account `pool_size` slots (1 by default), each a lazily opened connection owned by a lock; one selected mailbox per connection. A connection is dropped on any socket error or timeout (the account's other idle ones too when the server is unreachable), and after 5 minutes unused, with a single retry, always on a newly opened connection and never for a bug in core. A cancelled await does not stop the thread: a host whose deadline passes calls `interrupt(account)`, which shuts down the sockets of the connections in use, so the blocked calls fail at once with `unreachable` and are not retried. Login is AUTHENTICATE PLAIN (UTF-8) when offered, else LOGIN for ASCII credentials; login errors are logged by type only. `reachability` never raises, and a busy account is not reported down; its probe waits a second for a connection and `Timeouts.probe` (10 s) for each connect and reply, with no retry. Reads use EXAMINE and `BODY.PEEK[part]<0.N>`, decoded here (no BINARY: imapclient cannot parse literal8). COPYUID and APPENDUID come from `imaplib`'s response codes. `mailroom_core.mail.MailConnections` takes callables that look up an account and its password, so a host keys one instance per user; `close(account)` makes it log in afresh after a change. Folder roles come from the special-use flags, else from well-known names, in English and Italian only. `quota(account)` asks `GETQUOTAROOT INBOX` when the server offers QUOTA (`Capabilities.quota`) and gives a `Quota` of that root's `STORAGE` (in bytes) and `MESSAGE` usage and limit, None without QUOTA, when the server answers `NO` to GETQUOTAROOT (a temporary `NO [UNAVAILABLE]` aside, which fails as `unreachable`) or without either limit; an answer with no `QUOTAROOT` line counts every `QUOTA` line it carries. `IdleWatch` watches one folder through IDLE on a connection of its own, outside the slots (which close after 5 minutes unused), opened with the same TLS and login and selected with EXAMINE. Its thread waits in IDLE in 1 s slices, so `stop()` only sets an event. Each IDLE ends on a response or after 25 minutes; the watch then sends a NOOP and hands each batch of responses, the NOOP's included, to `on_change` as an `IdleChange` (`new_mail` on an EXISTS). `on_connect` runs on every connect; callbacks run on the watch's thread and their errors are logged by type. Reconnects back off from 5 s to 300 s; the watch ends with `failure` when its first connect fails, or on `auth_failed`, `mailbox_missing`, `invalid_argument` or `unsupported` (no IDLE). `test_login` logs in and out on such a connection, bounded by `Timeouts.probe`. |
| SMTP | Stdlib `smtplib` (`SMTP_SSL`, or `SMTP` and `starttls`) in a worker thread, always with an explicit verifying SSL context; one connection per send; partially refused recipients from `sendmail`'s result. |
| HTML to text | Our own port of the `html-to-text` subset the TypeScript server used, with an iterative walk and chunked output, over `lxml.html` (BSD-3) parsed from UTF-8 bytes with `huge_tree`. Not an HTML5 tree builder: lexbor, html5ever and html5lib are quadratic on deeply nested markup, lxml is linear and bounds the depth. |
| Quotes | A vendored port of `email-reply-parser` 2.3.9 (crisp-oss, MIT), with its multi-language quote headers and signatures. Not PyPI's `email_reply_parser`, which knows English only. |
| HTML for display | `nh3` (MIT, ammonia) over markup normalised by lxml, for the app's message view: tag, attribute and CSS-property allowlists, `url()` dropped from styles, remote `src` removed, `cid:` images inlined as `data:`. The app shows the result in a sandboxed iframe under its own CSP. |
| HTML to send | The same allowlists through nh3 (`render.outgoing_html`), with nothing that reaches out: links only `http(s)` and `mailto`, images only `cid:` parts the message carries, a raster `data:` image turned into such a part, any other image left out, element and all. Its allowlists and lxml pass are `safe_html`'s, in one private module (`render._html_policy`). A `div` keeps `SIGNATURE_MARK` (`data-mailroom-signature`), the mark of the owner's signature, unless `signature_mark` is false, as for a quoted or forwarded original (`quoted_original_html`, `forwarded_original_html`). |
| Crypto | `cryptography`: AES-256-GCM, HKDF. |
| Validation | pydantic v2, for the config and the settings; TOML through `tomllib`. |
| Store | Postgres through SQLAlchemy 2 (Core, synchronous) on psycopg 3, and Alembic. |
| Filters | `regex` for user patterns, which can time out. The sources the engine fetches are parsed by `mail` (`parsed_message`, on the standard library's `email` and core's own header decoding). |

## Modules

| Module | Holds |
|---|---|
| `config` | The settings document: accounts (with an optional name, colour and authentication server, `authserv_id`), identities, send modes, limits (`CoreSettings`) |
| `crypto` | Sealed secrets |
| `store` | `AccountStore` and core's migrations, in schema `core` |
| `render` | Bodies to text, quotes, the untrusted-content envelope, `safe_html`, `outgoing_html` |
| `mail` | Connections (`mail.imap`), operations on a selected folder (`mail.selected`), a folder's UIDs and flag changes (`mail.changes`), what an index keeps per message (`mail.index`), IDLE (`mail.idle`), refs, read, organize, folders (`mail.folders`), messages parsed for the filters (`mail.parsed`), compose, drafts, the tools' text, audit items |
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
one user. Every row in schema `core` carries its `user_id`. The tools' text takes the host's tool
names (`ToolNames`), so the app's MCP points the model at its own tools.

**Plain config.** Without the app, `mail.connections_from` builds the connections from the
settings the app stores, `[[account]]` tables and an optional `[limits]`, checked by
`core_settings_or_issues` (`ConfigError` lists every problem), and each account's password by id;
`smtp` and an identity are required as in the app, and the passwords stay out of the file. An
account's `id` is a positive integer, unique among the accounts (`id = 1`): the app hands them
out, and plain config writes its own. `MailConnections.from_accounts` does the same from
`AccountConfig`s.

```python
import os
import tomllib

from mailroom_core.mail import connections_from

with open("accounts.toml", "rb") as file:
    settings = tomllib.load(file)
connections = connections_from(settings, {1: os.environ["PERSONAL_PASSWORD"]})
```

## Store

Schema `core`, with its own Alembic history whose version table lives in that schema, so it
shares a database with the app's (`mailroom_core.store`, `AccountStore`):

- **`settings`**: the settings document, one row per revision, the current settings being the
  highest. Revisions are numbered per user (migration `core_0002`, which gave the rows stored
  before it to `owner`). `core_0005` made the account names of each user's current settings
  unique, compared NFKC-normalised, trimmed and case-folded: an account whose name an earlier
  one has became unnamed (older revisions untouched; its downgrade does nothing). A save waits
  up to 5 s for another save of the same user's settings (a per-user advisory lock, so users
  never wait on each other), then fails with `SettingsBusy`.
- **`account_ids`**: the sequence account ids come from (`new_account_id`), across the instance
  and never handed out twice, even for an account that is never saved.
- **`account_secrets`**: the sealed passwords.
- **`former_slugs`**: the slug each account had before ids became numbers (`core_0004`, which
  numbered every id in the settings, all revisions, the secrets and the send log, naming an
  unnamed account by its slug unless another account of that revision has that name, compared
  trimmed and case-insensitively). Its secrets stay sealed to that slug
  (`AccountStore.former_slugs`); the downgrade gives each id back its former slug, else its name
  when that is a free slug, else `account-<id>`, and a secret sealed to an id no longer opens.
  A host's own migration reads it to map its rows, and is the one place outside core that writes
  here: it adds rows for slugs only the host stored (an account removed before), and rewrites the
  `<account>/…` draft keys in `sends`; core's downgrade then reads those rows too.
- **`sends`**: the send log. `claim_send` writes a row before SMTP, counting and inserting under a
  per-user lock, so concurrent sends admit exactly the limit. A row with a `key` (`core_0003`,
  unique per user) is kept at least 30 days, the others at least one, pruned on the user's next
  send, and a retry of its key can't deliver twice: `sent` replays, `sending` answers the same id,
  `failed` claims again, and `unknown`, which is also what `sending` reads as past 360 s, claims
  again only with `confirm_resend`. Each attempt counts towards the limit. The claim locks the
  key's row, so an outcome lands either before its read, and replays, or after it, and misses the
  reclaimed row. A key a host derives from a ref (the app's `ref-<sha256>` for a draft without a
  `compose_id`) changed with the refs at `core_0004` and cannot be rewritten: a send claimed under
  the old one does not guard a send of the same draft after the upgrade.

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

`render.safe_html` is the app's (see *Stack*, HTML for display). Before nh3 cleans, an lxml pass
counts the remote images (`remote_images`: `<img>` sources, `srcset` candidates, `background`
attributes, CSS `url()`s; `loadable_remote_images`: the `<img>` sources alone, which `remote`
shows) and rewrites each link's `title` to the host it goes to, in the IDNA form a browser
computes (UTS #46, non-transitional, with the `idna` package: `ß` stays `ß`), and drops it when the
host is none a browser would visit (a space, a `>`); nh3 keeps only titles that pass wrote. `render.hosts` decides when a link's text names another
host than its own (neither it nor a parent or child of it, no public-suffix list):
`safe_html` then puts a marker after the link, and `html_to_text` adds `(⚠ host)` after it, so the
text body, MCP's included, carries the mark too. A backslash in an `http(s)` URL is read as a
slash, as a browser does.

## Read

`mailroom_core.mail` holds what the read tools do, minus the tool plumbing: `list_messages`,
`search_messages` (with `SearchQuery`), `thread_page`, `read_message`, `read_attachment` and
`html_view` return typed results (`Page` of `MessageSummary`, `MessageView`, `Attachment`),
`read_source` the whole source under EXAMINE (`too_large` over its cap, never cut), and
`mailroom_core.mail.text` writes the tools' text from them. Refs and cursors are the TypeScript
server's bytes but for the account, a number since version 2: a ref (`mailroom_core.mail.ref`) is
base64url of `[2, account, folder, uidValidity, uid]`, and one whose folder's UIDVALIDITY has
changed fails with `stale_reference`, never naming another message, as does a version 1 ref or
cursor, which named the account by its slug. Refs are not signed: the host checks the account is the caller's. A
folder that fails is never read again by the following pages: their cursor carries it as failed
(a third element, absent when nothing failed), and each page reports it in `failures` with its
code until paging ends. A search's account that failed before its folders were found is carried
too, with no folder: `search_messages` and a host's own search skip the accounts
`failed_accounts` names and pass the new ones to `read_page` as `account_failures`. A code that
is not core's own is carried only for an account, and only when the host declares it: the cursor
functions and `read_page` take `host_codes`, each of the host's codes with its fixed message (the
app's `password_missing`). A carried failure's message is the code's own, never the cursor's text
(`cursor_failures`). Header text comes decoded and flattened. RFC 2047 words, RFC 2231 parameters,
transfer encodings (fetched in windows with `BODY.PEEK[part]<offset.n>` up to the cap), charsets
and `format=flowed` are decoded. Pages read the sources of different accounts in threads of their
own, and fetch previews in one FETCH per body part number rather than one per message. Over
several sources a page fetches each one's candidates with INTERNALDATE only, merges them, then
fetches the rows it shows, and those only, whole; a source that fails at that second step is a
failure of the page like any other. `list_messages` and `search_messages` take `subfolders`: the
folder and its selectable subfolders at any depth (`with_subfolders`; INBOX in any case), without
the Sent, Drafts, All Mail, Archive, Junk and Trash folders or anything under them, unless the
folder has that role itself; past `MAX_LISTED_FOLDERS` (50) folders it is `invalid_argument`, as
is `subfolders` with `ALL_FOLDERS`. A list line can name the message's folder after the ref. Outside
Gmail, `thread_page` finds a conversation's candidates by a search on Message-ID, References and
In-Reply-To and one on the subject without its `Re:`/`Fwd:` prefixes, then reads their headers:
a server's full-text index may leave those headers out (Mailu), and a reply whose subject
changed is then missed. `thread_key` gives what a host keeps a conversation by (a muted
thread): the message's References, In-Reply-To and Message-ID ids, read as `parsed_message`
reads them from one `BODY.PEEK[HEADER.FIELDS (…)]` of those fields cut past 256 KiB, and on
Gmail its `X-GM-THRID`; it never sets `\Seen`. A page item
carries the arrival time as well as the Date header, and the size; a view carries `has_html` and,
when the read asks for `remote_check` (off by default, as it costs the HTML's download and a
sanitising pass), `remote_images` and `loadable_remote_images`. A ref's error names no tool. A view also carries `headers`
(`mail.message_headers`), parsed from one `BODY.PEEK[HEADER.FIELDS (…)]<0.65536>` fetched with
it: From, the List-* fields, Authentication-Results, DKIM-Signature, Delivered-To and
X-Original-To. Deliberate choices:

- **Every header parse is capped:** a block that reaches the 64 KiB cap is `truncated` and loses
  its last, possibly partial, field; each kind of field, each value and each list is capped too,
  and what cannot be read is left out, never raised.
- **List links** are the topmost of each field, `https:` (no userinfo) or `mailto:` only, at most
  2048 characters. `one_click` is the topmost List-Unsubscribe-Post being exactly
  `List-Unsubscribe=One-Click`; the List-Unsubscribe and List-Unsubscribe-Post fields are counted,
  as RFC 8058 wants exactly one of each.
- **Authentication-Results, an RFC 8601 subset:** each field, topmost first, gives its authserv-id
  and its `spf`, `dkim` and `dmarc` results with `header.d`, `header.b` and `header.from`; comments
  (nested) and quoted strings are honoured, other methods and properties outside the
  `ptype.property` form skipped. A field that cannot be read keeps its place with no results, under
  the authserv-id before its first semicolon when that much reads; `trusted_results` (the topmost
  field of the account's own authserv-id, `AccountConfig.authserv_id`) stops at a field whose
  authserv-id cannot be read, so it never falls through to a forged field below it. Nothing in
  `headers` is verified on its own.
- **DKIM-Signature** gives `d=`, the start of `b=` and the `h=` list of a well-formed signature;
  one with a duplicate or missing tag is left out.
- **Unsubscribing, judged, never done:** `one_click_unsubscribe` (pure) gives the `https:`
  List-Unsubscribe URL only for exactly one List-Unsubscribe and one List-Unsubscribe-Post of
  `List-Unsubscribe=One-Click`, in a block not cut, and a DKIM signature signing both that the
  `trusted_results` of the account's `authserv_id` passed, named by `header.d` and a `header.b` of
  at least 8 characters that no other signature shares; else it says why (`OneClickRefusal`). The
  app sends the POST; core makes no request. `mailto_fields` reads a `mailto:` link's first
  address, `subject` and `body` (RFC 6068), dropping every other field.
- **Checks a read makes, given the message's `AccountConfig`** (`read_message(account=…)`):
  `authentication` (`message_headers.authentication`) reads `trusted_results` only, and is
  `NOT_VERIFIED` without an `authserv_id` or a field of it; its DMARC counts (`dmarc_pass`) only for
  exactly one From, in one field of a block not cut, whose domain is `header.from` or a subdomain of
  it at a label boundary (relaxed alignment, compared in IDNA form with `ascii_host`); `spf_domain`
  (`smtp.mailfrom`'s) and `dkim_domain` (the `header.d` of the result `dkim` is) are ASCII host
  names, None when the text is none. `sent_to` (`identity.sent_to`, which `from_identity` answers
  with too) is the identity in To, Cc, then the delivery addresses. `sender_mismatch`
  (`render.sender_mismatch`, pure) compares each From display name's address-like and domain-like
  tokens with its real address after NFKC and case folding, a dotted word being a domain only when
  it ends in a top-level domain of the root zone (`render.tlds.TLDS`, kept by hand); cross-script
  lookalikes are not folded. `read_text` writes the three after the envelope (`checks_text`), in
  Mailroom's own words: fixed result words, those host names and the identity, nothing else of the
  message.
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

### Folder changes

`mailroom_core.mail.changes` reads what a host keeping its own copy of a folder needs, under
EXAMINE and changing nothing: `uids_of` (every UID, by UID SEARCH over ranges of at most
`UID_CHUNK` messages, bounded by the UIDs at every `UID_CHUNK`th message, since imaplib refuses an
answer line over 1 MB), `flags_of` (`UID FETCH 1:* (FLAGS)`, for a server without CONDSTORE) and
`changed_flags_of` (`UID FETCH 1:* (FLAGS) (CHANGEDSINCE n)`: changed and new messages, never
expunged ones, which a host finds by comparing UIDs; `unsupported` in a folder without
mod-sequences). `MailConnections.folder_uids`, `folder_flags` and `changed_flags` run them in a
session of their own. Each answer (`FolderUids`, `FolderFlags`) carries UIDVALIDITY and
HIGHESTMODSEQ as the EXAMINE gave them, before the read, so a change in between shows again next
time; flags leave out `\Recent`, which belongs to the session. On Gmail the flag reads fetch
`X-GM-LABELS` too (`FolderFlags.labels`, decoded by `stored_labels` as LIST decodes a folder's path;
Gmail changes a message's mod-sequence when its labels change), so a host's copy of the labels
stays current; None elsewhere. A pooled connection sends
`ENABLE CONDSTORE` when the server offers CONDSTORE and ENABLE (Dovecot reports no HIGHESTMODSEQ
on a SELECT otherwise): `MailboxContext.highest_modseq` comes from SELECT or EXAMINE and
`Folder.highest_modseq` from STATUS, None without CONDSTORE. `MailConnections.folders` takes a
predicate as `with_status`, to STATUS only the folders it holds true of: a host with its own counts
of the others.

### Index fetch

`mailroom_core.mail.index` reads what a host keeping an index of a folder stores, under EXAMINE and
changing nothing. `plan_index` lists the folder's UIDs to index (`changes.uids_matching`, range by
range), with `since` only those arrived on or after that day (SINCE, the age limit), newest (highest
UID) first in batches of `INDEX_BATCH`; the `IndexPlan` carries UIDVALIDITY and HIGHESTMODSEQ as
before the read. `fetch_index` reads one batch as `IndexedMessage`s with one FETCH: a page's own
items (envelope, flags without `\Recent`, structure, INTERNALDATE, size), the thread headers
(Message-ID, In-Reply-To, References, List-Id; cut past `parsed.MAX_HEADER_BYTES` as
`search.thread_key` reads them, `parsed_message` dropping the field the cut split), and on Gmail
X-GM-LABELS (decoded as LIST decodes a folder's path, so a label matches its folder),
X-GM-MSGID and X-GM-THRID; then the previews as a page makes them (`preview_of`). `date`, the
Date header, keeps the header's UTC offset as a page item's does (UTC when it has none), which an
index stores to give it back the same (`aware_date`, exported for that). It is refused
(`stale_reference`) once the folder's UIDVALIDITY is no longer the plan's. Every text is a page's
(decoded, flattened, capped; at most `index.INDEX_ADDRESSES` per address field) with each lone
surrogate as U+FFFD,
so a database takes it, and stays the sender's. `IndexedMessage.thread_key` is Gmail's thread id,
else the References root, else In-Reply-To, else the Message-ID: what the header says, which the
app's index keys a conversation by only once it checks the message answered (`mail-client.md`,
*Conversations*). `gmail_uid` finds a message's UID
in a label's folder by X-GM-MSGID (`unsupported` off Gmail), for acting there on an All Mail row;
`folder_index_plan`, `indexed_messages` and `gmail_label_ref` run them on `MailConnections`;
`gmail_label_refs` finds several messages of one label's folder in one session (a host acting
from that label's view on All Mail rows).

## Organize

`mailroom_core.mail.organize`: `move_messages`, `trash_messages`, `archive_messages` (to the folder
with role `archive`), `junk_messages` (to the folder with role `junk`, training `spam`) and
`set_flags` take up to 50 refs from any folders, check every ref and its
account before writing anything, and act per folder, a failing folder leaving the others done.
They return an `OrganizeResult`: per folder a `GroupResult` (count, new refs from COPYUID, Junk
training, flags read back, or the `MailError`), and per message an `AuditItem`
(`mailroom_core.mail.audit`, the sender's address and a hash of the subject) with the call's
`outcome`. `organize_text` writes the tools' text; a host raises a lone folder's error itself.
Out of Gmail's All Mail (`\All` on an `X-GM-EXT-1` server), which keeps every message,
`move_messages` only copies (`selected.copy_uids`: the destination's label added, the others
kept; items `copied`), but into Trash or Spam, and `archive_messages` is `invalid_argument`,
nothing done; trash and junk move as anywhere.

`set_flags` takes `seen`, `flagged` and `to_sort`, and reads each message's flags back
(`FlagState`), comparing them ignoring case as IMAP does; `SearchQuery.to_sort` searches `KEYWORD`
or `UNKEYWORD $MailroomSort`, and a list line shows `to sort`.

`mark_folder_read` takes a folder with the UIDVALIDITY and UIDNEXT its listing gave (`Folder`),
and in one read-write session refuses with `stale_reference` when the UIDVALIDITY changed, then
sets `\Seen` (`store_flags`, 1,000 UIDs a STORE) on what `UID SEARCH UNSEEN UID 1:<uidnext-1>`
finds: mail that arrived since the listing stays unread. It returns the count and notes one
`AuditItem` for the folder, `messages` that count, refused ones included.

`mailroom_core.mail.bulk` acts on every message a search matches rather than on refs.
`count_matching(connections, accounts, folder, query, subfolders=, action=)` counts what
`search_messages` would list (`search_sources` gives each account's folders and criteria), and
with a `BulkAction` (`BulkFlag`, `BulkMove`, `BulkTrash`) only what it would change: a flag
change adds the negation of the flags it sets (`UNSEEN` for `seen`…, ORed) to the search, a
move or a trash `UNDELETED`.
`act_on_matching(..., action, expected=, note=)` searches again per folder (`UID SEARCH`,
read-only), raises `MatchCountError` (`count_changed`, or `too_many` past `BULK_MAX`, 5,000,
with `matching`) unless the total is `expected`, then acts on at most `BULK_BATCH` (500) UIDs,
newest first, each folder a group done as `move_messages` and `set_flags` do theirs
(`move_uids`, `store_flags`, with their checks and Junk training), and returns a `BulkResult`
(`done`, `remaining`, `groups`, `failures`). A move over several folders (`ALL_FOLDERS`, or with
`subfolders`) leaves its destination and All Mail out of the folders it searches, counting
included; over every folder of an account with All Mail, where a search reads All Mail alone, it
reads instead the folders a message can leave (the inbox and the account's own folders, Gmail's
labels: not Starred, Important, Sent, Drafts, Junk or Trash), a message under several labels
counted and moved once per label. A move into a folder still searched, out of All Mail, or a
trash with Trash searched is `invalid_argument`, so what was done never matches again and a
repeated call never acts twice. A group copied but not removed (`CopiedNotRemovedError`, its
`GroupResult` failed with a `destination`) is left out of `remaining` and must not be repeated:
its messages are in both folders. Over several accounts an account or folder that fails is a `PageFailure` and the
others go on; over one, its error is raised. Notes an `AuditItem` per message, per failed
source, and per account on a refusal (`messages`, its count).

**Keywords.** A folder keeps keywords when its read-write SELECT answers PERMANENTFLAGS holding
`\*` (`MailboxContext.keywords`; never under EXAMINE). `MailConnections.keywords(account)` asks
it once of INBOX, read-write, and caches it next to the capabilities until `close`. A Gmail
account (`X-GM-EXT-1`) counts as keeping none, whatever it answers, since Gmail turns keywords
into labels.

They are built on `mailroom_core.mail.selected`, as `reply.mark_answered`, `reply.mark_forwarded`
and the drafts are:
operations by UID on the folder a `with_mailbox` session holds (`search_uids` and `message_id_uids`,
`fetch_sources` with `BODY.PEEK[]` (and `X-GM-THRID` on Gmail), `fetch_source` capped at a size with flags and INTERNALDATE,
`fetch_envelopes`, `append_raw` with flags and an arrival date, `move_uids`, `store_flags`,
`store_forwarded`, `delete_uids`, `listed_folders`), with the refusals of a move (`move_target`) and
`abandon_read_back` for a read-back failing after a write. Whatever acts inside a session of its own
uses them, folders included, since a second connection of the account would wait for the one it
holds. `store_flags` sets only `\Seen`, `\Flagged`, `\Answered` and the To sort keyword `TO_SORT`
(`$MailroomSort`), which is `unsupported` in a folder that keeps no keywords. The forward keyword
`FORWARDED` (`$Forwarded`, RFC 5788) is not among them: only `store_forwarded` sets it, for
`mark_forwarded` after a forward was sent, and only where the folder keeps keywords, so no agent
sets it through `set_flags`. `delete_uids` needs UIDPLUS and serves only a draft Mailroom wrote
(*Safety*), a filter's `delete` and a move to another account for good. The filter engine runs on
them too (*Filters*).

imapclient's `move()` needs MOVE, so without it core issues UID COPY, STORE `\Deleted` and UID
EXPUNGE of exactly those UIDs itself, which only UIDPLUS allows; a server with neither is refused.
When that COPY succeeds and the removal fails, the group fails but keeps the copies' new refs, its
error says they were copied but may not have been removed, so the move must not be repeated, and
names the new refs, so a host raising it alone still shows them; its audit items carry `copied`.

### Moving to another account

`mailroom_core.mail.transfer.move_to_account(connections, refs, account, folder, removal=,
max_bytes=, refuse_each=False)` moves messages to a folder of another account the connections serve, into an
`OrganizeResult` whose groups name `destination_account` and give the new refs there (with
`removal="trash"`, `trash` too: the source's Trash the originals went to):

- **Refused before anything is written:** a target account that is a source's own (that is a
  move), unknown, or whose folder `move_target` refuses (never Drafts or Sent; Inbox and Gmail's
  All Mail are allowed); `removal="delete"` without UIDPLUS on a source, `"trash"` without its
  Trash or a way to move; and, per folder, a message with no Message-ID (`unsupported`: its copy
  could not be confirmed) or over `max_bytes` (`too_large`; the host passes `move_cap`, from
  `Limits.move_max_mb`, 25 MB by default). With `refuse_each`, such a message is refused on its
  own instead (its audit item carries the code, its group's `refused` the UID and the error)
  and the rest of its folder moves; the mail client's moves keep the whole-folder refusal.
- **Per folder, in chunks of at most `max_bytes`,** in three sessions that never overlap (one
  account's held while opening another's could deadlock against a move the other way): read the
  chunk (`fetch_source`: bytes, flags, INTERNALDATE), write it to the target, remove what
  arrived.
- **A copy** is appended with the flags but `\Recent` and `\Deleted` and the original
  INTERNALDATE, and confirmed by APPENDUID and its ENVELOPE's Message-ID, else by reading the
  Message-ID of the messages from the UIDNEXT taken before the APPEND (`message_id_uids`). The
  lookup never relies on SEARCH HEADER Message-ID, which a server enforcing a full-text index
  that leaves Message-ID out (Mailu) never matches: it reads the Message-ID of the messages a
  search by UID or by size finds, or on Gmail searches `X-GM-RAW rfc822msgid:`. A
  message already in the target folder with that Message-ID and the same size, not taken as
  the copy of another message of the call, is taken as the copy: a move cut off after copying
  is repeated without copying twice, while a different message sharing the Message-ID (a
  list's copy, or one written to look like it) is not mistaken for it. Two moves of the same
  messages at once can each copy them: the guarantee is for a move repeated after another.
- **The original** goes only once its copy is confirmed, and after the caller's `copied`, when
  given, has heard of the copies (one that raises leaves them in both): to its account's Trash (one already in
  Trash stays), or for good with `delete_uids` of exactly those UIDs; on Gmail, where that would
  only drop a label, by a move to Trash and the expunge there.
- **Stops at the first error** in a folder. Copies whose originals could not be removed are in
  both: the error says so, not to move them again, and names their new refs; what was not
  reached is noted with the error.
- **Audit items:** per message moved, one for the original (`destination`,
  `destination_account`, `removed`, `copied` when it stayed) and one for the copy, in the target
  account, with `source_account` and `source_folder`.

## Folders

`mailroom_core.mail.folders`: `create_folder`, `rename_folder` (a move in the hierarchy too, its
subfolders and their mail going with it) and `delete_folder` take the parent's path (None for the
top level) and a name, and return a `FolderChange`. Each runs in one
`MailConnections.with_connection` session, which leaves a mailbox a `with_mailbox` left selected
with UNSELECT, or, on a server without it, opens a new connection, since CLOSE would expunge;
readying the connection may be retried once, the session's work never. Every check runs on the
session's own connection, LIST first. Each call notes exactly one `AuditItem` (`folder`, and
`destination` on a rename or `messages` on a delete), refused ones included. Deliberate choices:

- **Names:** 1 to `MAX_FOLDER_NAME` (255) characters, no control character, delimiter, `*` or
  `%`, no leading or trailing space, not `INBOX` at the top level (`check_folder_name`). A path
  nests at most `MAX_FOLDER_DEPTH` (32) levels and 1000 characters, checked for every subfolder
  a rename moves too. A parent must be listed, `\Noselect` ones included (`mailbox_missing`),
  and on a server without a delimiter there is none.
- **Protected folders:** INBOX and the folders with a role are never renamed or deleted, and a
  rename of a folder holding one is refused, as it would move it.
- **A delete needs the count:** the caller passes the `messages` it saw and the user confirmed;
  STATUS reads it again, and a different count is `FolderChangedError` (`folder_changed`, with
  the new count), nothing deleted. A folder with subfolders (`subfolders_of`), `\Noselect`
  ones too, is refused. Mail arriving between the STATUS and the DELETE goes with the folder,
  which IMAP cannot prevent.
- **Subscriptions** follow, best effort: a create subscribes, a rename moves the folder's and
  its subfolders' subscriptions, a delete unsubscribes. A refusal there is logged, and the write
  stands.
- **Answers:** `[ALREADYEXISTS]` is `folder_exists`, as is a folder already listed; `[CANNOT]`
  is `invalid_argument`; `[INUSE]` is `busy`. Mapped in this module only, so other commands
  keep theirs.
- **A lost answer:** when the CREATE, RENAME or DELETE itself fails as `unreachable` or
  `timeout`, the folders are listed once more, outside the session, and a change that shows is a
  success.

## Compose

`mailroom_core.mail.compose` (with `recipients`, `identity`, `reply`, `attachments` and
`outgoing`, the caps of the tools' input) builds outgoing messages with the standard library.
`compose` takes the Message-ID, so a draft's versions can share one. The `email` package builds
only the MIME body: its header classes parse their input, which would decode an encoded word in a
subject or a name back into the header, CR/LF included, and would turn a long Message-ID into an
encoded word, so the message's own headers are written by hand. Deliberate choices:

- `X-Mailroom-Draft` is `1`, `1; answers=<ref>` when sending the draft is to set `\Answered` on
  the message it answers, or `1; forwards=<ref>` when it is to set `$Forwarded` on the message it
  forwards (`draft_answers`, `draft_forwards`); a draft never carries both. Only those forms count
  as Mailroom's mark.
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
  core does not list as attachments, unless the source says `inline`: then it takes only such a
  part, with its Content-ID, for a forward or a draft's next version that keeps that HTML.
- **HTML bodies.** With `MessageInput.html`, the body is multipart/alternative of a text part,
  `html_to_text` over the sanitised HTML (`text` is not used; `html_text` gives it alone, for a
  client's switch to plain text), and that HTML; wrapped in a
  multipart/related, `type="multipart/alternative"`, with the inline parts it shows, if any; then
  multipart/mixed with the attachments. An `OutgoingAttachment` with a `content_id` is an inline
  part: written with an inline disposition and its Content-ID only while the HTML refers to it,
  never without HTML. A Content-ID that could not go in a header as it is, or that a `cid:` URL
  would escape (a `?`, a `%`, a space, past 250 characters) refers to nothing, so its image is dropped rather than the message refused.
  `check_fields` caps the HTML at `MAX_HTML_BYTES`, 3 MiB of UTF-8 (the other fields are in
  UTF-16 units); its `data:` images count there, not against the attachment cap. The text part's
  `html_to_text` takes twice that (`max_chars`), so it reads any such body whole. An audit item counts the attachments
  alone, not the inline parts.
- `quoted_original_html` puts the original's HTML, sanitised as a send would (its text, escaped,
  when it has none or more than `MAX_QUOTED_HTML_BYTES`, since HTML cannot be cut), in a
  `<blockquote type="cite">` under the attribution line, without its images.
  `forwarded_original_html` keeps the `cid:` images of `Original.inline`, which the forward
  carries as inline sources. `original_message` reads the HTML for them only `with_html`: a text
  reply or forward does without that second fetch. `Original.text` is cut at `MAX_QUOTED_CHARS`
  (20,000), which a host also holds a quoted selection to.

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
- A draft Mailroom wrote keeps its HTML: `Draft.html` is sanitised again on the way out (the
  mark is only as trustworthy as whoever wrote the draft) and `Draft.inline` lists the parts it
  shows, which the next save keeps as `inline` sources. `lossy` covers another client's HTML or
  inline images, an invite with no name, none of which a save keeps, and text or HTML past
  `MAX_DRAFT_BYTES`; a Mailroom HTML draft's parts are read up to `MAX_DRAFT_HTML_BYTES`, twice
  the longest HTML `check_fields` admits (room for the entities sanitising writes out), so that one
  reads back whole. A named `.ics` is an attachment, and lists as one.
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
  `\Answered` or `$Forwarded` can only add a note (`original_not_marked`,
  `original_not_marked_forwarded`), a `SendNote` with a stable `code` beside its English `text`,
  so a host can word it itself. If the store cannot be reached to record the outcome, the row stays
  `sending` and reads `unknown` past the ceiling, and the send's result stands.
- **A draft that was not filed stays.** It is then the only copy in the account, so `settle_draft`
  takes `filed`, still marks the original, and notes `draft_kept`. The send's key keeps a retry from
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
- A stored draft may reach 1.5 times `attachment_max_mb` plus 2 MiB, plus room for an HTML body
  at `MAX_HTML_BYTES` (its HTML and derived text in quoted-printable, its pasted images within
  those bytes), not `attachment_max_mb`, or a draft with attachments near the cap, which
  base64 grows by 37%, could be saved and never sent.

## Filters

The filter engine, `mailroom_core.filters`: rules that sort mail on an IMAP server, built in Python
or loaded from their data form. Its tests are in `tests/filters`.

- **A filter** is a criterion and an action: `Filter(criterion, action)` or
  `If(criterion).then(action)`.
- **A criterion** has two halves that must agree: an IMAP SEARCH query (`IMAPQuery`, run by the
  server) and a predicate on the fetched `Message`. `is_efficient` says the query alone is exact,
  so nothing is fetched. A looser query is only a prefilter and the predicate decides: `SUBJECT`,
  `FROM`, `TO`, `CC` and `BCC` match substrings, so the `*_is` criteria are prefiltered, and
  pattern criteria search wider. The `*_contains` and exact-address predicates ignore case, as
  the server's search does. A negated prefilter searches `ALL`; when it is a filter's whole
  criterion, the engine searches twice instead: outside the prefilter it matches unfetched,
  inside it the predicate decides. The predicate reads a `ParsedMessage`, whose headers all
  come from the header block's first `MAX_HEADER_BYTES` (256 KiB, cut at a whole field; a field
  beyond it reads as absent): the first of each address field, decoded, the subject as the view
  shows it, the UTC date, and as the body every inline plain-text part outside attached
  messages, else the HTML parts' visible text (so a body pattern matches across tags and never
  inside them), and the first Message-ID, In-Reply-To and References ids (each
  `<local@domain>` of printable ASCII, matched bracket by bracket so the sender's field is read
  in one pass; References kept to its first id and the newest, 100 in all) and List-Id
  identifier (`message_headers.list_id`). The `Message` adds, on Gmail, the
  thread id (`gmail_thread`, `X-GM-THRID`, which `fetch_sources` asks for where the server has
  `X-GM-EXT-1`). `from_domain` compares the From addresses' domains in `ascii_host` form,
  prefiltered by `FROM @domain` only for a plain ASCII one, since the server folds no Unicode;
  `list_is` and `in_thread` search `ALL`, a header search being unreliable (Gmail ignores it, a
  full-text index may leave the field out). `&`, `|` and `~` combine both halves,
  efficient only if every operand is. `IMAPQuery.Deferred` builds its query at search time, for
  criteria relative to the run (`OlderThanDays`), whose today is in the zone the host passes
  `Folder.run` as `time_zone` (a checked `TimeZoneName`; the machine's without one).
- **User patterns** compile when the criterion is built (a bad one is a `SpecError` on load) and
  match with `regex`, never `re`, under the process-wide match timeout (1 s for each match by
  default; past it, `MatchTimeoutError`).
- **An action** acts on a set of UIDs of the selected folder (`Selected`: the run's
  `MailboxContext` and the server's delimiter); `+` or `.then()` chains actions. They are core's
  operations (*Organize*): `move_to` and `trash` are the client's moves (`move_target`, then
  `move_uids`, with its COPY and `UID EXPUNGE` fallback under UIDPLUS), so a folder that is not
  there is `mailbox_missing` and Drafts or Sent are refused, while a move into the folder being
  filtered (Trash from Trash too) is skipped; `trash` without a folder takes the special-use
  Trash (`unsupported` without one), and `junk` the folder with the Junk role (likewise). Flag actions are `store_flags`: `star`, `mark_as_read` and
  the like, and `mark_to_sort` / `unmark_to_sort` for the To sort keyword, `unsupported` where the
  folder keeps no keywords; the criteria `is_to_sort` / `is_not_to_sort` (and `has_flag` with
  `TO_SORT`) search `KEYWORD` / `UNKEYWORD $MailroomSort`, and every flag predicate ignores case.
  `delete` removes for good (`delete_uids`, UIDPLUS only), which a filter may do because the user
  wrote it, saw its dry run and enabled it ([`requirements/filters.md`](requirements/filters.md)).
  `move_to_account` (an account id and a folder) cannot act inside the session, which holds one
  account's connection: it records the matched UIDs (`Selected.defer`), which later filters of
  the run then skip as if moved, and once the session has closed, a failed run's too, they are
  moved by `move_to_account` (*Moving to another account*) with `removal="trash"`, in batches of
  `MAX_REFS`, the folder joined with the target's delimiter, and `refuse_each`: a message with
  no Message-ID or over the cap is refused alone. Each filter's report lists the
  outcome per message in `transfers` (`TransferReport`: the target, per UID `ok` or the error
  code, with `removed` and `copied` as the audit items have them and a refused message's
  `reason`, the copies' new refs and the first error); any failure, such a refusal included,
  leaves the filter's `executed` False. A `seq` with `delete`, `trash`, `junk`, `move_to` or
  another `move_to_account` after a `move_to_account` (at any depth) is a `SpecError`: the
  messages are taken by then. A failure that is no
  `MailError` (the host's resolver raising, a bug) goes through `mail_error` too, so the
  transfers after it still run.
- **Specs** are the rules as data, the form the app stores (`spec`): leaves
  `{"type", "args"}`, the combinators `and`, `or`, `not`, `seq` for actions, and a filter
  `{"criterion", "action"}`. `load_filter` rebuilds an equal filter, `to_spec()` gives it back and
  `json_schema()` describes the format. A new criterion or action needs its `spec_type`, an entry
  in `spec/schema.json`, its name in the facade's `__all__` and a case in
  `tests/filters/test_spec.py` (whose coverage test, over that `__all__`, fails otherwise). **A change to an existing spec ships with a migration of the stored filters**, in
  the same pull request. Some arguments are the host's, not the spec's: a class lists them in
  `spec_host_args`, `to_spec()` leaves them out, a spec may not set them, and `load_*` passes
  them as it builds the leaf. `in_thread` is one: its
  `thread` is the host's opaque name, and `load_criterion(spec, threads=...)` (or
  `load_filter`) looks its `ThreadMembers` (Message-IDs, Gmail thread ids) up once, as the
  criterion is built; without a resolver, or for a name it does not know, it matches nothing.
  `move_to_account` is another: `load_action(spec, accounts=...)` (or `load_filter`) gives it the
  host's `AccountResolver`, which maps the target account id to an `AccountTarget` (connections
  serving it and the run's account, the `max_bytes` cap, and `copied`, called with each chunk's
  confirmed copies' refs before their originals are removed: the host's loop guard, whose
  `MailError` leaves those originals in both accounts and stops the moves left) when the move is
  made, and raises `MailError` for an account the run may not move to;
  without one, every message fails `unsupported`.
- **A run** (`Folder.run`, `run_into`) is one `with_mailbox` session for all the filters given,
  in order, each seeing what the earlier ones did: search, fetch only what the query cannot decide
  (`fetch_sources`, which never sets `\Seen`, parsed by `parsed_message`; cached for the run,
  dropped once acted on), act, or in a dry run only report, with the folder selected read-only (a dry run
  that lost its connection is retried once, its report started afresh). The folder's name is
  joined with the server's delimiter, asked before the session. Every search ANDs `UNDELETED`, so
  an original a move copied but could not expunge never matches again. `newest=n` (at least 1)
  looks the folder's newest `n` messages up once (`exists-n+1:exists`), ANDs their UID range into
  every search, so an earlier filter's expunge does not shift it, and sets the report's
  `limited_to_newest`. `skip` (`SkipUids`), given the folder's server name and UIDVALIDITY, asked once
  selected and again before each filter, returns UIDs no filter of the run matches (the app's
  copies from another account). Its `FolderReport` gives per filter what it matched (from one `ENVELOPE`
  fetch) and did; `run_into` fills it as filters finish, so a failure keeps what was done. Mail
  failures are `MailError`s; anything else (a pattern's `MatchTimeoutError`, a bug) leaves the
  session as itself, never as a `server_error`.
- **Searches**: `IMAPQuery.criteria()` writes a query as core's criteria, an AND flat, an AND
  inside OR or NOT as a group, and `search_uids` sends them (`CHARSET UTF-8` only for non-ASCII
  text; a line break in a value is `invalid_argument`).
- **The runner**: `Rules` pairs folders with their filters; `run_rules` runs them in turn, or one
  thread per folder with every failure raised together (`RunRulesError`); `marked_only` limits
  rules to messages with a marker, the star (`Flag.FLAGGED`) or To sort (`Flag.TO_SORT`), clearing
  it first; `monitor` keeps an `IdleWatch` per folder
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
- **Deletes only deliberately:** trash moves to `\Trash`. Nothing is expunged but what a move
  has copied elsewhere on a server without MOVE (*Organize*), and a draft Mailroom wrote itself
  (`X-Mailroom-Draft`) when it is replaced or deleted, always by `UID EXPUNGE` of exactly those
  UIDs (UIDPLUS required); a draft written anywhere else is moved to Trash. Mail is lost for good
  only on a deliberate act: a filter's `delete`, the user's own rule, which expunges exactly its
  UIDs too (without UIDPLUS it is `unsupported`); a move to another account asked to remove the
  originals for good, which expunges exactly them once their copies are confirmed (*Moving to
  another account*); and a folder's delete (*Folders*), which takes the message count the caller
  confirmed and is refused when the folder holds another. None is ever a side effect of another
  write.
- **Audit items:** each write and send, refused ones included, yields items (which the app keeps
  in its audit trail) naming the account, folder, UID, Message-ID, sender and recipients in
  clear, and the subject only as a SHA-256; a move to another account yields one in each.
- **Errors** are `MailError` codes with fixed messages, never the server's or a library's wording.
  An `unreachable` caused by a certificate that did not verify (itself, or wrapped in a login
  error) ends with the fixed `CERTIFICATE_DETAIL`, which a host may recognise. A server's
  temporary failure (RFC 5530's `[UNAVAILABLE]`, as Dovecot answers a login while its auth backend
  restarts) is `unreachable`, not `auth_failed`, so an IDLE watch reconnects instead of ending.
  Secrets never reach a log line, an error or a tool's text.

## Compatibility

**Sealed secrets** stay byte-compatible with mailroom-mcp's, so its accounts import unchanged:
HKDF info `mailroom account secrets`, AAD `mailroom/v1/<account>/<purpose>`,
`format ‖ nonce ‖ ciphertext ‖ tag`. `<account>` is the account's former slug where it has one
(`former_slugs`), else `#<id>`: no slug holds a `#`, so an id never opens what a slug of the same
digits sealed. The AAD names no user, so a blob copied to another user's account under the same
former slug would open. Doing that takes write access
to the database, which can replace the settings anyway.
