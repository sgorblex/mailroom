# Non-functional requirements

Engineering baseline for Mailroom. Product scope: [`../REQUIREMENTS.md`](../REQUIREMENTS.md).

## Implementation stack (reference)

| Area | Choice |
| --- | --- |
| Backend | Python 3.14+ (dev pin 3.15 via mise), FastAPI, SQLAlchemy 2 (sync) + psycopg 3, Alembic, uv; PyJWT and httpx for OIDC |
| Worker | procrastinate (psycopg 3) on the app's database, `python -m mailroom.worker`; it also watches folders over IMAP IDLE |
| Web | React 19, Vite, TanStack Router (code-based routes), Tailwind 4 + shadcn (`web/components.json`), i18next, Zod at the API boundary, ESLint `strictTypeChecked` (no Prettier) |
| Database | PostgreSQL 18 |
| Tests | pytest (+ xdist, one database per worker cloned from the migrated `mailroom_test`), Vitest (`node` and `jsdom` projects) |
| Containers | Podman, `mailroom-api` and `mailroom-web` images; Quadlet for deploy |
| Component family | mailroom-core, the mail layer, as a uv workspace member in `backend/core` (its own tests run from there: `mise run test-core`); imap-thingy from PyPI; mailroom-mcp, being retired, is not a dependency ([`../CONTEXT.md`](../CONTEXT.md)) |
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
- **No remote content by default.** Remote images are removed from the document and refused by
  its CSP until the user reveals them for that message (`?remote=1`, per message, never
  remembered); tracking pixels therefore never fire on open. What is removed whatever the user
  chooses: [`mail-client.md`](mail-client.md), the HTML route. Links open in a new top-level
  context with `noopener` and `noreferrer`.
- Plain-text rendering is the fallback and the default for the index.

### The app

- **Separate users.** Every account, filter and suggestion belongs to one user, and every query
  that reads them is scoped to the signed-in user. Admins manage users, not their mail. No sharing.
  See *Sign-in* below.
- **One service layer.** What a user may do with their accounts and filters (ownership by user id,
  the checks, the guards) lives in `mailroom.accounts.service`, `mailroom.filters.service` and
  `mailroom.mail.service`, which refuse with a `mailroom.errors.Refusal` (a kind, a `code`, a
  safe `message`). The REST routes only map them (`mailroom.api.refusals.http_refusal`:
  `not_found` 404 with the message alone, `invalid` 400, `conflict` 409, `busy` 429, `mail` by its
  code (`mail_status`: 502 by default), `unavailable` 503, `timeout` 504, `failed` 500), so every
  front end keeps the same rules.
- **Strict CSP on the app** (`web/nginx-default.conf`): `script-src 'self'` with no inline script
  (the dark theme follows `prefers-color-scheme` in CSS for that reason), `frame-ancestors 'none'`,
  `frame-src 'self'` (the HTML mail view), `object-src 'none'`, `base-uri 'none'`, plus `nosniff`
  and `Referrer-Policy: no-referrer`. The `/api/` location gets the last two but not the SPA's CSP:
  the API's HTML mail document sets its own. nginx hides the API's own copies of those two, so
  each is sent once. `style-src` allows `'unsafe-inline'` for UI-library style attributes
  (decided; revisit if that stops being needed).
- The SPA and API are **same-origin** (Vite proxy in dev, nginx in the image), so `/api/` has no
  CORS configuration at all. Only the OAuth metadata, token, register and revoke endpoints answer
  CORS (any origin, no credentials), for browser-based MCP clients.
- **Secrets** (account passwords, tokens) are sealed at rest as mailroom-core seals them and never
  appear in logs, errors or API responses. See *Mail accounts* below.
- **Destructive actions** never expunge: trash moves to `\Trash`, as in mailroom-mcp. The one
  exception is the removal of **a draft Mailroom wrote**, by `UID EXPUNGE` of exactly that UID
  ([`mail-client.md`](mail-client.md), *The own-draft exception*); a draft written elsewhere goes
  to Trash. Filters are outside this rule: a filter's `delete` action removes mail for good
  ([`filters.md`](filters.md)).
- **Sending** passes the send gate only: an `Admitted` token from `admit` is the one thing
  `deliver` takes, and the user's *Send* overrides a send mode except `off`. A send is counted and
  recorded before SMTP; a draft's send is keyed so it is delivered once (an agent's direct send has
  no key), and one whose delivery is uncertain is `unknown`, never retried by itself. Header values built from user input or mail have CR and LF
  stripped, and Bcc never goes in a transmitted header.
- **Learning never acts** ([`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Filter suggestions*).

### MCP server

Specification: [`mcp.md`](mcp.md). Code: `mailroom.mcp`.

- **`/mcp` takes a bearer token only**, never the session cookie, so a browser's ambient
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
- **Logs:** one `mcp_tool_called` per call, with `tool`, `user_id`, `client_id`, `grant_id`,
  `outcome` (`ok`, `refused`, `invalid_arguments`, `unknown_tool`, `failed`), `code`,
  `duration_ms` and `argument_keys`: the arguments' names, never their values. Changes log as from
  the web app, with `via` `mcp`.

### Built-in OAuth authorization server

Code: `mailroom.oauth`, self-contained (SQLAlchemy, the `mcp` SDK's authorize, token and register
handlers, httpx, and the `app.user` table; never the app's routes). Mounted when
`MAILROOM_MCP_AUTH` is `builtin` (the default) and `MAILROOM_PUBLIC_URL` is set. The `mcp` SDK is
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
  The host allowlist is what keeps this fetch from being an SSRF.
- **Consent:** `/oauth/authorize` stores the request (10 minutes) and redirects to
  `/connect?request=<id>`. The first user to open it claims it; nobody else can see or decide it.
  Approving creates a grant (a connected app) and a code; the redirect carries `code`, `state` and
  `iss` (RFC 9207), and so does every error redirect back to the client, the SDK's included. The
  screen's API (`/api/v1/oauth/requests/{id}`) needs the session cookie, its `POST` is refused
  cross-site like any other, and an unknown, expired or another user's request is a 404; the SPA,
  not the API, navigates to the returned redirect URL.
- **Connected apps** (`/api/v1/oauth/grants`): a user lists and revokes only their own grants;
  revoking deletes the grant and its tokens, and grants left from an earlier mode stay revocable.
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
  `oauth_authorization_code_reused`, `oauth_refresh_token_reused`, `oauth_grant_revoked`, with
  `user_id`, `client_id` and `grant_id` where known; never a token.
- **`/mcp`'s verifier** in builtin mode: an access token is looked up by its hash, so a revoked or
  expired one stops working at once; the `AccessToken` names the user and carries `grant_id`.

### Sign-in

- **Users** (`app.user`): a username, stored lowercase (1-64 of `a-z 0-9 . _ -`, starting with a
  letter or digit), an Argon2id password hash (passwords of 12-1024 characters), and an admin flag. `mise run create-user`
  makes one; startup warns while there are none. An unknown username still checks a dummy hash, and
  both failures answer the same 401, so neither timing nor wording tells whether a user exists.
- **Sessions** (`app.user_session`): one per signed-in browser, deleted with their user. The
  cookie `mailroom_session` carries a random token; the database keeps only its SHA-256. An absolute
  lifetime of `MAILROOM_SESSION_TTL_DAYS` (default 14), no sliding renewal; signing out deletes the
  row, and a login sweeps expired ones. Every route but health, readiness, version, login, logout
  (idempotent, so an expired tab can still sign out), `auth/me` (which answers `{"user": …}`) and
  SSO's `auth/oidc/*` depends on `require_user` (`backend/mailroom/api/deps.py`) and answers 401 without a valid
  session.
- **Cookie:** `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` except with
  `MAILROOM_ENV=development` (the dev server is plain HTTP).
- **CSRF:** no token. Same origin plus `SameSite=Strict`, and `CrossSiteRequestMiddleware` refuses
  (403) a state-changing `/api/` request whose `Sec-Fetch-Site` is not `same-origin`, or, without
  that header, whose `Origin` names another host. Add a token only if the API ever serves another
  origin.
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
  logged; a reset that the user must complete themselves is a later option, not a promise.
- **Own password** (`POST /api/v1/auth/password`): needs the current one, checked through the
  login throttle (the same per-client counter as sign-in) so a borrowed session cannot guess it;
  keeps this session and ends the others.
- **A session ending under the user:** any 401 from the API but a refused sign-in signs the web
  app out (`onSessionEnded` in `web/src/api/http.ts`), and the sign-in screen says the session
  has ended.
- **Refusals** about users answer 400 with `{"detail": {"code", "message"}}`; the web app words
  each `code` itself, in the reader's language.
- **Logs:** `login_succeeded` (with `user_id`; `via="sso"` through SSO), `login_failed`,
  `logout`, `user_created`,
  `user_removed`, `password_reset` (with `user_id`, the admin, and `target_user_id`),
  `password_changed`, `password_change_failed`, `sso_refused` and `sso_failed` (with the
  `outcome`), `sso_not_linked`, `sso_identity_linked` (with `user_id` and `via`: `profile` or the
  link claim), `sso_identity_unlinked`; never a password, a token, or a username.

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
  `//host`, `/\host`, a scheme or a control character), else `/`.
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
  the account id), byte-compatible with mailroom-mcp. Without the key (or with a malformed one)
  startup warns, the accounts stay listed, and adding, a new password, a test or a folder listing
  answers 503 `secrets_key_missing`. The app's own `app.account_filter_settings(user_id,
  account_id, run_filters)` holds the Run filters switch; no row is off.
- **API** (`/api/v1/accounts`, the signed-in user's own; another user's id is a 404): list, add
  (`POST`, with the password), edit (`PUT /{id}`, without the id; no password keeps it), remove
  (`DELETE /{id}`, its switch too), `POST /{id}/test` (an IMAP login and logout:
  `{"ok", "code", "message"}`). Responses carry `has_password`, never the password. An account's
  folders and its Run filters switch are the filters' routes ([Filters](#filters)).
- **Checks:** mailroom-core's config models (`core_settings_or_issues`) validate the whole document
  before each save; a refusal is 400 `{"detail": {"code": "invalid_account" | "account_exists",
  "message", "issues": [{"path", "message"}]}}`, `path` inside the account. A save racing another
  is 409 `accounts_changed` or 503 `accounts_busy`.
- **IMAP** goes through imap-thingy, implicit TLS only (`starttls_unsupported` otherwise), verified
  against the system CAs plus `MAILROOM_MAIL_CA_FILE` (the dev mail CA in dev), with a 20 s
  timeout on connecting and on every read. A failure is reported by kind (`login_failed`,
  `unreachable`, `timeout`, `tls_failed`, `imap_error`, `password_missing`) with the server's
  wording, the password masked; a folder listing answers it by `mail_status` (502 by default,
  504 for `timeout`, 409 for `password_missing` / `starttls_unsupported`).
- **Logs:** `account_added`, `account_changed`, `account_removed`, `account_tested` (with
  `outcome`), each with `user_id` and `account_id`, and the changes with `via` (`web`, or `mcp`
  for the MCP server); never a login, a host or a password.
- **Removing a user** removes, in the same transaction, the app's rows about their accounts and
  mailroom-core's (`forget_user`: settings with their history, sealed passwords, send log). While
  a save of their accounts holds them past mailroom-core's lock timeout, nothing is removed: 503
  `user_accounts_busy`.
- **Known risk — outbound connections:** a login test or a folder listing connects to whatever
  host and port the user entered, so any signed-in user can make the server open a TLS connection
  from its network to any `host:port` (internal services included) and learn from the failure kind
  whether something answered. Accepted because only admin-created users sign in;
  revisit before anyone else can.

### Mail client

What it does and the routes: [`mail-client.md`](mail-client.md). How:

- **Connections** go through mailroom-core's `MailConnections` (imapclient; implicit TLS or
  STARTTLS, verified as above), one per user per API process (`mailroom.mail.pool`), up to two
  connections per account, built on first use from the user's settings and sealed passwords. They
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
- **Organize calls** (move, archive, trash, flag) check every ref before connecting: a malformed
  one, or one of an account that is not the user's (404) or cannot log in (`password_missing`
  409, `secrets_key_missing` 503), refuses the batch and nothing is written. A call cut at the
  deadline answers `timeout`: the groups done before the cut stay done, and the ones after it are
  refused, not written (its connections refuse every mailbox once the deadline passed); no write
  is retried. The group running at the cut is cut with its connection: with `MOVE` it is done or
  not; without `MOVE` it can be copied but not removed, so after a 504 a client should reload the
  folders rather than repeat the call.
- **Logs:** `mail_folders`, `mail_listed`, `mail_searched`, `mail_read`, `mail_thread_read` (MCP
  only), `mail_html_read`, `mail_attachment_read`, each with `user_id`, `account_id`, `via`, `outcome` (`ok` or the code)
  and `duration_ms`; never a subject, an address, a folder name or a body. `mail_moved`,
  `mail_archived`, `mail_trashed` and `mail_flagged` carry `user_id`, `via`, `account_ids`,
  `ref_count`, `changed` (messages), `groups`, `failed_groups`, `codes` (the failed groups'),
  `outcome` (`ok`, `partial`, `refused`, `failed`, or the code of a refused call) and
  `duration_ms`: counts and codes only, never a folder, a ref, a `Message-ID` or a sender. Search criteria travel
  in a `POST` body, so access logs (nginx's, the host proxy's) never see them, and the web
  image's nginx logs paths without their query string (a list's `folder`, a search the SPA keeps
  in its URL; [`../deploy.md`](../deploy.md)). A ref is in the path of the message routes, and it
  encodes the account, the folder name and the UID: an access log does see that.
- **Drafts and sends** ([`mail-client.md`](mail-client.md), *Slice C*):
  - **Limits:** the user's `send_per_hour` and `max_recipients`, 50 addresses per field, a body of
    100,000 characters, a subject of 998, 10 new files and the user's `attachment_max_mb` (at most
    25) in total, a 2 MiB original read for quoting. `python-multipart` parses the one multipart
    route (`POST /drafts`); the web image's nginx allows `client_max_body_size 30m` on that exact
    location (repeating its security headers) and the host's proxy needs the same, and the route
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
  - **Logs:** `mail_prefilled`, `mail_draft_saved`, `mail_draft_read`, `mail_draft_found`,
    `mail_draft_deleted`, `mail_send_requested` and `mail_send_finished`, each with `user_id`,
    `account_id`, `via` (`web` or `mcp`), `outcome` (`ok`, the state or the code) and
    `duration_ms`, and a finished send's `send_id`, `state`, `filed`, `recipient_count` and
    `rejected_count`; `mail_send_not_filed` and `mail_send_not_settled` with their `code`. Never an
    address, a subject, a body, a filename, a `Message-ID`, a ref or the server's wording.
    mailroom-core produces an audit item for each draft and send (recipients, and a `removed` tag
    on a draft's removal), which the app does not keep yet ([`../TODO.md`](../TODO.md)): they are
    not logged here either.

### Filters

What they do and the route list: [`filters.md`](filters.md). How:

- **Storage** (schema `app`): `rule_set` (by `user_id` and the account's id in schema `core`),
  `filter` (`position` unique per rule set, deferred so a reorder swaps freely; `spec` as
  imap-thingy's `load_filter(spec).to_spec()` gives it back), `filter_run`. Removing an account
  removes its rule sets, and a rule set its filters and runs; removing a user removes all of
  theirs, with their accounts. Each rule
  set keeps its newest 100 runs, and every run still queued or in progress. A report is stored
  without NUL characters (`jsonb` refuses them; a header may decode to one).
- **The folder picker's** `GET /accounts/{id}/folders` lists through imap-thingy.
- **Refusals** are 400 `{"detail": {"code", "message"}}`: `invalid_spec` (imap-thingy's
  `SpecError` text, or the JSON Schema's or the regular expression's complaint, compiled with
  `regex` as imap-thingy matches it), `invalid_rule_set` (an `interval_minutes` exactly when
  `scheduled`), `invalid_order` (not every filter once), `too_many_filters` (a rule set holds 100
  at most), `run_filters_off`, `rule_set_disabled`; 409 `run_pending`.
- **Regular expressions** are matched by imap-thingy with the `regex` package, each match bounded
  by its match timeout (`DEFAULT_MATCH_TIMEOUT`, 1 s per message, process-wide): a pattern that
  backtracks past it raises `MatchTimeoutError`, which fails the run with `match_timeout`.
- **Dry run** (`POST /rule-sets/{id}/dry-run`), synchronous: the enabled filters, wrapped for
  starred only, run through imap-thingy's `Folder.run(dry_run=True)` (read-only select) on the
  folder's newest 500 messages, for about 30 s (then the connection is closed: 504
  `dry_run_timeout`). A pattern out of time is 400 `match_timeout`. The folder path is joined with
  the delimiter the server reports. Mailbox failures answer as a folder listing does (by
  `mail_status`: 502, 504 `timeout`, 409 `password_missing` / `starttls_unsupported`; 503
  `secrets_key_missing`); any other failure is 500
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
    `rule_set_disabled`), commits `started_at`, runs the enabled filters (wrapped for starred only)
    through imap-thingy's `Folder.run_into` with no transaction open, and records `finished_at`,
    the report and the error (the filters finished so far kept). A pattern out of imap-thingy's
    match timeout is `match_timeout`, logged by its exception's type only (its text quotes the
    pattern); anything unexpected is `run_failed`, its exception's type name only.
    A run removed meanwhile (with its rule set or account) is left alone. A run whose job is
    neither waiting nor running when the worker starts (its worker stopped during it) is closed
    with the error `interrupted`.
  - `dispatch_scheduled`, a procrastinate periodic task every minute, queues each runnable
    `scheduled` rule set whose last scheduled run was queued at least `interval_minutes` ago
    (30 s early, for the minute's start), or never.
  - The **IDLE supervisor**, a thread of the worker process, keeps one imap-thingy `monitor` (on
    its own thread, stoppable) per (user, account, folder) of the runnable `new_mail` rule sets.
    Every 30 s it starts and stops watches to match the rule sets and Run filters, restarts one
    whose account's server, login or password changed, and starts again one that died (its first
    connection failed): at once, then after 30 s, doubling up to 30 min while it keeps failing
    without reaching its folder; each new reason is logged once. New mail, and every (re)connection of a watch
    (for mail that came while unwatched), queues the folder's `new_mail` runs; the IDLE
    connection itself never acts. Filter edits need no restart: each run loads them.
  - Jobs run 4 at a time (synchronous tasks, each on a thread); besides being notified of new jobs,
    the worker looks for them every second, so a run held back by its mailbox's lock starts within
    a second of the lock freeing. One worker process (below): a second would watch every folder
    twice, so it exits.
  - SIGTERM or SIGINT: procrastinate gives the jobs in progress 60 s, then the watches are stopped
    (each within the 20 s IMAP timeout).
- **A run's `result`** is imap-thingy's folder report, each filter's entry with its `filter_id` and
  the spec as stored; a dry run's adds `limited_to_newest` (null when the whole folder was looked
  at). A real run keeps the first 200 matched messages per filter, with `matched_total` when there
  were more; when it fails part way, `result` holds the filters finished before the failure and
  `error` repeats the failure. `error` is `<code>: <message>`.
- **Logs:** `rule_set_added`, `rule_set_changed`, `rule_set_removed`, `filter_added`,
  `filter_changed`, `filter_removed`, `filters_reordered`, `dry_run` (with `outcome`),
  `run_requested`, `run_filters_changed` (with `account_id`); from the worker `run_queued` and
  `rule_set_run` (with `trigger` and `outcome`: `ok`, the error's code, or `gone`); with `user_id`
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
- On the production host the database and role are **`mailroom`**; standalone mailroom-mcp uses **`mailroom_mcp`**.
- Revisions are **timestamped** (`YYYYMMDDHHMM`, file `<rev>_<slug>.py`) and form one linear chain.
- Every revision **downgrades**: `mise run check-migrations` runs upgrade → downgrade base →
  upgrade on a scratch database, locally and in CI.
- The API migrates at startup (`MAILROOM_RUN_STARTUP_MIGRATIONS=1` in the image and in dev), schema
  `core` first; run a
  single API worker, or migrate externally before scaling. The worker **never** migrates: it waits
  until the database is at its head, then starts.
- **One worker process** at a time: it holds a session advisory lock, and a second one exits. At
  start it marks failed every job still `doing`, which a stopped worker left (its `lock` would hold
  back every later job with the same lock). Stopping, it gives running jobs 60 s, then aborts them.
- An unmerged branch's migrations are edited in place, never followed by a fixing migration;
  merged revisions are immutable.

## Structured logging

Logs go to stderr: JSON lines in production, one human-readable line per record in development
(`MAILROOM_LOG_FORMAT`, `MAILROOM_LOG_LEVEL`). Every HTTP request gets an `X-Request-ID` (accepted
from the client when sane) carried on each record. Never log message subjects, bodies or secrets in
clear (hash them, as mailroom-mcp's audit does).

## Automated testing

- **Backend:** pytest against real PostgreSQL (`TEST_DATABASE_URL`, loopback and `_test` only unless
  `MAILROOM_INTEGRATION_TESTS_ALLOW_ANY_DATABASE=1`). The coordinator migrates `mailroom_test`; each
  xdist worker clones it. A test that writes goes through the `db_session` fixture: one outer
  transaction rolled back afterwards, the code under test committing to savepoints; `api_client`
  binds the routes to it.
- **mailroom-core** runs its own pytest from `backend/core`. Its mail tests need a Dovecot and two
  Mailpits (implicit TLS, STARTTLS) and skip without them: `mise run test-core-mail` starts
  throwaway ones (`scripts/test-mail.sh`), apart from the dev mail backends; CI runs them as
  detached steps.
- **Web:** Vitest; `*.node.test.ts(x)` in bare Node, everything else in jsdom with
  `src/test/setup.ts`. Test by accessible role and label.
- **OIDC** tests run against `mailroom.dev.fake_idp` (also `mise run dev-idp`) through
  `httpx.MockTransport`, so they never reach the network.
- No end-to-end browser test framework; browser checks by agents use chrome-devtools-mcp
  (`AGENTS.md` — *Manual / browser-based testing*), not CI.

## Internationalisation

English is the source language; Italian is kept in parity (`mise run check-web` runs `i18n:check`).
Locales live in `web/src/locales/<lng>/<namespace>.json`. Shared strings are in `common`; a feature
whose screens load lazily keeps its own namespace (`filters`), added when they load and falling
back to `common`, so its strings stay out of the first load.

## CI/CD and releases

[`ci-cd.md`](ci-cd.md).
