# Mail client

The web mail client, built in slices. **Slice A (read)** is specified here and never changes the
mailbox: not even `\Seen` is set by opening a message. Triage and compose (slices B and C) extend
this file when they start. Product scope: [`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Mail
client*. Rendering and IMAP come from **mailroom-core** 0.1.1 (`render` and `mail`, connections in
`mail.imap`), over IMAP directly: no index and no live updates yet.

## Slice A: read

- **Folders** per account, with their role and unread/total counts.
- **Message list** of one folder, newest first, 25 at a time with a "load more".
- **Search** over one account or all, with the search fields of mailroom-mcp's `search`.
- **Message view:** headers, the text body (quotes and signatures collapsed), attachments and a
  calendar note; a **standalone HTML view** when the message has an HTML body; attachment download.
- **Remote content** (remote images, `http` or `https`) is blocked and revealed per message
  ([Glossary](../GLOSSARY.md)), never remembered; what is removed either way is in the HTML
  route below.

The screens live under `/mail`: account and folder sidebar with counts, message list, message view
(three panes on desktop, stacked on a phone). The text body is the default; a message whose text
body is empty opens on the HTML view. The HTML view is an
`<iframe src="/api/v1/mail/messages/{ref}/html">` with `sandbox="allow-popups
allow-popups-to-escape-sandbox"`, never `allow-scripts` or `allow-same-origin`. Links open
top-level with `noopener noreferrer`. Security:
[`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *HTML mail*.

## REST contract

Every route is under `/api/v1/mail`, needs a [user session](../GLOSSARY.md), and sees only the
signed-in user's accounts. All are `GET` but search, a `POST` with a JSON body so that search
terms (addresses, subjects, words of a body) are never in an API URL. The SPA does keep a search
in its own URL (`?q=`), so the access logs in front of it, the web image's nginx and the host's
proxy, must log paths without their query string ([`../deploy.md`](../deploy.md)). Mail-derived strings (subject, names,
previews, body) are raw text, never HTML: the client escapes them. Control and invisible
characters are already stripped from headers.

### Refs and cursors

- A **ref** names one message exactly (account, folder, UIDVALIDITY, UID): base64url of the JSON
  array `[1, account, folder, uidValidity, uid]`, byte-compatible with mailroom-mcp's
  (`src/mail/ref.ts`). It is **opaque to the client**: store it, put it in a URL, never build or
  parse it. It is URL-safe, so it goes in the path as is. A ref whose folder changed (UIDVALIDITY)
  answers `stale_reference`; one of an account that is not the user's answers `not_found`, as a
  missing one.
- A **cursor** is equally opaque (mailroom-mcp's `encodeCursor`: base64url of `[1, positions]`):
  pass `next_cursor` back as `cursor`, with the same query. A malformed one is `invalid_argument`.

### Errors

A refusal is `{"detail": {"code": "<code>", "message": "<fixed text>"}}`
([`refusals.py`](../../backend/mailroom/api/refusals.py): FastAPI's `detail` wrapper, as for every
other route). The message is a fixed, secret-free text; server wording never reaches it. A
malformed request (bad query value, wrong type) is FastAPI's 422 with its `detail` list, as
elsewhere.

Mailbox failures carry the 14 codes of mailroom-mcp (`src/imap/errors.ts`):

| Code | Status | Message |
| --- | --- | --- |
| `auth_failed` | 409 | The mail server rejected the login for this account. (The user fixes the password.) |
| `unreachable` | 502 | The mail server for this account could not be reached. |
| `timeout` | 504 | The mail server did not answer in time. |
| `mailbox_missing` | 404 | That folder does not exist on this account. |
| `unsupported` | 502 | The mail server does not support this operation. |
| `busy` | 429 | The account is busy with another request; try again. |
| `stale_reference` | 409 | This message reference is out of date (the folder changed). Search again for a fresh one. |
| `not_found` | 404 | No such message or part. |
| `invalid_argument` | 400 | Invalid argument. (Optionally followed by what is wrong.) |
| `too_large` | 413 | Too large to return. |
| `send_disabled` | 409 | Sending is disabled for this account (send_mode off): no drafts or sends. |
| `rate_limited` | 429 | The hourly send limit is reached; nothing was sent. |
| `recipient_refused` | 502 | The mail server refused the message; nothing was sent. |
| `server_error` | 502 | The mail server reported an error. |

The last three of the send group (`send_disabled`, `rate_limited`, `recipient_refused`) belong to
slice C; slice A never answers them. The app's own codes keep their statuses: `password_missing`
and `starttls_unsupported` 409, `secrets_key_missing` 503, an unknown account `not_found` 404
(coded). A mailbox failure carrying a code not in this table keeps its code, with status 502.

In a list or search page a failing source does not fail the page: it is reported in `failures` by
code, below.

### Types

```jsonc
// Address
{ "name": "Ada Lovelace", "address": "ada@example.org" }   // name "" when absent

// Flags
{ "seen": true, "flagged": false, "answered": false, "draft": false }
```

`date` is the `Date` header, else the arrival time (INTERNALDATE), as ISO 8601 with offset (UTC
as `Z`: `"2026-10-30T09:12:00Z"`), or `null` when the message has neither.

### `GET /accounts/{account_id}/folders`

```jsonc
{
  "account_id": "work",
  "folders": [
    { "path": "INBOX", "role": "inbox", "unread": 3, "total": 120 },
    { "path": "Archive/2025", "role": null, "unread": 0, "total": 842 }
  ]
}
```

`role` is `inbox`, `sent`, `drafts`, `trash`, `junk`, `archive`, `all` or `flagged` (from the
server's special-use flags and INBOX, else from a well-known name), else `null`. Unselectable
folders (`\Noselect`) are left out. Order: the folders with a role first, in the order inbox,
flagged, sent, drafts, all, archive, junk, trash, then the rest by path. Counts are always present
here (STATUS; 0 for a folder whose STATUS failed). Errors: 404 `not_found` (account), 409, 502,
504.

### `GET /accounts/{account_id}/messages?folder=INBOX&limit=25&cursor=`

One folder, newest first (by arrival: UID). `folder` defaults to `INBOX`; `limit` is 1..50,
default 25 (a value outside is 422). Response: a **page**.

```jsonc
{
  "messages": [
    {
      "ref": "WzEsIndvcmsiLC…",
      "account_id": "work",
      "folder": "INBOX",
      "date": "2026-10-30T09:12:00Z",
      "from": { "name": "Ada Lovelace", "address": "ada@example.org" },
      "subject": "Notes on the engine",
      "flags": { "seen": false, "flagged": false, "answered": false, "draft": false },
      "has_attachments": true,
      "preview": "Dear Charles, the notes are attached…",
      "size": 48213
    }
  ],
  "next_cursor": "WzEsW1sid29yayIs…",   // null when there is no more
  "failures": [ { "account_id": "work", "folder": "INBOX", "code": "unreachable" } ]
}
```

- `has_attachments` is true when the message's structure has an attachment; inline parts (the
  images its HTML shows in place) do not count.
- `preview` is the first 200 characters (UTF-16 code units, mailroom-core's `PREVIEW_LENGTH`) of
  the rendered text body, one line, quotes collapsed; `""` when there is none. `size` is the
  message size in bytes.
- `from` is the first sender (`name` and `address` `""` when there is none); `subject` is `""`
  when absent.
- A single folder that fails answers its error status instead of a page.

### `POST /search`

The fields of mailroom-mcp's `search` (`src/mcp/tools/listing.ts`) as a JSON body; all given
criteria must match. Results newest first by arrival (INTERNALDATE), merged across sources, the
same page shape.

```jsonc
{ "account": "*", "folder": "INBOX", "from": "ada@example.org", "limit": 25, "cursor": null }
```

| Field | Meaning |
| --- | --- |
| `account` | An account id, or `*` for every account of the user (default `*`). |
| `folder` | A folder path, or `*` for every folder (default `INBOX`). `*` leaves out **Junk and Trash** on every account, and an account with an `\All` folder (Gmail's All Mail) is read through it only. |
| `from`, `to` | Address or name contains this. |
| `subject` | Subject contains this. |
| `text` | Headers or body contain this. |
| `since`, `before` | `YYYY-MM-DD` (a real calendar date; `since` inclusive, `before` exclusive), compared with the arrival date (INTERNALDATE), not the `Date` header. |
| `unread`, `flagged` | Booleans. |
| `has_attachment` | `true` only (approximate: `multipart/mixed`); `false` is ignored, not a criterion. |
| `gmail_query` | Gmail search syntax (X-GM-RAW); Gmail accounts only, else `unsupported`. |
| `limit`, `cursor` | As for the list. |

Every field is optional. The criteria and `cursor` take `null`, meaning absent; `account`,
`folder` and `limit` do not (422). An unknown field is 422. With no criteria,
everything in the folder matches. A text criterion with a line break or NUL is 400
`invalid_argument`. A search of one account and one folder that fails answers its error status
instead of a page, as the list does. Otherwise a source that cannot be searched is reported in
`failures` (`folder` is `null` when the account failed before a folder was chosen) and the others
still answer. Each account of an all-accounts search has its own deadline, so a slow one is a
`timeout` failure, not a 504 for the whole page. An account without a password that opens is such
a failure too, with the app's code `password_missing`.

### `GET /messages/{ref}?offset=0&include_quoted=false`

```jsonc
{
  "ref": "WzEsIndvcmsiLC…",
  "account_id": "work",
  "folder": "INBOX",
  "from": { "name": "Ada Lovelace", "address": "ada@example.org" },        // null when absent
  "to": [ { "name": "", "address": "charles@example.org" } ],
  "cc": [],
  "reply_to": [],
  "date": "2026-10-30T09:12:00Z",
  "subject": "Notes on the engine",
  "message_id": "<a1b2@example.org>",                                        // null when absent
  "flags": { "seen": false, "flagged": false, "answered": false, "draft": false },
  "text": {
    "content": "Dear Charles, …",
    "removed_chars": 0,
    "incomplete": false,
    "next_offset": null,
    "quoted_omitted": true
  },
  "has_html": true,
  "has_remote_content": true,
  "attachments": [ { "part": "2", "name": "notes.pdf", "type": "application/pdf", "size": 40211 } ],
  "calendar": null
}
```

- `text` is the body part mailroom-core picks (text/plain, else HTML converted to text), with
  quoted history and signatures left out unless `include_quoted=true`: nothing in `content` marks
  the gap; `quoted_omitted` is true when something was left out, so the client offers to show it
  (always false with `include_quoted=true`). It is cut to a **window of
  `body_chars` characters** (the user's limit in their mailroom-core settings, default 6000, at
  least 500); `next_offset` is
  where the next window starts (pass it as `offset`, `>= 0`), `null` at the end. `removed_chars`
  counts invisible characters stripped (a phishing and prompt-injection trick: the client may say
  so); `incomplete` is true when part of the body was too large or too deeply nested to render.
  `content` is `""` for an empty body or a message with no text body.
- `has_html` is true when the message has an HTML body: the client then offers the HTML view.
- `has_remote_content` is true when that HTML has remote images (`http` or `https`: what
  `remote=1` would load); false when `has_html` is false.
- `attachments` lists parts to download (`part` like `"2"` or `"1.2"`); an attached message
  (`message/rfc822`) is one attachment. Inline parts (images the HTML shows in place, by
  `Content-ID`) are not attachments. `type` is the declared type, `name` is `""` when unnamed.
  `size` is the decoded size in bytes, estimated from the encoded one (base64 at 57 in 78), so it
  may differ a little from the download's.
- `calendar` is `null` or one `{part, name, type, size}` of the `text/calendar` part (an invite;
  downloadable, not parsed yet).
- Reading **does not set `\Seen`.** Errors: 400, 404, 409 (stale), 502, 504.

### `GET /messages/{ref}/html?remote=0`

A **standalone HTML document**, not JSON: `remote=0|1` (default `0`). The sanitised HTML body
(mailroom-core's `safe_html`: allowlisted tags, attributes and CSS properties; no script, forms,
frames, `base`, event handlers). `cid:` images are inlined as `data:` up to a total budget; past
it an image loses its `src` and keeps its alt text, with no placeholder. Remote (`http(s)`) image
sources are removed unless `remote=1`. Whatever `remote` says, nothing else remote is kept: every
`<style>` element is dropped with its content (remote stylesheets and fonts with it), only
allowlisted properties of inline `style` attributes survive, and `url()` and `srcset` are
removed. A message with no HTML body is 404 `not_found`. The API sets these response headers:

```
Content-Type: text/html; charset=utf-8
Content-Security-Policy: sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cache-Control: private, no-store
```

With `remote=1` the CSP's `img-src` is `data: http: https:` instead (an `http:` image on an
`https` page is the browser's mixed content: it upgrades or blocks it). The SPA may frame the
document and nothing else may; the nginx `/api/` location adds no CSP of its own. Errors are the
usual JSON bodies with their statuses. The client fetches the document first and frames it only
once that answered 200, so a failure shows as the client's own error, never inside the frame.

### `GET /messages/{ref}/parts/{part}`

A download of one attachment, inline part or the calendar part (`part` matches
`^\d+(\.\d+)*$`), transfer encoding decoded. Headers: `Content-Disposition: attachment; filename*=UTF-8''…` (the name
cleaned; a fallback name when absent), `Content-Type` **sniffed** by mailroom-core's content-type
rules (the four displayed raster images and plain-text kinds keep their type; anything else
`application/octet-stream`; never `text/html` or `image/svg+xml`), `X-Content-Type-Options:
nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, `Cache-Control: private,
no-store`. Capped at the user's `attachment_max_mb` (mailroom-core settings, default 10): over
it, 413 `too_large`. A part that is not an attachment, an inline part or the calendar part of that
message: 404 `not_found`.

## MCP

The same service layer backs the `mail_read` tool group
([`mcp.md`](mcp.md)): `messages_list`, `messages_search`, `message_read`, `thread_read`,
`attachment_read`. The REST routes carry the structured shapes above; the tools return
mailroom-core's text inside the untrusted envelope, its hints naming the app's tools, and a
collapsed quote as a marker line that says how to read it. There is no REST route for threads yet.

## Later slices

- **B (organize):** flag, mark read, move, archive, trash with ref batches; mark-read on open is a
  web choice.
- **C (drafts and send):** compose, reply, forward, drafts, send through the send gate.
- Threaded list view, and the index with live updates ([`../REQUIREMENTS.md`](../REQUIREMENTS.md)).
