# Mail client

The web mail client, built in slices. **Slice A (read)** is specified here and never changes the
mailbox: not even `\Seen` is set by opening a message through the API or MCP. **Slice B
(organize)** changes flags and folders, and never deletes. **Slice C (drafts and send)** writes
mail: compose, reply, forward, drafts and sending. Product scope:
[`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Mail client*. Rendering and IMAP come from
**mailroom-core** (`render` and `mail`, connections in `mail.imap`; 0.1.2 for slices A and B, the
0.1.3 for slice C's compose, drafts and send), over IMAP directly: no index and no
live updates yet.

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

## Slice B: organize

Slice B lets the user change a message's flags and folder from the web client and the MCP tools.
Behaviour is mailroom-core's `mail.organize` (`move_messages`, `archive_messages`,
`trash_messages`, `set_flags`), itself the behaviour of mailroom-mcp's `organize` tools.

### Actions

- **Move** to a folder of the message's account, by path.
- **Archive:** move to the account's folder with role `archive`. The web toolbar hides the action
  for an account without one.
- **Trash:** move to the account's folder with role `trash`. This is never a permanent delete:
  mailroom does not delete mail.
- **Flag:** set or clear `\Seen` (read/unread) and `\Flagged` (star/unstar); `seen`, `flagged` or
  both. Only these two flags can be changed. The answer reads them back per message.

A **batch** is 1 to 50 refs, from any folders and accounts of the user (not 0, not 51: 422 on the
REST routes, `invalid_argument` from mailroom-core). Every ref is parsed and its account checked
**before any write**: a malformed ref (400 `invalid_argument`), one of an account that is not the
user's (404 `not_found`, as a missing message) or one of an account that cannot log in
(`password_missing` 409, `secrets_key_missing` 503) fails the whole call and nothing is written.
The refs are then grouped by account and folder, and each group is one SELECT (read-write) and one
operation.

### Partial results

Groups succeed or fail independently, and a write is never retried. A group is all-or-nothing,
except a move on a server without `MOVE` whose removal fails after the copy: the group fails with
its `new_refs` and "do not repeat the move", and the messages may be in both folders.
The answer has one **result per group**: `account`, `folder` (the source folder), `ok`, `count`
(messages changed), and:

- `new_refs`: the refs in the destination, when the server reports where the messages landed
  (COPYUID); `null` when it did not (the client then searches the destination), empty for flags.
- `junk`: `"spam"` or `"ham"` when the move trained the account's Junk (below), else `null`.
- `flags`: for the flag route, each message's `seen` and `flagged` read back (`null` when the read
  failed; the change stands).
- `error`: `{code, message}` of the group's failure, else `null`.

The call answers `200` when at least one group succeeded, with the failures in the list. When
**no** group succeeded, it answers the refusal of the first failure: its code's status from the
[errors table](#errors) and mailroom-core's message ([Refusals](#refusals) below). The web client
shows each failed group with the mail error wording and updates the rows of the others.

### Refusals

| Case | Code |
| --- | --- |
| A ref names a folder whose UIDVALIDITY changed | `stale_reference` (409) |
| Some messages of a group are gone from the folder | `not_found` (404) for the whole group: "N of these messages are no longer in <folder>; search again." |
| Destination folder missing or `\Noselect` | `mailbox_missing` (404) |
| Destination is the folder the messages are in | `invalid_argument`: "They are already in that folder." |
| Destination is Drafts or Sent (moving there would make a draft or sent copy of a message that is neither) | `invalid_argument` |
| Trash, and the messages are already in Trash | `invalid_argument`: "They are already in Trash; mailroom never deletes permanently." |
| Trash, and the account has no Trash folder | `unsupported` |
| Archive, and the account has no Archive folder | `unsupported`: "<account> has no Archive folder." |
| Neither `seen` nor `flagged` given | `invalid_argument`: "Give `seen`, `flagged` or both." |
| The server has neither MOVE nor UIDPLUS | `unsupported` |
| A move without MOVE copied the messages but could not remove them from the source | The connection's failure code (`server_error` for a refusal, as something was written), with `new_refs`: "They were copied to the destination but may not have been removed from this folder: do not repeat the move. New refs: …" |

INBOX is matched case-insensitively, as everywhere.

### Never expunge

Mailroom never deletes mail and expunges nothing it did not move, apart from
[the own-draft exception](#the-own-draft-exception). With the `MOVE` extension a move
is one command. Without it but with `UIDPLUS`, it is `UID COPY`, `UID STORE +FLAGS \Deleted` and
`UID EXPUNGE` of **exactly those UIDs**. A server with neither is refused (`unsupported`) rather
than risk a plain `EXPUNGE` that would remove other messages already marked `\Deleted`.

### Junk training

A move into the account's Junk folder is a `spam` training signal and a move out of it a `ham`
one, as in mailroom-mcp: the group result's `junk` says which, and the tool text tells the agent
the message can be trained. Nothing learns from it on its own ("learning never acts",
[`../../AGENTS.md`](../../AGENTS.md)).

### Mark read on open

Opening a message in the web client marks it read: once the message view has loaded with
`flags.seen` false, the client calls the flag route with `seen: true`; the row and the folder's
unread count update without a reload. This is **a web choice only**. `GET /messages/{ref}` and the
MCP `message_read` still never set `\Seen`.

### Web client

- **Multi-select:** a checkbox per row, and a select-all for the loaded page.
- **Toolbar** acting on the selection, or on the open message when nothing is selected: Archive
  (hidden when the account has no archive-role folder), Trash, Move to… (a picker over the
  account's folders, Drafts, Sent and the folder the messages are in left out), Star/Unstar, Mark
  read/unread.
- Results update the list in place: moved or trashed rows disappear, flags change on the row, and
  failed groups are shown with the mail error wording.
- A selection over 50 refs is sent 50 at a time; a refused batch is shown as failed groups and the
  rest are still sent; only when every batch is refused is the outcome worded as that refusal.
- **Keyboard shortcuts** on `/mail`:

| Key | Action |
| --- | --- |
| `j` / `k` | Next / previous message |
| `x` | Toggle selection of the current message |
| `e` | Archive |
| `#` | Trash |
| `s` | Star or unstar |
| `u` | Mark unread |
| `v` | Open the move picker |
| `Escape` | Close the picker, else clear the selection |

  Shortcuts are ignored while focus is in a text field (an input that takes text, a textarea, a
  select or a contenteditable element), with Ctrl or Meta held (AltGr aside), and with Alt on a
  letter key: a `#` typed with AltGr or Option, as on an Italian keyboard, still trashes. A held
  key repeats only `j` / `k`. They do not reach the page while focus is inside the HTML view's
  frame. Every control has an accessible label; strings are in the `mail` namespace in English
  and Italian.

### REST routes

JSON bodies (unknown fields are 422), CSRF as for every state-changing route.

| Route | Body |
| --- | --- |
| `POST /messages/move` | `{"refs": ["…"], "folder": "Archive/2025"}` |
| `POST /messages/archive` | `{"refs": ["…"]}` |
| `POST /messages/trash` | `{"refs": ["…"]}` |
| `POST /messages/flag` | `{"refs": ["…"], "seen": true, "flagged": false}` (`seen`, `flagged` optional, at least one) |

`refs` holds 1 to 50 strings of at most 4096 characters. Answer, `200`:

```jsonc
{
  "results": [
    {
      "account": "work",
      "folder": "INBOX",
      "ok": true,
      "count": 2,
      "new_refs": ["WzEsIndvcmsiLC…"],      // null when the server gave no COPYUID; [] for flags
      "junk": null,                         // "spam" | "ham" | null
      "flags": [],                          // flag route: [{ "ref", "seen", "flagged" }]
      "error": null                         // or { "code": "not_found", "message": "…" }
    }
  ]
}
```

Refs in `flags` and `new_refs` are the client's to store; the source refs of a moved message are
stale afterwards. Errors: 400, 404, 409, 422, 429, 502, 504, and the answers cannot be cached
(`Cache-Control: private, no-store`).

### MCP

The tools `messages_move`, `messages_trash` and `messages_flag` (group `mail_organize`,
[`mcp.md`](mcp.md)) take the same refs (up to 50, any folders) and return mailroom-core's text
inside the untrusted envelope. When every group fails, the tool error is mailroom-core's text
inside the envelope too: a lone group's error message, or the per-folder text when there were
several. mailroom-core returns an audit item per message
(account, folder, UID, `Message-ID`, sender address, a SHA-256 of the subject, destination, junk,
result); the app does not keep them yet ([`../TODO.md`](../TODO.md)) and logs counts and codes
only, never these fields.

## Slice C: drafts and send

Slice C lets the user write mail in the web client and lets agents do the same through the MCP
tools. It is the one slice that sends mail, so its rules are the strictest: read
[Send rules](#send-rules) before changing any of it. Behaviour is mailroom-core's `mail.compose`,
`mail.reply`, `mail.attachments`, `mail.drafts` and `send` modules, themselves the behaviour of
mailroom-mcp's `draft` and `send` tools. Web composing is **plain text**; rich text is tier 3
([`../REQUIREMENTS.md`](../REQUIREMENTS.md)).

### Compose

- **Modes:** a new message, **reply**, **reply all** and **forward**. A reply or forward starts
  from the message open in the client; the web client asks `POST /drafts/prefill` for the fields
  and shows them in the composer. Prefilling writes nothing.
- **Reply** is addressed to the original's `Reply-To`, else its sender; **reply all** adds its
  other recipients, minus the account's identities (every address it can send as, not just the one
  answering). A reply to one's own message, from Sent for example, has nobody left that way and
  goes to its To. The subject gets one `Re: `, the threading headers (`In-Reply-To`,
  `References`) continue the thread, and the body quotes the original. The original is read up to
  2 MiB and its text is then cut to 20,000 characters, in a quote and in a forward alike; a longer
  one is cut, never refused.
- **Forward** has an empty recipient list and the subject with one `Fwd: `. The body holds a
  "Forwarded message" block with the original's headers and text, and the original's
  attachments are offered as `keep` entries (the user can drop each). Only reply and reply all
  set `\Answered` on the original; a forward sets nothing.
- **Identity:** the composer's `From` is a choice among the account's
  [identities](../GLOSSARY.md). A reply or reply all preselects the identity the original was
  addressed to, else the account's default; a new message and a forward use the default.
  An address outside the identities is `invalid_argument`. The accounts API gives the web
  client each account's `identities` (`address`, `name`, `default`), its `send_mode`,
  `attachment_max_mb` (the user's limit, at most 25) and `max_recipients`, so the composer can say
  early what is not possible.
- **Recipients** are To, Cc and Bcc: each at most 50 addresses, together at most the user's
  `max_recipients` (mailroom-core settings, default 10). An address must be deliverable: one `@`,
  an ASCII local part, a domain that is ASCII or punycode. Anything else is 422
  `recipient_refused` before a connection is made. Autosave saves only the recipients the user
  has committed (a chip), so an address half typed cannot fail a save.
- **Attachments** are new uploads (`files[]`) or parts of existing messages (`keep`: `ref` and
  `part`); an agent never passes file content (MCP). Only attachment and calendar parts of a
  message can be kept; inline images and the body parts cannot. Limits are in
  [Limits](#limits).
- **Headers** are one line: CR and LF are stripped from every header value built from user input
  or mail (subject, names, addresses), so a header cannot be injected.

### Drafts

A draft is a message in the account's `\Drafts` folder. **The web client always composes through
a draft**: autosave replaces the draft, and Send saves it and then sends that draft. A closed tab
or a crashed browser therefore loses at most the last few seconds, and there is one path from
composer to server.

- **An IMAP message cannot be edited**, so saving is: check that the old version can be removed
  (`check_removable`), APPEND the new one, then remove the old one: never the other way round,
  so a failed save leaves the last good draft. A save whose removal fails stands, with
  `replaced: false`. The client serialises its saves, always passes the latest `ref`, and never
  starts a save while one is running.
- **`compose_id`** is a UUID the client makes when the composer opens. It becomes the draft's
  `Message-ID` (`<compose_id@domain>`). The `compose_id` is the same across every version and the
  domain follows the `From`, so a version saved under another identity may change it; a save whose
  answer was lost is found again by the `compose_id` alone (`GET /drafts/find`) instead of saved
  twice, and it is the key of the send ([state machine](#send-state-machine)). A save that
  replaces a version with a `compose_id` must carry the same one, else `invalid_argument`: a new
  one would forget how the draft's send went. A draft not made by Mailroom has none: the client
  generates one, and the next save replaces it, unless a send under its ref is `sending`, `unknown`
  or `sent`. Such a save is refused, 409 `draft_not_editable`, with `send` (`send_id`, `state`,
  `code`) in its `detail`: its key is its ref, so a new version would stand free of that send. The
  key needs only the ref, so this holds once the draft is gone too, which is how a delivered one
  normally ends. An own draft has no such limit, as its key survives a save.
- **Fields:** the draft keeps `From`, To, Cc, Bcc (Bcc is kept in the draft and in the Sent copy,
  never on the wire), subject, the plain text and the attachments. A header
  `X-Mailroom-Draft: 1` marks a draft Mailroom wrote; when the draft answers a message it reads
  `X-Mailroom-Draft: 1; answers=<ref>`. The header is stripped before the message is sent.
- **`lossy`:** a draft written elsewhere may hold what the plain-text editor cannot keep (an HTML
  body, inline images, formatting). `GET /drafts/{ref}` then answers `lossy: true` and the
  composer warns that saving keeps only the plain text and the attachments. Only a save does
  that: a draft that is sent without being edited goes out exactly as stored (an HTML body
  included, which is also what `draft_send` does), under the key its ref gives when it has no
  `compose_id`.
- **Opening** a message in the Drafts folder opens it in the composer, and does not mark it read.
  An account in send mode `off` cannot save one, so there it reads as any other message.
- **One composer** is open at a time, above the panes: sidebar and list links leave it alone, and
  opening a message puts it behind that message until the user goes back to the draft. The URL's
  `compose` param names it by `compose_id` and account, not by ref, so a reload brings it back
  from `GET /drafts/find`; if nothing was saved there is nothing to bring back.
- **Removing** a draft (`DELETE /drafts/{ref}`, or the old version of a save) follows
  [the own-draft exception](#the-own-draft-exception).
- **`autosave`:** a save answers `autosave: false` when the server lacks `UIDPLUS`, because each
  version would then be moved to Trash instead of removed and autosave would fill it. The web
  client then saves only when the user asks (a *Save draft* button, and on Send).
- A `keepalive` request flushes the latest text when the page is hidden (`pagehide`); it carries
  text only, no files, and only what fits the browser's 64 KiB keepalive quota: for a longer
  draft the browser refuses the request, the edits since the last save are lost, and the last
  saved version stays.

### The own-draft exception

[*Never expunge*](#never-expunge) has one exception: **a draft Mailroom wrote**, identified by its
`X-Mailroom-Draft` header, is removed with `UID STORE +FLAGS \Deleted` and `UID EXPUNGE` of
**exactly its UID** (needs `UIDPLUS`), so another message already marked `\Deleted` is left
alone. Without `UIDPLUS` that removal is not made, which is why such an account answers
`autosave: false`. A draft written elsewhere is **moved to Trash**, never expunged, and a draft
the user deletes is subject to the same rule. Nothing else is ever expunged. The reason is
practical: an edited draft is a new message, and keeping every version in Trash would bury the
user's own Trash.

### Send mode and who is acting

The account's [send mode](../GLOSSARY.md) limits what **agents** may do through MCP. The user
clicking *Send* in the web client **overrides it, except for `off`**.

| Send mode | MCP agent (`message_send`, `message_reply`, `message_forward`, `draft_send`) | Web *Send* |
| --- | --- | --- |
| `off` | Refused, `send_disabled`; draft tools refused too | Refused, `send_disabled`; compose, save and delete are refused too |
| `drafts` | Saved as a draft, and the answer says so; `draft_send` is refused, `invalid_argument` (it would only be saved as a draft again) | Sent |
| `approval` | Sent only if every recipient matches the account's `auto_approve` rules, else the whole message is saved as a draft (nobody gets it); `draft_send` is refused, `invalid_argument`, instead | Sent |
| `direct` | Sent | Sent |

There is no outbox for `approval`: whatever is not auto-approved is drafted. The hourly send limit
and the recipient cap bind both. In the web client, an account in `off` has its compose actions
disabled, with a hint saying why; and its settings hint says what each mode lets **agents** do
(and that the user's own *Send* always works unless the mode is `off`).

### Send rules

These bind every code path that sends.

- **Only an admitted message reaches SMTP.** The gate's `admit` checks the mode (with who
  authorised: the user or an agent), the recipients and the limits, and returns an `Admitted`
  token; only that token can be passed to `deliver`. The app's phases (admission under the
  request, delivery in the background) cannot skip it.
- **Counting before sending.** `claim_send` counts the user's sends of the last hour and inserts
  the new row under a per-user advisory lock, so parallel requests cannot all pass the limit. The
  row is written **before** SMTP: a send that fails midway may still have been delivered, so it
  counts.
- **No double delivery.** A web send is keyed by its account and `compose_id`. The same key is
  claimed once (a unique index per user), so a repeated click, a retried request or two tabs
  deliver once.
- **One connection, verified TLS:** one SMTP connection per send, certificate and
  host name checked, implicit TLS or STARTTLS as the account says; a server that does not offer
  a required STARTTLS fails the send.
- **Filing is secondary.** Filing to Sent, removing the draft and setting `\Answered` are separate
  calls after delivery. Each can only set `filed: false` or add a note; none can turn a
  delivered message into a failure, and none is retried in a way that could send again. A draft
  that did not reach Sent stays in Drafts, as the only copy in the account, with a note; its key
  still stops it going twice.
- **MCP direct sends** (`message_send`, `message_reply`, `message_forward`) have no `compose_id`
  and use a fresh `Message-ID`, so an agent's repeated call is not guarded: the tool texts tell the
  agent not to resend ([`mcp.md`](mcp.md)).

### Send state machine

A send is a row with a state, the key, the account and the result. A keyed row (a draft's send)
stays at least 30 days and a key-less one (an agent's direct send) a day; rows are pruned when the
user next sends, so a direct send's `GET /sends/{id}` is `not_found` after a day.

```
            claim
  (none) ───────────▶ sending ──▶ sent
                         │
                         ├──────▶ failed   the message definitely did not leave
                         └──────▶ unknown  delivery cannot be confirmed
```

- **`sending`** while the background runner works on it. **`sent`** after the server accepted the
  message. **`failed`** only when no byte of the message can have left: the server refused it, or
  the attempt ended before the `DATA` command (a refused connection, a TLS failure, a login that
  failed, a timeout before `DATA`, a 4xx or 5xx reply to a command or to `DATA`), or the send
  waited in the queue past 360 s and never began (`failed` with `timeout`). **`unknown`** for any
  socket failure, timeout or lost connection once `DATA` has begun, and for a `sending` row older
  than 360 s (a restart or a stuck runner), because the message may have been delivered.
- **Admission and delivery are separate.** `POST /drafts/{ref}/send` checks the draft, the mode
  and the limits under the 30 s mail deadline, then answers `202 {send_id}`; a `SendRunner`
  (4 threads, at most 2 per user) delivers. When the runner has no free thread for the user the
  route answers 429 `busy` **before** claiming anything. The client then polls
  `GET /sends/{id}` until the state is not `sending` and, once it is `sent`, until `settled`.
- **Retry of the same key:** `sent` replays the stored result (200); `sending` returns the same
  `send_id` (202); `failed` is claimed again and delivered afresh (the earlier attempt counted
  toward the limit); `unknown` answers **409 `delivery_unknown`** unless the request has
  `confirm_resend: true`, which the user gives with *Send anyway*. An own draft's retry reaches the
  key only while the draft exists, because the key is read from it: the 200 is what a draft left
  behind (its removal failed) answers, and once a send has removed the draft a retry is 404
  `not_found`, so the client then looks in Sent, or follows the `send_id` it already holds. A draft
  written elsewhere is keyed by its ref, so a retry reaches its send with the draft gone as well,
  and answers the same way. With `confirm_resend` there, or with no such send, it stays 404
  `not_found`: there is no draft left to send.
- **SMTP timeouts:** 15 s to connect, 60 s per command, 60 s plus the message size at 128 KiB/s
  for `DATA`, and a watchdog at 360 s for the whole send.
- A deploy or restart during a send leaves it `unknown` by design.
- **The result** (`GET /sends/{id}`): `state`; `code` (the refusal's code for `failed`, else
  `null`); `filed` (the Sent copy was stored; `null` until known, and known only after `sent`, together with
  the notes); `rejected` (recipients the
  server refused while accepting others: delivered to the rest); `notes` (fixed English texts such
  as "Sent, but not filed in Sent."); `note_codes` (stable codes of the notes, for a client that
  words them itself: `draft_not_removed`, `draft_kept`, `original_not_marked`; not being filed has
  none, `filed` says it); `settled` (filing and what follows it are over, so `filed` and the notes
  are final: `sent` comes earlier, and a restart can leave it false, so a client stops waiting
  after 70 s, past the two 30 s calls that follow a delivery).
  Another user's `send_id` is 404.

### Texts in the web client

In meaning (the strings are in the `mail` namespace, English and Italian, the same keys):

- **Saving:** "Saving…", "Saved", "Not saved" with a retry; for `autosave: false`, that the draft is
  saved only on request. A draft the client cannot save keeps the last good version on the server.
  A save answered with `ref: null` is stored but not located: closing is fine, while Send refuses,
  saying the draft is still being saved, until `GET /drafts/find` shows a different ref, so that
  it can never deliver the version before. A save whose answer was lost and that is then found
  there is not written twice, but another writer's version may have taken that ref, so Send saves
  the form over it first, and refuses the same way if it cannot.
- **Sending:** "Sending…"; on `sent`, once `settled` or after 70 s without it, "Sent" (with "not
  filed in Sent" when `filed` is false, who the server refused when `rejected` is not empty, and
  the notes worded from `note_codes`, never the server's English); on `failed`, that the message
  was **not sent** and stays in Drafts, with the reason of the code. More recipients than
  `max_recipients` are refused at Send, naming the number.
- **Already sent:** a first answer of `200` means an earlier delivery spent the key, and this click
  delivered nothing, whatever the draft now holds, so it never says "Sent". The composer stays
  open on the banner a reopened draft of a `sent` key shows; Send stays blocked there and what is
  left to do is delete the draft. A `200` after a lost answer may be this click's own delivery and
  reads as sent.
- **`unknown`:** "Can't confirm delivery. The message stayed in Drafts." with three actions:
  **Open Sent** (look for it), **Keep draft** (dismiss, the draft stays) and **Send anyway** (the
  message may then arrive twice; sends with `confirm_resend`). Reopening the draft shows the same
  banner until it is settled.
- **A draft written elsewhere that its send holds** (`sending`, `unknown` or `sent`): the server
  refuses a new version of it, so it opens read-only, saying why, with its fields off and nothing
  saved. The banner's actions stay, and so does *Delete draft*; *Send anyway* needs no save, so it
  sends the draft as stored. An own draft stays editable, since its key survives a save. A save
  refused `draft_not_editable` means such a send came from elsewhere after the draft was opened:
  the composer takes the send's state from the refusal, as the draft may be gone, and behaves the
  same, saving nothing more. The typed text stays on screen and selectable, marked as not saved,
  and *Close* warns that it is lost. Until the send fails, *Send* and *Send anyway* are off, since
  they would deliver the stored version and not that text. *Open Sent*, *Delete draft* and *Close*
  stay.
- **Refusals:** `send_disabled` (sending is off for this account), `rate_limited` ("The hourly send
  limit is reached. Nothing was sent.": the web client does not show when the next send is
  possible, though the API's message says it), `too_large`, `recipient_refused`, `busy` (try again
  in a moment), `draft_not_editable` (the send of a draft written elsewhere holds it),
  `invalid_argument` (the stored draft can't go out as it is, its headers malformed for example:
  changing anything saves a clean version, and Send then works). `not_found`
  or `stale_reference` on Send mean the ref held is no longer the draft: the composer looks up what
  is there now and, if it finds a version, says the draft changed elsewhere, that the text is saved
  over that version, and to press Send again.
- **Leaving:** closing the composer or the tab with unsaved text flushes a save; nothing asks the
  user to confirm leaving a saved draft. Only a save that fails on closing asks, since closing then
  loses the latest changes. *Send anyway* and deleting a draft ask first too.

### Keyboard shortcuts

Added to the table under [Web client](#web-client): `r` reply, `a` reply all and `f` forward
(on the open message), `c` compose a new message (from any `/mail` screen; the account is the
current one). They follow the same rules as the others (ignored in text fields and with
Ctrl or Meta held) and **do not fire while the composer is open**, wherever focus is. They are
disabled for an account whose send mode is `off`.

### Limits

The user's own limits (`max_recipients`, `send_per_hour`, `attachment_max_mb`, and `body_chars`
for reading) are set on their *Profile* page ([`../REQUIREMENTS.md`](../REQUIREMENTS.md)).

| Limit | Value |
| --- | --- |
| Addresses per field (To, Cc, Bcc) | 50 |
| Recipients of one message | the user's `max_recipients` (default 10), over To, Cc and Bcc |
| Sends per hour | the user's `send_per_hour` (default 20), across their accounts; counted when claimed |
| Body text | 100,000 characters |
| Subject | 998 characters |
| New files per draft | 10 |
| Attachments, in total (uploads and kept parts) | the user's `attachment_max_mb` (default 10), at most 25 MB |
| Upload request | 30 MB at `/api/v1/mail/drafts` (nginx), refused early on `Content-Length` |
| Save requests at once | 3 per user, in each API process; one more is 429 `busy` before its body is read |
| Original read for quoting | 2 MiB |
| Text quoted or forwarded | 20,000 characters (the original's text is cut to this) |
| Existing parts kept (`keep`) in a save | 50 (422 beyond it) |
| Attachments on an MCP tool | 10 (`attachments`, by ref and part) |
| Send rows kept | keyed: at least 30 days; key-less: a day; pruned on the user's next send |
| Concurrent sends | 4 in the process, 2 per user |

Over a field's cap, or over the recipient cap, is 400 `invalid_argument` (a send with no
recipient too: "Name at least one recipient."); over the attachment total or the upload size, 413
`too_large`; over the hourly limit, 429 `rate_limited`, whose message gives the time the next send
is possible.
The host's reverse proxy needs the same 30 MB limit on that route ([`../deploy.md`](../deploy.md)).

One race stays open by choice: the check that a send holds a draft written elsewhere is not atomic
with that send's claim, so a send landing during a save's APPEND can leave an own version with a
new key. It takes two writers on one such draft within one APPEND, and a second delivery then needs
the user to press Send on what they just edited; a lock across the APPEND costs more than that.

### Slice C routes

Every route is under `/api/v1/mail`, on the mail router: so the `Cache-Control: private,
no-store` rule, the ownership rules (another user's account, ref or send is `not_found`) and the
CSRF check apply. Bodies are JSON except `POST /drafts`. Unknown fields are 422.

| Route | Body | Answers |
| --- | --- | --- |
| `POST /drafts/prefill` | `{"ref", "mode": "reply" \| "reply_all" \| "forward"}` | 200 the fields below; writes nothing |
| `POST /drafts` | multipart: `draft` (JSON) and `files[]` | 201 `{ref, parts, replaced, autosave}` |
| `GET /drafts/{ref}` | | 200 the draft's fields, `lossy`, `parts`, `send` |
| `GET /drafts/find?account=&compose_id=` | | 200 `{ref, parts}` of the newest version; 404 `not_found` |
| `DELETE /drafts/{ref}` | | 200 `{removed}` |
| `POST /drafts/{ref}/send` | `{"confirm_resend": false}` (optional) | 202 `{send_id}`; 200 already sent; 409; 429 |
| `GET /sends/{id}` | | 200 the send's result |

The static route `GET /drafts/find` is matched before `GET /drafts/{ref}`.

```jsonc
// POST /drafts: the `draft` part (to, cc, bcc are lists of Address; text is plain text)
{
  "account": "work",
  "compose_id": "6f1c0a2e-…",                  // UUID; required
  "ref": "WzEsIndvcmsiLC…",                    // the latest version being replaced; null for the first save
  "answers": "WzEsIndvcmsiLC…",                // the message being replied to; null otherwise
  "from": "me@example.org",                    // one of the identities; null: the default
  "to": [ { "name": "", "address": "ada@example.org" } ],
  "cc": [], "bcc": [],
  "subject": "Re: Notes on the engine",
  "text": "Thanks, …",
  "keep": [ { "ref": "WzEsIndvcmsiLC…", "part": "2" } ]   // parts of existing messages, or of the draft itself
}
// 201
{ "ref": "WzEsIndvcmsiLC…", "parts": [ { "part": "2", "name": "notes.pdf", "type": "application/pdf", "size": 40211 } ],
  "replaced": true, "autosave": true }
```

- `ref` in the answer is the new version's ref, the one the client uses from now on; it is `null`
  when the server did not report where the draft landed and a search by `Message-ID` found
  nothing yet (the client then calls `GET /drafts/find`). `parts` lists the attachments of the
  saved version, as the message view's `attachments`: after a save, the client keeps its
  attachment tray from `parts`, with `keep` entries that name the draft's own parts. `replaced`
  is true when the previous version was removed.
- A draft with no recipient is a valid draft; a send needs at least one.
- `POST /drafts/prefill` answers `{account, from, to, cc, bcc, subject, text, answers, attachments}`,
  the fields of the draft it would start (`answers` null for a forward; `attachments`, for a
  forward, the original's `{ref, part, name, type, size}` that the composer offers as `keep`), `from` the
  identity address chosen as above. A recipient of the original that is not
  [deliverable](#compose) is left out, since it would refuse every save of the draft.
- `GET /drafts/{ref}` answers `{ref, account, compose_id, from, to, cc, bcc, subject, text, answers,
  lossy, parts, send}`: `compose_id` is `null` for a draft not written by Mailroom; `send` is
  `null`, or `{send_id, state, code}` of the send of this draft, so reopening shows an `unknown`
  banner. `bcc` is shown to its owner.
- `DELETE /drafts/{ref}` answers `{"removed": "expunged" | "trashed"}`.
- `GET /sends/{id}` answers `{send_id, state, code, filed, rejected, notes, note_codes, settled}`
  ([send state machine](#send-state-machine)); `POST /drafts/{ref}/send` answers `202` with
  `{send_id}` or, when that key is already `sent` and the draft is still there, `200` with the same
  body as `GET /sends/{id}`; once the draft is removed, 404 `not_found`. `send_id` is a string,
  opaque to the client. A draft written elsewhere has no `compose_id`, so its key comes from its
  ref.
- A draft's `ref` and `answers` must name the same account as `account`, else `invalid_argument`;
  `keep` may name any of the user's accounts. A message answered that is gone by the first save
  no longer threads it: the draft is saved all the same, without `answers`. A save keeps the
  threading headers of the version it replaces.
- Errors: 400 `invalid_argument`; 404 `not_found` (account, ref, draft, send); 409
  `stale_reference`, `send_disabled`, `delivery_unknown`, `draft_not_editable`,
  `password_missing`; 413 `too_large`;
  422 `recipient_refused`; 429 `busy`, `rate_limited`; 502, 504.

### MCP

The `mail_draft` and `mail_send` groups ([`mcp.md`](mcp.md)) are the same service functions
behind the same gate, with the agent as the one acting: the send mode limits them as in the
[table above](#send-mode-and-who-is-acting), and the hourly limit and recipient cap bind them.
Attachments are named by `ref` and `part` only: an agent never passes file content.

## REST contract

Every route is under `/api/v1/mail`, needs a [user session](../GLOSSARY.md), and sees only the
signed-in user's accounts. Reads are `GET` but search, a `POST` with a JSON body so that search
terms (addresses, subjects, words of a body) are never in an API URL; the four organize routes
(slice B) are `POST` too. Every response, the `401` of a missing or expired session included,
carries `Cache-Control: private, no-store`. The SPA does keep a search
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

Mailbox failures carry the 14 codes of mailroom-mcp, plus `delivery_unknown` (`src/imap/errors.ts`):

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
| `too_large` | 413 | Too large: the message, attachment or upload is over its limit. (Optionally followed by the limit that was passed.) |
| `send_disabled` | 409 | Sending is disabled for this account (send_mode off): no drafts or sends. |
| `rate_limited` | 429 | The limit is *N* per hour; the next send is possible at *time (UTC)*. |
| `recipient_refused` | 422 | A recipient is not a deliverable address, or the mail server refused the message; nothing was sent. |
| `delivery_unknown` | 409 | Delivery could not be confirmed: the message may already have gone out, so it was not sent again. |
| `server_error` | 502 | The mail server reported an error. |

The send group (`send_disabled`, `rate_limited`, `recipient_refused`, `delivery_unknown`) belongs
to slice C; slices A and B never answer them. The app's own codes keep their statuses: `password_missing`,
`starttls_unsupported` and `draft_not_editable` 409, `secrets_key_missing` 503, an unknown account `not_found` 404
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
| `has_attachment` | `true` only (approximate: `multipart/mixed`; on Gmail, which ignores that header search, `has:attachment` through X-GM-RAW, over one account or all); `false` is ignored, not a criterion. |
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

- Threaded list view, and the index with live updates ([`../REQUIREMENTS.md`](../REQUIREMENTS.md)).
