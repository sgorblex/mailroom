# Non-functional requirements

Engineering baseline for Mailroom. Product scope: [`../REQUIREMENTS.md`](../REQUIREMENTS.md).

## Implementation stack (reference)

| Area | Choice |
| --- | --- |
| Backend | Python 3.14+ (dev pin 3.15 via mise), FastAPI, SQLAlchemy 2 (sync) + psycopg 3, Alembic, uv; PyJWT and httpx for OIDC; dnspython and lxml for account autoconfiguration |
| Worker | procrastinate (psycopg 3) on the app's database, `python -m mailroom.worker`; it also watches folders over IMAP IDLE and syncs the mail index |
| Web | React 19 with React Compiler (Babel through `@rolldown/plugin-babel`; the message list opts out with `'use no memo'`, as TanStack Virtual's virtualizer changes in place), Vite, TanStack Router (code-based routes), TanStack Query (in-memory cache of the mail screen's reads), TanStack Virtual (the message list), Tailwind 4 + shadcn (`web/components.json`) with the app's own tokens (`web/src/index.css`), the Figtree font bundled from `@fontsource-variable/figtree`, Downshift (headless combobox: the command palette), TipTap 3 (the composer's rich-text editor, loaded with it), i18next, Zod at the API boundary, ESLint `strictTypeChecked` (no Prettier) |
| Database | PostgreSQL 18 |
| Tests | pytest (+ xdist, one database per worker cloned from the migrated `mailroom_test`), Vitest (`node` and `jsdom` projects) with axe-core for accessibility |
| Containers | Podman, `mailroom-api` and `mailroom-web` images; Quadlet for deploy |
| Component family | mailroom-core, the mail layer, as a uv workspace member in `backend/core` (its own tests run from there: `mise run test-core`), which reads hosts with `idna` (UTS #46, as browsers do); the filter engine inside it (`mailroom_core.filters`, imap-thingy until 2026-11-11); mailroom-mcp, retired, never was a dependency ([`../CONTEXT.md`](../CONTEXT.md)) |
| MCP server | the `mcp` SDK's low-level server, Streamable HTTP session manager and bearer-auth middleware, bounded to one minor because the app uses them directly ([`mcp.md`](mcp.md)) |

Tool pins: `mise.toml` (dev), the digest-pinned `.woodpecker/*.yaml` step images (CI), the
Containerfiles, `backend/.python-version`, `web/package.json` `packageManager`, and
`npm:chrome-devtools-mcp` in `mise.toml` for agent browser verification only. Renovate groups each
tool's pin sites (`renovate.json`).

## Security

**Threat model.** Each user is trusted with their own mail and nobody else's; **mail content is
not trusted at all**. A message is written by a stranger
and may try to run script, track the reader, phish, or — through the MCP — instruct a model.

### HTML mail

- HTML bodies render **only inside a sandboxed iframe**, whose `src` is a **standalone HTML
  document** the API serves (`GET /api/v1/mail/messages/{ref}/html`,
  [`mail-client.md`](mail-client.md)) with its own CSP header, not a `srcdoc`: a `srcdoc` iframe
  inherits the SPA's CSP, and an opaque-origin document gets no cookie for its `cid:` images. The
  HTML is sanitised by mailroom-core (`nh3`: allowlist, no script, forms, frames, `base` or event
  handlers; `cid:` parts inlined as `data:`) before it is served. The iframe has `sandbox`
  without `allow-scripts` and without `allow-same-origin`, so the message has an opaque origin
  and can neither run script nor reach the app's cookies, storage or API. Never
  `dangerouslySetInnerHTML` mail content into the app's own document.
- The iframe document carries its **own strict CSP** (`default-src 'none'`; images only from
  `data:` (the inlined `cid:` parts), unless the user reveals remote content; no scripts, no
  forms, no frames, `base-uri 'none'`), in addition to server-side sanitisation of the HTML.
- **Dark mode is CSS in that document.** `?dark=1` changes only the served document's own
  `<style>` (dark colours, or an inverting `filter` with images inverted back, both off under the
  `#mailroom-original` fragment, which only the app's own empty element can be), under the same
  CSP (`style-src 'unsafe-inline'`) and sandbox: no script in the frame or the app touches the
  message, and nothing of it reaches the app shell, which only picks the URL from its theme and
  the reader's toggle.
- **Wide HTML is fitted with CSS too.** The document wraps the message in a block of its own
  (a `class`, which `safe_html` keeps on no element of the message) that scrolls sideways
  whatever is wider than the frame. Given the message's layout width (`SafeHtml.layout_width`),
  stepped `@media (width<…)` rules zoom that block out so the layout fits a narrower frame, a
  phone's, as phone mail apps do (`html_document` in `api/routes/mail/read.py`): no script, and a
  browser without `zoom` scrolls instead.
- **HTML views kept:** each API process keeps the sanitised HTML views it made (its inlined
  `cid:` images included) in memory for 60 seconds, 32 MiB in all, the oldest dropped first
  (`mailroom.mail.service.read.HtmlViews`), so that opening a message reads and sanitises its HTML
  once: the read's remote check makes the view, and the frame's check and document, light or
  dark, take it. Keyed by the user and their accounts' revision, the message's account, folder,
  UIDVALIDITY and UID (whose content never changes) and whether remote images are shown: never
  answered to another user, nothing written to disk, gone with the process. With them, for ten
  minutes, each message's verified sender (DMARC passed for its one From) as its read found it,
  for `remote=sender` below; never whether the user trusts it, which is asked each time.
- The one HTML body shown outside that iframe is the **printable page** (`GET
  /api/v1/mail/messages/{ref}/print`), a tab of its own for the browser's print: the same
  sanitised HTML without remote images, boxed in so it paints nothing outside its own box and
  takes none of the page's own styles, its headers escaped, under a CSP that sandboxes it
  (popups only, for its links), loads nothing but `data:` images, runs no script and lets no one
  frame it. The source download (`…/source`) is a file, served as a part is.
- **No remote content by default.** Remote images are removed from the document and refused by its
  CSP until the user reveals them for that message (`?remote=1`); tracking pixels therefore never
  fire on open. A remembered sender's message asks `?remote=sender`: the server keeps remote
  images only if, as it serves that document, the user still remembers the message's verified
  sender, so a read the client kept (or another tab's) never shows remote content under a trust
  since removed. The only thing remembered is a **sender** the user chose
  (`app.remote_content_sender(user_id, address, created_at)`, one row per user and address), and
  only from a message whose DMARC passed at the account's own authentication server for its one
  From, which the server checks itself; it counts again only for such a message (`remote_trusted` in
  [`mail-client.md`](mail-client.md)), never for a From anyone could write. Logged as
  `remote_sender_added` / `remote_sender_removed` with `user_id`, never the address. What is removed
  whatever the user chooses: [`mail-client.md`](mail-client.md), the HTML route. Links open in a new
  top-level context with `noopener` and `noreferrer`.
- **HTML first, still in the frame.** A message with HTML opens on its HTML view, unless the user
  reads its sender as text (`app.sender_view_preference(user_id, address, view, created_at)`, one
  row per user and address, `view` only `text`). That choice only changes which view opens, so
  any From counts; it never loads remote content nor takes the HTML out of the sandboxed frame.
  Logged as `sender_view_text` / `sender_view_html` with `user_id`, never the address.
- **Links show where they go.** A link's `title` is always the host it goes to (IDNA as a browser
  computes it, UTS #46, so a lookalike in another script shows as `xn--…`), or none when no
  browser would take it for a host; the sender's is dropped. A link whose text names
  another host is marked, in the HTML and in the text body alike; `http(s)` URLs are read as a
  browser reads them (backslashes as slashes) before the host is taken. The check has no
  public-suffix list and reads text the sender may hide with styles: it warns, the title is
  what to trust.
- **HTML the user sends** is sanitised on the server by mailroom-core (`render.outgoing_html`,
  [`../core.md`](../core.md) — *Compose*): the same allowlists, no script, event handlers, forms,
  frames, objects or style sheets, links only `http(s)` and `mailto`, images only the message's own
  `cid:` parts (`data:` images become such parts), styles without `url()` or `expression()`;
  the one attribute it adds is `data-mailroom-signature` on a `div`, the signature's mark,
  dropped from a quoted or forwarded original. A signature is sanitised the same way, without
  images.
  The web composer's editor (TipTap) parses only `cid:` images, so a paste fetches nothing; it
  never renders mail HTML as HTML in the SPA's document, only the nodes of its own schema.
- **Imported HTML** (designed mail, [`../core.md`](../core.md#designed-mail)) is mail content too,
  whoever built it: its HTML goes through the same `outgoing_html` (`data:` images left out, its
  `cid:` references kept for the checks), and only Content-Type and Content-Transfer-Encoding of
  the file's headers survive: From, Message-ID and the rest are Mailroom's, built as any send's,
  CRLF stripped. The `email` package parses it under caps (100 parts, 16 deep, the HTML and the
  text 3 MiB each, the other parts the attachment cap), and any exception it raises is
  `invalid_argument`, so nothing of the file reaches a log. The checks' Content-IDs and file
  names go to the user, and to an agent inside the untrusted envelope. It is previewed only
  through the message view's sanitiser and sandbox (`GET /messages/{ref}/html`, whose `dark` only
  changes the document's CSS, its CSP and the frame's sandbox the same), and sent only through
  the send gate, a test to the user's own address included.
- Plain-text rendering is the fallback and the default for the index.
- **No request on mail's behalf, but one.** Nothing a message names is fetched, with one
  exception: a **one-click unsubscribe** (RFC 8058) the user asks for on one message (`POST
  /mail/messages/{ref}/unsubscribe`; never automatic, no MCP tool). The server reads the message
  itself and posts only when it has exactly one List-Unsubscribe, with an `https:` link, and one
  List-Unsubscribe-Post of `List-Unsubscribe=One-Click`, and a DKIM signature that signs both is
  the one the topmost Authentication-Results field of the account's own `authserv_id` passed
  (named by `header.d` and `header.b`); a header block cut at its cap is refused. The app sends
  it, never mailroom-core, through `safe_fetch` as a POST (*Outbound HTTP to a host from input*
  below): no cookies, no `Referer`, no redirect. Each attempt, refused ones included, is an
  audit record (`unsubscribe`), and the log line `mail_unsubscribed` carries `user_id`,
  `account_id` and the outcome, never the URL or the list (the message's read before it logs as
  `mail_unsubscribe_checked`). A `mailto:` unsubscribe is only a draft
  ([`mail-client.md`](mail-client.md), *Compose*) the user sends through the send gate.

### The app

- **Separate users.** Every account, filter and suggestion belongs to one user, and every query
  that reads them is scoped to the signed-in user. Admins manage users and
  the instance's settings, not their mail. No sharing. See *Sign-in* below.
- **One service layer.** What a user may do with their accounts and filters (ownership by user id,
  the checks, the guards) lives in `mailroom.accounts.service`, `mailroom.filters.service` and
  `mailroom.mail.service`, which refuse with a `mailroom.errors.Refusal` (a kind, a `code`, a
  safe `message`). The REST routes only map them (`mailroom.api.refusals.http_refusal`:
  `not_found` 404 with the message alone, `invalid` 400, `conflict` 409, `busy` 429, `mail` by its
  code (`mail_status`: 502 by default), `unavailable` 503, `timeout` 504, `failed` 500), so every
  front end keeps the same rules.
- **Strict CSP on the app** (`web/nginx-default.conf`): `script-src 'self'` with no inline script
  (so the user's theme is set on `<html>` by the bundle itself, before its first render, from a
  copy kept in the browser), fonts only from the app's own origin (bundled, under `default-src
  'self'`), `frame-ancestors 'none'`,
  `frame-src 'self'` (the HTML mail view), `object-src 'none'`, `base-uri 'none'`, plus `nosniff`
  and `Referrer-Policy: no-referrer`. The `/api/` location gets the last two but not the SPA's CSP:
  the API's HTML mail document sets its own. nginx hides the API's own copies of those two, so
  each is sent once. `style-src` allows `'unsafe-inline'` for UI-library style attributes
  (decided; revisit if that stops being needed). The production build fails on a chunk that
  evaluates strings (`eval`, `new Function`; `web/vite.config.ts`), which `script-src 'self'`
  would refuse at run time.
- **Installable, not offline.** `web/public/manifest.webmanifest` (standalone, at `/`, the light
  background as its colours) and its icons, drawn from the header's mark (an SVG, PNGs at 192 and
  512, a maskable 512, and a 180 `apple-touch-icon`), let a phone or a desktop browser install the
  app. nginx serves the manifest as `application/manifest+json`, which its stock types lack;
  `default-src 'self'` covers loading it, so the CSP names no `manifest-src`. There is no service
  worker: the installed app needs the server as the tab does. `web/src/manifest.node.test.ts`
  checks the manifest, its icons' sizes, the links in `index.html` and the nginx location. The
  PNGs are committed; `mise run web-icons` renders them again with `rsvg-convert`, the 192 and 512
  from `web/public/icon.svg` and the maskable 512 and `apple-touch-icon` from the full-bleed
  `web/icons/icon-maskable.svg` (kept outside `public/`, so it is not shipped).
- **Staging installs as Mailroom staging.** `web/public/manifest-staging.webmanifest` is the same
  manifest named *Mailroom staging*, its icons the same mark with a band of the deploy banner's
  amber along the bottom (`*-staging*` beside each production file, rendered by the same
  `web-icons` task). One image serves every environment, so `index.html` names production's;
  once `/api/v1/version` answers an environment the deploy banner shows (anything but production
  and development), the banner points the head's manifest, favicon, `apple-touch-icon` and
  `apple-mobile-web-app-title` at staging's. Production, and a page whose `/version` has not
  answered, keep production's, so production never shows the staging icon. Chrome reads the
  manifest link when it installs and Safari reads the head when a page is added to the home screen
  or the Dock, both after the swap; the favicon changes in the tab once the API answers. The
  manifests carry no `id`: an app's identity is per origin, and staging's origin is not
  production's, so the two install side by side. nginx serves both manifests from one location.
- The SPA and API are **same-origin** (Vite proxy in dev, nginx in the image), so `/api/` has no
  CORS configuration at all. Only the OAuth metadata, token, register and revoke endpoints answer
  CORS (any origin, no credentials), for browser-based MCP clients.
- **Secrets** (account passwords, tokens) are sealed at rest as mailroom-core seals them and never
  appear in logs, errors or API responses. See *Mail accounts* below.
- **Mail metadata at rest.** The mail index (`app.mail_index_folder`, `app.mail_index_message`)
  keeps, per message of an indexed folder, what lists and search show without asking the server:
  From, To and Cc, subject, Date (with its UTC offset) and arrival time, flags and keywords, size,
  whether it has attachments, Message-ID, In-Reply-To, References root, thread key, List-Id, the
  snippet a list shows and on Gmail its labels and ids. Never a body or an attachment: a search for
  text stays live on the server. Rows are plain (no column encryption: the database's own protection
  applies), mail-derived and so untrusted, never written to a log line in clear, and read only
  scoped to their user (each message row carries its folder's user, enforced by its foreign key).
  They go with their account and their user, and a folder's with its switch turned off.
- **Conversations are not the sender's to join.** References and In-Reply-To are written by the
  sender, so a message joins a conversation on the index only when the user's index already
  holds the message it answers, in the same account, with the same subject without its prefixes
  (Gmail's own thread id stands); an action on a conversation never takes the user's own Sent
  and Drafts copies, and the web asks before trashing, archiving, moving or reporting whole one
  whose newest sender is new to it ([`mail-client.md`](mail-client.md#conversations)). Revision `202611211500`
  indexes the rows by Message-ID, References root and In-Reply-To, and sets the keys stored
  before by that rule.
- **Saved views** (`app.saved_view`, [`mail-client.md`](mail-client.md#views)): per user, a name
  and folder paths or a search line the user wrote, kept plain and never logged; read only scoped
  to their user, they go with it, and an account's removal drops its folders from them.
- **Signatures** (`app.identity_signature`, [`mail-client.md`](mail-client.md#signatures)): per
  user, account and identity address, the HTML (sanitised) and text the user wrote, kept plain and
  never logged; they go with their account and their user.
- **Held sends** (`app.scheduled_send`, [`mail-client.md`](mail-client.md) — *Send state
  machine*): per user, the account, the draft's ref and send key, its subject and addresses (for
  the Scheduled list), an agent's connected app and grant, and when it goes, never logged
  but by id; read only scoped to their user, they go with their account and their user, their
  jobs with them.
- **Snoozes** (`app.snooze`, [`mail-client.md`](mail-client.md#snooze)): per user, the account,
  the message's `Message-ID` and size, the folder it came from, its Snoozed folder and ref there,
  its subject and sender (for the Snoozed list) and when it comes back, never logged but by id;
  read only scoped to their user, they go with their account and their user, their jobs with
  them (the messages stay in Snoozed).
- **Vacation replies** (`app.vacation`, `app.vacation_replied`,
  [`mail-client.md`](mail-client.md#vacation-reply)): per user and account, the settings (days,
  subject, sanitised HTML, identity) and each sender answered with when, kept to answer a sender
  once in 4 days (the account's next claim deletes the older ones); never logged, read only scoped to their user, they go with their account and
  their user.
- **Deletes only deliberately.** Trash moves to `\Trash`, as in mailroom-mcp, and nothing is
  expunged but what a move copied elsewhere and **a draft Mailroom wrote**, by `UID EXPUNGE` of
  exactly that UID ([`mail-client.md`](mail-client.md), *The own-draft exception*); a draft
  written elsewhere goes to Trash. Mail is lost for good only by a filter's `delete` action, the
  user's own rule ([`filters.md`](filters.md)), a **folder's delete**
  ([`mail-client.md`](mail-client.md), *Folder management*) and a **move to another account**
  asked to delete the originals for good, only once their copies there are confirmed
  ([`mail-client.md`](mail-client.md), *Moving to another account*). Every permanent delete
  keeps these safeguards:
  - **a confirmation on the web** that says what goes for good (a folder's: its message count;
    a move's: how many originals, and from which account to which), and a guard on the server
    that the thing confirmed is still what goes (a folder's count read again: `folder_changed`;
    a move's originals removed only once each copy is confirmed);
  - **a destructive MCP tool** (`destructiveHint`), whose description says the loss is
    permanent and that it is called only on the user's explicit request;
  - **never a side effect**: it is its own call, never a step of another action (organizing,
    `mail_organize`, never deletes permanently);
  - **refusal tests**: each refusal is tested to leave the mailbox untouched;
  - **an audit record** for each call, refused ones included.
- **Sending** passes the send gate only: an `Admitted` token from `admit` is the one thing
  `deliver` takes, and the user's *Send* overrides a send mode except `off`. A send is counted and
  recorded before SMTP; a draft's send is keyed so it is delivered once (an agent's direct send has
  no key), and one whose delivery is uncertain is `unknown`, never retried by itself. A held send
  the worker sends goes through the same gate with the draft's key, so its job run twice delivers
  once; so does a vacation reply, keyed by the message it answers, its sender claimed before SMTP,
  and decided by the message's header fields only, never its text. Header values built from user
  input or mail have CR and LF stripped, and Bcc never goes in a transmitted header.
- **Learning never acts** ([`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Filter suggestions*).
- **Outbound HTTP to a host from input** (an email address's domain, a URL a document names, a
  client ID metadata document's URL, a one-click unsubscribe URL) goes through
  `mailroom.net.safe_fetch`, against SSRF: a GET, or a POST of a urlencoded form of at most 1 KiB,
  to `https://` on the default port with no credentials in the URL; every address the host resolves
  to must be public (a private, loopback, link-local, multicast, reserved or unspecified IPv4 or
  IPv6 address, an IPv4 one mapped or 6to4-embedded in IPv6 included, refuses the fetch), and the
  connection goes to that vetted address with the hostname kept for SNI, the certificate check and
  `Host`. No redirect unless the caller allows a few for a GET (a POST never follows one), each
  vetted again, and one to another host drops the caller's `Authorization` and `Cookie`; no
  environment proxy, no cookies, no `Referer`; connect and read timeouts, a deadline for the whole
  fetch and a cap on the body, which is asked for (`Accept-Encoding: identity`) and taken only
  unencoded, so no compressed answer grows past the cap. The caller judges the status and the
  content type. Tested on a mock transport and resolver.

### Editions

What they are: [`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Editions*. Code:
`mailroom.config.features`.

- **`MAILROOM_FEATURES`** is read once per process: by `create_application` in the API (after the
  root `.env`), and first thing by the worker (below). Comma separated, case-insensitive, trimmed;
  unset is every feature, blank is none. An unknown name is a `ValueError` naming the variable and
  the choices, so the API fails at import, as with an invalid `MAILROOM_MCP_AUTH`. The lifespan
  logs the effective features once.
- **What each one mounts:** `mail` the `/api/v1/mail` routes and their `NotStored` middleware;
  `filters` the filters router (rule sets, runs, the schema, an account's folders and
  `filter-settings`); `mcp` `/mcp` and its metadata, the built-in authorization server's routes and
  `/api/v1/oauth/requests` and `/api/v1/oauth/grants`. Without `mcp` the MCP variables are not
  read at all. `MailPool` is always built (an account's login test and the filters' folder
  picker use it); `SendRunner` exists only with `mail` or `mcp` (`AppContext.send_runner` is
  None otherwise). The base always mounts sign-in, SSO, users, accounts and
  linked identities.
- **The worker** reads it first, before the database: with neither `mail` (the index's sync) nor
  `filters` it logs why and exits 0, and an unknown name exits 1. Its rule set runs, scheduled
  dispatcher and the rule sets' IDLE watches need `filters`; the index's jobs and IDLE watches
  need `mail` (each job checks again when it runs).
- **The web app** takes the features from `/api/v1/auth/me` (all of them from an older API that
  does not name them) and keeps them across sign-in and sign-out. The nav, Profile's connected
  apps, an account's *Run filters* switch and its send mode (shown with `mail` or `mcp`) follow
  them. A screen whose feature is off stays a route and says it is not available, without
  loading its chunk or calling its API. The one exception is the mail screen's chunk on a load
  that opens on it (`/mail`, or `/` for a user whose home is mail): it is fetched alongside
  `/me`, from what this browser kept of the last one (`mailroom.ui.opening`: the home and whether
  mail ran), and not where that said mail was off; `/me` still decides what is shown.
- **Account and user removal** still delete the filter rows of what they remove whatever the
  features: cleaning up is the base's job.
- **`/api/v1/auth/me` names the features to anyone,** signed in or not, so the web app knows them
  before anyone signs in: an unauthenticated caller learns which features an instance runs,
  nothing more.

### MCP server

Specification: [`mcp.md`](mcp.md). Code: `mailroom.mcp`.

- **`/mcp` takes a bearer token only** (an OAuth access token, or a personal API token in every
  mode: *Personal API tokens*), never the session cookie, so a browser's ambient
  credentials never reach it. The verifier maps a token to a user id (`AccessToken.subject`) and
  must have issued it for `{MAILROOM_PUBLIC_URL}/mcp`; every tool then goes through the service
  layer as that user. In external mode the token's `aud` stands for the resource: an
  external issuer's JWT is checked by `mailroom.auth.oidc` (asymmetric algorithms only) and maps
  to a user only through a linked sign-in.
- **DNS rebinding:** `Host` must be `MAILROOM_PUBLIC_URL`'s (421 otherwise) and an `Origin`, when
  sent, its origin (403).
- **Strict inputs:** each tool's arguments are a Pydantic model with `extra="forbid"` and
  `strict=True`, with the REST API's bounds; a refusal's message reaches the model, nothing else
  does ("Internal error", the details in the log).
- **Mail strings** a tool returns (a run's matched `from` and `subject`) are wrapped in
  `<untrusted_email nonce="…">…</untrusted_email nonce="…">`, the tag name inside neutralised
  (also after NFKC folding: when the folded text holds it, the folded text is passed on with every
  occurrence rewritten, even beside a plain one), as mailroom-mcp wraps mail.
- **Off the event loop:** tools are async and run database and IMAP work in worker threads, at
  most 10 at a time, so a slow mailbox stalls neither the API nor the REST routes' threads.
- **Per-user cap:** a user has at most 2 tool calls running at once in an API process; one more
  is refused at once with a busy tool error, so one user cannot hold every tool thread. Mailbox
  work has a wall-clock deadline: `dry_run` 30 s, `folders_list`, the `mail_read`,
  `mail_organize` and `mail_draft` tools 30 s, the `mail_send` tools 30 s for admission and then up
  to 45 s waiting for the delivery (a send not over by then is "not confirmed, do not resend")
  (the mail pool's: the account's connections in use are cut, a `timeout` refusal; *Mail
  client* below).
- **Request size:** bounded after the bearer check and before any of the body is read
  (`RequestLimit` in `mailroom.mcp.app`): 40 MiB for a caller approved for `mail_draft` or
  `mail_send` who declares the length, at most 2 such requests per user in an API process (429
  beyond); 1 MiB for everything else (413). The web image's nginx allows `client_max_body_size 40m`
  on the exact location `/mcp`, with a 130 s read timeout, since a send is followed for up to 105 s.
- **Files from agents** ([`mcp.md`](mcp.md#files-from-agents)): decoded strictly, their type checked
  against their bytes, capped with the parts kept, and only as far as the send mode lets any
  message go.
- **Logs:** one `mcp_tool_called` per call, with `tool`, `user_id`, `client_id`, `grant_id`,
  `token_id`, `outcome` (`ok`, `refused`, `invalid_arguments`, `unknown_tool`, `failed`), `code`,
  `duration_ms` and `argument_keys`: the arguments' names, never their values. Changes log as from
  the web app, with `via` `mcp`.

### Built-in OAuth authorization server

Code: `mailroom.oauth`, self-contained (SQLAlchemy, the `mcp` SDK's authorize, token and register
handlers, httpx, and the `app.user` table; never the app's routes). Mounted when
`MAILROOM_MCP_AUTH` is `builtin` (the default) or `oidc` and `MAILROOM_PUBLIC_URL` is set. The `mcp` SDK is
bounded to one minor because the server relies on its handlers.

- **Issuer** `{MAILROOM_PUBLIC_URL}/oauth`, its RFC 8414 metadata at
  `/.well-known/oauth-authorization-server/oauth` (written by us: the SDK's advertises client
  secrets); endpoints `/oauth/{authorize,token,register,revoke}`.
- **Public clients only**, PKCE S256 only, one scope `mailroom`; `resource` must be
  `{MAILROOM_PUBLIC_URL}/mcp` (a token request naming another gets `invalid_target`). No client
  secret is ever issued, whatever a registration asks for.
- **Redirect URIs** on an allowlist: claude.ai's and claude.com's `/api/mcp/auth_callback`,
  `http://` loopback on any port and path, and `MAILROOM_OAUTH_EXTRA_REDIRECT_URIS` (https only).
- **Throttles** (in memory, per process): registration (`/oauth/register`) 10 per client key and
  30 per IPv6 /48 an hour; `/oauth/authorize` 30 per client key in 10 minutes; metadata document
  fetches 10 per client key in 10 minutes, whichever endpoint caused them. Over a limit is a 429
  with `Retry-After` (a fetch over its limit is not made). `/oauth/authorize` refuses a `state`
  over 1024 characters.
- **Registered clients** are capped at 1000; at the cap a registration evicts the oldest client
  that was never used and holds no grant, and is refused only when there is none. Clients without
  a grant are forgotten after a day if never used, after 30 days unused otherwise.
- **Client ID metadata documents:** a `client_id` that is an https URL on a host in
  `MAILROOM_OAUTH_CIMD_HOSTS` (default `claude.ai,claude.com`; empty turns them off), default
  port, with a path and no query, fragment or dot segment, is fetched with no redirects, 5 s in
  all and at most 64 KB. Its `client_id` must equal the URL, it must be a public client, and its
  redirect URIs must be on the allowlist. Cached in `app.oauth_client` for 24 hours. A refetch
  that fails in transit (timeout, transport error, a status other than 200) keeps the cached copy
  serving for up to 7 days after its last good fetch; a document that is fetched and invalid is
  not served. A failed fetch is remembered per process for 5 minutes and not repeated meanwhile.
  Against SSRF, the host allowlist is checked first, then the fetch goes through `safe_fetch`
  (*Outbound HTTP to a host from input* above), so an allowed host that resolves to a private
  address is never fetched: that, like a URL `safe_fetch` refuses or a document over the cap,
  counts as invalid; no answer in time, a host that does not resolve, a redirect or a transport
  error count as failing in transit.
- **Consent:** `/oauth/authorize` stores the request (10 minutes) and redirects to
  `/connect?request=<id>`. The first user to open it claims it; nobody else can see or decide it.
  In `oidc` mode it redirects to the SSO start instead, and the SSO callback claims the request
  for the session it signs the browser in with: no other session can see or decide it.
  Approving creates a grant (a connected app) for the tool groups the user checked, and a code;
  a choice that cannot be approved (none, a group not served, one without a group it requires)
  is a 400 that leaves the request open. The redirect carries `code`, `state` and
  `iss` (RFC 9207), and so does every error redirect back to the client, the SDK's included. The
  screen's API (`/api/v1/oauth/requests/{id}`) needs the session cookie, its `POST` is refused
  cross-site like any other, and an unknown, expired or another user's request is a 404; the SPA,
  not the API, navigates to the returned redirect URL.
- **Connected apps** (`/api/v1/oauth/grants`): a user lists, changes the tool groups and the limits of, and
  revokes only their own grants; revoking deletes the grant and its tokens, and grants left from
  an earlier mode stay revocable.
- **Tool groups per grant:** a grant records the groups its user approved, and its access token
  carries them, so `/mcp` lists and runs only those tools: a call to another served tool is
  refused (`not_approved`) and logged like any refusal. A token from an external issuer names
  no groups and gets every served one.
- **Limits per grant:** `account_ids` and `send_ceiling` (null: none) come with the access token
  and make the call's reach (`mailroom.reach`), carried on its `AppContext`: the services read the
  accounts through `AppContext.accounts`, cut to it with each send mode capped, and rows that name
  an account are cut by `Reach.covers`, so an account beyond is answered as one that is not the
  user's. Limits that cannot be read reach nothing and send nothing. The gate stays the one place
  sends are decided: it is given the capped account. The worker reads the grant again for a held
  send. Only the user's own accounts can be chosen (400 `accounts_not_approvable`, the request
  left open); one removed is taken out of the grants, never leaving one unlimited.
- **Opaque secrets, hashed:** request ids, codes and tokens are 256-bit random strings and only
  their SHA-256 is stored. A code is single use and marked used as it is read, so a failed PKCE
  check burns it; 10 minutes. A used code stays until it expires, and presented again it ends its
  grant and the tokens issued from it (RFC 6749, 4.1.2). Access tokens last 1 hour, refresh
  tokens 30 days and rotate on every use; a spent refresh token presented again ends the whole
  grant. Revoking either token ends the grant. Removing a user removes their grants (cascade).
  Expired rows are swept on writes.
- **Logs:** `oauth_client_registered`, `oauth_client_registration_refused` (with `reason`),
  `oauth_client_registration_throttled`, `oauth_client_evicted`, `oauth_authorization_throttled`,
  `oauth_client_document_refused`, `oauth_authorization_approved`, `oauth_authorization_denied`,
  `oauth_token_issued` (`trigger`: `authorization_code` or `refresh_token`),
  `oauth_authorization_code_reused`, `oauth_refresh_token_reused`, `oauth_grant_revoked`,
  `oauth_grant_groups_changed` (with the groups `added` and `removed`),
  `oauth_grant_limits_changed` (with `account_ids` and `send_ceiling`),
  `oauth_sso_signed_in` (`oidc` mode: the SSO callback claimed a request), with
  `user_id`, `client_id` and `grant_id` where known; never a token.
- **`/mcp`'s verifier** in builtin mode: an access token is looked up by its hash, so a revoked or
  expired one stops working at once; the `AccessToken` names the user and carries `grant_id` and
  the grant's `tool_groups`, `account_ids` and `send_ceiling`.

### Sign-in

- **Users** (`app.user`): a username, stored lowercase (1-64 of `a-z 0-9 . _ -`, starting with a
  letter or digit), an Argon2id password hash (passwords of 12-1024 characters), an admin flag,
  an optional time zone (`time_zone`, an IANA name; null means UTC), and what "mark all as read"
  on a folder with subfolders does first (`mark_read_subfolders`, true by default: them too;
  revision `202611191200`), and how far back the mail index keeps their mail
  (`index_max_age_days`, null for no limit of their own; revision `202611201300`), and whether
  the web groups their lists by conversation (`conversations`, true by default; revision
  `202611211400`), and how the web looks (`appearance`, below; revision `202611222000`).
  `mise run create-user`
  makes one; startup warns while there are none. An unknown username still checks a dummy hash, and
  both failures answer the same 401, so neither timing nor wording tells whether a user exists.
- **Sessions** (`app.user_session`): one per signed-in browser, deleted with their user. The
  cookie `mailroom_session` carries a random token; the database keeps only its SHA-256. An absolute
  lifetime of `MAILROOM_SESSION_TTL_DAYS` (default 14), no sliding renewal; signing out deletes the
  row, and a login sweeps expired ones. Every route but health, readiness, version, login, logout
  (idempotent, so an expired tab can still sign out), `auth/me` (which answers `{"user": …, "features": […]}`) and
  SSO's `auth/oidc/*` depends on `require_caller` (`backend/mailroom/api/deps.py`) and answers 401 without a valid
  session, or a personal API token where the route is open to them (below).
- **Sessions list** (`GET /api/v1/auth/sessions`, `DELETE …/{id}`, `POST …/end-others`; the cookie
  only, the user's own only; revision `202611241100`): each live session's `device`, `ip`,
  `created_at`, `last_seen_at`, `expires_at` and whether it is `current`, that one first. `device`
  names the browser and the system (`Firefox · Linux`) from fixed tables
  (`mailroom.auth.sign_in_client`), null when neither is recognised; the User-Agent itself is never
  kept. `ip` is recorded at sign-in as far as `session_ip` goes (*Instance settings*): nothing, an
  IPv4 /24 or IPv6 /48 (`203.0.113.x`, `2001:db8:1:x`), or all of it. The list shows no more than the
  setting allows now, but what an earlier setting recorded stays stored until its session ends.
  `last_seen_at` is written at most every 5 minutes, by any request the session
  signs in and by its open live updates. Ending one is a 404 for an id that is not the user's, and
  a 409 `current_session` for this one, which ends by signing out. An ended session's next request
  is a 401, and its live updates end at their next heartbeat. The web lists them on *Profile*.
- **Cookie:** `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` except with
  `MAILROOM_ENV=development` (the dev server is plain HTTP).
- **CSRF:** no token. Same origin plus `SameSite=Strict`, and `CrossSiteRequestMiddleware` refuses
  (403) a state-changing `/api/` request whose `Sec-Fetch-Site` is not `same-origin`, or, without
  that header, whose `Origin` names another host. Add a token only if the API ever serves another
  origin.
  A personal API token changes nothing here: the check comes before, whoever the request speaks for.
- **Login throttling:** after n consecutive failures a client waits 2^(n-1) s, capped at 15 min,
  before an attempt is even checked (429 with `Retry-After`); an attempt counts from the moment it
  is let through, so parallel guesses wait too, and a success resets it. A client is an IPv4
  address or an IPv6 /56: the last address the host's reverse proxy appended to `X-Forwarded-For`,
  which the web container's nginx passes on and uvicorn trusts from loopback. In memory, per API
  process.
- **Managing users** (`/api/v1/users`, admins only: 401 signed out, 403 otherwise): add,
  remove, reset a password. Nobody removes themselves, and a removal locks both users' rows in id
  order first, so two admins removing each other at once leave one: there is always an admin. A
  reset ends all of that user's sessions, and is not for the admin's own password; removing a
  user deletes their sessions with them. A reset lets an admin sign in as that user, so it is
  logged; a reset that the user must complete themselves is a later option, not a promise. With
  `revoke_access` (false when absent; the web's form has it ticked) it also revokes all their API
  tokens and their grants, in the same transaction (`mailroom.auth.app_access`), logged as each
  is alone (an external issuer's tokens are not Mailroom's to revoke).
- **Instance settings** (`GET`/`PUT /api/v1/admin/settings`, admins only, as above;
  `app.instance_setting`, revision `202611241000`): `audit_retention_days` (a JSON integer, 1-3650;
  until set, `MAILROOM_AUDIT_RETENTION_DAYS`, 90 without it) and `session_ip` (`none`, `partial`,
  the default, or `full`: what of the client's IP a session records at sign-in, and at most shows). A
  `PUT` carries only the settings to change, so that a form left open undoes no other admin's change;
  anything else (null included) is a 422. Only the settings it changes get a row, with who changed it
  and when (null once that admin is removed), so the others keep following their defaults; a stored
  value that is no longer an option reads as its default. Read through a 5-second cache in each
  process, which a change there drops (`mailroom.config.instance_settings`). The web's form is the
  *Users* page's *Instance* section.
- **Own password** (`POST /api/v1/auth/password`): needs the current one, checked through the
  login throttle (the same per-client counter as sign-in) so a borrowed session cannot guess it;
  keeps this session and ends the others. `revoke_access` as in a reset, unticked in the web.
- **Own time zone** (`PUT /api/v1/auth/me/time-zone`, `{"time_zone": "Europe/Rome"}`): any
  letter case, kept as the IANA name; an unknown zone is a 422, `null` unsets it. `auth/me`'s
  `user` carries it (`time_zone`, null until set). The MCP read tools, reply and forward quotes,
  the send limit's refusal and the filters' `older_than_days` use it; unset, UTC. The web sets it
  on *Profile*, and saves the browser's zone once when it finds a signed-in user without one
  (`web/src/auth/auth-provider.tsx`); its dates use the saved zone, or the browser's when this
  browser does not know it.
- **Own "mark all as read" default** (`PUT /api/v1/auth/me/mark-read-subfolders`,
  `{"mark_read_subfolders": false}`, a JSON boolean): whether marking a folder with subfolders
  read takes them too unless the user picks the other item
  ([`mail-client.md`](mail-client.md) — *Folder management*). `auth/me`'s `user` carries it;
  logged as `mark_read_subfolders_changed`. The web sets it on *Profile*.
- **Own index age limit** (`PUT /api/v1/auth/me/index-max-age`, `{"index_max_age_days": 365}`,
  a JSON integer from 1 to 36500, or null): messages older are not indexed, and stay readable
  live; `MAILROOM_INDEX_MAX_AGE_DAYS` caps it for everyone
  ([`mail-client.md`](mail-client.md) — *Index*). `auth/me`'s `user` carries it; logged as
  `index_max_age_changed`.
- **Own conversations default** (`PUT /api/v1/auth/me/conversations`, `{"conversations":
  false}`, a JSON boolean): whether the web's lists are grouped by conversation
  ([`mail-client.md`](mail-client.md#conversations)). `auth/me`'s `user` carries it; logged as
  `conversations_changed`.
- **Own appearance** (`PUT /api/v1/auth/me/appearance`, some of `{"theme", "accent",
  "density", "text_size", "reading_pane", "home", "swipe_left", "swipe_right"}`, at least one;
  null leaves one as it is): how the web looks, the same on every device
  (`mailroom.auth.appearance`); `home` is the page the app opening on `/` shows (`dashboard`, or `mail`, `filters`,
  `accounts`, `activity`: the dashboard where the instance does not offer it). `app.user.appearance` (JSONB) keeps only the choices the user
  made, merged over the earlier ones, so a later change of a default reaches whoever never chose;
  `auth/me`'s `user` carries every choice, a default where none was made or where a stored one is
  no longer an option. An unknown choice or option is a 422; logged as `appearance_changed`. The
  web reads an option it does not know as its default too (`web/src/api/auth.ts`), so neither an
  older nor a newer API breaks sign-in. What suits one device only (a pane's width or height, a
  collapsed folder pane, the account Mail last showed) stays in that browser, under
  `mailroom.ui.<name>` (`web/src/lib/device-prefs.ts`).
  The theme, accent and text size are also copied there (`mailroom.ui.look`, `web/src/theme/look.ts`)
  so a load starts in them before `auth/me` answers; the server's value then replaces the copy.
  The home is copied the same way, with whether mail ran (`mailroom.ui.opening`,
  `web/src/auth/opening.ts`), only to fetch the mail screen's code early (*Editions*).
  Every accent, light and dark, meets WCAG AA for its text pairs (*Accessibility*), which
  `web/src/theme/contrast.node.test.ts` checks against `index.css`. Native checkboxes, radios and
  ranges take the accent too (`accent-color` from `--primary`).
- **A session ending under the user:** any 401 from the API but a refused sign-in signs the web
  app out (`onSessionEnded` in `web/src/api/http.ts`), and the sign-in screen says the session
  has ended.
- **Refusals** about users answer 400 with `{"detail": {"code", "message"}}`; the web app words
  each `code` itself, in the reader's language.
- **Logs:** `login_succeeded` (with `user_id`; `via="sso"` through SSO), `login_failed`,
  `logout`, `session_ended` and `other_sessions_ended` (with `user_id`, the latter with the
  `count`), `user_created`,
  `user_removed`, `password_reset` (with `user_id`, the admin, and `target_user_id`),
  `instance_settings_changed` (with `user_id`, the admin, and the `settings`' names),
  `password_changed`, `mark_read_subfolders_changed`, `index_max_age_changed`,
  `conversations_changed`, `appearance_changed`,
  `password_change_failed`, `sso_refused` and `sso_failed` (with the
  `outcome`), `sso_not_linked`, `sso_identity_linked` (with `user_id` and `via`: `profile` or the
  link claim), `sso_identity_unlinked`; never a password, a token, or a username.

### Personal API tokens

Code: `mailroom.auth.api_tokens`, `mailroom.api.deps`, `mailroom.api.token_access`,
`mailroom.mcp.verifier`.

- **Token** (`app.api_token`, revision `202611241300`; deleted with its user): `mrt_<prefix>_<secret>`,
  8 hex digits and 256 random bits. The row keeps the prefix, to show, and the whole only as its
  SHA-256, with its name, `tool_groups`, `account_ids` and `send_ceiling` (as a grant's:
  [`mcp.md`](mcp.md) — *Limits*), `last_used_at` (written at most once a minute) and `expires_at`
  (null for never). Revoking deletes the row, so the next request is a 401; an expired one is a
  401 too and stays listed until revoked. A removed account leaves the tokens narrowed to it, and
  one left without any reaches none. Never logged.
- **Managing** (`GET`/`POST /api/v1/me/api-tokens`, `DELETE …/{id}`; the cookie only, the user's
  own only): a `POST` takes `name`, `groups` (checked as at consent, 400 `groups_not_approvable`),
  `account_ids` and `send_ceiling` (400 `accounts_not_approvable`), and `expires_in_days` (30, 90,
  365 or null), and answers the `token` that once. 50 per user (409 `too_many_tokens`). The groups
  offered are those the MCP server serves or, with it off, those `MAILROOM_MCP_TOOL_GROUPS`
  selects that the instance has REST routes for; one no longer offered is not held.
- **On the REST API:** `Authorization: Bearer`, without the session cookie. Both at once is a 400
  `two_credentials` rather than one quietly winning (other schemes' credentials are not read). A
  route refuses every token (403 `token_not_accepted`) unless marked `open_to_tokens` with the
  tool groups that open it, one of which the token must hold (403 `token_not_approved`): those
  with an MCP counterpart, and reads and organize writes of the same kind (the exports too); and
  cancelling a held send the same token scheduled, any other being `not_found` to it (audited as
  `send_cancel`, refused ones too once the worker has taken it). Closed, so the
  cookie's only: password, profile settings, sessions, linked sign-ins, users, instance settings,
  grants and consent, tokens; an account's settings, test, quota, signatures and vacation reply, and
  the user's limits (a token could widen itself through them); the test send, one-click
  unsubscribe, live updates, and what only serves the web's screens. A test lists the open routes,
  so a new one is a decision.
- **It acts as an agent, not as the user:** its reach narrows `AppContext` as a grant's does; the
  send gate takes its sends as an agent's (the account's send mode and the ceiling apply, and
  `confirm_resend` is ignored), whatever the route says; `/accounts` gives what `accounts_list`
  does; filters change only on a disabled rule set, and enabling one takes `filters_run`. Its held send
  keeps the token and fails once that is revoked or expired (`token_revoked`) or no longer allows it
  (`not_approved`). Its records say `agent` with the `token_id`, named after it while it exists.
  One `api_token_request` line per request: `user_id`, `token_id`, `outcome`.
- **On `/mcp`**, in every mode: `PersonalTokenVerifier` takes a token of that form first and leaves
  any other to the mode's own verifier; its `AccessToken` carries the claims a grant's does.

### Single sign-on

- **Off** unless `MAILROOM_PUBLIC_URL` and `MAILROOM_OIDC_ISSUER`, `_CLIENT_ID` and
  `_CLIENT_SECRET` are all set (a warning names what is missing). `MAILROOM_PUBLIC_URL`
  (`backend/mailroom/config/public_url.py`) is an origin, `https://` or plain `http://` on loopback,
  with a valid host (an IP address or an ASCII DNS name: letters, digits, inner hyphens) and the
  scheme's default port dropped (`:443`, `:80`), parsed once at startup (anything else is ignored
  with a warning, as if unset); the callback, `{MAILROOM_PUBLIC_URL}/api/v1/auth/oidc/callback`, is
  never built from a request's `Host`.
- **Provider** (`backend/mailroom/auth/oidc.py`): discovery (`issuer` must match, a trailing slash
  aside; every endpoint `https://` or loopback) cached for an hour, the JWKS fetched over `httpx`
  with no redirects, a 5 s timeout and a 256 KB cap, refetched when a token names an unknown `kid`
  (at most every 30 s). A failed fetch of either is cached too: not tried again for 30 s, and
  meanwhile the last good copy (up to a day old) stays in use, with a warning, so a provider
  outage neither piles requests up behind the fetch nor signs everyone out. A token's header (`alg`,
  `kid`) is checked before anything is fetched. JWTs (`verify_jwt`, also the MCP server's for an external issuer) are
  checked with PyJWT: RS256, ES256, PS256 or EdDSA only, **never HS\*** or `none`; `iss`, `aud`,
  `sub`, `exp` and `iat` required, 60 s of leeway. An ID token also needs the flow's `nonce`, and
  `azp` equal to the client when it has several audiences or names one.
- **Flow** (`backend/mailroom/auth/sso.py`): authorization code with PKCE (S256) and a nonce,
  client secret by HTTP Basic (or in the form when the provider supports only that). `start`
  stores the flow in `app.sso_login_state` under the SHA-256 of `state` (10 minutes, swept on
  each start, at most 10 000 at once and 20 per client; a client, by `client_key` as for the
  login throttle, starts at most 10 a minute, in memory per process: else `busy`) and sets `mailroom_sso_binding`, a random token whose
  SHA-256 the row keeps: `HttpOnly`, `SameSite=Lax` (it must come back on the provider's
  cross-site redirect), `Path=/api/v1/auth/oidc`. The callback deletes the row before anything
  else (single use) and refuses it without the matching cookie, so a callback another browser
  started is never signed in (login CSRF). `return_to` is a path on this site (`/…`, never
  `//host`, `/\host`, a scheme or a control character), else `/`. A sign-in answers with a page
  that goes on to `return_to` by a meta refresh (`no-store`, `Referrer-Policy: no-referrer`,
  `default-src 'none'`, no script) rather than a redirect, so the `SameSite=Strict` session cookie
  goes along from this site; a link and the failures redirect.
- **Who signs in:** the user an `app.external_identity` `(issuer, sub)` links to, deleted with
  their user. Linking from the *Profile* page (`intent=link`) needs the session, which the
  `SameSite=Strict` cookie only brings from this site, and refuses a sign-in already linked to
  someone else. With `MAILROOM_OIDC_LINK_CLAIM=preferred_username` (the only value; anything
  else, `email` included since a username never holds an `@`, is ignored with a warning) an
  unlinked sign-in whose `preferred_username` equals an existing username exactly (no case
  folding or trimming) links on first use, unless that user already has a sign-in linked from
  the issuer; an existing link never moves. The provider must not let users choose or change
  their usernames, or one could claim another's account. Nobody is created.
- **Failures** come back to `return_to` with `sso_error=<code>` (`not_linked`, `state_invalid`,
  `browser_mismatch`, `denied`, `unavailable`, `failed`, `identity_taken`, `signed_out`,
  `not_configured`, `busy`), which the web app words.
- **Routes:** `GET /api/v1/auth/oidc/config` (`{enabled, name}`), `/start`, `/callback`;
  `GET /api/v1/me/identities` and `DELETE /api/v1/me/identities/{id}` behind the session.

### Mail accounts

- **Storage:** mailroom-core's `AccountStore(engine, str(user.id))` over its schema `core` in the
  app's database: one settings document per user (revisions, replaced whole) and the passwords
  sealed beside it (AES-256-GCM under a subkey of `MAILROOM_SECRETS_KEY`, 64 hex digits, bound to
  the account: its former slug, else its id; [`../core.md`](../core.md) — *Compatibility*). Without the key (or with a malformed one)
  startup warns, the accounts stay listed, and adding, a new password, a test or a folder listing
  answers 503 `secrets_key_missing`. The app's own `app.account_filter_settings(user_id,
  account_id, run_filters)` holds the Run filters switch; no row is off.
- **Ids:** an account's id is a positive integer from mailroom-core's `account_ids` sequence,
  given when the account is added, unique across the instance and never reused; the web never
  shows it. Accounts added before were numbered by `core_0004` and the app's `202611171200`,
  which rewrote every stored id in one upgrade (the settings and their history, sealed secrets,
  send log and draft send keys, rule sets, Run filters switches, audit records and their
  `destination_account` / `source_account`, run reports, waiting jobs' locks), naming an unnamed
  account by its slug unless another of the user's accounts already has that name (compared
  trimmed and case-insensitively; slugs, all lowercase, never clash with each other). Refs and
  cursors made before read as `stale_reference`.
- **API** (`/api/v1/accounts`, the signed-in user's own; another user's id is a 404, one that is
  not a positive integer a 422): list, add (`POST`, with the password and no id: the answer
  carries it), edit (`PUT /{id}`; no password keeps it), remove
  (`DELETE /{id}`, its switch too), `POST /{id}/test` (an IMAP login and logout:
  `{"ok", "code", "message"}`), `GET /{id}/quota` (the server's quota, kept 10 minutes:
  [`mail-client.md`](mail-client.md) — *Mailbox quota*), `GET /autoconfig?address=` (suggested
  servers, stored nowhere: [`mail-client.md`](mail-client.md) — *Account autoconfiguration*).
  Responses carry `has_password`, never the password. The listing logs in to no server, so
  nothing waits on a mailbox: its `keywords` is null, as in the answer to a change.
  `GET /keywords` says whether each account's server keeps keywords (To sort), in the accounts'
  order (`[{"account_id", "keywords"}]`): mailroom-core's cached `keywords`, asked of every
  account side by side for 5 s at most, null for one that cannot log in or has not answered (only the
  connection asking is cut then, never the account's other reads and writes). One
  whose server refused the login or could not be reached is not asked again for 5 minutes,
  unless the account changes. MCP's `accounts_list` still carries `keywords`, asked the same
  way. An account may have a `name` (1 to 64
  characters, not blank) and a `colour` (`slate`, `red`, `orange`, `amber`, `green`, `teal`,
  `sky`, `blue`, `violet` or `pink`), each null when unset and given back as stored, so a
  client tells an unnamed account from one named after its login. Responses also carry a
  read-only `display_name`, what to call the account: its `name`, else its login (a body that
  sends it back has it ignored). An account may also name its authentication server,
  `authserv_id` (an RFC 8601 authserv-id: up to 253 characters of an RFC 2045 token, such as
  `mx.google.com`; null when unset, and then no Authentication-Results field is believed for its
  mail). An edit replaces `name`, `colour` and `authserv_id` like the other fields. No
  two of a user's accounts share a name, compared NFKC-normalised, trimmed and whatever their
  case; unnamed accounts share none. Core's `core_0005` repaired any stored duplicates: in each
  user's current settings the first account keeps the name and later ones become unnamed. An
  account's folders and its Run filters switch are the filters' routes ([Filters](#filters)).
- **Checks:** mailroom-core's config models (`core_settings_or_issues`) validate the whole document
  before each save; a refusal is 400 `{"detail": {"code": "invalid_account", "message",
  "issues": [{"path", "message"}]}}`, `path` inside the account. A name another of the user's
  accounts has, on an add or an edit that changes the name, is 409 `account_name_taken` in the
  same shape (its issue at `["name"]`), checked in the same revision as the save; an edit that
  keeps the account's name is never refused for it. A name equal to the account's stored name
  once normalised as above (another case, say) counts as kept. A save racing another is 409
  `accounts_changed` or 503 `accounts_busy`.
- **Limits** (`GET` / `PUT /api/v1/me/limits`, mounted with the mail layer): the signed-in user's
  `send_per_hour`, `max_recipients`, `body_chars`, `attachment_max_mb` and `move_max_mb` (a
  message moved to another account, 25 MB by default), in the same settings document as the
  accounts. A `PUT` replaces them all (a body without `move_max_mb`, from a client older than
  it, keeps the stored one), checked by mailroom-core's models plus the 25 MB attachment cap and
  the 50 MB move cap, every field at fault named at once; a refusal is 400 `invalid_limits`
  with `issues`, a save at the same moment 409 `settings_changed` or 503 `settings_busy`. A stored attachment or move limit above its
  cap reads as the cap. Logged as `limits_changed`.
- **IMAP:** everything goes through mailroom-core's connections, implicit TLS or STARTTLS as the
  account says. An account's login test (mailroom-core's `test_login`: a login and logout on a
  connection of its own, each reply bounded by its 10 s probe timeout), the folder picker's
  listing and the dry run use the API's mail pool, with its gates and deadline
  ([Mail client](#mail-client); a dry run's is 30 s, *Filters* below); the worker's runs and IDLE
  watches and the index's sync use the worker's own pool (below). **Connection budget per
  account:** the API's pool (2), the worker's pool (1), one IDLE connection per folder of its
  `new_mail` rule sets, and at most 2 for the index (INBOX or Gmail's All Mail, and the folder
  viewed last), a folder both watch counting once; a server that caps an account's connections
  (Gmail 15, Dovecot's default 10 per user and IP) leaves room for the user's other clients. TLS is verified against the system CAs plus
  `MAILROOM_MAIL_CA_FILE` (the dev mail CA in dev). A failure carries mailroom-core's mail error
  code and fixed message (`auth_failed`, `unreachable`, `timeout`, `busy`, `mailbox_missing`,
  `server_error`, …; an `unreachable` whose certificate did not verify ends with core's fixed
  `CERTIFICATE_DETAIL`, which the web says in the reader's language), or the app's own
  `password_missing` or `secrets_key_missing`; never the server's wording. A folder listing
  answers it by `mail_status` (502 by default, 504 for `timeout`, 429 for `busy`, 404 for
  `mailbox_missing`, 409 for `auth_failed` / `password_missing`), and anything unexpected with
  500 `failed`. Migration `202611121200` rewrote the runs recorded earlier to these codes and
  their fixed messages.
- **Logs:** `account_added`, `account_changed`, `account_removed`, `account_tested` (with
  `outcome`), each with `user_id` and `account_id`, and the changes with `via` (`web`, or `mcp`
  for the MCP server); `account_autoconfig` with `user_id` and `outcome` (the source, or
  `none`); never a login, a host or a password.
- **Removing a user** removes, in the same transaction, the app's rows about their accounts (and the
  senders whose remote content they show and those whose mail they read as text, and the ledger
  of copies their filters moved between accounts) and mailroom-core's (`forget_user`: settings with their
  history, sealed passwords, send log). While a save of their accounts holds them past
  mailroom-core's lock timeout, nothing is removed: 503 `user_accounts_busy`.
- **Known risk — outbound connections:** a login test or a folder listing connects to whatever
  host and port the user entered, so any signed-in user can make the server open a connection
  from its network to any `host:port` (internal services included) and learn from the failure kind
  whether something answered. Accepted because only admin-created users sign in;
  revisit before anyone else can.

### Mail client

What it does and the routes: [`mail-client.md`](mail-client.md). How:

- **Connections** go through mailroom-core's `MailConnections` (imapclient; implicit TLS or
  STARTTLS, verified as above), one per user per API process (`mailroom.mail.pool`, built on
  every instance: the login test and the folder picker use it too), up to two connections per
  account, built on first use from the user's settings and sealed passwords. They
  are logged out after 5 minutes unused, when an account is changed or removed, when its user is
  removed, and when the user's settings are found at a new revision (a change made by another
  process). The lifespan closes them all.
- **Fairness:** a call first passes, in the request's own thread, three gates: the process's (24
  calls holding or waiting at once, well below the 40 request threads, so mail never takes them
  all; one more is 429 `busy` at once), the user's (4 calls at once) and, on one account, the
  account's (2, its connections). At the last two a few callers wait, 10 s at most for both
  together, and any more answer 429 `busy` at once. Only then does it take one of the pool's 16
  shared threads, so one user's slow server or burst of requests holds neither the shared threads
  nor more than a few request threads. A gate opens again when the work ends.
- **Deadline:** a call answers 504 `timeout` after 30 s, and the account's connections in use are
  cut then (mailroom-core's `interrupt`), which ends the work at once and frees them; a call of
  the same user on that account at that moment fails as well (10 s to connect, 30 s for any one
  server reply). An all-accounts search gives each account 12 s to list its folders (and, for
  `has_attachment` or `gmail_query`, to read its capabilities, which can log in even when one
  folder is searched) and cuts the ones still reading their mailbox at 25 s (an account already
  done is left alone, so another call on it goes on): they are `timeout` failures of the page,
  and the rest answer.
- A call that fails unexpectedly answers 500 `failed` and logs `mail_call_failed` (with `call`,
  the event's name) and its traceback; the event's outcome is `failed`. A password that stops
  opening once a call began is `password_missing`, also when mailroom-core reported it as a
  page's `server_error`.
- **Errors** are mailroom-core's codes with their fixed messages, never the server's wording
  (statuses: [`mail-client.md`](mail-client.md), *Errors*).
- Every answer under `/api/v1/mail/` carries `Cache-Control: private, no-store`, whatever its
  status: a refusal, a 422, the 401 of a missing session and the cross-site 403 too (a middleware
  outside the cross-site check, `mailroom.api.routes.mail.NotStored`).
- **Live updates** ([`mail-client.md`](mail-client.md), *Live updates*; `mailroom.mail.events`):
  each API process opens one psycopg async connection at startup, LISTENs on `mailroom_index`
  and closes it at shutdown (a lost one is opened again after 5 s; what was announced meanwhile
  is not replayed). A notification carries ids only; for a user with a stream open on that
  process the hub reads the folder's row and new rows in a thread, after the commit that
  announced them, and puts the events on each of that user's streams' queues. A stream is an
  asyncio task, not a thread, and holds no database connection while it waits: its interest's
  writes (open, each heartbeat, close) commit at once. The stream registers with the hub when
  its body starts, so a browser gone before that leaves nothing behind, and each heartbeat checks
  its session again: signed out or expired, the stream ends and drops its interest. Limits: 10 streams per user per process
  (one more is 429), a queue of 100 events per stream (a stream behind by that much is closed,
  `mail_events_dropped`), a heartbeat comment every 25 s, which keeps a proxy's read timeout
  from closing a quiet stream (it must exceed 25 s) and renews the interest. `X-Accel-Buffering:
  no` keeps nginx from buffering the stream; a host proxy that buffers responses must not buffer
  `text/event-stream` ([`../deploy.md`](../deploy.md)). Uvicorn gives open requests 5 s at a
  stop (`--timeout-graceful-shutdown`), since a stream never ends by itself. Logs:
  `mail_events_dropped` and `mail_events_read_failed` (`user_id`, the error's type) and
  `mail_interest_failed` (`user_id`, the error's type); never a folder, a sender or a subject.
- **Desktop notifications** ([`mail-client.md`](mail-client.md), *Desktop notifications*) are
  the browser's own, shown by the page from the user's own stream: no push service, nothing sent
  elsewhere. A notification's text is the sender and subject alone, as plain text. The browser's
  permission is asked only from the Profile's button. Changing the folders logs
  `notification_folders_changed` (`user_id`, `account_id`, how many folders), never a path.
- **Organize calls** (move, archive, trash, flag) check every ref before connecting: a malformed
  one, or one of an account that is not the user's (404) or cannot log in (`password_missing`
  409, `secrets_key_missing` 503), refuses the batch and nothing is written. A call cut at the
  deadline answers `timeout`: the groups done before the cut stay done, and the ones after it are
  refused, not written (its connections refuse every mailbox once the deadline passed); no write
  is retried. The group running at the cut is cut with its connection: with `MOVE` it is done or
  not; without `MOVE` it can be copied but not removed, so after a 504 a client should reload the
  folders rather than repeat the call.
- **Logs:** `mail_folders`, `mail_quota`, `mail_listed`, `mail_searched`, `mail_read`,
  `mail_thread_read` (MCP only), `mail_html_read`, `mail_attachment_read`, `mail_source_read`,
  `mail_print_read`, `mail_filter_draft_read`, `mail_thread_key_read` (a mute),
  `mail_view_listed` (a view's page, `account_id` `*`), each with `user_id`, `account_id`, `via`,
  `outcome` (`ok` or the code) and `duration_ms`; never a subject, an address, a folder name or a
  body. `mail_addresses_suggested` (a recipient field's suggestions) carries `user_id`, `via`,
  `count` and `duration_ms`, never what was typed nor an address.
  `view_created`, `view_changed`, `view_removed` and `views_reordered` carry `user_id` and
  `view_id` (not for a reorder), never a view's name, search or folder. `mail_moved`, `mail_archived`, `mail_junked`,
  `mail_trashed` and `mail_flagged` carry `user_id`, `via`, `account_ids`, `ref_count`, `changed`
  (messages), `groups`, `failed_groups`, `codes` (the failed groups'), `outcome` (`ok`,
  `partial`, `refused`, `failed`, or the code of a refused call) and `duration_ms`: counts and
  codes only, never a folder, a ref, a `Message-ID` or a sender. `mail_bulk_counted` (`POST
  /messages/count`) is logged as `mail_searched` is; `mail_bulk_acted` (`POST /messages/bulk`)
  carries `user_id`, `via`, `account_ids`, `expected`, `done`, `remaining`, `groups`,
  `failed_groups`, `codes`, `outcome` and `duration_ms`, never a search term.
  `mail_folder_created`, `mail_folder_renamed`, `mail_folder_deleted` and
  `mail_folder_marked_read` carry `user_id`, `via`, `account_id`, `outcome`, `duration_ms` and
  counts only (`rule_sets`, `filters`, `rule_sets_updated`, `messages`); `folder_rule_sets_failed`,
  a rename whose rule sets could not follow, the error's type. Search criteria travel
  in a `POST` body, so access logs (nginx's, the host proxy's) never see them, and the web
  image's nginx logs paths without their query string (a list's `folder`, a search the SPA keeps
  in its URL; [`../deploy.md`](../deploy.md)). A ref is in the path of the message routes, and it
  encodes the account, the folder name and the UID: an access log does see that.
- **Drafts and sends** ([`mail-client.md`](mail-client.md), *Slice C*):
  - **Limits:** the user's `send_per_hour` and `max_recipients`, 50 addresses per field, a body of
    100,000 characters or of 3 MiB of UTF-8 in HTML (a save's own `html` part), a subject
    of 998, 10 new files and the user's `attachment_max_mb` (at most
    25) in total, a 2 MiB original read for quoting. `python-multipart` parses the one multipart
    route (`POST /drafts`, a save or, with `?convert=text`, the switch to plain text); the web image's nginx allows `client_max_body_size 30m` on that exact
    location (repeating its security headers) and the host's proxy needs the same (attachments
    plus 5 MiB for the fields fit it), and the route
    refuses early from `Content-Length`, then at the user's limit as the body arrives, once the
    user is known: FastAPI's own multipart handling reads the body first. A user's fourth save at
    once is `busy` before its body is read, since the mail gates come only after that. Files past
    1 MiB spool to the API container's temporary directory until the body has been read into
    memory, before the save runs.
  - **Execution:** admission runs under the 30 s mail deadline; delivery runs in a `SendRunner`
    (`mailroom.mail.sends`: 4 threads, 2 per user, `busy` before anything is claimed). SMTP
    timeouts: 15 s to connect, 60 s per command, 60 s plus the size at 128 KiB/s for `DATA`, a
    watchdog at 360 s. SMTP uses one connection per send with a verified certificate. At shutdown
    a running send gets 5 s, then its connection is cut.
  - **Storage:** mailroom-core's `core.sends` holds a row per send (a key, a state, the account
    and the result), keyed rows for at least 30 days and key-less ones (an agent's direct send) for a
    day, pruned when the user next sends; claiming counts and inserts under a per-user advisory
    lock.
  - **Logs:** `mail_prefilled`, `mail_draft_saved`, `mail_draft_imported`, `mail_draft_checked`,
    `mail_draft_read`, `mail_draft_found`, `mail_draft_deleted`, `mail_send_requested` and
    `mail_send_finished`, each with `user_id`, `account_id`, `via` (`web` or `mcp`), `outcome`
    (`ok`, the state or the code) and `duration_ms`, and a finished send's `send_id`, `state`, `filed`, `recipient_count` and
    `rejected_count`; `mail_send_not_filed` and `mail_send_not_settled` with their `code`. Never an
    address, a subject, a body, a filename, a `Message-ID`, a ref or the server's wording.
    mailroom-core produces an audit item for each draft and send (recipients, and a `removed` tag
    on a draft's removal), which goes to the audit trail (below), never to these logs.
- **Audit trail** (`app.audit_record`, `mailroom.audit`): one record per message a write acted
  on or tried to, and per folder created, renamed, deleted or marked read, refused attempts
  included: the item's fields (account, folder, UID, `Message-ID`, sender and recipients in
  clear, the subject's SHA-256, destination, flags, junk, removal, send mode and delivery stage, the
  name and size of each file an agent gave or renamed,
  a deleted folder's confirmed message count, the count a folder's mark read marked, an
  account's match count on a refused bulk call, the rule sets and filters a rename changed) with
  who acted (`user` on the web, `agent` with the MCP `client_id` and `grant_id` or the personal API token's `token_id`, or `filter`, a
  real run, with its `rule_set_id` and `run_id`), the action (`move`, `move_to_account`,
  `archive`, `junk`, `trash`, `flag`, `draft_save`, `draft_remove`, `send`,
  `send_cancel` (a token's cancel of its own held send: the draft's folder and UID), `folder_create`,
  `folder_rename`, `folder_delete`, `mark_folder_read`, `bulk_flag`, `bulk_move`, `bulk_trash`,
  `unsubscribe` (the list's id, the URL's host and the status it answered, never the URL),
  `mute` and `unmute` (the `muted_thread_id`; a mute's folder and UID), `snooze` (the move into
  Snoozed) and `wake` (marked unread and the move back, or `not_found` with the `snooze_id` for a
  message gone from Snoozed), `vacation_reply` (the reply's send, or why the message got none,
  with its folder, UID and From address),
  or the filter's action as its report names it) and the result. A move to another account is
  recorded in both: the original's record names the `destination_account`, the copy's (in the
  target account) its `source_account` and `source_folder`. A run records each message its report
  lists (the folder as its segments joined by `/`, UID, the sender's address, the date, the
  subject's hash, the filter's id and name), one more record counting those past the report's
  200, and, when the run failed partway, one `run_failed` record naming the filter it failed on,
  which may have acted on some messages; a filter's move to another account is recorded per
  message as it went, refusals included (one refused on its own with its `reason`), and per copy
  in the target account. Never a subject or
  a body. The web's draft saves are
  not recorded (they are the user typing; an agent's are); its explicit deletes and every send
  are. A record is written by the thread that did the work, as the work reports it (an organize
  call's items once its groups are done, so a call cut at its deadline still records what it
  changed), in a transaction of its own; a failure is logged as `audit_write_failed` (counts and
  the error's type) and never changes the write's answer. Records outlive a removed account
  and go with their user; those older than the instance's `audit_retention_days` (*Sign-in*,
  *Instance settings*; 90 days unless set, where it used to be 365) are purged as new ones are
  written, at most once an hour in each process, in a transaction
  apart (`audit_purge_failed` when it fails). `GET /api/v1/me/audit` (mounted with the mail
  layer or filters; `Cache-Control: private, no-store`) lists the user's own, newest first,
  `limit` (1-100, default 50) at a time with an opaque `cursor` (400 `invalid_cursor` for any
  other), filtered by `account_id`, `action`, `actor`, `result` (`failed`: every result but
  `ok`, refusals included) and app, revoked since or not: `grant_id` (one connected app), `client_id`
  (a client, whichever grants it came through; the only one for an external issuer's) or
  `token_id` (a personal API token). What an app set off but did not do itself is not under it: a
  rule set's run it started (the filter's) and a snooze's `wake` (an agent's, with no app named).
  Each carries the connected app's or API token's name and the rule set's while they
  exist; the web's *Activity* page (`/activity`, with mail, MCP or filters) reads it, its
  filters in the query string (`account`, `actor`, `action`, `result`, and `app`: `grant:`, `token:`
  or `client:` with the id). MCP's
  `audit_list` lists the same ([`mcp.md`](mcp.md)). `GET /api/v1/me/export/audit.csv` (the same
  mount, caches and reach; a token needs `audit_read`) downloads it as CSV, newest first: the
  list's fields (so never a subject, only its hash), the account's login while there is one, `at` in
  UTC and `detail` as JSON, of the days `since` to `until` in the user's time zone, both included
  (400 `invalid_range`). Streamed by keyset pages of 500, each a transaction of its own, so a slow
  download holds neither memory nor a connection. Text a spreadsheet would run as a formula (an
  app names itself) has an apostrophe before it, also where one splitting on `;`, a tab or a line
  break would start a cell; `detail` escapes its `;` as `\u003b`. Each export, this and the rule sets'
  ([`filters.md`](filters.md#export)), logs `data_exported` with `user_id`, `token_id` and
  `what`, nothing of its content.

### Mail index sync

What the index is: [`mail-client.md`](mail-client.md) — *Index*. The worker ([Filters](#filters))
keeps it, with `mail`:

- `dispatch_index`, a periodic task every 5 minutes, queues `sync_account(user_id, account_id)`
  for every account of every user (`queueing_lock` `index_account:<user id>:<account id>`). That
  job lists the account's folders (no STATUS), gives each one first seen its row and deletes the
  rows of folders no longer listed (a rename is a folder gone and one first seen), then queues
  `sync_folder(user_id, account_id, folder_id)` (`queueing_lock` `index_folder:<folder id>`) for
  each folder on the index that is not `ready` or was last synced 5 minutes ago (4 minutes early:
  a folder's job starts behind the account's listing and its other folders' jobs, yet is due at
  the next turn). Both take the mailbox's `lock`, so one folder of an account syncs at a time,
  between its rule set runs.
- A folder's job surveys it under EXAMINE (UIDVALIDITY, UIDNEXT, HIGHESTMODSEQ and the UIDs since
  the age limit's day, SINCE), deletes the rows whose UIDs it no longer lists (expunged, or older
  than the limit), and fetches the UIDs the index lacks newest first, 500 a batch, half a second
  between batches, each batch committed (the folder's counts with it, the first sync's progress).
  A batch that fails other than on the mailbox is fetched again one message at a time; a message
  that still fails is skipped (its UID and the exception's type logged), stays readable live, and
  holds up no other. It fetches at most 20 batches, then queues the next job behind every other
  one, so one large mailbox takes turns with the others.
- The job that fetched all that was missing compares the flags: those changed since the stored
  HIGHESTMODSEQ where the folder has mod-sequences (CONDSTORE `CHANGEDSINCE`), else every
  message's (`UID FETCH 1:* (FLAGS)`); on Gmail each message's labels with them (`X-GM-LABELS`,
  whose changes Gmail counts as mod-sequence changes), so a message archived or labelled elsewhere
  leaves or joins its views. It then stores UIDNEXT, HIGHESTMODSEQ and the age limit's day, and
  marks the folder `ready` with `last_synced_at`. A UIDVALIDITY that changed drops the folder's
  rows and builds them again (`syncing`).
- Each write locks the folder's row and stops if the user switched it off meanwhile. A mailbox
  failure marks the folder `failed` with its code (or the app's `password_missing` /
  `secrets_key_missing`), anything unexpected with `failed`, its exception's type logged; the
  next turn tries again. The sync never changes mail: every read is under EXAMINE. On Gmail only
  All Mail has a row, so only it is synced.
- The API queues `sync_folder` too, in its own transaction, for a folder one of its writes left
  unsure or that a send or a draft wrote to, and the worker for the folders a held send wrote to
  ([`mail-client.md`](mail-client.md) — *Index*, the app's own writes).
- Each commit of a job that changed the folder's rows (removed, flags, a batch stored) or its
  state (`syncing`, `ready`, `failed`) calls `pg_notify` on `mailroom_index` in that transaction,
  so the API's live updates hear of it once it commits.

### Filters

What they do and the route list: [`filters.md`](filters.md). How:

- **Storage** (schema `app`): `rule_set` (by `user_id` and the account's id in schema `core`),
  `filter` (`position` unique per rule set, deferred so a reorder swaps freely; `spec` as
  the filter engine's `load_filter(spec).to_spec()` gives it back), `filter_run`,
  `filter_match_day` (a filter's real-run matches per UTC day, added to by the worker as a run
  finishes), `muted_thread` (by `user_id` and account id; its Message-IDs as `jsonb`, 200 at
  most, and Gmail's thread id), and `rule_set.kind` (`user`, or the account's one hidden `mute`
  rule set, a partial unique index), `rule_set.disabled_reason` (why the app turned it off),
  `transfer_ledger` (the copies a filter's move to another account made, and the messages a
  snooze brought back, keyed by user, account, folder, UIDVALIDITY and UID: the loop guard; 90 days, a folder's entries dropped when its
  UIDVALIDITY changes). Removing an account removes its rule sets, muted threads and ledger entries,
  and the runs on it of the rule sets for all accounts, and turns off the user's other rule sets
  moving to it; a rule set's removal removes its filters and runs; removing a user removes all of
  theirs, with their accounts. Each rule set keeps its newest 100 runs, and every run still queued
  or in progress, and its filters' match days of the last 30, pruned as runs are. A report is stored
  without NUL characters (`jsonb` refuses them; a header may decode to one).
- **The folder picker's** `GET /accounts/{id}/folders` lists through the mail service, as MCP's
  `folders_list` does: the selectable folders, each split on its own delimiter, without counts.
- **Refusals** are 400 `{"detail": {"code", "message"}}`: `invalid_spec` (the filter engine's
  `SpecError` text, or the JSON Schema's or the regular expression's complaint, compiled with
  `regex` as the filter engine matches it), `invalid_rule_set` (an `interval_minutes` exactly when
  `scheduled`), `invalid_order` (not every filter once), `too_many_filters` (a rule set holds 100
  at most), `run_filters_off`, `rule_set_disabled`; 409 `run_pending`.
- **Regular expressions** are matched by the filter engine with the `regex` package, each match bounded
  by its match timeout (`DEFAULT_MATCH_TIMEOUT`, 1 s per message, process-wide): a pattern that
  backtracks past it raises `MatchTimeoutError`, which fails the run with `match_timeout`.
- **Dry run** (`POST /rule-sets/{id}/dry-run`), synchronous: the enabled filters, wrapped for
  the rule set's marker, run through the filter engine's `Folder.run(dry_run=True, newest=500)`
  (read-only select, every search bounded to the folder's newest 500 messages) on the user's
  connections from the API's mail pool, under its gates and a 30 s deadline (`MailPool.call`):
  past it, the user's connections to the account in use are cut and the answer is 504
  `dry_run_timeout`. It holds one of the account's two API connections meanwhile. A pattern out of
  time is 400 `match_timeout`. The folder path is joined with the delimiter the server reports.
  Mailbox failures answer by `mail_status`: 502, 504 `timeout`, 429 `busy`, 404
  `mailbox_missing` for a folder that is not there, 409 `auth_failed` / `password_missing`; 503
  `secrets_key_missing`; a line break in a search value is 400 `invalid_argument`. A dry run
  executes no action, so a move's refusals (a missing folder, Drafts or Sent, no Trash) show only
  in a real run. Any other failure is 500
  `run_failed`, its exception's type name only. Either way the run joins the history. One dry run
  per user at a time, across API processes: a session advisory lock on a connection of its own,
  taken with `pg_try_advisory_lock`; a second one meanwhile is 429 `dry_run_busy` and not
  recorded.
- **Queueing a run** — Run now (`POST /rule-sets/{id}/run`, 202), the scheduled dispatcher, new
  mail — inserts a queued `filter_run` (no `started_at`) and, in the same transaction, the
  procrastinate job `run_rule_set(run_id)` with `lock` `mailbox:<user id>:<account id>` (one job
  per mailbox at a time) and `queueing_lock` `rule_set:<id>` (one waiting run per rule set: a
  trigger meeting a waiting run adds nothing, as that run will see the same mail).
- **The worker** (`mailroom/worker/`) runs nothing for an account whose Run filters is off, nor for
  a disabled rule set:
  - `run_rule_set` checks both again when it starts (else the run's error is `run_filters_off` or
    `rule_set_disabled`), commits `started_at`, runs the enabled filters (wrapped for the marker)
    through the filter engine's `Folder.run_into`, on the user's connections leased from the
    worker's mail pool, with no transaction open, and records `finished_at`, the report and the
    error (the filters finished so far kept). A mailbox failure is `<code>: <message>`,
    mailroom-core's code and fixed message. A pattern out of the filter engine's match timeout is
    `match_timeout`, logged by its exception's type only (its text quotes the pattern); anything
    unexpected is `run_failed`, its exception's type name only. A move that copied messages but
    could not remove them (no MOVE, the removal refused) is `copied_not_removed`, and the run
    turns its rule set off (`enabled` false, its `disabled_reason` set, logged as
    `rule_set_turned_off`), since every trigger would copy them again; the engine's searches also
    skip messages marked `\Deleted`. So does a move to another account that left a message in
    both (`in_both_accounts`). Such a move's target is the run's `RunTargets` (checked again, the
    user's move cap), its copies written to `transfer_ledger` before their originals go, on the
    run's session, which commits each step; every run passes the engine the ledger's UIDs of its
    folder to skip, read again before each filter.
    A run removed meanwhile (with its rule set or account) is left alone. A run whose job is
    neither waiting nor running when the worker starts (its worker stopped during it) is closed
    with the error `interrupted`.
  - `dispatch_scheduled`, a procrastinate periodic task every minute, queues each runnable
    `scheduled` rule set whose last scheduled run was queued at least `interval_minutes` ago
    (30 s early, for the minute's start), or never.
  - The **IDLE supervisor**, a thread of the worker process, keeps one mailroom-core `IdleWatch`
    per (user, account, folder) it wants: with `filters` each folder of the runnable `new_mail`
    rule sets; with `mail` the index's, at most 2 per account: INBOX when it is on the index (on
    Gmail, where All Mail is the account's only row, All Mail) and the indexed folder a browser
    viewed last (`app.mail_view_interest`: user, account, folder, `expires_at`, written by the
    API; a Gmail label is All Mail's, and a folder off the index has nothing to keep fresh). A
    folder both want has one watch doing both. Watches run on mailroom-core's connections
    (implicit TLS or STARTTLS as the account says) from the worker's own mail pool (`Mailboxes`:
    one connection per account, 15 s to connect, 120 s per reply, 60 s to wait for the account's
    connection; no gates, no deadline).
    A rule set's folder is its segments joined with the delimiter the server reports, looked up
    on the pool's connection under a lease held only for that and for building the watch, so the
    pool logs that connection out once unused; the watch itself idles on a connection of its own,
    outside the pool, selected read-only. The supervisor learns each account's delimiter from its
    watches, so a rule set's folder of several segments shares the index's watch from then on
    (renamed, not restarted). Every 30 s, and at once on each notification on Postgres's
    `mailroom_interest` channel (the API announces a viewing interest there; the supervisor
    LISTENs on a psycopg connection of its own, opened again 30 s after it is lost), the
    supervisor deletes the expired interests and starts and stops watches to match the rule sets,
    Run filters and the index, restarts one whose account's IMAP server (host, port, TLS mode),
    login or password changed, and starts again one that died (its first connection failed, or
    the server refused the login, has no such folder or cannot IDLE: mailroom-core's code and
    fixed message): at once, then after 30 s, doubling up to 30 min while it keeps failing
    without reaching its folder; each new reason is logged once. A watch that loses its
    connection reconnects by itself, backing off from 5 s to 5 min. New mail (an EXISTS), and
    every (re)connection of a watch (for what came while unwatched), queues the folder's
    `new_mail` runs; any change (EXISTS, any FETCH, of flags or only of a MODSEQ or Gmail
    labels, EXPUNGE) and every (re)connection queues the indexed folder's `sync_folder` (one
    waiting per folder: its `queueing_lock`). The IDLE connection itself never acts. Filter edits
    need no restart: each run loads them, and a watched folder's reactions follow the rule sets
    and the index without one. The index's other folders keep the 5-minute resync.
  - The **mail index's sync** runs in this worker too, with `mail` and without filters:
    [Mail index sync](#mail-index-sync).
  - **Held sends** run here too, with `mail`: `send_draft(scheduled_id)`, deferred with
    `schedule_at` in the transaction that writes its `app.scheduled_send` row, without the
    mailbox's `lock` (waiting for its time, it would hold back every later job of the mailbox; a
    send goes alongside them, as the API's does), sends the draft through mailroom-core's send
    gate on the user's connections from the worker's pool, its SMTP sender built as the API
    builds it
    ([`mail-client.md`](mail-client.md) — *Send state machine*). Logged as `mail_scheduled_send`
    (with `account_id`, `scheduled_id` and `outcome`: `ok`, `gone` or the row's code, `internal`
    for an unexpected error, logged as `mail_scheduled_send_broke` with its type); the API
    logs `mail_send_scheduled` and `mail_send_unscheduled`.
  - **Snoozes** come back here too, with `mail`: `wake_snooze(snooze_id)`, deferred with
    `schedule_at` in the transaction that writes its `app.snooze` row, without the mailbox's
    `lock` (as `send_draft`), moves the message back on the user's connections from the worker's pool
    ([`mail-client.md`](mail-client.md#snooze)); a wake that failed is deferred again 15 minutes
    later. Logged as `mail_snooze_woken` (with `account_id`, `snooze_id` and `outcome`: `ok`,
    `gone`, `missing` or the code); the API logs `mail_snooze_held` and `mail_snooze_woken_now`.
  - **Vacation replies** go from here, with `mail`: an index sync's new rows in INBOX queue
    `vacation_reply(user_id, account_id, ref)` in the transaction storing them, one waiting per
    message, with the mailbox's `lock`, while the account's reply is on; the job checks the
    message's header fields and sends the reply through mailroom-core's send gate as the user
    ([`mail-client.md`](mail-client.md#vacation-reply)). Logged as `mail_vacation_reply` (with
    `account_id` and `outcome`: `ok`, `off`, `gone`, or why it got none); nothing of the message.
  - Jobs run 4 at a time (synchronous tasks, each on a thread); besides being notified of new jobs,
    the worker looks for them every second, so a run held back by its mailbox's lock starts within
    a second of the lock freeing. One worker process (below): a second would watch every folder
    twice, so it exits.
  - SIGTERM or SIGINT: procrastinate gives the jobs in progress 60 s, then the watches are stopped
    (an idling watch within about a second, its logout within 5 s; the supervisor waits 10 s at
    most for them all, and one still connecting ends on its own and acts on nothing), then the
    worker's mail pool is logged out.
- **A run's `result`** is the filter engine's folder report, each filter's entry with its `filter_id` and
  the spec as stored; a dry run's adds `limited_to_newest` (null when the whole folder was looked
  at). A real run keeps the first 200 matched messages per filter, with `matched_total` when there
  were more; when it fails part way, `result` holds the filters finished before the failure and
  `error` repeats the failure. `error` is `<code>: <message>`.
- **Logs:** `rule_set_added`, `rule_set_changed`, `rule_set_removed`, `filter_added`,
  `filter_changed`, `filter_removed`, `filters_reordered`, `dry_run` (with `outcome`),
  `run_requested` and `run_filters_changed` (with `account_id`); from the worker
  `mail_index_synced` (when a job changed anything: with `account_id`, `folder_id`, `rebuilt`,
  `fetched`, `removed`, `flags_changed` and `more`), `mail_index_sync_failed` (with `error`, the code),
  `mail_index_listing_failed` and `mail_index_folders_gone` (never a folder's path), `run_queued` and
  `rule_set_run` (with `account_id`, `trigger` and `outcome`: `ok`, the error's code, or `gone`) and
  `match_count_failed`, `thread_join_failed` and `ledger_write_failed` (with `error`, the
  exception's type), `rule_set_turned_off` (with `reason`, its code);
  `thread_muted` and `thread_unmuted` (with `account_id` and `muted_thread_id`, never an id of
  the message); with `user_id`
  and `rule_set_id`, `filter_id` or `run_id`, and from the API `via` as for accounts; never a spec
  or a message.

## Data persistence & schema migrations

- All app tables live in PostgreSQL schema **`app`** (`Base.metadata` schema). Alembic's version
  table is **`app.alembic_version`**, so mailroom-core's history (schema `core`) can share the
  database without either seeing the other's. `alembic/env.py` creates the schema before Alembic
  writes its version table; autogenerate only looks at `app`.
- **procrastinate**'s tables live in schema **`procrastinate`**, out of autogenerate. A revision
  applies procrastinate's own `schema.sql`, frozen beside it (`alembic/versions/sql/`); a later
  procrastinate release, whose minor `pyproject.toml` bounds, comes with a revision applying its
  migration files. The worker's connections put that schema on their `search_path`.
- mailroom-core's tables live in schema **`core`**, under its own Alembic history and version
  table (`core.alembic_version`). Everything that migrates (API startup, `mise run migrate`,
  `reset-db`, the test databases, `check-migrations`) runs mailroom-core's `migrate` first, then
  the app's history, so app revisions may rely on `core`.
- In production the database and role are **`mailroom`**. The mail index's revision creates the
  `pg_trgm` extension (trusted: the role needs `CREATE` on the database) in schema `app` unless
  the database has it already; its downgrade drops it only from `app`.
- Revisions are **timestamped** (`YYYYMMDDHHMM`, file `<rev>_<slug>.py`) and form one linear chain.
- Every revision **downgrades**: `mise run check-migrations` runs upgrade → downgrade base →
  upgrade on a scratch database, locally and in CI.
- The API migrates at startup (`MAILROOM_RUN_STARTUP_MIGRATIONS=1` in the image and in dev), schema
  `core` first; run a
  single API worker, or migrate externally before scaling. The worker **never** migrates: it waits
  until the database is at its head, then starts.
- **One worker process** at a time: it holds a session advisory lock, and a second one exits. At
  start it marks failed every job still `doing`, which a stopped worker left (its `lock` would hold
  back every later job with the same lock), and queues `send_draft` again for every held send,
  and `wake_snooze` for every snooze, whose job is neither waiting nor running. Stopping, it
  gives running jobs 60 s, then aborts them.
- An unmerged branch's migrations are edited in place, never followed by a fixing migration;
  merged revisions are immutable.

## Structured logging

Logs go to stderr: JSON lines in production, one human-readable line per record in development
(`MAILROOM_LOG_FORMAT`, `MAILROOM_LOG_LEVEL`). mailroom-core's records (`mailroom_core.*`, the
filter engine's from WARNING) and procrastinate's take the same handler, with their fields (account,
folder, mapped code). Every HTTP request gets an `X-Request-ID` (accepted from the client when sane)
carried on each record. Never log message subjects, bodies or secrets in
clear (hash them, as mailroom-mcp's audit does).

## Automated testing

- **Backend:** pytest against real PostgreSQL (`TEST_DATABASE_URL`, loopback and `_test` only unless
  `MAILROOM_INTEGRATION_TESTS_ALLOW_ANY_DATABASE=1`). The coordinator migrates `mailroom_test`; each
  xdist worker clones it. A test that writes goes through the `db_session` fixture: one outer
  transaction rolled back afterwards, the code under test committing to savepoints; `api_client`
  binds the routes to it. The mail tests (REST, MCP, dry runs, the worker's runs) need a Dovecot
  and a Mailpit and skip without them: `mise run test-backend-mail` starts throwaway ones
  (`scripts/test-mail.sh`), whose certificates they trust; against the dev mail backends
  (`MAILROOM_MAIL_CA_FILE` set) they verify TLS as production does. CI runs them on the same
  detached steps as mailroom-core's.
- **mailroom-core** runs its own pytest from `backend/core`. Its mail tests need a Dovecot and two
  Mailpits (implicit TLS, STARTTLS) and skip without them: `mise run test-core-mail` starts
  throwaway ones (`scripts/test-mail.sh`), apart from the dev mail backends; CI runs them as
  detached steps. Both test Dovecots take `dev/dovecot-test.conf`'s overrides (the special-use
  folders, a login limit above what the parallel workers open).
- **Web:** Vitest; `*.node.test.ts(x)` in bare Node, everything else in jsdom with
  `src/test/setup.ts`, which stands `src/test/fake-virtualizer.ts` in for TanStack Virtual (jsdom
  lays nothing out): every row is rendered, and `scrollListToEnd` reads the next page. It also
  gives ranges and the document the empty measures ProseMirror asks for. Test by accessible role
  and label.
- **Editions:** the shared test apps pass `features=ALL_FEATURES`, so a developer's
  `MAILROOM_FEATURES` does not change the suite; `tests/test_editions.py` builds the others.
- **OIDC** tests run against `mailroom.dev.fake_idp` (also `mise run dev-idp`) through
  `httpx.MockTransport`, so they never reach the network.
- No end-to-end browser test framework; browser checks by agents use chrome-devtools-mcp
  (`AGENTS.md` — *Manual / browser-based testing*), not CI.

## Accessibility

WCAG 2.2 AA is the aim; what is enforced:

- **Keyboard:** every control is reached and worked by the keyboard, with the patterns ARIA gives
  its role (menus, tabs, the tree, the splitter); a dialog or the drawer takes focus and gives it
  back. `eslint-plugin-jsx-a11y` runs in `check-web`.
- **Focus:** whatever the keyboard focuses shows a ring in the `ring` token (`:focus-visible` in
  `web/src/index.css`, or the element's own `focus-visible:ring`); nothing hides it without one.
- **Contrast:** text pairs 4.5:1 and the ring, the star mark and other non-text marks 3:1 against
  what they sit on, every theme and accent, in `web/src/theme/contrast.node.test.ts`.
- **axe in `test-web`:** `expectNoAxeViolations` (`web/src/test/axe.ts`, axe-core) runs on the app
  shell (signed out, signed in, its menu), Mail (the list, a text and an HTML message, the phone
  layout with its drawer, the composer with its toolbar and an attachment), Home, the Profile,
  Accounts with the add form, Filters, a rule set, the filter editor, Activity and Users. Any
  finding fails the test. Contrast is left to the test above, since jsdom
  loads no stylesheet.
- **Reduced motion:** with `prefers-reduced-motion`, transitions and animations stop (the drawer
  and swipe, smooth scrolling on the Profile).

## Internationalisation

English is the source language; Italian is kept in parity (`mise run check-web` runs `i18n:check`).
Locales live in `web/src/locales/<lng>/<namespace>.json`. Shared strings are in `common`; a feature
whose screens load lazily keeps its own namespace (`filters`), added when they load and falling
back to `common`, so its strings stay out of the first load.

## CI/CD and releases

[`ci-cd.md`](ci-cd.md).
