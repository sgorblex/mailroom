# MCP server

Mailroom's own MCP server, `mailroom-app`, at `/mcp` in the API process, with its OAuth
(a built-in authorization server, or an external issuer such as Authentik or Keycloak) and web
SSO. It exposes what a model needs to work on a user's filters: accounts, folders, rule sets,
dry runs and runs, and, group by group, the mail tools: each mail group turns on (status
`stable`) once the client does what it does; `mail_read` comes with slice A of the
[mail client](mail-client.md). Standalone mailroom-mcp keeps serving the rest meanwhile
([`../CONTEXT.md`](../CONTEXT.md)). Product scope:
[`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *MCP server*.

## Tools

Tools are registered in [tool groups](../GLOSSARY.md). `MAILROOM_MCP_TOOL_GROUPS` chooses which
groups are registered: by default the `stable` ones; a `preview` group only when named; a
`planned` group never (an unknown or `planned` name is skipped with a warning in the log). Which
tools a client gets is instance config, not OAuth scope.

| Group (status, default) | Tool | Annotations |
| --- | --- | --- |
| `filters_read` (stable, on) | `accounts_list` (id, login, `has_password`, `run_filters`), `folders_list`, `filter_schema`, `rule_sets_list`, `rule_set_get` (with its filters), `runs_list` | readOnly, idempotent |
| `filters_write` (stable, on) | `rule_set_create`, `rule_set_update`, `filter_add`, `filter_update`, `filters_reorder` | none |
| | `rule_set_disable` | idempotent |
| | `rule_set_delete`, `filter_delete` | destructive |
| `filters_run` (stable, on) | `dry_run` | readOnly, openWorld |
| | `rule_set_enable`, `run_now`, `account_run_filters(account_id, on)` | destructive |
| `mail_read` (planned until slice A ships, then stable, on) | `messages_list`, `messages_search`, `message_read`, `thread_read`, `attachment_read` | readOnly, idempotent, openWorld |
| `mail_organize`, `mail_draft`, `mail_send` | placeholders | planned, off |

A destructive hint makes clients such as claude.ai ask the user before each call.

### Safety rules

- **A rule set created over MCP starts disabled**, whatever the input says.
- **Write tools refuse an enabled rule set** ("disable it first"): `rule_set_update`,
  `rule_set_delete`, the filter tools (`filter_delete` too) and `filters_reorder` act only on a
  disabled one. The path is disable → edit → dry run → enable.
- **Turning things on is a separate, destructive call**: `rule_set_enable` (which says so when the
  account's *Run filters* is off), `run_now` (with the same guards as *Run now* in the web app) and
  `account_run_filters`, the account's [Run filters](../GLOSSARY.md) switch.
- `dry_run` changes nothing and shares the user's one-dry-run-at-a-time slot with the web app.
- Everything else follows [`filters.md`](filters.md): the same checks, limits and ownership as the
  REST API, through the same service layer. A user only ever sees and changes their own data.

### Tool contract

- **Inputs** are strict (unknown fields refused, no type coercion) with the REST API's bounds; a
  filter `spec` is checked as the API checks it.
- **Errors:** a refusal returns a tool error with its fixed message; anything unexpected returns
  "Internal error", the details only in the log.
- **Mail is untrusted.** Strings that come from mail (a run's `from` and `subject`) are wrapped in
  an `<untrusted_email nonce="…">` envelope whose closing tag carries a random nonce. The tag
  name inside is rewritten, also when it only shows after NFKC folding (fullwidth letters,
  invisible characters between them), even next to a plain one. The server instructions tell the
  model that the content is data, not instructions.
- **Tools are async** and run database and IMAP work off the event loop, so a slow mail server
  never stalls the API.
- **Two calls per user at a time:** a user's third concurrent tool call (in one API process) is
  refused at once as busy ("wait for one, then retry"), so one user cannot take every tool thread.
  `dry_run` and `folders_list` stop after 30 s of mailbox work and answer a timeout refusal.
- **Audit:** each call logs `mcp_tool_called` with the user, client, grant, tool, outcome and
  duration, and the argument *keys* only, never their values. Changes made through the service
  layer log as they do from the web app, with `via="mcp"`.

## Authentication

`MAILROOM_MCP_AUTH` picks the mode. `/mcp` takes a bearer token only, never the session cookie;
without a valid one it answers 401 with `WWW-Authenticate` pointing at the protected-resource
metadata (RFC 9728). The token's subject is a Mailroom [user](../GLOSSARY.md).

| Mode | Who issues tokens | |
| --- | --- | --- |
| `builtin` (default) | Mailroom's own authorization server | Users sign in with their Mailroom password or SSO, then approve the client. |
| `external` | An OIDC provider (Authentik, Keycloak, …) | Mailroom verifies its JWTs against the issuer's JWKS. |
| `off` | — | `/mcp` is not served. |

Without `MAILROOM_PUBLIC_URL` the MCP server is off whatever the mode, with a warning in the log.

### Built-in authorization server

OAuth 2.1, self-contained in its own module (it depends on the database and a small user
interface, not on the app's routes), so it can move to a shared package later.

- **Clients are public**; PKCE with S256 is required. One scope, `mailroom`.
- **Registration:** dynamic (DCR) at `/oauth/register`, throttled per client and per IPv6 /48,
  with a cap on the number of clients (a full register evicts its oldest never-used client; a
  client never used is forgotten after a day), or a **client ID metadata document** (CIMD) whose
  URL is the `client_id`: its host must be in `MAILROOM_OAUTH_CIMD_HOSTS`, the fetch follows no
  redirects, times out after 5 s and reads at most 64 KB, the document's `client_id` must equal
  its URL, and it is cached for 24 hours. When fetching it again fails in transit (timeout, error,
  a status other than 200), the cached copy keeps serving for up to 7 days after its last good
  fetch; a document fetched and found invalid stops the client at once. A failed fetch is not
  repeated for 5 minutes, and fetches count against the requesting client's limit.
- **`/oauth/authorize`** is throttled per client and refuses a `state` over 1024 characters.
  Every redirect back to the client, errors included, carries `iss` (RFC 9207).
- **Redirect URIs** must be on the allowlist: claude.ai's and claude.com's `auth_callback`,
  loopback on any port, and `MAILROOM_OAUTH_EXTRA_REDIRECT_URIS`.
- **`resource`** must equal `{MAILROOM_PUBLIC_URL}/mcp` at `/oauth/authorize`; a token request
  that names another is refused with `invalid_target`.
- **Consent** is a web app screen. `/oauth/authorize` stores the request (10 minutes) and
  redirects to `/connect?request=<id>`; the screen signs the user in if needed (password or SSO)
  and shows the client and what it gets, to approve or deny. (The session cookie is
  `SameSite=Strict`, so it does not arrive on the cross-site navigation from the client; the
  screen's same-origin requests do carry it.) The screen names the client as it calls itself
  (and, for a metadata document, the host serving it) and the host the browser goes back to. The
  first user to open a request claims it; nobody else can see or decide it.
- **Lifetimes:** an authorization code is single use and lasts 10 minutes; an access token 1 hour;
  a refresh token 30 days, rotated on every use. Presenting a used code again, or a spent refresh
  token, revokes the whole grant and its tokens, and so does revoking either token.
- **Tokens are opaque** and the database keeps only their SHA-256, so revoking takes effect at
  once and there is no signing key to manage.
- Each approval is a [connected app](../GLOSSARY.md): the user sees and revokes them on their
  *Profile* page (name, when connected, when last used: a token exchange or a call on `/mcp`,
  recorded at most once a minute); revoking ends the grant's tokens at once,
  so the app's next call is a 401. Removing a user removes their grants.

### External issuer

`MAILROOM_MCP_AUTH=external`. The built-in authorization server is not mounted (`/oauth/*` and its
metadata answer 404), and nothing is stored about the client.

- Tokens are JWTs signed with RS256, ES256, PS256 or EdDSA; **HS\* is always refused** (Authentik
  falls back to HS256 without a signing key).
- Checked: `iss` (`MAILROOM_MCP_OIDC_ISSUER`, by default `MAILROOM_OIDC_ISSUER`, as its discovery
  document states it), `aud` (one of `MAILROOM_MCP_OIDC_AUDIENCES`; Authentik puts the
  `client_id` there), `sub`, `exp` and `iat` with 60 s of leeway, and
  `MAILROOM_MCP_OIDC_REQUIRED_SCOPE` when set (in `scope`, or a `scp` list).
- **ID tokens are refused.** Authentik's ID tokens share the access tokens' `aud`, so a token
  that carries a `nonce` claim is refused, and so is one whose `typ` header is present and is
  neither `JWT` nor `at+jwt` (case-insensitive, `application/` optional; Authentik's access tokens
  say `JWT`). An ID token minted without a nonce cannot be told apart this way.
- `aud` is what binds a token to Mailroom: the provider need not honour the `resource`
  parameter, so the token's resource indicator is not checked in this mode.
- The signing keys come from the issuer's JWKS, fetched and cached as for SSO ([Security: Single
  sign-on](nonfunctional-requirements.md#single-sign-on)).
- The user is found through a [linked sign-in](../GLOSSARY.md) for `(iss, sub)`; a `sub` linked to
  no user gets a 401. Nobody is created. The audit line's client is the token's `azp` (or
  `client_id`).
- Without an issuer or an audience, the MCP server is off, with a warning in the log.
- The protected-resource metadata names the external issuer in `authorization_servers` exactly
  as configured, trailing slash or not (clients compare issuers as strings), and its
  `scopes_supported` is what to ask that issuer for: `openid`, `offline_access` (for a refresh
  token) and `MAILROOM_MCP_OIDC_REQUIRED_SCOPE` when set. Built-in mode advertises `mailroom`. The
  *Connected apps* section says the identity provider manages access (and, when it lists any,
  that those apps were connected before).
- Recommended with Authentik: **one application** for both web SSO and MCP, so a user's linked
  sign-in serves both.

## Web SSO

"Sign in with `MAILROOM_OIDC_NAME`" on the sign-in screen (and on the consent screen), over OIDC,
for users an admin has already created. **Nobody signs up through SSO.**

- Authorization code flow with PKCE and a nonce. The callback is a cross-site GET, so its `state`
  is checked against a database row and against a separate `SameSite=Lax` binding cookie set when
  the flow started (no login CSRF). It then sets `mailroom_session` and redirects to `return_to`,
  which must be a relative path.
- **Linking policy.** By default an identity is linked only from the *Profile* page, while signed
  in; an unlinked identity cannot sign in. With `MAILROOM_OIDC_LINK_CLAIM=preferred_username`,
  the first SSO sign-in links on its own when its `preferred_username` equals an existing user's
  username exactly (no case folding or trimming), and that user has no sign-in linked from this
  issuer yet. Only for a provider whose users cannot choose their usernames. Linking never
  changes an existing link.
- A user lists and unlinks their linked sign-ins on their *Profile* page.

## Endpoints

| Path | What |
| --- | --- |
| `/mcp` | Streamable HTTP (stateless, JSON responses), bearer token only. An exact route: no `/mcp/` redirect. |
| `/.well-known/oauth-protected-resource/mcp` | Protected-resource metadata (RFC 9728). |
| `/.well-known/oauth-authorization-server/oauth` | Built-in AS metadata (RFC 8414); issuer `{MAILROOM_PUBLIC_URL}/oauth`. |
| `/oauth/authorize`, `/oauth/token`, `/oauth/register`, `/oauth/revoke` | Built-in AS. |
| `/connect?request=<id>` | Consent screen (web app). |
| `/api/v1/oauth/requests/{id}` | `GET` the pending request; `POST {"decision": "approve" \| "deny"}` answers `{"redirect_to"}`, the client's redirect URL (session, same origin; 404 for an unknown, expired or another user's request). |
| `/api/v1/oauth/grants` | `GET` `{"auth", "grants"}`: the MCP auth mode and the user's connected apps (client name, created, last used); `DELETE /{id}` revokes one, its tokens with it (404 for another user's). |
| `/api/v1/auth/oidc/config`, `/start`, `/callback` | Web SSO: whether it is on and its label, start, callback. |
| `/api/v1/me/identities` | `GET` the user's linked sign-ins; `DELETE /{id}` unlinks one. |

Allowed hosts for the MCP transport come from `MAILROOM_PUBLIC_URL`, never from the request's
`Host`. The web container's nginx and the Vite dev proxy pass `/mcp`, `/oauth/`,
`/.well-known/oauth-` and `/.well-known/openid-configuration` to the API: a client probing OpenID
discovery for the built-in server gets the API's 404, not the web app.

## Data model

Schema `app`. All state is in the database, so it survives restarts and works with several API
workers; expired rows are swept on writes.

| Table | Columns |
| --- | --- |
| `external_identity` | `id`, `user_id` (cascade), `issuer`, `subject`, `created_at`, `last_used_at`; unique `(issuer, subject)` |
| `sso_login_state` | `state_hash`, `nonce`, `code_verifier`, `intent` (`login` \| `link`), `link_user_id`, `return_to`, `browser_binding_hash`, `client_key`, `expires_at` |
| `oauth_client` | `client_id`, `kind` (`dcr` \| `cimd`), `client_name`, `redirect_uris`, `metadata`, `fetched_at`, `created_at`, `last_used_at` |
| `oauth_authorization_request` | `id_hash`, `client_id`, `params`, `expires_at` |
| `oauth_authorization_code` | `code_hash`, `client_id`, `user_id`, `grant_id`, `redirect_uri`, `redirect_uri_explicit`, `code_challenge`, `resource`, `scopes`, `expires_at`, `used_at` |
| `oauth_grant` | `id`, `user_id` (cascade), `client_id` (cascade), `resource`, `scopes`, `created_at`, `last_used_at` |
| `oauth_token` | `token_hash`, `grant_id` (cascade), `kind` (`access` \| `refresh`), `expires_at`, `used_at`, `created_at` |

## Configuration

| Variable | Meaning |
| --- | --- |
| `MAILROOM_PUBLIC_URL` | The instance's public origin. Required for SSO and the MCP server; unset, both are off. |
| `MAILROOM_MCP_AUTH` | `builtin` (default), `external` or `off`. |
| `MAILROOM_MCP_TOOL_GROUPS` | Registered tool groups; default `filters_read,filters_write,filters_run`. |
| `MAILROOM_OAUTH_EXTRA_REDIRECT_URIS` | Redirect URIs allowed beyond the built-in list. |
| `MAILROOM_OAUTH_CIMD_HOSTS` | Hosts whose client ID metadata documents are fetched; default `claude.ai,claude.com`. |
| `MAILROOM_OIDC_ISSUER`, `MAILROOM_OIDC_CLIENT_ID`, `MAILROOM_OIDC_CLIENT_SECRET` | Web SSO's provider and confidential client; SSO is off without them. |
| `MAILROOM_OIDC_NAME` | The SSO button's label. |
| `MAILROOM_OIDC_LINK_CLAIM` | Unset (link from the profile only) or `preferred_username`; anything else is ignored with a warning. |
| `MAILROOM_MCP_OIDC_ISSUER` | External mode's issuer; defaults to `MAILROOM_OIDC_ISSUER`. |
| `MAILROOM_MCP_OIDC_AUDIENCES` | Accepted `aud` values; required in external mode. |
| `MAILROOM_MCP_OIDC_REQUIRED_SCOPE` | A scope every external token must carry; optional. |

## Open questions

- Whether claude.ai discovers an external issuer through the OIDC or the RFC 8414 metadata path,
  and whether it needs the client credentials entered by hand. Settle before calling external mode
  done.
- Whether a real Authentik access token's header and claims match what the ID-token refusal
  assumes (`typ` `JWT` or absent, no `nonce`). Check with a real token before calling external
  mode done.
- The consent screen's reliance on same-origin requests carrying a `SameSite=Strict` cookie after
  a cross-site navigation needs checking by hand in Firefox and Safari.
