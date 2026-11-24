# Mail client

The web mail client, built in slices. **Slice A (read)** is specified here and never changes the
mailbox: not even `\Seen` is set by opening a message through the API or MCP. **Slice B
(organize)** changes flags and folders, and never deletes, but for
[a move to another account](#moving-to-another-account) asked to delete the originals for good. **Slice C (drafts and send)** writes
mail: compose, reply, forward, drafts and sending. [**Folder management**](#folder-management)
creates, renames and deletes folders: Mailroom deletes mail only deliberately, and a folder's
delete is the one in the mail client. Product scope:
[`../REQUIREMENTS.md`](../REQUIREMENTS.md) — *Mail client*. Rendering and IMAP come from
**mailroom-core** (`render` and `mail`, connections in `mail.imap`; 0.1.2 for slices A and B, the
0.1.3 for slice C's compose, drafts and send), over IMAP directly. Lists, searches without a
body's text and folder counts read the [index](#index) where it holds every folder they involve,
and the server otherwise; the web client keeps them up to date from the
[live updates](#live-updates), and shows new mail as [desktop
notifications](#desktop-notifications).

## Slice A: read

- **Folders** per account, with their role and unread/total counts.
- **Message list** of one folder, newest first, read 25 at a time as it is scrolled: a
  virtualised list (only the rows in view and a few around them are in the page), whose next
  page is read as the view comes within ten rows of the last one loaded, so a folder of tens of
  thousands of messages stays smooth; a page that fails says so, with *Try again*. The same list
  shows searches, views and conversations. Optionally with its subfolders merged in
  (`subfolders`, below).
- **Search** over one account or all, with the search fields of mailroom-mcp's `search`, typed
  in the [search syntax](#search-syntax) or set in the form behind *More options*; a
  search shown offers *Create filter from this search* where the instance runs filters
  ([`filters.md`](filters.md#filter-drafts)).
- **Quick filters** above the list, *Unread*, *Starred* and *With attachments*: toggle buttons
  (`aria-pressed`) that narrow the folder or search shown as `is:unread`, `is:flagged` and
  `has:attachment` would, over any criterion of the search itself (a list of a folder becomes a
  search of that folder, its subfolders included when they are listed with it, while the folder
  stays the one open and the search box empty). The URL carries them (`only`); another folder or
  a new search starts without them. A row the user changed so it no longer matches (opened, and
  so read, under *Unread*; unstarred under *Starred*) stays in place, live updates reading the
  list again too, until the folder, the search or the filters change; `j` and `k` go on from it.
- **Scheduled** below the views in the sidebar: the user's [scheduled sends](#send-later).
- **Account switcher** at the top of the sidebar: a button with the account shown, its name and
  colour, opening a menu of the user's accounts (the ARIA menu pattern, the one shown checked).
  The sidebar's tree lists that account's folders only. Choosing another shows its inbox, or keeps
  the view open while the tree changes. The account in the URL is the one shown; `/mail` without
  one shows the account last shown in this browser (`mailroom.ui.mail.account`), else the first.
- **Views** below the account switcher: *All inboxes* and the user's saved [views](#views), in
  their order, each with the unread count of its folders when every one is listed already (none
  for a search). Opening one (`view` in the URL) lists it as a folder is listed, each row naming
  its account and folder, with selection, the row and toolbar actions and live updates (a change
  to one of its folders, or one getting ready, reads it again). Its actions send each row's
  `listed_folder` as its own source folder (`source_folders`), so a Gmail row (an All Mail ref)
  is acted on in the label folder it was listed under and leaves that one only, in a view of
  different folders too; `source_folder` is INBOX for *All inboxes*, a search's folder, or the
  folder a view's folders all share. No quick filters, and selecting every matching
  message stays a folder's or a search's. The folders it leaves out, off the index, are named
  above the list. A search shown offers *Save as view* (named in a dialog, then opened), which
  keeps the line as the list shows it, the quick filters on included (`from:ada` with *Unread*
  on saves `from:ada is:unread`), refused for one with words or Gmail search, which the index cannot answer; each saved view's
  menu (its ⋯ button) renames it, moves it up or down, or deletes it (its mail stays).
- **Conversations** in the list, when the user groups them (the Profile's *Conversations*, on by
  default; [below](#conversations)): a folder, search or view read from the index shows each
  conversation of several messages as one row, its latest message's, with its first writers in
  place of the sender, how many messages it has, unread while one of them is, starred when one
  is, and the paperclip when one has an attachment. Opening it (`thread` in the URL, beside
  `message`) shows its messages, read live and oldest first, above the latest one; each opens in
  turn below them, the row staying the current one; they are read again after an action and when
  one of their accounts' folders changes. Opened, the conversation's every message is marked
  read, also when followed by a link and the list shows its row only after the message loaded.
  A row loaded further down that a newer message brought into the first page goes from below. A row's actions (its checkbox, star, To sort, menu, a drag, the toolbar and keys on
  it or on its latest message open) act on every message of it, each ref checked as any other;
  their rows go or change together, but for the user's own Sent and Drafts copies, which only
  show in the thread. Trash, archive, a move or a spam report of a conversation of several whose
  newest sender is new to it (`latest_from_new_sender`) asks first, in a dialog naming its count:
  *All N messages*, *Only the message shown* (its latest, alone) or *Cancel*, focused (with
  several such rows: the whole conversations or only their newest messages). Each Gmail message
  of it goes from the label folder it was listed under (`listed_folders`). Undo takes back a move as always, and a flag change where the
  row said each message's flag (not a read mark over a partly read conversation, nor a star or To
  sort mark on one starred or marked in part). A source read live is listed message by message.
- **Message view:** headers, the text body (quotes and signatures collapsed), attachments and a
  calendar note; a **standalone HTML view** when the message has an HTML body; attachment download.
- **Sender checks** in the message view's headers, from the read's `sender_mismatch`, `sent_to` and
  `authentication` (below): a warning under From when the display name names another address or
  site, quoting the real address, never the name; the authentication verdict, *Verified* with the
  DMARC domain only on `dmarc_pass`, a warning when SPF, DKIM or DMARC is `fail`, else *Not
  confirmed*, each with the three results, SPF and DKIM naming the domain each was for (*DKIM pass
  (evil.example)*; a DMARC `pass` that is not `dmarc_pass` reads *pass, but not for this From*), or
  *Not verified* (pointing to the account's authentication server) when nothing is believed, the
  verdict left out for a message in the account's Sent or Drafts folder (the user's own); *Sent
  to* the identity it arrived at. More than three To and Cc addresses
  together are summed up as *You and N others* (or *N recipients* when the identity is not among
  them), shown in full on request.
- **Remote content** (remote images, `http` or `https`) is blocked and revealed per message
  ([Glossary](../GLOSSARY.md)). Only a **sender** is ever remembered, and only from a message
  whose DMARC passed for its one From at the account's own authentication server
  (`remote_trusted` and [`/remote-senders`](#remote-content-senders) below; the server checks,
  never the client). The web offers *Always for this sender* beside *Show remote images* only on
  `dmarc_pass`, loads the HTML with `remote=sender` at once when `remote_trusted` (the server
  checks the trust again as it serves it, so a read kept from before the sender was forgotten
  elsewhere loads nothing remote; a click in the tab asks `remote=1`), and lists the
  remembered senders, each removable, on the *Profile* page. Beside the gate, a line says how many
  remote images are blocked (`remote_images`) or shown (`loadable_remote_images`, and whether for
  the message or its sender), and the gate offers to show them only when some can be. What is
  removed either way is in the HTML route below.
- **Mailing list** row in the message view's headers, from `headers.mailing_list`: the list's name
  (else its id) as plain text, its archive as a link only when `https:` (new tab, `noopener
  noreferrer`), and an Unsubscribe button that acts only when the reader asks. It takes the first
  way the message allows: [one click](#one-click-unsubscribe) when `one_click_unsubscribe`, behind
  a confirm dialog that names the host and then says how the list answered; else a draft to the
  `mailto:` address in the composer ([prefill](#compose) `unsubscribe`), when the account can
  write mail; else the `https:` link in a new tab. *Filter instead* beside it opens the filter
  [drafted from the message](filters.md#filter-drafts), when the instance runs filters.
- **Print and download:** in the open message's *More actions* menu, *Print* opens the message's
  [printable page](#get-messagesrefprint) in a new tab, for the browser's own print, and
  *Download .eml* saves [its source](#get-messagesrefsource), and *Filter messages like this*
  opens the filter [drafted from it](filters.md#filter-drafts) where the instance runs filters;
  the row's context menu and the command palette offer them too, the composer open or not.
- **Mute conversation:** in that menu too, where the instance runs filters, it
  [mutes](#muted-threads) the message's conversation; the toast says so, with Undo, and when the
  account's Run filters is off, that muting has no effect until it is on. The message then shows
  it is muted (and that note), the menu offering *Unmute conversation* instead. Only a mute made
  on the mail screen since it opened is known there: the API names no thread by message. The
  Filters page lists every muted conversation under its account, with when it was muted, the
  same note, and *Unmute*. The row's context menu and the command palette offer both.
- **Link safety:** in the HTML view each link's tooltip is the host it really goes to (IDNA form,
  never the sender's own title), and a link whose text names another host (not the link's, nor a
  parent or child of it) is followed by a red *⚠ host* marker naming the real one; no
  interstitial page. The text body marks such a link `[text](url) (⚠ host)`, MCP's too.

The screens live under `/mail`: account and folder sidebar with counts, message list, message view
(three panes on desktop, one at a time on a phone; on desktop each pane scrolls on its own). On desktop
the **reading pane** sits where the user put it on the *Profile* (the same on every device): on
the right of the list (the default), below it, or off. With it on the right, a divider between the
list and the message view sets the list's width (30rem, 480 px, by default, from 320 to 960 px and
always leaving the message view 480 px): dragged, or focused and moved with the arrow keys, and a
double-click puts it back; this browser keeps the width. A window too narrow for it narrows the
list only while it is. Below, the list's toolbar takes one row (*Compose*, the search box with its
buttons, the shortcut sheet), and the same divider, a horizontal line, sets the list's height:
until this browser keeps one, 40 % of the height the two share, and at least room for five rows
(at the density chosen) where that still leaves the message view 240 px; from 160 to 1,200 px
whatever the window. A double-click goes back to the share. Off, the list takes the room, and an open message (or the
composer) takes the list's place, as wide as the list and the pane together, with *Back to the
list* and, above the message, the toolbar acting on the selection; Escape, or `u` with nothing
selected, goes back too (*Mark unread* then shows no shortcut). The list stays underneath, out of
reach of the keyboard and screen readers (`inert`) and scrolled where it was, and going back puts
the focus on the message's row (closing the composer, on *Compose*). A narrow screen (a phone, or
a window under 1,024 px, `lg`) always opens a message in place of the list, with Back, Escape and
`u`, whatever the choice.

**On a phone** (any window below `lg`, 1024 px, where the sidebar is no column) the list takes the
width under a **Mail header**: ☰ (*Folders*), the name of what the list shows (the folder, the
view, or *Search results*), *Search mail*, which shows the search form and puts the cursor in it
(shown anyway while a search is, when it only puts the cursor there), and *Compose*. ☰ opens the **folders drawer**, the sidebar
(account switcher, views, folder tree) sliding in from the left as a modal dialog: focus moves
into it and stays there, the page behind out of reach; Escape, a tap on the scrim, its *Close
the folders* button or a folder or view picked from it closes it, and the focus goes back to ☰;
so does the composer taking the screen (*Edit* in its *Scheduled* list). A menu opened in it
(the account switcher's, a folder's) opens inside the dialog, and Escape in a dialog opened from
it closes that dialog alone. An open
message takes the screen with Back, its action bar fixed at the foot of the screen, above the
tab bar and the home indicator, with Reply, Reply all and Forward as icons (their names kept
for screen readers) and what does not fit in *More actions*, as on desktop, the toasts showing above it; *Back to draft* and the
sent or scheduled notice stay above the message. The composer takes the screen likewise. The phone has no
rail and no shortcut button; the desktop layout is unchanged.

On a touch screen (whatever its width) a **swipe** of a message row runs the action the user chose
for that side on the *Profile* (the same on every device): left moves to Trash and right archives
unless changed; each side may instead mark read or unread, star or unstar, mark To sort or sorted,
or do nothing. It acts on that row alone, whatever is selected, through the same path as the
toolbar, with its toast and Undo. While the finger moves, the action's icon and name show behind
the row, in its colour once letting go would run it: past 35% of the row's width, or a quick
flick of at least 40 px; short of that the row goes back. A swipe, acting or not, never opens
the message; a keyboard or screen reader activation of the row right after one still does. Only
a sideways move swipes: a move first more up or down than sideways is the list's scroll, which
the browser keeps (`touch-action: pan-y`). A mouse never swipes (it drags the row to a folder),
and a side whose action is *Nothing*, or not available there (no Archive folder), does not move.
A long press to select, the selection toolbar and the open message's action bar stay the way to
act without a swipe (WCAG 2.5.1, pointer gestures), and the row moves without its glide under
*reduce motion*.

On desktop the sidebar folds to a **rail** of
icons (a button at its top, or `[`): *All inboxes* and the account's role folders (Inbox, Starred,
Drafts, Sent, Archive, Junk, Trash, those it has), each a link named with its unread count and
marked with a dot while it has unread mail. This browser keeps the choice
(`mailroom.ui.mail.folderPane`); until one is made, the sidebar is a rail below `xl` (1280 px) and
open above, and a choice that ends where that default would at the window's width goes back to
it. While folded, hovering or focusing the rail shows the whole sidebar over the list, taking no
room, until the pointer or the focus leaves, Escape, or a folder or view is picked from it.
The sidebar shows an account's folders as a tree
(the ARIA tree pattern, from `?all=true`): the folders with a role first, then the others by
name; each level opens and closes, by mouse or with the arrow keys, and this browser remembers
which are open per account (at first, those above the role folders; always those above the
folder shown). A level that holds only folders (`\Noselect`, or not listed at all) is not a
link, and a closed level shows the unread count of everything under it (All Mail and Starred
left out, as they count it again). The move picker and the role actions (Archive, Trash, Drafts,
Sent) use selectable folders only. Each listed folder has a menu (its ⋯ button, a right-click,
or Shift+F10 on it) to mark all its mail read, make a subfolder, rename or move it and delete
it, and each account a button for a new top-level folder: see
[*Folder management*](#folder-management) for what the web asks before each. A folder with
subfolders the list would read (not those with a role kept apart, below; at most 50 folders with
itself) is listed with them or without by its menu's "Show subfolders' mail here" or the button
on its row (titled "Show this folder only" while they are); this browser remembers the choice per
account and folder (a rename carries it over, a delete forgets it), the URL carries it (`sub`),
each row then names its folder, the folder's unread count becomes that of what the list reads,
and a search in that folder reads its subfolders too. A folder that no longer has such
subfolders is listed alone, whatever was chosen.
A message with HTML opens on the HTML view, unless the user reads its sender as text
(`preferred_view`; a message whose text body is empty opens on the HTML view all the same). The
Text/HTML switch stays, and a switch is remembered for the message's From
([sender views](#sender-views)): Text from then on, or HTML again. The *Profile* page lists the
senders read as text, each removable. In a dark theme the frame asks for the `dark` document
(below), following the theme as it changes; *Original colours* above it, a toggle shown only
once that document is framed (never beside an error) and marked with a check when pressed, shows
the message's own for as long as it is open, never remembered. It switches
the frame's URL fragment alone (`#mailroom-original`, back to `#mailroom-dark`): the same
document, with no request and no reload. The HTML view is an
`<iframe src="/api/v1/mail/messages/{ref}/html">` with `sandbox="allow-popups
allow-popups-to-escape-sandbox"`, never `allow-scripts` or `allow-same-origin`. Links open
top-level with `noopener noreferrer`. Security:
[`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *HTML mail*.

### Search syntax

One line of terms sets a search's criteria, folder and account: `from:ada is:unread "Q1 report"`.
Terms are separated by ASCII whitespace (space, tab, CR, LF, form feed, vertical tab) and all
must match; any other space character, a no-break space included, is part of the term. A key and
the values of `is:` and `has:` ignore case; double quotes keep spaces in a value and drop out of
it (`from:"Ada Lovelace"`), with no escapes.

| Term | Sets ([`POST /search`](#post-search)'s field) |
| --- | --- |
| `from:`, `to:`, `subject:` | `from`, `to`, `subject`: contains the value. |
| `is:unread`, `is:read` | `unread` true, false. |
| `is:flagged` | `flagged` true. |
| `is:to_sort` | `to_sort` true: marked To sort. |
| `has:attachment` | `has_attachment` true. |
| `after:YYYY-MM-DD`, `before:YYYY-MM-DD` | `since` (that day on), `before` (up to that day, not included), compared as those fields are. |
| `in:` | `folder`: a folder path as the server names it, or `*` for every folder. |
| `account:` | `account`: an id, `*`, or an account's name, else its login, ignoring case (refused when no account or several have it). |
| any other word | `text`, the words joined by one space, a key the syntax does not know included (`label:x`, a URL). |

Refused, never quoting the query: a quote not closed (`unterminated_quote`), a key without a value
(`empty_value`: `from:` alone, `from: ada`, `from:""`), an `is:` or `has:` value not above
(`bad_value`), a date that is not a real `YYYY-MM-DD` (`bad_date`), and one criterion given two
different values (`conflict`: `from:a from:b`, `is:read is:unread`). The backend's parser
(`mailroom.mail.search_syntax`) and the web client's are held to the same vectors,
`web/src/test/search-syntax-vectors.json`. MCP's `messages_search` takes it as `query`
([`mcp.md`](mcp.md)).

The web client's search box takes it, read as the user types: each term shows as a chip (*From:
ada*, *Unread*, a word) whose ✕ removes it from the line, and a line at fault says why under the
box, in the user's language, and is not searched. The form behind *More options* shows the same
search and writes each change back into the line: a criterion's first term (or a term at fault
of its key, such as a bad `after:` date) rewritten in place, its others dropped, and a box
unticked drops its term (`is:read` is only typed); an account by its name, else login, when
that names it alone, else its id; the account and folder shown are the line's default, so
choosing them drops `account:` and `in:`. A quote left open keeps the form from being edited
until it is closed. A search from the URL is shown as a line again, its words first; Gmail's own
search stays a field of the form.

## Slice B: organize

Slice B lets the user change a message's flags and folder from the web client and the MCP tools.
Behaviour is mailroom-core's `mail.organize` (`move_messages`, `archive_messages`, `junk_messages`,
`trash_messages`, `set_flags`), itself the behaviour of mailroom-mcp's `organize` tools.

### Actions

- **Move** to a folder of the message's account, by path, or of
  [another of the user's accounts](#moving-to-another-account). Out of Gmail's All Mail, which
  keeps every message, a move only adds the destination's label, but into Trash or Spam.
- **Archive:** move to the account's folder with role `archive`. The web toolbar hides the action
  for an account without one. Out of Gmail's All Mail it changes nothing: `invalid_argument`.
- **Report spam:** move to the account's folder with role `junk`, which trains the account's Junk
  as any move there does ([Junk training](#junk-training)); audited as `junk`. REST and the web
  only: an agent moves to the Junk folder by its path.
- **Trash:** move to the account's folder with role `trash`. This is never a permanent delete:
  organizing deletes no mail (deleting a whole folder is [its own action](#folder-management)).
- **Flag:** set or clear `\Seen` (read/unread), `\Flagged` (star/unstar) and the
  [To sort](../GLOSSARY.md) keyword `$MailroomSort`; `seen`, `flagged`, `to_sort`, at least one.
  Only these can be changed. To sort is `unsupported` in a folder whose server keeps no keywords
  (and on Gmail); the accounts listing's `keywords` says which do. The answer reads them back
  per message.

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
- `destination_account`: on a move to another account, that account (`new_refs` are its), else
  `null`.
- `junk`: `"spam"` or `"ham"` when the move trained the account's Junk (below), else `null`.
- `flags`: for the flag route, each message's `seen`, `flagged` and `to_sort` read back (`null`
  when the read failed; the change stands).
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
| Report spam, and the account has no Junk folder | `unsupported`: "<account> has no Junk folder." |
| None of `seen`, `flagged`, `to_sort` given | `invalid_argument`: "Give `seen`, `flagged`, `to_sort` or several." |
| `to_sort` in a folder that keeps no keywords | `unsupported` |
| The server has neither MOVE nor UIDPLUS | `unsupported` |
| A move without MOVE copied the messages but could not remove them from the source | The connection's failure code (`server_error` for a refusal, as something was written), with `new_refs`: "They were copied to the destination but may not have been removed from this folder: do not repeat the move. New refs: …" |

INBOX is matched case-insensitively, as everywhere.

### Never expunge

Organizing deletes no mail, and Mailroom expunges nothing it did not move, apart from
[the own-draft exception](#the-own-draft-exception); mail is lost for good only deliberately: a
filter's `delete`, [a move to another account](#moving-to-another-account) asked to delete the
originals once copied, and a [folder's delete](#folder-management). With the `MOVE` extension a move
is one command. Without it but with `UIDPLUS`, it is `UID COPY`, `UID STORE +FLAGS \Deleted` and
`UID EXPUNGE` of **exactly those UIDs**. A server with neither is refused (`unsupported`) rather
than risk a plain `EXPUNGE` that would remove other messages already marked `\Deleted`.

### Moving to another account

`POST /messages/move-to-account` moves messages to a folder of another of the user's accounts
(mailroom-core's `move_to_account`, [`../core.md`](../core.md) — *Moving to another account*).
`removal` says what becomes of the originals: `trash`, to their account's Trash (one already in
Trash stays there), or `delete`, for good.

- **Each message** is copied whole with its flags and arrival date, the copy confirmed by its
  `Message-ID` (APPENDUID, else a search; on Gmail its own search), and only then removed from
  where it was. One already in the target folder (the same `Message-ID`) is not copied again, so
  a move cut short (a deadline of 55 s, the connection lost) can simply be repeated.
- **Delete for good** expunges exactly the moved UIDs once their copies are confirmed (UIDPLUS
  required); on Gmail, where that would only remove a label, the originals go to Trash and are
  expunged there. It is never a default: the request names it, the web asks for confirmation and
  MCP has a destructive tool of its own.
- **Refused before anything is written**, with the [batch](#actions) checks: a target account
  that is not the user's (404 `not_found`) or is a ref's own (`invalid_argument`: that is a
  move), a target folder that is missing (`mailbox_missing`), Drafts or Sent
  (`invalid_argument`), `delete` without UIDPLUS or `trash` without a Trash folder on a source
  (`unsupported`); and per folder a message with no `Message-ID` (`unsupported`: its copy could
  not be confirmed) or over the user's `move_max_mb` (`too_large`, 413; 25 MB by default, at
  most 50: the message is held whole while it is copied).
- **A folder stops at its first error.** Originals whose copies are confirmed but which could not
  be removed are in both accounts: the group's error says so, not to move them again, and gives
  their `new_refs`; the call then answers `200` with its groups even when none succeeded, so the
  client learns it. One whose copy could not be confirmed stays where it was.
- **Audit:** each message moved is recorded twice, as `move_to_account` in each account: the
  original's record names the `destination_account` and how it was `removed`, the copy's its
  `source_account` and `source_folder`. A delete for good refused whole is recorded too, once
  per message, with the refusal's code.

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

- **Multi-select:** a checkbox per row (Shift+click sets every row from the one clicked before
  to this one, as this one now is), and a select-all for the loaded rows. A row's checkbox shows
  while the row is hovered or holds the focus, and every row's once one is selected; hidden, it
  keeps its place and the keyboard still reaches it. On a touch screen it shows only once a row is
  selected, taking no width before (screen readers still reach it): a finger held still on a row
  selects or unselects it, instead of the browser's or the row's menu, and the lifted finger
  opens nothing. The open row's highlight takes its whole width, its checkbox's too.
- **Density:** comfortable rows (a sender line, the subject and the message's opening), or
  compact ones with less padding and no opening, as the user chose on the *Profile* (the same
  on every device).
- **Select all matching:** once every loaded row is selected and the list or search has more, a
  line above the list says so and, from [`POST /messages/count`](#every-message-a-search-matches),
  offers to select all the messages it matches (a folder's list being a search with no criteria);
  past 5,000 it says to narrow the search instead, and when the count fails it says so. Then the
  toolbar says *All N matching selected*, every row shows checked and counts as selected (its
  menu and a drag act as on the selection), and changing the selection or any action on rows
  leaves this mode. Only the flag changes, Move to… and Trash act on them; the
  other actions are greyed out, saying why, and their keys do nothing. Since the loaded rows do
  not tell which way a flag change should go, each is offered both ways: Star and Unstar, Mark To
  sort and Mark sorted, Mark as read and Mark as unread (the keys set: `s` stars, `t` marks To
  sort; `u` marks unread). Each asks first in a dialog stating how many messages it would change,
  counted by the server with the action (for a flag change, only those not already so; for a
  move, over the folders it moves from), then calls
  `POST /messages/bulk` with that count, and again with each answer's `remaining` until none
  remain, showing a progress bar and a Stop that takes effect between calls. A `count_changed`
  refusal states the new count and asks again; `too_many` and a folder that failed end it, saying
  how many were left unchanged. At the end the list and the folders are read again and the
  selection cleared; done in full, the toast says so, without Undo.
- **The message's action bar**, at the top of the open message (fixed at the foot on a phone): Reply, Reply all and Forward
  as labelled buttons; its triage actions (the toolbar's, below) as icon buttons whose name and
  tooltip say the action and, while nothing is selected (the keys then act on the open message),
  its key; and *More actions*, a menu of the rest (Print, Download
  .eml, Filter messages like this, Mute or Unmute conversation, Snooze). It keeps to one row: as
  the pane narrows, the buttons that do not fit move to the top of *More actions*, still naming
  their key, To sort first, then Report spam, Move to…, Move to another account…, Reply all and
  Forward; Reply, Archive, Trash, Star and Mark read or unread stay, giving way only on a bar too
  narrow even for them (Reply never); on a phone, the same at the screen's width. It acts on the open
  message (or the conversation its row stands for) whatever is selected in the list; with a
  selection its move pickers are its own, Escape closing them without clearing the selection.
- **Toolbar** above the list, shown only while messages are selected and acting on them: Archive
  (hidden when the account has no selectable archive-role folder), Report spam (likewise without a
  junk-role one), Trash, Move to… (a picker over
  the account's selectable folders, Drafts, Sent and the folder the messages are in left out),
  Move to another account… (when the user has an account none of them is in: the account, then
  whether the originals go to Trash, the default, or are deleted for good, then the folder;
  deleting for good asks first, naming how many and from which account to which),
  Star/Unstar, Mark To sort/Mark sorted ([To sort](../GLOSSARY.md)), Mark read/unread.
- **A context menu on each row** (a right-click, or Shift+F10 or the context-menu key on it)
  offers the toolbar's actions, with Reply, Reply all, Forward, Print and Download .eml when it
  acts on one message,
  each naming its key. On a selected row it acts on the selection; on any other row, on that row
  alone, the selection left as it was, but for Move to… and Move to another account…, whose picker
  makes the row the selection. Shift+right-click keeps the browser's own menu. The toolbar, the
  shortcuts, the message's action bar and these menus read one list of actions; the keys act on
  the selection, or on the open message when nothing is selected.
- **Drag and drop:** a row dragged onto a folder of the sidebar's tree moves the selection when the
  row is in it, else that row alone; a folder they cannot go to (one Move to… leaves out, a
  level that holds only folders) does not take the drop, and one that does is highlighted while
  the drag is over it. A folder of their own account is the toolbar's move, with its Undo; a
  folder of another account (from a search) opens the Move to another account… picker on that
  account's folders, focus on the folder dropped on and the originals going to Trash, so nothing
  moves until the user picks; a drop never deletes for good. A selection spanning several
  accounts (from a search) takes no drop, as Move to… is not offered for it either. From the
  keyboard, `v` is the way.
- **A star on each row** stars or unstars that one message, whatever is selected or open, without
  opening it: shown over the row's end (taking no width, the date under it showing none of
  itself) while the row is hovered or holds the focus, never on a touch screen (where the
  message's actions star it), and always there for screen readers, a toggle button named *Star*
  whose pressed
  state says whether the message is starred (its tooltip says *Star* or *Unstar*). While another
  action runs it is disabled and looks it, and a click sends nothing. Its result shows as any
  action's (below).
- **A To sort mark on each row**, next to the star, marks that one message To sort or sorted the
  same way: a toggle named *To sort*, filled when marked (its tooltip says *Mark To sort* or
  *Mark sorted*). A starred or To sort row shows it by small marks next to its date (a star in the
  star colour, a bookmark), named *Starred* and *To sort* for screen readers.
- Where the account's server keeps no keywords (the accounts listing's `keywords` is `false`),
  the toolbar's To sort action and the row's mark are greyed out, their tooltip saying why, and
  neither they nor `t` send anything; where it is not known, they are offered and the server's
  `unsupported` is shown as any failure.
- Results update the list in place: moved or trashed rows disappear, flags change on the row, and
  failed groups are shown with the mail error wording; a group of a move to another account
  whose copies were made but whose originals stayed says they are in both.
- **Undo:** an action that went through in full says so in a toast at the foot of the screen,
  which stays across a change of folder or search and closes after 8 s, the clock paused while
  the pointer is on it or focus is in it; a live region announces it. An action's failure stays a
  notice under the toolbar, without Undo, and a new action closes the toast. For a move, archive,
  report of spam or trash the toast offers **Undo**, which moves each source folder's messages
  back from the `new_refs` the action answered (50 refs a request) and reads the list again; when a group
  answered no `new_refs` (or fewer than it moved) the toast says it cannot be undone instead.
  For a flag change (star, To sort, read) Undo sets back the flags it changed on each message,
  the refs being the same. A move to another account offers no Undo. Undo sends its own requests,
  audited as any action of the user's: undoing a move out of Junk is a move into it, which trains
  the account as that move would ([Junk training](#junk-training)). Undo's own failure shows in
  the toast, which says it did not take back every change. Closing the toast puts focus back
  where it was before the toast took it.
- **A filter after a report of spam:** where the instance runs filters, a report of spam that went
  through in full, its messages all from one sender of one account, offers under the toolbar a
  filter sending that sender's mail to Junk (`from_is` → `junk`), saying it acts only while its
  rule set and the account's Run filters are on. Nothing is added unless the user chooses *Add the
  filter*: it goes to a rule set of the account's `INBOX` that runs by itself (one acting on new
  mail, an enabled one first), else to a new one, made `new_mail` and disabled as any new rule set
  (a retry after the filter failed adds to that one). Then a disabled rule set and Run filters off
  are each named with a button turning it on; neither is turned on unasked. Undo, *No thanks* or
  another report of spam takes the offer away.
- A selection over 50 refs is sent 50 at a time; a refused batch is shown as failed groups and the
  rest are still sent; only when every batch is refused is the outcome worded as that refusal.
- **Keyboard shortcuts** on `/mail`, the screen's own keys and the action registry's:

| Key | Action |
| --- | --- |
| `j` / `k` | Next / previous message, scrolled into view |
| `x` | Toggle selection of the current message |
| `[` | Fold the folder pane to its rail, or open it again (desktop) |
| `Escape` | Close the picker, else clear the selection, else go back to the list from a message open in its place (reading pane off, or a phone) |
| `?` | Show the shortcut sheet |
| Ctrl+K / ⌘+K | Open the command palette |
| `r` / `a` / `f` | Reply / reply all / forward ([below](#keyboard-shortcuts)) |
| `c` | Compose a new message ([below](#keyboard-shortcuts)) |
| `e` | Archive |
| `!` | Report spam |
| `#` | Trash |
| `v` | Open the move picker |
| `s` | Star or unstar |
| `t` | Mark To sort or sorted |
| `u` | Mark unread; with nothing selected and a message open in place of the list, go back to the list |

  Shortcuts are ignored while focus is in a text field (an input that takes text, a textarea, a
  select or a contenteditable element), with Ctrl or Meta held (AltGr aside), and with Alt on a
  letter key: a `#` typed with AltGr or Option, as on an Italian keyboard, still trashes. A held
  key repeats only `j` / `k`. They do not reach the page while focus is inside the HTML view's
  frame. Every control has an accessible label; strings are in the `mail` namespace in English
  and Italian.

- **The shortcut sheet** (`?`, or the keyboard button beside *Compose*) is a dialog listing every
  key of this table in three groups (moving around, writing, acting on messages), read from the
  action registry and the screen's own keys, so it cannot drift from what the keys do.
- **The command palette** (Ctrl+K, ⌘+K on a Mac; again to close) is a dialog with a combobox over
  a list (the ARIA combobox and listbox pattern: ↑ and ↓ move, wrapping, Page Up and Page Down go
  to the first and the last, Enter or a click chooses, Escape closes). It offers, in groups: the
  registry's actions that can run now on the selection or the open message (as the toolbar lists
  them, with their keys), every folder that can hold mail of every account (its account beside
  it), the accounts, and the app's pages as the nav lists them. Each word typed must start a word
  of an entry (a folder path's levels are words) or occur in it, or have its letters in order in
  one of its words, case and accents aside; the best matches come first. With nothing typed, the
  last five choices come first (kept in this browser only; none when storage is refused), those
  that can still be chosen. It has its own key, as the shortcuts take none with Ctrl or Meta: it
  works in the search fields, but not while typing in the composer nor over another dialog. Its
  input is marked as a search field that password managers should not offer to fill.
- **Cache:** what the screen reads (accounts, folders, list pages, messages) goes through an
  in-memory cache (TanStack Query), never persisted and dropped at sign-out. Conversations are
  read again each time they are shown, and an account's folders whenever the sidebar mounts (the
  live stream, opened again, reads them only when they were not read since it closed). A list's
  first page is kept for five minutes once nothing shows it, so coming back to it (from Home,
  another folder) shows it at once while it is read again behind it; only a page read while the
  list is shown counts as the server's flags. The accounts are kept between visits to Mail, which
  shows them at once and reads them again behind them; the held sends are read on mount when
  older than 30 seconds. A message read is kept for five minutes once nothing shows it, and read
  again when opened after 30 seconds (after 5 when it says its sender's remote content is shown),
  shown meanwhile as kept; the screen's own flag changes (a
  mark-read, a star, To sort, Undo, a folder marked read), the flags a list read again carries,
  and the sender choices (remote images, Text or HTML) are written into it, and a message moved
  away is dropped from it. A kept read never decides remote content: see *Remote content* above.
  A row the mouse rests on, a row pressed, and the row after the one j or k opened are read ahead
  the same way, and a read once asked always lands in the cache, so the message opened from it
  (the first one too) is not read twice; reading never marks a message read, only opening it does.
  The cache reads nothing on its own: no retry of a failed read, no new read on focus or reconnect.
- **Opening another message** keeps the one shown on screen, dimmed, out of reach (`inert`) and
  `aria-busy`, until the next one has loaded, a status line telling a screen reader it is loading;
  the keys and the action bar act on nothing meanwhile. Only the first message opened in the pane
  shows *Loading the message…* in its place.

### REST routes

JSON bodies (unknown fields are 422), CSRF as for every state-changing route.

| Route | Body |
| --- | --- |
| `POST /messages/move` | `{"refs": ["…"], "folder": "Archive/2025", "source_folder": "INBOX", "source_folders": {"…": "Work"}}` |
| `POST /messages/move-to-account` | `{"refs": ["…"], "account": "home", "folder": "INBOX", "removal": "trash"}` (`removal`: `trash` or `delete`) |
| `POST /messages/archive` | `{"refs": ["…"]}` |
| `POST /messages/junk` | `{"refs": ["…"]}` |
| `POST /messages/trash` | `{"refs": ["…"]}` |
| `POST /messages/flag` | `{"refs": ["…"], "seen": true, "flagged": false, "to_sort": true}` (`seen`, `flagged`, `to_sort` optional, at least one) |

`refs` holds 1 to 50 strings of at most 4096 characters. `source_folder` (move, archive, junk
and trash; optional) is the folder the user acted from, where a Gmail All Mail ref is acted on
([Index](#index), *Reads*); the web sends the folder it shows, none over every folder.
`source_folders` (same routes; optional) gives a ref its own, over `source_folder`: each key one
of `refs`, else 422. The web sends each row's `listed_folder` there, so a row of a view or a
search over several folders is acted on in the label folder it was listed under. Answer, `200`:

```jsonc
{
  "results": [
    {
      "account": 1,
      "folder": "INBOX",
      "ok": true,
      "count": 2,
      "new_refs": ["WzIsMSwiSU5CT1gi…"],      // null when the server gave no COPYUID; [] for flags
      "destination_account": null,          // a move to another account: that account
      "junk": null,                         // "spam" | "ham" | null
      "flags": [],                          // flag route: [{ "ref", "seen", "flagged", "to_sort" }]
      "error": null                         // or { "code": "not_found", "message": "…" }
    }
  ]
}
```

Refs in `flags` and `new_refs` are the client's to store; the source refs of a moved message are
stale afterwards. Errors: 400, 404, 409, 422, 429, 502, 504, and the answers cannot be cached
(`Cache-Control: private, no-store`).

### Every message a search matches

*Select all matching* acts on every message a list or a search matches, not only the refs the
client has loaded, with mailroom-core's `count_matching` and `act_on_matching`
([`../core.md`](../core.md) — *Organize*). The scope and criteria are those of
[`POST /search`](#post-search) without `limit` and `cursor` (`account` a number or `*`, `folder`
a path or `*`, `subfolders`, the criteria); a list is a search with no criteria. The action is
`{"kind": "flag", "seen", "flagged", "to_sort"}` (at least one), `{"kind": "move", "folder"}`
(of each message's account, never Drafts or Sent) or `{"kind": "trash"}`.

| Route | Body | Answer |
| --- | --- | --- |
| `POST /messages/count` | `{"account": 1, "folder": "INBOX", "from": "shop", "action": {"kind": "flag", "seen": true}}` (`action` optional) | `200` `{"total", "failures"}` |
| `POST /messages/bulk` | `{"account": "*", "folder": "INBOX", "subject": "invoice", "action": {"kind": "trash"}, "expected": 1234}` | `200` `{"done", "remaining", "results", "failures"}` |

- **The count** is of what the action would still change, over the folders it acts on: with
  `action` a flag change leaves out the messages that already have it and a move the folders it
  does not move from (below); without one, every match. The client shows the count with the
  action, the user confirms it, and the client sends it as `expected`, an integer from 0 to
  5,000; a count taken without the action can differ, and is then refused as `count_changed`.
- **Each bulk call** searches again (`UID SEARCH` per folder), refuses with `count_changed`
  (409, carrying `matching`, the count now) when the count is not `expected`, and with
  `too_many` (409) past 5,000, writing nothing; otherwise it acts on at most **500** messages,
  newest first in each folder, the folders in search order, by UID set on the paths of the
  organize routes (a move's `MOVE`, or UIDPLUS's copy and `UID EXPUNGE` of exactly those UIDs;
  a trash is a move; flags by `STORE`). `remaining` is the count to send as `expected` next:
  the client repeats the call until it is 0, or stops at a group's failure. `results` holds one
  group per folder acted on, as the organize routes answer them (`flags` always `[]`); when
  every group failed, the call answers the first group's error instead.
- **A move over several folders** (`folder` `*`, or `subfolders`) leaves its destination out of
  the folders searched, so a move from a search of every folder works, and All Mail too, out of
  which nothing moves (Gmail keeps the message there). Over every folder of an account with All
  Mail, where a search reads All Mail alone, the move reads instead the folders a message can
  leave: the inbox and the account's own folders (Gmail's labels), not Starred, Important, Sent,
  Drafts, Junk or Trash. A message there under several labels is counted, and moved, once per
  label; one only in All Mail (archived) is not moved.
- **What would still match is refused** (`invalid_argument`), so that a repeated call never acts
  on a message twice: a move into the one folder searched, a move out of All Mail searched alone,
  a trash with Trash among the folders searched.
- **Over every account** (`account` `*`) an account or folder that cannot be searched, or an
  account the action cannot apply to (no Trash, no such destination), is in `failures` as in a
  search (`folder` `null` when the account failed before a folder was chosen; an account without
  a usable password `password_missing`) and the others are counted and acted on. Over one
  account, its failure, or that of its one folder, is the answer.
- **Audit:** each message acted on, or failed, is a record of its own, with action `bulk_flag`,
  `bulk_move` or `bulk_trash` and the fields of the organize records; a refused call is one
  record per account searched (`folder`, the scope's, and `messages`, its count), and a failed
  account one record with its code.

Errors: 400 `invalid_argument`, 404 `not_found` (account) and `mailbox_missing`, 409
`count_changed`, `too_many`, `auth_failed` and `password_missing`, 422, 429, 502, 504. There is
no MCP tool.

### Snooze

<a id="snooze"></a>**Snooze** takes one message out of its folder until a time, and the worker
brings it back ([`../GLOSSARY.md`](../GLOSSARY.md)):

- **Snoozing** (`mailroom.mail.service.snooze`) reads the message and the account's `Snoozed`
  folder (mailroom-core's `snooze_target`), creating it at the top level when missing (a label on
  Gmail; audited `folder_create`). Refused `invalid_argument` for a message in Snoozed already,
  in Gmail's All Mail, Drafts, Sent, Junk or Trash (it could not come back there), or without a
  `Message-ID`; for a time without its offset, past, or more than a year ahead; `already_snoozed`
  (409) for a message of the account with that `Message-ID` snoozed already. Then, in one
  transaction, an `app.snooze` row (the `Message-ID` and size, the folder it came from, the
  Snoozed folder, its subject and sender for the list, when it comes back, who snoozed it) and the
  worker's job `wake_snooze`, deferred to that time, without the mailbox's lock (waiting, it
  would hold back the mailbox's other jobs); then the move into Snoozed (`move_messages`, audited
  `snooze`). On Gmail a snoozed message is always out of INBOX: the move from INBOX drops its
  label, and one snoozed from a label's view, which the move takes off that label only, has
  INBOX's label taken off too (`leave_inbox`; one that stayed is logged, the snooze kept). A move that fails drops the row and its
  job; one that timed out or lost the server (`timeout`, `unreachable`), which may have been done
  all the same, keeps the row with that code and wakes it 2 minutes later instead, which brings the
  message back or records it missing. A Gmail label view's ref is acted on in the folder it
  was listed under (`source_folder`), as a move's. Other clients see the message in Snoozed
  meanwhile.
- **Waking** (`mailroom.worker.snoozes`): at its time the job finds the message in Snoozed by its
  ref there, else by its `Message-ID` and size (`find_snoozed`, never by subject; on Gmail
  `X-GM-RAW rfc822msgid:`), marks it unread, then moves it back to its folder, or to INBOX when
  that folder is gone, audited `wake` as whoever snoozed it, and the row goes. On Gmail it always
  comes back to INBOX (the move adds INBOX's label), and gets back the label it was snoozed from
  when that was another. **A woken message is not new mail:** its new refs go into the filter
  runs' ledger ([`filters.md`](filters.md#moves-to-another-account), the loop guard) as soon as the
  move answered, so the `new_mail` rule sets its arrival queues, a muted thread's mark-as-read,
  every later run of that folder and the vacation reply ([*Vacation reply*](#vacation-reply))
  leave it alone, unread where it was; the live updates may still notify that it is back. A
  message no longer in Snoozed (another client moved or deleted it) is recorded (`wake`,
  `not_found`) and the snooze dropped; one snoozed in the last 2 minutes is tried again instead,
  as its move may still be on its way. A wake that fails (the server
  unreachable, a missing password) keeps the row with its code and is tried again 15 minutes
  later. A job lost with a stopped worker is queued again when the next one starts; snoozes go
  with their account and their user, the messages staying in Snoozed. Renaming Snoozed, the
  folder a message came from, or a folder above either, in Mailroom carries the snoozes along.
- **The web:** the message's *Snooze* (beside reply, print and mute; and in a row's menu) asks
  until when: later today (18:00, until 17:00), tomorrow (08:00), next week (Monday 08:00), or a
  date and time, all in the user's time zone. The message leaves the list, its pane closes, and a
  toast says until when, with Undo, which brings it back now. The sidebar's *Snoozed* entry, with
  a count, lists the snoozed messages (subject, sender, account, when and where they come back, a
  failed wake's reason) with *Bring back now*.
- **Routes** (CSRF as for every state-changing route):
  - `POST /messages/{ref}/snooze` `{"until", "source_folder"}` (`source_folder` optional): 201
    `{snooze_id, account, subject, sender, folder, until, code}`; 400, 404, 409
    `already_snoozed`, 422 for a time without its offset; the move's errors.
  - `GET /snoozes`: `{"snoozes": [...]}`, soonest back first.
  - `POST /snoozes/{id}/wake`: brings it back now (its job runs at once); 200 the snooze; 404
    once it is back, or another user's.

### MCP

The tools `messages_move`, `messages_trash` and `messages_flag` (group `mail_organize`,
[`mcp.md`](mcp.md)) take the same refs (up to 50, any folders) and return mailroom-core's text
inside the untrusted envelope. `messages_snooze` (same group) [snoozes](#snooze) one ref until
`until` and says until when in the user's time zone and where it comes back, inside the
envelope. When every group fails, the tool error is mailroom-core's text inside the envelope
too: a lone group's error message, or the per-folder text when there were several. mailroom-core returns an audit item per message
(account, folder, UID, `Message-ID`, sender address, a SHA-256 of the subject, destination, junk,
result), which the app keeps in the user's audit trail, from the web as from MCP
([`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Audit trail*); its logs
keep counts and codes only, never these fields.

## Folder management

The user creates, renames and deletes an account's folders; mailroom-core's `create_folder`,
`rename_folder` and `delete_folder` ([`../core.md`](../core.md) — *Folders*), each on one
connection with no mailbox selected, every check there. Marking a folder read is
`mark_folder_read` (*Organize* there), on the folder selected read-write.

- **Create** a folder under a parent (`null`: the top level), subscribed. Refused when it exists
  (`folder_exists`) or the parent is not listed (`mailbox_missing`).
- **Rename**, which also moves a folder in the hierarchy: its subfolders, their mail and their
  subscriptions go with it. Every rule set and filter action (`move_to`, `trash`) of the account
  that names the folder or one under it is pointed at the new path, enabled ones included: they
  keep acting on the same folder ([`filters.md`](filters.md) — *Folders renamed or deleted*). The
  answer lists them, and apart (`all_accounts_rule_sets`) the rule sets for all accounts naming it,
  which a rename on one account leaves as they are; the web's notice of the rename says how many,
  their moves there failing on this account until they are changed. When that database step fails,
  the rename stands and the answer says `rule_sets_updated: false`; nothing renames the folder back
  (that would be another write that can fail).
- **Delete** a folder **and its mail, for good**: not to Trash, and nothing recovers it. The
  caller sends `messages`, the count it showed the user and the user confirmed; the server reads
  the count again (STATUS) and refuses with `folder_changed`, carrying the new `messages`, when
  it differs. Refused, before the folder is touched, while any rule set or filter action of the
  account, or of a rule set for all accounts, names the folder or one under it (`folder_in_use`, listing up to 10 of them as
  `rule_sets: [{id, name}]`), and while the folder has subfolders (`\Noselect` ones too). On
  Gmail a folder is a label: its mail stays in All Mail. Mail that arrives between the count and
  the delete goes with the folder; IMAP cannot close that window, the count keeps it to seconds.
- **Mark read** every unread message of a folder. The caller sends the `uid_validity` and
  `uid_next` of the listing the user confirmed from: only messages below that `uid_next` are
  marked, so mail that arrived since stays unread, and a changed UIDVALIDITY is refused with
  `stale_reference`. A Gmail label's folder (INBOX included) counted from the [index](#index)
  has no such values in its listing: the caller sends both `null`, and the server reads the
  folder's own STATUS at that moment instead, so mail that arrived between the listing and the
  call is marked too. Both `null` for a folder that is not a Gmail label's, or only one of them
  `null`, is `invalid_argument`. The answer counts the messages marked. A folder marked read
  with its subfolders is one call per folder, each with its own listing's values and its own
  audit record; whether that is what the user gets first is theirs to choose
  (`mark_read_subfolders`, [`nonfunctional-requirements.md`](nonfunctional-requirements.md) —
  *Sign-in*).

**On the web** the actions live in the sidebar's folder menus. What the server would refuse is
disabled with the reason (INBOX and role folders, a folder holding one for a rename, a folder with
subfolders for a delete), and names are checked before they are sent. The rename dialog says
that the rule sets follow the folder, and after a rename the screen follows the folder shown to
its new path. **The delete is a confirmation:** the dialog counts the folder's messages afresh
and asks "Delete “X” and its N messages for good? They do not go to Trash and cannot be
recovered." (Cancel focused; the Gmail note on an account with All Mail). On `folder_changed` it
shows the new count and asks again; on `folder_in_use` it lists the rule sets, each a link to
its page. A deleted folder on screen gives way to the Inbox. **Mark all as read** is enabled
while the folder has unread mail, its listing's UIDs sent back as they came (`null` for a
Gmail label's folder counted from the index);
a second click while one is under way sends nothing more. A folder with subfolders a list of it
would read (`with_subfolders`'s: none with a role kept apart, nor under one) offers it twice,
"with subfolders" and "this folder only", the user's default first: the whole subtree unless they
chose the folder alone on their *Profile* page (`mark_read_subfolders`); a level holding only
folders offers the subtree alone. With subfolders, each folder the listing counted unread mail in
is marked by a call of its own with its own `uid_validity` and `uid_next`, one after the other,
and one refused leaves the others to go on. Above 50 unread in all it asks first, stating the
count (and, with subfolders, each folder and its count) of the listing it sends the values of.
Then the folder counts and the list are read again, and a status line says how many were marked;
a folder refused is named with the reason, and on `stale_reference` the folders are listed again
and the user starts over from the menu.

Names: 1 to 255 characters, no control characters, no delimiter, `*` or `%`, no leading or
trailing space, not `INBOX` at the top level; at most 32 levels and 1,000 characters, for every
subfolder a rename moves too (`invalid_argument`). INBOX and the folders with a role (Sent,
Drafts, Trash, Junk, Archive, All, Flagged, by special-use or by name) are never renamed or
deleted, nor a folder holding one renamed (`invalid_argument`).

Every call is recorded in the audit trail, refused ones included: `folder_create` (`folder`),
`folder_rename` (`folder`, `destination`, the `rule_sets` and `filters` ids it changed and any
`all_accounts_rule_sets` it left, or `rule_sets_error: true`), `folder_delete` (`folder`, `messages`), `mark_folder_read` (one record
for the folder: `folder`, and `messages`, the count marked). Its log lines, `mail_folder_created`,
`mail_folder_renamed`, `mail_folder_deleted` and `mail_folder_marked_read`, carry `via`,
`account_id`, `outcome`, `duration_ms` and counts (`rule_sets`, `filters`, `rule_sets_updated`,
`messages`), never a folder's name. The standing safeguards of a permanent delete:
[`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Security*, *The app*.

| Route | Body | Answer |
| --- | --- | --- |
| `GET /accounts/{account_id}/folders?all=true` | | The [listing](#get-accountsaccount_idfolders) with the `\Noselect` folders too |
| `POST /accounts/{account_id}/folders` | `{"parent": "Projects", "name": "2026"}` | `201` `{"account_id", "path"}` |
| `POST /accounts/{account_id}/folders/rename` | `{"folder": "Projects/2026", "parent": null, "name": "2026"}` | `200` `{"account_id", "folder", "path", "rule_sets", "filters", "rule_sets_updated", "all_accounts_rule_sets"}`: `folder` the old path, `path` the new one |
| `POST /accounts/{account_id}/folders/delete` | `{"folder": "Projects/2026", "messages": 12}` | `200` `{"account_id", "folder", "messages"}` |
| `POST /accounts/{account_id}/folders/mark-read` | `{"folder": "Lists", "uid_validity": 1700000000, "uid_next": 812}` | `200` `{"account_id", "folder", "marked"}` |

Paths travel in the body, since they hold the delimiter. Unknown fields are 422; `parent` is
required (`null` for the top level), so that a rename names where the folder goes; `folder` and
`parent` are at most 1,000 characters and `name` 255; `messages` is a JSON integer, 0 or more;
`uid_validity` and `uid_next` JSON integers from 1 to 2³², or `null` (both required). CSRF as
for every state-changing route. Errors: 400 `invalid_argument`, 404 `not_found` (account) and
`mailbox_missing`, 409 `folder_exists`, `folder_changed`, `folder_in_use` and
`stale_reference`, 422, 429, 502, 504. A create, rename, delete or mark read that outlives the 30 s deadline answers
`timeout` while the server may finish it: the next listing shows the outcome, and a rename that
finished late still points its rule sets at the new path.

The MCP tools `folder_create`, `folder_rename` and `folder_delete` (group `mail_folders`,
[`mcp.md`](mcp.md)) are these routes for an agent; `folder_delete` is destructive and only for the
user's explicit request.

## Slice C: drafts and send

Slice C lets the user write mail in the web client and lets agents do the same through the MCP
tools. It is the one slice that sends mail, so its rules are the strictest: read
[Send rules](#send-rules) before changing any of it. Behaviour is mailroom-core's `mail.compose`,
`mail.reply`, `mail.attachments`, `mail.drafts` and `send` modules, themselves the behaviour of
mailroom-mcp's `draft` and `send` tools. A message is **plain text or HTML** (rich text, tier 2 in
[`../REQUIREMENTS.md`](../REQUIREMENTS.md)): the routes and the MCP tools take HTML with inline
images, and the web composer writes either ([Web editor](#web-editor)).

### Compose

- **Modes:** a new message, **reply**, **reply all** and **forward**. A reply or forward starts
  from the message open in the client; the web client asks `POST /drafts/prefill` for the fields
  and shows them in the composer. Prefilling writes nothing.
- **Reply** is addressed to the original's `Reply-To`, else its sender; **reply all** adds its
  other recipients, minus the account's identities (every address it can send as, not just the one
  answering). A reply to one's own message, from Sent for example, has nobody left that way and
  goes to its To. The subject gets one `Re: `, the threading headers (`In-Reply-To`,
  `References`) continue the thread, and the body quotes the original, its date in the user's
  time zone (UTC until they set one), as a forward's header block has it. The original is read up to
  2 MiB and its text is then cut to 20,000 characters, in a quote and in a forward alike; a longer
  one is cut, never refused. In HTML the quote is the attribution line, then a
  `<blockquote type="cite">` of the original's HTML, sanitised as a send is (its text when it has
  none, or when its HTML is over 1 MiB), without its images.
- **Quote the selection:** Reply or Reply all (button or shortcut) with text selected in the open
  message's text view quotes only that text, sent as the prefill's `quote` (at most 20,000
  characters; the web cuts a longer selection), under the same attribution line, in the text and,
  as text, in the HTML. The HTML view's frame cannot be read without loosening its sandbox, so a
  selection there, or none, quotes the whole message. A forward or an unsubscribe ignores `quote`.
- **Forward** has an empty recipient list and the subject with one `Fwd: `. The body holds a
  "Forwarded message" block with the original's headers and text (in HTML, its sanitised HTML with
  its `cid:` images, whose inline parts are offered as `keep` entries with `inline: true`), and the
  original's attachments are offered as `keep` entries (the user can drop each). Reply and reply all set
  `\Answered` on the original once sent; a forward sets the `$Forwarded` keyword, where the
  server keeps keywords (not Gmail), and a failure to set it is the note
  `original_not_marked_forwarded`. No agent can set `$Forwarded` through `messages_flag`. The
  list's rows show both marks.
- **Unsubscribe by mail** (`mode` `unsubscribe`) starts a draft to the message's List-Unsubscribe
  `mailto:` link, read by the server from the message: its first address alone, and its
  `subject` (else `unsubscribe`) and `body`, percent-decoded; any `cc`, `bcc`, further `to` or
  other field it names is dropped. `answers` is null. A message without such a link, or whose
  address is not [deliverable](#compose), is 400 `no_unsubscribe_address`. It is sent like any
  draft, by the user, through the send gate. The one-click kind is
  [its own route](#one-click-unsubscribe).
- **Identity:** the composer's `From` is a choice among the account's
  [identities](../GLOSSARY.md). A reply, reply all, forward or unsubscribe by mail preselects
  the identity the message arrived at (`sent_to`), else the account's default, as MCP's reply and
  forward do; a new message uses the default.
  An address outside the identities is `invalid_argument`. The accounts API gives the web
  client each account's `identities` (`address`, `name`, `default`), its `send_mode`,
  `attachment_max_mb` (the user's limit, at most 25) and `max_recipients`, so the composer can say
  early what is not possible.
- **Signatures:** <a id="signatures"></a>each identity can have one, written on the *Profile*
  page in the same rich editor (no images: they are dropped) and kept per user, account and
  address (`app.identity_signature`) as HTML sanitised as a send is, with its text part; one
  without text is removed. They are forgotten with the account and with the user. A message starts
  with the signature of the identity it goes from, in a marked block: in HTML a
  `<div data-mailroom-signature>` (the one attribute `render.outgoing_html` keeps for it, dropped
  from a quoted or forwarded original), in text below a `-- ` line. A new message in the web
  composer has it at its end, below an empty paragraph; a prefill has it below the empty
  paragraph and above the quote or the forwarded message (its HTML has the block even without a
  signature, empty, so one can go there; an unsubscribe by mail has none). Changing `From` swaps
  it for the new identity's while the block holds the old one as it was put there; one the user
  edited stays, and in plain text only a `-- ` block standing whole is swapped. An agent's draft
  or send ([MCP](#mcp-1)) gets it too, unless `include_signature` is false. Routes:
  `GET /api/v1/accounts/{id}/signatures` lists each of the account's identities, in its order,
  as `{address, html, text}` (both empty for none); `PUT` the same path with `{address, html}`
  (at most 20,000 characters, 422 beyond) keeps one and answers it, 404 `identity_not_found` for
  an address that is none of the account's identities. Neither is under `/api/v1/mail`.
- **Recipients** are To, Cc and Bcc: each at most 50 addresses, together at most the user's
  `max_recipients` (mailroom-core settings, default 10). An address must be deliverable: one `@`,
  an ASCII local part, a domain that is ASCII or punycode. Anything else is 422
  `recipient_refused` before a connection is made. Autosave saves only the recipients the user
  has committed (a chip), so an address half typed cannot fail a save.
- **Address suggestions:** <a id="address-suggestions"></a>a recipient field is a combobox: once
  typing pauses (200 ms) it asks `POST /addresses` for what was typed and lists up to 10
  addresses, each with its name, those already in the field left out; arrows move, Enter or Tab
  picks one as a chip, Escape closes the list, and Enter with none highlighted commits the typed
  text as before. The server reads them from the [index](#index) alone and keeps nothing for
  them, over the user's indexed, ready folders: first the addresses they sent to (To and Cc of
  their accounts' Sent folders' rows, on Gmail of All Mail's rows labelled `\Sent`), then the
  people who wrote to them directly (the one From of a row that names one of their identities in
  To or Cc, Junk's rows left out); never list mail (a `List-Id`), a no-reply address
  (`noreply`, `no-reply`, `do-not-reply` as a word of it) or one of the user's identities (of
  every account). Each group is ranked by how many rows name the address, then by the latest;
  an address in both is the first group's, with the name it last went by there (none when that
  is longer than a draft's 1,024 characters). An address
  matches when it, its name or a word of its name starts with the typed text, ignoring case.
  Which folders are Sent and Junk is asked of each account's server (its folder list, at most
  5 s for all of them, each kept for 5 minutes under the same account settings, so typing does
  not list again): one that cannot say in time gives no Sent folder, so its sent-to addresses
  wait, and the others' still count. While the answer for newer text is on its way, the list
  keeps the last one, narrowed to what still matches.
- **HTML:** a draft's `html`, when given, is its body (in a save, a multipart part of its own):
  it is sanitised on the server (mailroom-core's `render.outgoing_html`: no scripts, event
  handlers, forms, frames, objects or style sheets, links only `http(s)` and `mailto`, images
  only the message's own `cid:` parts, a `data:` image turned into one) and sent as
  multipart/alternative, its text part made from it on the server
  (`text` is then not used), inside a multipart/related with the inline images it shows. An inline
  image is a new upload whose `content_ids` entry names its Content-ID, or a kept part with
  `inline: true` (the draft's own, or a forwarded message's); one the HTML does not show is left
  out.
- **Web editor:** <a id="web-editor"></a>the composer writes rich text by default, in a TipTap
  (ProseMirror) editor: bold, italic, underline, links (`http(s)` and `mailto` only: a typed
  address without a scheme gets `https://`, or `mailto:` for a mail address), bulleted and
  numbered lists, quotes, from a toolbar or their shortcuts (Ctrl/⌘ B, I, U, K for a link,
  Shift+7 and Shift+8 for the lists, Shift+B for a quote). An image pasted or dropped into the text
  goes in place as an inline upload under a Content-ID the composer makes (`<uuid>@mailroom`),
  held to the attachments' limits; another file dropped there is attached. A paste that also
  carries text or HTML pastes that, not the picture of it a word processor adds. Once a save
  replaces the version an image deleted from the text was a part of, that part goes with it, so an
  undo brings back the image's place but not the image. Only `cid:` images
  parse, so nothing pasted makes the composer fetch anything; an upload shows from the file
  itself (a `data:` URL), a kept part from its download. A reply or forward opens on the prefill's
  `html` (its quote, a forward's block with its inline images), a draft on its saved HTML; the
  editor holds only what its schema has, so formatting outside it (tables, colours, fonts) is
  flattened. A save carries `html` (and an empty `text`). A per-message switch goes to plain
  text, after a warning that the formatting, links and images go: the text is the server's
  conversion of the HTML (`POST /drafts?convert=text`, `render.html_to_text` over the sanitised
  HTML, the text part the message would be sent with; refused, and the composer stays in rich
  text, when that text is over the plain-text cap). It is the save's route, which the host's proxy
  already lets 30 MiB through to, so the conversion asks nothing more of it. Back to rich text,
  each line becomes a paragraph. A plain-text draft, an unsubscribe by mail and a draft written
  elsewhere open in plain text. The editor loads with the composer, not with the mail screen, and
  runs under the SPA's CSP: the production build fails on a chunk that evaluates strings.
- **Attachments** are new uploads (`files[]`) or parts of existing messages (`keep`: `ref` and
  `part`); an agent never passes file content (MCP). Only attachment and calendar parts of a
  message can be kept as attachments, inline parts only as inline parts, never the body parts.
  Limits are in [Limits](#limits).
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
  never on the wire), subject, the plain text or the HTML with its inline images, and the
  attachments. A header
  `X-Mailroom-Draft: 1` marks a draft Mailroom wrote; when the draft answers a message it reads
  `X-Mailroom-Draft: 1; answers=<ref>`, when it forwards one `X-Mailroom-Draft: 1; forwards=<ref>`.
  The header is stripped before the message is sent.
- **`lossy`:** a draft written elsewhere may hold what a save cannot keep (an HTML body, inline
  images, formatting): only Mailroom's own HTML drafts are read back with their HTML.
  `GET /drafts/{ref}` then answers `lossy: true` and the composer warns that saving keeps only the
  plain text and the attachments. Only a save does
  that: a draft that is sent without being edited goes out exactly as stored (an HTML body
  included, which is also what `draft_send` does) but for its Date, which is the time it goes out
  (an undo's or a send later's too, not the time it was saved), under the key its ref gives when it has no
  `compose_id`.
- **Designed mail.** A message built elsewhere (an .eml, or an HTML file with its images and
  attachments) is imported as a draft as it was built (`POST /drafts?import=eml|html`, MCP
  `draft_import`): its MIME structure and parts are kept, its own text part too (one is made from
  the HTML when it has none), and it is never composed again: a save over it is refused, 400
  `invalid_argument`, while an import may replace it or any other draft, keeping its thread,
  but for a reply or a forward Mailroom saved (400 `invalid_argument`), and `?import=kept` saves
  it again as built under new From, recipients and subject. Its HTML is still sanitised as any the
  user writes (no scripts, forms, event handlers, style sheets, or anything remote but links),
  its headers are Mailroom's (From an identity, a new `Message-ID` on its `compose_id`; To, Cc,
  Bcc and subject the request's, else the .eml's), and the marker reads
  `X-Mailroom-Draft: 1; designed=…` with what the import removed. It reads back whole (`designed:
  true`, never `lossy`) and is sent as stored, through the same Send, undo and send later. The
  import answers, and `GET /drafts/{ref}/findings` repeats, the checks to show before sending
  ([`core.md`](../core.md#designed-mail)): what was removed, images without `alt`, a `cid:` with no
  part and a part never shown, HTML over Gmail's 102 KB clip, remote images, no text part, and the
  whole message over the attachment cap.
- **Designed mail in the web client.** *Import*, beside *Compose*, takes an .eml, or an HTML file
  with its images and attachments, as a new draft of the account shown, and opens it in the
  composer. There its body is not edited: in its place are the checks, worded by `code` (the
  server's `text` for a code the client does not know, the `items` after it), and a preview
  through the message view's sandboxed frame (`GET /messages/{ref}/html`, remote images blocked),
  at the pane's width or a phone's (375 px), light or dark (`dark=1`, as the message view shows
  it in a dark theme). Its attachments are listed, with nothing to add or remove. From, the
  recipients and the subject are edited as in any draft and saved by keeping it as built
  (`?import=kept`), so Send, Undo and Send later work as for any other draft. *Send a test to me* sends it as stored to the account's default identity, subject
  prefixed `[Test]` (`POST /drafts/{ref}/test`), the draft left as it is: through the send gate
  as the user, audited and filed in Sent as any send (Gmail would file it anyway), with a new
  Message-ID, so the real send keeps its own thread. *Replace* imports a file again over the
  draft, under the From, recipients and subject the composer holds.
- **Opening** a message in the Drafts folder opens it in the composer, and does not mark it read.
  An account in send mode `off` cannot save one, so there it reads as any other message.
- **One composer** is open at a time, above the panes: sidebar and list links leave it alone, and
  opening a message puts it behind that message until the user goes back to the draft. The URL's
  `compose` param names it by `compose_id` and account, not by ref, so a reload brings it back
  from `GET /drafts/find`; if nothing was saved there is nothing to bring back.
- **Removing** a draft (`DELETE /drafts/{ref}`, or the old version of a save) follows
  [the own-draft exception](#the-own-draft-exception). On Gmail it leaves no copy in All Mail or
  Trash: a draft saved twice and then removed was gone from both (tried by hand, 2026-11-09).
- **Audit trail:** an agent's saves are recorded, the web's are not (they are the user typing);
  removing a draft and sending are, from either
  ([`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Audit trail*).
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
- **Filing is secondary.** Filing to Sent, removing the draft and marking the original are separate
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
     │                   ▲   │
     │ schedule          │   ├──────▶ failed   the message definitely did not leave
     ▼        send_at    │   └──────▶ unknown  delivery cannot be confirmed
   held ─────────────────┘
     │ cancel
     ▼
  (none, the draft in Drafts)
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
- **Held sends** (one mechanism for undo send and send later): scheduling a draft checks it as
  Send would (the draft, its recipients, an account in send mode `off`) and writes an
  `app.scheduled_send` row (user, account, the draft's ref and send key, its subject and
  addresses for the Scheduled list, who scheduled it, `send_at`, state) with the worker's job
  `send_draft`, deferred to `send_at`, in the same
  transaction; `send_at` carries its offset, a year ahead at most, and a moment past sends at
  once. A draft has one held send
  at a time (`already_scheduled`), and none once a send of it began (`send_started`). At
  `send_at` the job takes the row (`held` → `sending`, committed); unless a send of the draft's
  key began meanwhile (Send now from another tab, or an agent's `draft_send`: that send decides,
  and the row goes once it was delivered), it reads the draft, which must still be the version
  scheduled (a later save replaces it, which leaves the held send `failed`), and sends it through
  the gate as the web's Send does, with the draft's key: a job run again (the worker stopped
  between the claim and the delivery, and the next one queued it again) finds that send and
  delivers nothing. **The gate's `by` is who scheduled it, not the worker:** a held send the web
  made goes as `user`, one an agent made through MCP as `agent`, so the account's send mode
  applies to it as it would to the agent's own `draft_send` at that moment. Once the key's send was
  delivered the row goes, and the send says the rest; a refusal before it, a failed delivery, one
  not confirmed or an unexpected error (`internal`) leaves the row `failed` with its code, the
  draft in Drafts. A job that ends before the gate audited it (the draft gone or changed,
  `stale_reference`, the account unusable, `password_missing`, `internal`) writes a `send` audit
  record of its own, refused with that code and the draft's folder and UID, nothing of the
  message. Cancel, while the row is `held` (or `failed`), deletes the row and the job and
  leaves the draft; once the worker has taken it, cancel is `send_started`. The service offers
  `schedule_send`, `cancel_scheduled` and `list_scheduled` (soonest first); a held send goes with
  its account and its user.
- **Undo send:** <a id="undo-send"></a>each user's `undo_send_seconds` (`app.user`, 0 to 30,
  10 by default), picked on their *Profile* page, is how long the web's Send holds a message
  (`PUT /api/v1/auth/me/undo-send`, `{"undo_send_seconds": 10}`, a JSON integer, answers the
  user; `auth/me`'s `user` carries it; logged as `undo_send_changed`).
  `POST /drafts/{ref}/send` with `undoable: true` (and no `confirm_resend`) holds the draft as a
  held send at the server's now plus that delay, counted from once the draft was checked, and
  answers 202 with it (`remaining_ms`: how long it is still held, by the server's clock); asked
  again while it is held or being sent (a lost answer), the same one, and `already_scheduled`
  while the draft is held for later than that. With the delay at 0, or once a send of the draft
  began or the draft is gone, it is the plain Send above. The web closes the composer and shows
  *Sending to … in 7 s* with **Undo** for `remaining_ms` in a toast centred at the foot of the
  screen (above a phone's tab bar), larger than the others and with no timer of its own, each held
  send its own: the seconds left count down on screen while its live region says once whom it
  goes to and for how long Undo lasts. Undo cancels it (`DELETE /scheduled/{id}`) and opens the draft again in
  the composer; past that, or refused `send_started` (*Too late to undo*), the web follows it
  (`GET /scheduled/{id}` until its row is gone, then `GET /sends?account=&compose_id=`, the send of
  the draft's key, as `GET /sends/{id}`) to *Sent* or to why it was not sent, said in the same
  toast (assertively) with *Open draft*. A
  row gone with no send was cancelled elsewhere. A draft written elsewhere and sent as it is has no
  `compose_id` to follow it by, so it goes at once. The hold lives on the server: a closed tab or a
  restart does not stop it.
- **Send later:** <a id="send-later"></a>the composer's **Send later** saves the draft, checks it
  as Send does, and offers *Later today* (18:00, until 17:00), *Tomorrow morning* and *Monday
  morning* (08:00; Monday unless tomorrow is Monday) and a date and time of the user's own, all in
  the user's time zone (the browser's while they have none), still to come and a year ahead at
  most. `POST /drafts/{ref}/schedule` (`{"send_at"}`, an ISO 8601 time with its offset, still to
  come; else 400 `invalid_argument`, and 422 without the offset) holds it as a held send
  as the user and answers 201 with it; the composer closes on *Scheduled for …*, the draft staying
  in Drafts. The sidebar's **Scheduled** entry, with how many there are, lists the user's held
  sends of every account (`GET /scheduled`, soonest first, the ones that failed included with why,
  and an Undo's while it waits, gone from the count once undone): the subject, the addresses, the
  account and when it goes, with **Edit** (cancels it and opens the draft in the composer) and
  **Cancel send** (cancels it, the draft left in Drafts); one the worker has taken shows *Sending…* with neither. Editing the
  draft any other way before it goes makes it another version, so the held send then fails and
  stays listed, with why, until cancelled. A draft with a held send still to go, opened from
  Drafts, says *Scheduled for …* in the composer (`GET /drafts/{ref}` gives it as `scheduled`):
  the user's first change cancels the held send (`DELETE /scheduled/{id}`) before anything is
  saved, and the composer then says the schedule was cancelled and it is a normal draft. When the
  worker took it first (`send_started`, or a `sending` one opened), the composer says the draft is
  being sent and turns read-only, saving nothing over it; a cancel that did not get through keeps
  the change unsaved until the next change tries again. An agent schedules with `draft_send`'s
  `send_at` ([`mcp.md`](mcp.md)), sent then as the agent, so the send mode applies to it as to
  `draft_send`, at scheduling and when it goes. Its held send keeps the connected app and grant
  that scheduled it (`client_id`, `grant_id`): the list shows *Scheduled by* the app, the send is
  audited as the app, and once the grant is revoked or no longer approves `mail_send` the held
  send fails (`grant_revoked`, `not_approved`; refused and audited, the draft kept).
- **The result** (`GET /sends/{id}`): `state`; `code` (the refusal's code for `failed`, else
  `null`); `filed` (the Sent copy was stored; `null` until known, and known only after `sent`, together with
  the notes); `rejected` (recipients the
  server refused while accepting others: delivered to the rest); `notes` (fixed English texts such
  as "Sent, but not filed in Sent."); `note_codes` (stable codes of the notes, for a client that
  words them itself: `draft_not_removed`, `draft_kept`, `original_not_marked`,
  `original_not_marked_forwarded`; not being filed has
  none, `filed` says it); `settled` (filing and what follows it are over, so `filed` and the notes
  are final: `sent` comes earlier, and a restart can leave it false, so a client stops waiting
  after 70 s, past the two 30 s calls that follow a delivery).
  Another user's `send_id` is 404.

### Vacation reply

<a id="vacation-reply"></a>Each account can answer new mail while the user is away
([`../GLOSSARY.md`](../GLOSSARY.md)), as RFC 3834 asks of an automatic responder:

- **Settings** (`app.vacation`, `mailroom.mail.vacation`), on the *Profile* page with the mail
  client: on or off, a first and a last day (either open; days in the user's time zone), the
  subject, the reply written in the composer's rich editor (kept sanitised as a message's HTML,
  images dropped), and the identity it goes from, or the one each message was addressed to.
  `GET /api/v1/accounts/{id}/vacation` answers `{enabled, starts_on, ends_on, subject, html, text,
  identity, answering, indexed}` (`answering`: on, and today is one of its days; `indexed`: new
  mail reaches it, INBOX's index row being on and synced, on Gmail All Mail's, else the *Profile*
  says to turn the index on for it and saving says it is not replying yet); `PUT` with `{enabled,
  starts_on, ends_on, subject, html, identity}` keeps it and answers the same; 400
  `invalid_vacation` for an identity not the account's, a last day before the first, or one
  switched on without a subject or a text. Logged as `vacation_changed` (`enabled` only).
- **What it answers:** the index's sync is where the worker learns of new mail (IDLE's new mail
  on INBOX queues it, and so does the dispatcher every 5 minutes): its new rows in INBOX (on
  Gmail, All Mail's rows labelled `\Inbox`), never a first sync's or a rebuild's, each queue the
  job `vacation_reply`, in the transaction that stores them, while the account's reply is on, the
  message arrived after it was switched on (a minute's slack for the server's clock) and on one
  of its days. Rows in the filter runs' ledger are skipped: a snoozed message woken into INBOX
  comes back, it does not arrive ([*Snooze*](#snooze), *Waking*), and a filter's copy from
  another account is the user's own move. An account whose INBOX is off the index is not
  answered (`indexed` false). Queueing runs in a savepoint: one that fails is logged
  (`vacation_queue_failed`, with the error's type) and costs the batch's rows nothing.
- **The job** (`mailroom.worker.vacation`) checks again that the reply answers today, then reads
  the message's header fields and asks mailroom-core's `vacation_refusal`
  ([`../core.md`](../core.md)). Only header fields decide, never the message's text. Refused:
  `no_sender` (not exactly one deliverable From, or a null Return-Path), `auto_submitted`
  (`Auto-Submitted` other than `no`, `X-Autoreply`/`X-Autorespond`, or `X-Auto-Response-Suppress`
  asking for none), `bulk` (`Precedence` bulk, junk or list), `mailing_list` (any `List-*`,
  `Mailing-List` or `X-Mailing-List` field), `no_reply_sender` (a no-reply, mailer-daemon,
  postmaster, bounce, list request or owner address, in From or Return-Path), `own_address` (any
  identity of the user's accounts), `not_addressed` (none of the account's identities in To or
  Cc: a Bcc, a list, a forward). A message let through has its sender claimed in
  `app.vacation_replied` (account, sender, when), committed before anything is sent, unless the
  account answered them in the last 4 days (`replied_recently`); the account's claims older than
  that go with it; the reply then goes through the
  send gate as the user (`by: user`) under a key of its own message, so a job run again never
  sends twice, in every send mode but `off` (`send_disabled`), with the hourly limit. It goes to
  the From address alone, from the chosen identity, with the user's subject and text, threaded
  (`In-Reply-To`, `References`) and marked `Auto-Submitted: auto-replied`; it is filed in Sent.
  A reply that surely did not leave gives the claim back, so the sender's next message is
  answered: one the gate refuses before SMTP, and one whose delivery failed before the server
  took it (the send recorded `failed`: a refused connection, a failed login, the recipient
  refused). One whose delivery is unknown (`delivery_unknown`, or an unexpected error in it) may
  have gone out, so the claim stays.
- **Audit:** every reply and every refusal is a `vacation_reply` record as the user: the gate's
  for a reply (`send`'s detail), else the refusal's code with the message's folder, UID and From
  address. A message gone by then is `not_found`; an account that cannot log in, its code.
- Vacation replies and their senders go with their account and their user.

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
- **Undo send:** "Sending…" with **Undo** while the delay runs; "Not sent: the draft is open
  again." after Undo; "Too late to undo: the message is being sent." when the worker took it
  first; then "Sent" as above, or why it was not sent with *Open draft*.
- **Send later:** "Send later", its choices and "Times are in …"; "Scheduled for … It stays in
  Drafts until then."; a time past or too far ahead is refused in the dialog; `already_scheduled`
  ("This draft is scheduled already…") and `send_started` in words of their own. The Scheduled
  list: "Sends …", "Sending…", or why it was not sent; "Scheduled by …" for an agent's;
  "Cancelled. The message stayed in Drafts." A draft opened with its held send: "Scheduled for …
  Changing this draft cancels the scheduled send.", then "The scheduled send was cancelled: this
  is a normal draft now.", or "This draft is already being sent: changes here are not saved.".
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
- **Designed mail:** "Imported as built: its body is sent as it is and can't be edited here.";
  each check in words of its own; "Test sent to …" once the test was delivered, or that it was
  not sent and why, or that its delivery can't be confirmed; an import refused
  `invalid_argument` says the file is not a message Mailroom can import, in the dialog, which
  stays open.
- **Leaving:** closing the composer or the tab with unsaved text flushes a save; nothing asks the
  user to confirm leaving a saved draft. Only a save that fails on closing asks, since closing then
  loses the latest changes. *Send anyway* and deleting a draft ask first too.

### Keyboard shortcuts

`r` reply, `a` reply all and `f` forward (on the open message), `c` compose a new message (from
any `/mail` screen; the account is the current one), in the table under [Web client](#web-client).
They follow the same rules as the others (ignored in text fields and with
Ctrl or Meta held) and **do not fire while the composer is open**, wherever focus is. They are
disabled for an account whose send mode is `off`.

### Limits

The user's own limits (`max_recipients`, `send_per_hour`, `attachment_max_mb`, `body_chars`
for reading, and `move_max_mb` for [a move to another account](#moving-to-another-account)) are
set on their *Profile* page ([`../REQUIREMENTS.md`](../REQUIREMENTS.md)).

| Limit | Value |
| --- | --- |
| Addresses per field (To, Cc, Bcc) | 50 |
| Recipients of one message | the user's `max_recipients` (default 10), over To, Cc and Bcc |
| Sends per hour | the user's `send_per_hour` (default 20), across their accounts; counted when claimed |
| Body text | 100,000 characters |
| Body HTML | 3 MiB of UTF-8 (`MAX_HTML_BYTES`), its `data:` images included, an agent's quoted or forwarded original too; the same cap in a save's `html` part, which is raw UTF-8; over MCP the 1 MiB request comes first |
| The `draft` part of a save | 1 MiB, the HTML not in it (`400 invalid_argument` beyond) |
| MCP request | 1 MiB (the app's MCP endpoint) |
| Subject | 998 characters |
| New files per draft | 10, inline images included |
| Attachments, in total (uploads and kept parts) | the user's `attachment_max_mb` (default 10), at most 25 MB |
| Upload request | attachments plus 5 MiB (the `html` and `draft` parts, the framing): 30 MiB at most at `/api/v1/mail/drafts`, the web image's nginx limit there, which the host's proxy must allow too; refused early on `Content-Length` |
| Imported message (`POST /drafts?import=`) | within the upload request's limit; its HTML and its text 3 MiB each, its other parts the attachment total, 100 parts nested 16 deep (400 `invalid_argument` beyond, 413 `too_large` for the parts); `html` files 10, as a save's. An .eml must fit the request whole and encoded (base64 adds about a third), so its attachments come to about 70% of the cap in practice |
| HTML converted to plain text (`POST /drafts?convert=text`) | the HTML cap in its `html` part, within the same route's limits; a result over the body text's 100,000 characters is 400 `invalid_argument` |
| Save requests at once | 3 per user, in each API process; one more is 429 `busy` before its body is read |
| Original read for quoting | 2 MiB |
| Text quoted or forwarded | 20,000 characters (the original's text is cut to this) |
| Selection quoted (`quote`) | 20,000 characters (422 beyond it) |
| HTML quoted or forwarded | 1 MiB of the original's HTML; a longer one goes as its text |
| Existing parts kept (`keep`) in a save | 50 (422 beyond it) |
| Attachments on an MCP tool | 10 (`attachments`, by ref and part) |
| Send rows kept | keyed: at least 30 days; key-less: a day; pruned on the user's next send |
| Concurrent sends | 4 in the process, 2 per user |

Over a field's cap, or over the recipient cap, is 400 `invalid_argument` (a send with no
recipient too: "Name at least one recipient."); over the attachment total or the upload size, 413
`too_large`; over the hourly limit, 429 `rate_limited`, whose message gives the time the next send
is possible.
The host's reverse proxy needs the same 30 MiB limit on that route, and no other beyond its
default ([`../deploy.md`](../deploy.md)).

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
| `POST /drafts/prefill` | `{"ref", "mode": "reply" \| "reply_all" \| "forward" \| "unsubscribe", "quote"?}` | 200 the fields below; writes nothing |
| `POST /addresses` | `{"q", "limit"?}` (`q` 1–200 characters, `limit` 1–10, default 10) | 200 `{addresses: [{name, address, sent}]}` ([suggestions](#address-suggestions)); writes nothing |
| `POST /drafts` | multipart: `draft` (JSON), `html` (optional, the HTML body as is) and `files[]` | 201 `{ref, parts, inline, replaced, autosave}` |
| `POST /drafts?convert=text` | multipart: `html` alone | 200 `{text}`: the text part `html` would be sent with; writes nothing |
| `POST /drafts?import=eml` | multipart: `draft` (JSON, below) and `eml` (the file) | 201 a save's answer and `findings` ([designed mail](#drafts)) |
| `POST /drafts?import=html` | multipart: `draft` (JSON, with `content_ids`), `html` (raw) and `files[]` | 201 the same |
| `POST /drafts?import=kept` | multipart: `draft` (JSON, with `ref`) alone | 201 the same: the imported draft `ref` saved again as built under the part's fields; 409 `not_as_built` for another draft, or one whose HTML is not as an import leaves it |
| `POST /drafts/{ref}/test` | `{"test_id"}`, the click's (`compose_id`'s form) | 202 `{send_id, to}`: the draft as a test to `to`, the account's default identity ([designed mail](#drafts)); the same `test_id` again answers that send, nothing sent twice |
| `GET /drafts/{ref}/findings` | | 200 `{findings}`: the checks of the draft as stored |
| `GET /drafts/{ref}` | | 200 the draft's fields, `lossy`, `parts`, `inline`, `send`, `scheduled` |
| `GET /drafts/find?account=&compose_id=` | | 200 `{ref, parts, inline}` of the newest version; 404 `not_found` |
| `DELETE /drafts/{ref}` | | 200 `{removed}` |
| `POST /drafts/{ref}/send` | `{"confirm_resend": false, "undoable": false}` (optional) | 202 `{send_id}`, or the held send for `undoable` ([undo send](#undo-send)); 200 already sent; 409; 429 |
| `GET /sends/{id}` | | 200 the send's result |
| `GET /sends?account=&compose_id=` | | 200 the result of the send of that draft's key; 404 `not_found` |
| `POST /drafts/{ref}/schedule` | `{"send_at"}` | 201 the held send ([send later](#send-later)); 400 `invalid_argument` for a time past or over a year ahead; 409 `already_scheduled`, `send_started` |
| `GET /scheduled` | | 200 `{scheduled: [...]}`, the user's held sends, soonest first |
| `GET /scheduled/{id}` | | 200 the held send `{scheduled_id, account, ref, subject, recipients, send_at, remaining_ms, state, code, by, app}` (`by` `user` or `agent`, `app` an agent's connected app); 404 `not_found` once it went or was cancelled |
| `DELETE /scheduled/{id}` | | 200 the held send it was, the draft left in Drafts; 409 `send_started`; 404 |

The static route `GET /drafts/find` is matched before `GET /drafts/{ref}`.

An import's `draft` part is `{account, compose_id, ref?, from?, to?, cc?, bcc?, subject?,
content_ids?}`: `ref` an earlier version to replace (required by `kept`, which takes only a
draft imported as built, else 409 `not_as_built`), the address fields and `subject` left out
(or null) to take the .eml's (none, for `kept`), `from` null for the default identity (never the file's), and
`content_ids` as a save's, for `html` only (an `eml` import with any is 400 `invalid_argument`, as
are one without its file part and a Content-ID a save would leave out as unfit for a header). A finding is `{code, text, count, items}`: `code` one of
`removed_scripts`, `removed_forms`, `removed_handlers`, `removed_images`, `removed_data_images`,
`removed_remote`, `removed_styles`, `text_derived`, `image_without_alt`, `cid_without_part`,
`part_not_referenced`, `html_clipped`, `remote_images`, `no_text_part`, `too_large`; `text` an
English sentence; `items` the Content-IDs or file names involved, at most 20.

```jsonc
// POST /drafts: the `draft` part (to, cc, bcc are lists of Address; text is plain text). The HTML
// body is the `html` part, raw and not JSON: escaping could otherwise make it outgrow its part.
{
  "account": 1,
  "compose_id": "6f1c0a2e-…",                  // UUID; required
  "ref": "WzIsMSwiSU5CT1gi…",                    // the latest version being replaced; null for the first save
  "answers": "WzIsMSwiSU5CT1gi…",                // the message being replied to; null otherwise
  "forwards": null,                            // the message being forwarded; null otherwise
  "from": "me@example.org",                    // one of the identities; null: the default
  "to": [ { "name": "", "address": "ada@example.org" } ],
  "cc": [], "bcc": [],
  "subject": "Re: Notes on the engine",
  "text": "Thanks, …",                          // unused with an html part, e.g. <p>Thanks, <img src="cid:a1@mailroom"></p>
  "content_ids": ["a1@mailroom", null],          // per files[] in order: the Content-ID of an inline image, null for an attachment
  "keep": [ { "ref": "WzIsMSwiSU5CT1gi…", "part": "2" },     // parts of existing messages, or of the draft itself
            { "ref": "WzIsMSwiSU5CT1gi…", "part": "1.2", "inline": true } ]   // an inline part the HTML shows
}
// 201
{ "ref": "WzIsMSwiSU5CT1gi…", "parts": [ { "part": "2", "name": "notes.pdf", "type": "application/pdf", "size": 40211 } ],
  "inline": [ { "part": "1.2", "name": "", "type": "image/png", "size": 5120, "content_id": "a1@mailroom" } ],
  "replaced": true, "autosave": true }
```

- `ref` in the answer is the new version's ref, the one the client uses from now on; it is `null`
  when the server did not report where the draft landed and a search by `Message-ID` found
  nothing yet (the client then calls `GET /drafts/find`). `parts` lists the attachments of the
  saved version, as the message view's `attachments`: after a save, the client keeps its
  attachment tray from `parts`, with `keep` entries that name the draft's own parts. `inline`
  lists the inline parts its HTML shows, each with the `content_id` its `cid:` URLs name, kept the
  same way with `inline: true`. `replaced` is true when the previous version was removed.
- `content_ids` is at most as long as `files[]` (else 400 `invalid_argument`); a file past its end
  is an attachment. A Content-ID is at most 250 characters; one with characters a header cannot
  carry as they are (a `?`, a space) is left out with its image.
- A draft with no recipient is a valid draft; a send needs at least one.
- `POST /drafts/prefill` answers `{account, from, to, cc, bcc, subject, text, html, answers,
  forwards, attachments, inline}`, the fields of the draft it would start (`html` the same body in
  HTML, an empty paragraph and the [signature](#signatures)'s block above the quote or the
  forwarded message, null for an unsubscribe;
  `answers` the ref for a reply, `forwards` the ref for a forward, else null; `attachments`, for a
  forward, the original's `{ref, part, name, type, size}` that the composer offers as `keep`;
  `inline`, for a forward, the original's inline parts its `html` shows, the same with
  `content_id`, to keep with `inline: true`), `from` the
  identity address chosen as above. A recipient of the original that is not
  [deliverable](#compose) is left out, since it would refuse every save of the draft.
- `GET /drafts/{ref}` answers `{ref, account, compose_id, from, to, cc, bcc, subject, text, html,
  answers, forwards, lossy, designed, parts, inline, send, scheduled}`: `html` is the HTML of a draft Mailroom wrote with
  one, sanitised again as it is read, else null (`text` is then its text part); `designed` is
  true for a message imported as built; `compose_id` is
  `null` for a draft not written by Mailroom; `send` is
  `null`, or `{send_id, state, code}` of the send of this draft, so reopening shows an `unknown`
  banner; `scheduled` is `null`, or its held send still to go (`held` or `sending`, as
  `GET /scheduled/{id}` answers it, [send later](#send-later)). `bcc` is shown to its owner.
- `DELETE /drafts/{ref}` answers `{"removed": "expunged" | "trashed"}`.
- `GET /sends/{id}` answers `{send_id, state, code, filed, rejected, notes, note_codes, settled}`
  ([send state machine](#send-state-machine)); `POST /drafts/{ref}/send` answers `202` with
  `{send_id}` or, when that key is already `sent` and the draft is still there, `200` with the same
  body as `GET /sends/{id}`; once the draft is removed, 404 `not_found`. `send_id` is a string,
  opaque to the client. A draft written elsewhere has no `compose_id`, so its key comes from its
  ref.
- A draft's `ref`, `answers` and `forwards` must name the same account as `account`, and a draft
  sets `answers` or `forwards`, not both, else `invalid_argument`;
  `keep` may name any of the user's accounts. A message answered that is gone by the first save
  no longer threads it: the draft is saved all the same, without `answers`. A save keeps the
  threading headers of the version it replaces.
- Errors: 400 `invalid_argument`; 404 `not_found` (account, ref, draft, send); 409
  `stale_reference`, `send_disabled`, `delivery_unknown`, `draft_not_editable`,
  `not_as_built`, `password_missing`; 413 `too_large`;
  422 `recipient_refused`; 429 `busy`, `rate_limited`; 502, 504.

### MCP

The `mail_draft` and `mail_send` groups ([`mcp.md`](mcp.md)) are the same service functions
behind the same gate, with the agent as the one acting: the send mode limits them as in the
[table above](#send-mode-and-who-is-acting), and the hourly limit and recipient cap bind them.
Attachments are named by `ref` and `part` only: an agent never passes file content. A body is
`text` or `html`, sanitised as the web's is, its text part made from it. `draft_create`,
`message_send`, `message_reply` and `message_forward` add the identity's
[signature](#signatures) below the text and above a quote (in text and in HTML), unless
`include_signature` is false; `draft_update` and `draft_send` leave the body as it is.

## REST contract

Every route is under `/api/v1/mail`, needs a [user session](../GLOSSARY.md), and sees only the
signed-in user's accounts. Reads are `GET` but search, a `POST` with a JSON body so that search
terms (addresses, subjects, words of a body) are never in an API URL, and so are the count and
bulk action over what a search matches; the four organize routes (slice B) and the folder writes
([folder management](#folder-management)) are `POST` too.
Every response, the `401` of a missing or expired session included, carries `Cache-Control:
private, no-store`. The SPA does keep a search
in its own URL (`?q=`), so the access logs in front of it, the web image's nginx and the host's
proxy, must log paths without their query string ([`../deploy.md`](../deploy.md)). Mail-derived strings (subject, names,
previews, body) are raw text, never HTML: the client escapes them. Control and invisible
characters are already stripped from headers.

### Refs and cursors

- A **ref** names one message exactly (account, folder, UIDVALIDITY, UID): base64url of the JSON
  array `[2, account, folder, uidValidity, uid]`, the account by its id. It is **opaque to the
  client**: store it, put it in a URL, never build or
  parse it. It is URL-safe, so it goes in the path as is. A ref whose folder changed (UIDVALIDITY)
  answers `stale_reference`, as does one made before account ids were numbers (version 1, the
  account by its slug); one of an account that is not the user's answers `not_found`, as a
  missing one.
- A **cursor** is equally opaque (base64url of `[2, positions]`, then, when a folder or an
  account failed, `[account, folder, code, certificate]` for each, `folder` `null` for an
  account; a page read from the [index](#index) has version 3): pass `next_cursor` back as
  `cursor`, with the same query. A malformed one is `invalid_argument`; one made before account
  ids were numbers is `stale_reference`, as is an index one whose folder left the index or
  changed since (start over without it).

### Errors

A refusal is `{"detail": {"code": "<code>", "message": "<fixed text>"}}`
([`refusals.py`](../../backend/mailroom/api/refusals.py): FastAPI's `detail` wrapper, as for every
other route). The message is a fixed, secret-free text; server wording never reaches it. A
malformed request (bad query value, wrong type) is FastAPI's 422 with its `detail` list, as
elsewhere.

Mailbox failures carry the 14 codes of mailroom-mcp, plus `delivery_unknown` (`src/imap/errors.ts`),
and mailroom-core's `folder_exists`, `folder_changed`, `count_changed`, `too_many` and
`not_as_built`:

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
| `rate_limited` | 429 | The limit is *N* per hour; the next send is possible at *time (the user's time zone)*. |
| `recipient_refused` | 422 | A recipient is not a deliverable address, or the mail server refused the message; nothing was sent. |
| `delivery_unknown` | 409 | Delivery could not be confirmed: the message may already have gone out, so it was not sent again. |
| `folder_exists` | 409 | A folder with that name already exists. |
| `folder_changed` | 409 | The folder changed since it was read. It holds *N* messages now; nothing was deleted. (`detail.messages`: *N*.) |
| `count_changed` | 409 | The messages matching changed since they were counted. *N* match now; nothing was changed. (`detail.matching`: *N*.) |
| `too_many` | 409 | Too many messages match to act on them all. *N* match; at most 5000 are acted on together. Narrow the search. (`detail.matching`: *N*.) |
| `not_as_built` | 409 | That draft is not as it was imported, so it cannot be kept as built. (`POST /drafts?import=kept`.) |
| `server_error` | 502 | The mail server reported an error. |

The send group (`send_disabled`, `rate_limited`, `recipient_refused`, `delivery_unknown`) belongs
to slice C; slices A and B never answer them, nor the folder group (`folder_exists`,
`folder_changed`, [folder management](#folder-management)), nor the bulk group (`count_changed`,
`too_many`, [every message a search matches](#every-message-a-search-matches)). The app's own codes keep their
statuses: `password_missing`, `draft_not_editable` and `folder_in_use` (a rule set names the
folder: `detail.rule_sets`, up to 10 `{id, name}`) 409, `secrets_key_missing` 503, an unknown account `not_found` 404
(coded). A mailbox failure carrying a code not in this table keeps its code, with status 502. The
account's login test, its folder listing and the filters' runs report mailbox failures with the
same codes and messages. An `unreachable` whose cause is a TLS certificate that did not verify ends
with "Its TLS certificate did not verify."; the web adds that reason, translated, to its wording.

In a list or search page a failing source does not fail the page: it is reported in `failures` by
code, below. It is not read again: `next_cursor` carries it as failed, every later page of the
same query reports it again in `failures`, with the code (and message) it failed with, and paging
ends once the other sources are done. A page whose `failures` is empty is then complete for the
sources it read, whichever page it is. An account that failed before a folder was chosen (folder
`null`, an all-accounts search's `timeout` included) is carried the same way: later pages report
it without asking it again, even if it would answer now. An account skipped as
`password_missing` is carried too: later pages report it without checking its password again,
even once it is back.

### Types

```jsonc
// Address
{ "name": "Ada Lovelace", "address": "ada@example.org" }   // name "" when absent

// Flags
{ "seen": true, "flagged": false, "answered": false, "forwarded": false, "draft": false, "to_sort": false }
```

`forwarded` is the `$Forwarded` keyword a sent forward sets.

`date` is the `Date` header, else the arrival time (INTERNALDATE), as ISO 8601 with offset (UTC
as `Z`: `"2026-10-30T09:12:00Z"`), or `null` when the message has neither.

An account is named by its id, a positive integer the server gives it (`account_id`, `account`,
`destination_account`, the `{account_id}` in a path); the web shows its name, never the id.

### `GET /accounts/{account_id}/folders`

```jsonc
{
  "account_id": 1,
  "folders": [
    { "path": "INBOX", "role": "inbox", "unread": 3, "total": 120, "delimiter": "/", "selectable": true,
      "uid_validity": 1700000000, "uid_next": 4211 },
    { "path": "Archive/2025", "role": null, "unread": 0, "total": 842, "delimiter": "/", "selectable": true,
      "uid_validity": 1700000001, "uid_next": 843 }
  ]
}
```

`role` is `inbox`, `sent`, `drafts`, `trash`, `junk`, `archive`, `all` or `flagged` (from the
server's special-use flags and INBOX, else from a well-known name), else `null`. `delimiter` is
the account's hierarchy delimiter (`null` on a server without one). Unselectable folders
(`\Noselect`) are left out, unless `?all=true`: then they are listed too, `selectable: false`,
counts 0 (a tree's parents). Order: the folders with a role first, in the order inbox,
flagged, sent, drafts, all, archive, junk, trash, then the rest by path. Counts are always present
here (STATUS; 0 for a folder whose STATUS failed). `uid_validity` and `uid_next` are STATUS's
too, what a [mark read](#folder-management) sends back: `null` for an unselectable folder or one
whose STATUS failed. A folder the [index](#index) holds whole (ready, no age limit) takes its
counts, UIDVALIDITY and UIDNEXT from the index instead, as of its last sync and the app's own
writes since (UIDNEXT past the highest UID it holds), and is not asked STATUS. On Gmail, while
the index holds All Mail so, INBOX and each label's folder (Sent, Drafts, Starred and Important
by their labels) are not asked STATUS either: their counts are those of All Mail's indexed
messages carrying their label, read in one query, and their `uid_validity` and `uid_next` are
`null` (All Mail's are never theirs); Spam and Trash, which All Mail leaves out, are still asked,
and so are the label folders when the index's counts cannot be read. Errors: 404 `not_found` (account), 409, 502, 504.

### `GET /accounts/{account_id}/messages?folder=INBOX&limit=25&cursor=&subfolders=false`

One folder, newest first (by arrival: UID). `folder` defaults to `INBOX`; `limit` is 1..50,
default 25 (a value outside is 422). Response: a **page**.

With `subfolders=true` the page merges the folder with its selectable subfolders at any depth,
newest first by arrival (INTERNALDATE), each item naming its `folder`. Left out are the folders
with the role Sent, Drafts, All Mail, Archive, Junk or Trash and everything under them, unless
the listed folder has that same role; INBOX matches in any case. More than 50 folders in all is
400 `invalid_argument`, naming the cap; a folder not listed is 404 `mailbox_missing`. In that
mode a folder that cannot be read is reported in `failures`, as in a search, never refused.

```jsonc
{
  "messages": [
    {
      "ref": "WzIsMSwiSU5CT1gi…",
      "account_id": 1,
      "folder": "INBOX",
      "date": "2026-10-30T09:12:00Z",
      "from": { "name": "Ada Lovelace", "address": "ada@example.org" },
      "subject": "Notes on the engine",
      "flags": { "seen": false, "flagged": false, "answered": false, "forwarded": false, "draft": false, "to_sort": false },
      "has_attachments": true,
      "preview": "Dear Charles, the notes are attached…",
      "size": 48213,
      "listed_folder": null
    }
  ],
  "next_cursor": "WzIsW1sxLCJJTkJP…",   // null when there is no more
  "failures": [ { "account_id": 1, "folder": "INBOX", "code": "unreachable" } ]
}
```

- `has_attachments` is true when the message's structure has an attachment; inline parts (the
  images its HTML shows in place) do not count.
- `preview` is the first 200 characters (UTF-16 code units, mailroom-core's `PREVIEW_LENGTH`) of
  the rendered text body, one line, quotes collapsed; `""` when there is none. `size` is the
  message size in bytes.
- `from` is the first sender (`name` and `address` `""` when there is none); `subject` is `""`
  when absent.
- `listed_folder` is the folder the row was listed under when it is not the row's own: a Gmail
  row read from the index names All Mail, listed under INBOX or a label's folder (the first of
  the read's that the message carries); `null` otherwise. Lists, searches and views give it.
- A single folder that fails answers its error status instead of a page.
- `group=thread` asks for the page [by conversation](#conversations); `conversations` is then
  the list of them, one for each message, else null (and always without `group`). Any other
  `group` is 422.

### `POST /search`

The fields of mailroom-mcp's `search` (`src/mcp/tools/listing.ts`) as a JSON body; all given
criteria must match. Results newest first by arrival (INTERNALDATE), merged across sources, the
same page shape.

```jsonc
{ "account": "*", "folder": "INBOX", "from": "ada@example.org", "limit": 25, "cursor": null }
```

| Field | Meaning |
| --- | --- |
| `account` | An account's id (a number), or `*` for every account of the user (default `*`). |
| `folder` | A folder path, or `*` for every folder (default `INBOX`). `*` leaves out **Junk and Trash** on every account, and an account with an `\All` folder (Gmail's All Mail) is read through it only. |
| `from`, `to` | Address or name contains this. |
| `subject` | Subject contains this. |
| `text` | Headers or body contain this. |
| `since`, `before` | `YYYY-MM-DD` (a real calendar date; `since` inclusive, `before` exclusive), compared with the arrival date (INTERNALDATE), not the `Date` header: a day of the user's time zone when read from the [index](#index) (with the live read of what its age limit leaves out), the server's day when read live (IMAP `SINCE` and `BEFORE` compare dates only, as the server dates its mail). |
| `unread`, `flagged` | Booleans. |
| `to_sort` | Boolean: marked [To sort](../GLOSSARY.md) (`$MailroomSort`), or not; where the server keeps no keywords, no message is marked, so `true` matches nothing. |
| `has_attachment` | `true` only (approximate: `multipart/mixed`; on Gmail, which ignores that header search, `has:attachment` through X-GM-RAW, over one account or all); `false` is ignored, not a criterion. |
| `gmail_query` | Gmail search syntax (X-GM-RAW); Gmail accounts only, else `unsupported`. |
| `subfolders` | `true`: the folder's subfolders too, as for the list (default `false`); with `folder` `*`, 400 `invalid_argument`. |
| `limit`, `cursor` | As for the list. |
| `group` | `"thread"`: [by conversation](#conversations), as for the list; null or absent: not. |

Every field is optional. The criteria and `cursor` take `null`, meaning absent; `account`,
`folder` and `limit` do not (422). An unknown field is 422. With no criteria,
everything in the folder matches. A text criterion with a line break or NUL is 400
`invalid_argument`. A search of one account and one folder that fails answers its error status
instead of a page, as the list does (not with `subfolders`, whose folders fail in `failures`).
Otherwise a source that cannot be searched is reported in `failures` (`folder` is `null` when the
account failed before a folder was chosen) and the others still answer. Each account of an all-accounts search has its own deadline, so a slow one is a
`timeout` failure, not a 504 for the whole page. An account without a password that opens is such
a failure too, with the app's code `password_missing`.

### `GET /messages/{ref}?offset=0&include_quoted=false`

```jsonc
{
  "ref": "WzIsMSwiSU5CT1gi…",
  "account_id": 1,
  "folder": "INBOX",
  "from": { "name": "Ada Lovelace", "address": "ada@example.org" },        // null when absent
  "to": [ { "name": "", "address": "charles@example.org" } ],
  "cc": [],
  "reply_to": [],
  "date": "2026-10-30T09:12:00Z",
  "subject": "Notes on the engine",
  "message_id": "<a1b2@example.org>",                                        // null when absent
  "flags": { "seen": false, "flagged": false, "answered": false, "forwarded": false, "draft": false, "to_sort": false },
  "text": {
    "content": "Dear Charles, …",
    "removed_chars": 0,
    "incomplete": false,
    "next_offset": null,
    "quoted_omitted": true
  },
  "has_html": true,
  "remote_images": 2,
  "loadable_remote_images": 1,
  "attachments": [ { "part": "2", "name": "notes.pdf", "type": "application/pdf", "size": 40211 } ],
  "calendar": null,
  "headers": {
    "mailing_list": {                                                       // null without List-* fields
      "id": "engine.example.org", "name": "Engine notes",
      "archive": null, "post": null, "help": null,
      "unsubscribe_https": "https://example.org/u/abc", "unsubscribe_mailto": null,
      "one_click": true, "unsubscribe_fields": 1, "unsubscribe_post_fields": 1
    },
    "authentication": [
      { "authserv_id": "mx.example.net",
        "results": [ { "method": "dkim", "result": "pass", "header_d": "example.org",
                       "header_b": "AbCd1234", "header_from": null } ] }
    ],
    "dkim_signatures": [ { "d": "example.org", "b": "AbCd1234…", "h": ["from", "list-unsubscribe"] } ],
    "from_fields": 1,
    "delivered_to": ["charles@example.org"],
    "truncated": false
  },
  "sender_mismatch": false,
  "sent_to": "charles@example.org",                                       // null when none matches
  "authentication": {
    "verified": true, "spf": "pass", "spf_domain": "example.org", "dkim": "pass",
    "dkim_domain": "example.org", "dmarc": "pass", "dmarc_pass": true, "from_domain": "example.org"
  },
  "remote_trusted": false,
  "one_click_unsubscribe": true,
  "preferred_view": "html"
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
- `remote_images` counts the remote images (`http` or `https`) that HTML asks for: `<img>`
  sources (what `remote=1` would load), `srcset` candidates, `background` attributes and CSS
  `url()`s (removed either way); 0 when `has_html` is false. `loadable_remote_images` counts the
  `<img>` sources alone: the client offers to show remote images only when it is above 0.
- `attachments` lists parts to download (`part` like `"2"` or `"1.2"`); an attached message
  (`message/rfc822`) is one attachment. Inline parts (images the HTML shows in place, by
  `Content-ID`) are not attachments. `type` is the declared type, `name` is `""` when unnamed.
  `size` is the decoded size in bytes, estimated from the encoded one (base64 at 57 in 78), so it
  may differ a little from the download's.
- `calendar` is `null` or one `{part, name, type, size}` of the `text/calendar` part (an invite;
  downloadable, not parsed yet).
- `headers` is what mailroom-core parsed from From, the List-* fields, Authentication-Results,
  DKIM-Signature, Delivered-To and X-Original-To (*Read* in [`core.md`](../core.md)), from at most
  64 KiB of them (`truncated` when cut). **None of it is verified:** any field may be forged, and
  only the topmost `authentication` entry of the account's own authentication server (its
  `authserv_id`, *Account autoconfiguration*) can be believed. List links are `https:` or
  `mailto:` only, the topmost of each field;
  `authentication` and `delivered_to` (X-Original-To's addresses, then Delivered-To's) are topmost
  first. The client shows `mailing_list` alone, as the *Mailing list* row above.
- `sender_mismatch` is true when any From display name holds an address-like or domain-like token
  (after NFKC and case folding) other than its real address, or a domain neither the real
  address's domain nor a parent or child of it: `service@paypal.com <x@evil.example>`. A dotted
  word is a domain only when its last label is a top-level domain of the root zone, so `Node.js
  Weekly` or `Mr.Robot` names none. With no public-suffix list, `Amazon.co.uk` from `amazon.com`
  warns: another registrable domain. A warning shows `from.address`, never the name. Lookalikes
  across scripts are not folded: a name naming one still warns, but a lookalike real address under
  a plain name does not.
- `sent_to` is the account's identity the message arrived at: the first of To, Cc, then the
  delivery addresses (X-Original-To, Delivered-To, topmost first) that is one; `null` when none is
  (a Bcc, a list).
- `authentication` is the verdict of the account's own authentication server: the topmost
  Authentication-Results field whose authserv-id is the account's `authserv_id`, never another
  (one above it may be forged). `verified` is false, and every result `null`, when the account has
  no `authserv_id` or no field carries it: *not verified*. `spf`, `dkim` (`pass` when any
  signature passed, else the first result) and `dmarc` are as written, `null` when absent.
  `spf_domain` is the domain SPF checked (`smtp.mailfrom`'s) and `dkim_domain` the signing domain
  (`header.d`) of the result `dkim` is, each an ASCII host name (IDNA), `null` when absent or not a
  valid host name.
  `dmarc_pass` is true only when `dmarc` is `pass` for a message with exactly one From (one field,
  one address, and the header block not cut) whose domain is the result's `header.from` or a
  subdomain of it (DMARC relaxed alignment: `noreply.github.com` under `github.com`, never a parent
  of `header.from` nor `evilgithub.com`), both compared in IDNA form, the From's domain then being
  `from_domain`.
- `remote_trusted` is true when `dmarc_pass` is and the user remembers `from.address`
  ([remote content senders](#remote-content-senders)): the client then asks for the HTML view
  with `remote=sender` at once, which the HTML route decides again as it answers.
- `one_click_unsubscribe` is true when [`POST /messages/{ref}/unsubscribe`](#one-click-unsubscribe)
  would post rather than refuse: the client then offers one click. The route checks again.
- `preferred_view` is the view the message opens in: `html` when it has HTML, unless the user
  reads its first From address as text ([sender views](#sender-views)); `text` otherwise.
- Reading **does not set `\Seen`.** Errors: 400, 404, 409 (stale), 502, 504.

### `GET /messages/{ref}/html?remote=0`

A **standalone HTML document**, not JSON: `remote=0|1|sender` (default `0`; `sender` keeps
remote images only when, as the request is answered, DMARC passed for the message's one From and
the user remembers it, as `remote_trusted` says; the CSP follows that decision), and `dark=0|1` (default
`0`), which adapts it to a dark theme with CSS in the document alone: HTML that sets no
background or text colour of its own (mailroom-core's `SafeHtml.sets_colours`) gets dark colours
(`color-scheme: dark`, a dark page, light text and links); HTML that sets some is inverted (`filter:
invert(1) hue-rotate(180deg)`, which keeps its hues) with its images inverted back. The dark
document holds the light one's styles too: every dark rule applies only while the URL's fragment
is not `#mailroom-original`, the id of an empty, `hidden` element at the top of its body (CSS
`:root:not(:has(#mailroom-original:target))`, no script), and the sanitised HTML keeps no `id` or
`name`, so no element of the message can take that place. The message view and the preview of
designed mail both use it. The sanitised HTML body
(mailroom-core's `safe_html`: allowlisted tags, attributes and CSS properties; no script, forms,
frames, `base`, event handlers). `cid:` images are inlined as `data:` up to a total budget; past
it an image loses its `src` and keeps its alt text, with no placeholder. Remote (`http(s)`) image
sources are removed unless `remote=1`. Whatever `remote` says, nothing else remote is kept: every
`<style>` element is dropped with its content (remote stylesheets and fonts with it), only
allowlisted properties of inline `style` attributes survive, and `url()` and `srcset` are
removed. Each link's `title` is the host it goes to, in IDNA form, and a link whose text names
another host is followed by a `<span>` marker, `⚠` and the real host, styled in full so that the
sender's styles around it cannot hide it. A message with no HTML body is 404 `not_found`. The API sets these response headers:

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
usual JSON bodies with their statuses. The client asks [`…/html/check`](#get-messagesrefhtmlcheck)
first and frames the document only once that answered, so a failure shows as the client's own
error, never inside the frame. The sanitised view is kept for a minute
([`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *HTML views kept*): the
message read's remote check makes it without `remote` (its `cid:` images inlined), so the check,
the document and a switch to `dark` that follow it are not read from the server again.

### `GET /messages/{ref}/html/check`

`remote=0|1|sender` as for `…/html`: 204 with no body when that document would be served, else its
refusal (the same JSON body and status). The client asks it before it frames the document, the
message view and the preview of designed mail alike; it never fetches the document itself.

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

### `GET /messages/{ref}/source`

The whole message as stored, a download: `Content-Type: message/rfc822`, `Content-Disposition:
attachment; filename*=UTF-8''…` (the subject cleaned as a part's file name is, else `message`,
then `.eml`), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none';
sandbox`, `Cache-Control: private, no-store`. Read under EXAMINE, never setting `\Seen`, and
capped at the user's `attachment_max_mb`: over it, 413 `too_large`, never cut. Errors as for
`GET /messages/{ref}`.

### `GET /messages/{ref}/print`

A **standalone HTML page** for the browser's own print, opened in a tab of its own: the subject
as its title and heading, From, To, Cc, Date (the user's time zone) and each attachment's name
and size, all escaped, then the body: the HTML body as the [HTML view](#get-messagesrefhtmlremote0)
sanitises it without `remote` (its `cid:` images inlined, remote ones removed), else the whole
text body, quotes included, escaped. It runs no script. Headers:

```
Content-Type: text/html; charset=utf-8
Content-Security-Policy: sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cache-Control: private, no-store
```

Nothing from the message is in a response header. Errors as for `GET /messages/{ref}`.

### `GET /messages/{ref}/filter-draft`

A filter the filter editor starts from, made from the message's List-Id and sender, never its
subject: `{"spec", "criteria", "name"}`, the spec's action `no_op`
([`filters.md`](filters.md#filter-drafts), which also has the search's counterpart). The message is
read for its headers, never marked read; `400` `nothing_to_filter` without a List-Id or a From
address. Errors as for `GET /messages/{ref}`.

### Muted threads

A muted thread's new messages in INBOX arrive marked read, by the account's filter runs, so only
while its *Run filters* is on ([`filters.md`](filters.md#muted-threads)).

- `POST /messages/{ref}/mute` (no body): mutes the message's conversation on its account, by the
  ids its headers carry (References, In-Reply-To, Message-ID) and on Gmail its thread; read
  without marking it read. 201 `{"id", "account_id", "ids", "gmail", "run_filters",
  "created_at"}`: `ids` counts the Message-IDs it is known by, `gmail` whether its Gmail thread
  too, `run_filters` the account's switch. A thread already muted that shares one of them is the
  one answered, with the others added. `400` `nothing_to_mute` for a message with none; the
  read's errors as for `GET /messages/{ref}`.
- `GET /muted`: `{"threads": [...]}`, each as above, oldest first.
- `DELETE /muted/{id}`: 204, or 404 (`"No such muted thread"`).

Nothing from the message is answered but those counts: no subject, no id.

### Remote content senders

The senders whose remote content the user shows without asking, by From address (lowercase), one
each; addresses travel in bodies, never in a URL.

- `GET /remote-senders`: `{"senders": [{"address", "created_at"}]}`, by address.
- `POST /remote-senders` `{"ref"}`: the server reads the message and remembers its From only when
  `dmarc_pass` (one From, DMARC passed at the account's own authentication server); otherwise 400
  `sender_not_verified`, so an account without an `authserv_id` remembers none. 201 with the
  sender; one already remembered keeps its `created_at`. The read's errors as for `GET
  /messages/{ref}`.
- `POST /remote-senders/remove` `{"address"}` (compared lowercase): 204, or 404 when not
  remembered.

Removing the user removes them ([`nonfunctional-requirements.md`](nonfunctional-requirements.md),
*Mail accounts*).

### Sender views

The senders whose mail the user reads as text rather than HTML, by From address (lowercase), one
each; addresses travel in bodies, never in a URL. It changes only how a message is shown, so any
From counts: no sender authentication is asked.

- `GET /sender-views`: `{"senders": [{"address", "created_at"}]}`, by address.
- `POST /sender-views` `{"ref", "view"}`: the server reads the message's first From; `view`
  `text` remembers it (one already remembered keeps its `created_at`), `html` forgets it. 200
  `{"address", "view"}`; 400 `no_sender` for a message without a From. The read's errors as for
  `GET /messages/{ref}`.
- `POST /sender-views/remove` `{"address"}` (compared lowercase): 204, or 404 when not
  remembered.

Logged as `sender_view_text` / `sender_view_html` with `user_id`, never the address. Removing the
user removes them ([`nonfunctional-requirements.md`](nonfunctional-requirements.md), *Mail
accounts*).

### One-click unsubscribe

`POST /messages/{ref}/unsubscribe` (no body) unsubscribes from the list the message came from by
RFC 8058's one click, for the signed-in user only: no MCP tool does it, and nothing does it on its
own. The server reads the message again (a stale ref is `stale_reference`) and posts
`List-Unsubscribe=One-Click` to its List-Unsubscribe `https:` URL only when the account's
authentication server vouches for it; otherwise 400 with one of:

| Code | When |
| --- | --- |
| `unsubscribe_unavailable` | No List-Unsubscribe, or no List-Unsubscribe-Post of exactly `List-Unsubscribe=One-Click`. |
| `unsubscribe_repeated` | More than one List-Unsubscribe or List-Unsubscribe-Post, or a header block cut at its cap. |
| `unsubscribe_not_https` | The List-Unsubscribe has no `https:` link (a `mailto:` one is the [prefill](#compose)'s). |
| `unsubscribe_unverified` | The account has no `authserv_id`. |
| `unsubscribe_not_signed` | No DKIM signature that signs both fields was passed by the topmost Authentication-Results field of the account's `authserv_id` (named by its `header.d` and a `header.b` of at least 8 characters, and only one signature answering to them). |

Once posted, 200 `{"outcome", "status", "list_id"}`: `outcome` `ok` (the list answered 2xx, its
page over the cap or not), `rejected` (another status, in `status`, a redirect too: it is not
followed), `refused` (`safe_fetch` would not send there: a non-public address) or `failed` (no
answer: a timeout, a host that does not resolve, a broken connection). The POST goes through `safe_fetch`, with no cookies,
no `Referer` and no redirect followed
([`nonfunctional-requirements.md`](nonfunctional-requirements.md), *No request on mail's behalf,
but one*). Every attempt, a refused one included, is an audit record `unsubscribe` with the
message's folder, UID, `Message-ID`, sender and `list_id`, and once posted the URL's host and the
status; the URL itself, and its query, are kept nowhere.

## Home

The web opens on **Home** (`/`), a summary of the user's own mail and filters, unless their
appearance's `home` (the Profile's *Open on*) names Mail, Filters, Accounts or Activity: then the
app opening on `/` (a tab's first page) goes there instead, while the instance offers that page;
Home in the nav still opens the summary. The API's status, the old landing page, is at `/health`,
in the user menu. Cards, each left out where the instance does not run its feature:

- **Unread**: All inboxes (the sum) and each account's INBOX, linking to the view and to the
  folder. A count is the index's: an INBOX's own row, or on Gmail All Mail's unread rows carrying
  INBOX's label; a dash while the index holds no readable copy (the account is not indexed yet)
  or holds it only under an age limit, whose rows leave out the older unread mail.
- **Waiting on you**: Starred and To sort (the marks in the indexed folders of every account,
  linking to a search of every folder), Snoozed coming back today (before midnight in the user's
  time zone, overdue ones included; with how many are snoozed in all) and Scheduled (held sends,
  the next one's time, and those that failed in red). Snoozed and Scheduled open their lists over
  the mail screen (`/mail?list=snoozed`, `list=scheduled`).
- **Filters at a glance**: each rule set (never the hidden muting ones), by name, with its recent
  real runs (those finished in the last 7 days among the runs kept, the newest 100 per account, so
  a rule set run often shows fewer than the week's) and how many failed (in red), the messages its
  filters acted on in that week (the 30-day match counts, by UTC day), its last run, and a rule set the app
  turned off after an error; links to the rule set, Filters and Activity.
- **Tiles** for Mail, Filters, Accounts, Activity and the Profile, as the instance offers them.

`GET /api/v1/home` answers it from the app's tables only (the index, snoozes, scheduled sends,
runs and match counts), never a mail server, so it answers at once on any instance: `mail` (null
without the mail client: `inboxes` `[{account_id, display_name, colour, unread}]`, `unread`,
`starred`, `to_sort`, `snoozed`, `snoozed_today`, `scheduled`, `next_scheduled_at`,
`scheduled_failed`) and `filters` (null without filters: `rule_sets` `[{id, name, account_id,
enabled, disabled_reason, runs, failed_runs, last_run_at, last_run_failed, acted_on}]`). Only the
signed-in user's own.

## Index

The mail index keeps each indexed folder's message metadata in the app's database, never a body
([`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Mail metadata at rest*). A
folder off the index is read live, as before the index.

- **Folders.** Each selectable folder of an account gets its row (`app.mail_index_folder`) the
  first time the index sees it (its status below, a switch), switched on but for the folder in the
  Junk role and, off Gmail, the virtual folders (`\All` or `\Flagged` special-use, or the `all`
  or `flagged` role), whose mail other folders hold: these stay listed, off until switched on.
  Trash is indexed. The user switches each one on or off per account. Off deletes the
  folder's indexed messages and forgets its sync state; on leaves it `pending`, to be built.
- **Gmail** (`X-GM-EXT-1`, never by host name): only All Mail (the `\All` folder) is indexed,
  with each message's labels. INBOX and the label folders are read from it by label, so their
  switch is All Mail's; Spam and Trash, which All Mail leaves out, stay live. A Gmail account
  whose All Mail is not listed over IMAP indexes nothing.
- **Age limit.** The user's own (`index_max_age_days`, 1-36500 days, null for none), capped by
  the instance's `MAILROOM_INDEX_MAX_AGE_DAYS` (unset or not a whole number: no cap; clamped to
  1-36500): the smaller of the two is in force, counted back from today in the user's time zone.
  Older messages are not indexed and stay readable live.
- **Sync.** The worker keeps the index (with the mail client; it runs without filters too). Every 5
  minutes it lists each account's folders, giving a new one its row and dropping the rows of one
  gone or renamed, and syncs each folder on the index that is not ready or was synced 5 minutes
  ago: a first sync fetches newest first in throttled batches (so `indexed` grows towards
  `total`), taking turns with the user's other folders and accounts; a resync adds new mail,
  drops what was expunged or is now older than the age limit, and brings flags up to date
  (CONDSTORE where the server has it), on Gmail the labels with them. A message that cannot be
  read into the index is skipped and stays readable live. A folder whose UIDVALIDITY changed is
  built again. The sync never changes mail. Each commit that changed a folder's rows or state is
  announced as the app's own writes are (below), a batch of new mail with the range of its UIDs
  (`"new": [low, high]`: those from the folder's UIDNEXT at its last pass on, so never on a first
  sync or a rebuild). Details:
  [`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Mail index sync*.
- **Freshness.** Besides the 5-minute turn, the worker watches through IMAP IDLE, per account,
  INBOX when it is on the index (on Gmail All Mail, which INBOX is a label of) and the indexed
  folder a browser viewed last, while that browser's interest lasts (`app.mail_view_interest`,
  each row with its expiry; the API holds them for its event streams, [Live
  updates](#live-updates)): new mail, a flag changed or a message expunged
  there, by any client, syncs that folder at once (one sync waiting per folder). A folder a
  `new_mail` rule set watches shares that watch. Details:
  [`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Filters* (the IDLE
  supervisor).
- **The app's own writes.** A move, archive, report as spam, trash, flag change, move to another
  account, bulk call or folder marked read, from the web or an agent, updates the index in the
  same request from the server's answer, never from another read: a moved message's row leaves
  its folder and, when the server said where it landed (COPYUID) in a folder indexed under that
  UIDVALIDITY, takes its new UID there (its arrival time and flags kept; one older than that
  folder's age limit is left out); a flag change sets the flags asked; a folder marked read gets
  `\Seen` below the UIDNEXT it was marked to. What the answer leaves unsure queues that folder's
  sync instead: a group that failed or was refused, a move without COPYUID or whose source was not
  indexed, a ref whose UIDVALIDITY the index does not hold, a bulk call (its UIDs come without
  one; the rows change and the folders sync besides), a move to another account (its destination
  and the Trash its originals went to), a folder whose sync was running as it was written (that
  sync may have read the flags before the write). On Gmail a folder read from All Mail queues All
  Mail's sync. The index is written once the call answered, outside its deadline (by the call's
  own thread when the deadline cut it). A write whose answer names only where it wrote queues
  those folders' syncs once it went through: a send delivered (Sent, which it was filed in or the
  server filed it in itself, the draft it sent and what it answers or forwards; by the send's own
  thread, the worker's for a held send), a draft saved or imported (Drafts) or deleted (Drafts,
  and Trash for one written elsewhere), and one an agent's send mode kept in Drafts; a send that
  failed changed nothing and queues none. Each folder whose rows changed or whose sync was queued
  is announced on Postgres's `mailroom_index` channel (`pg_notify`,
  `{"user_id", "account_id", "folder_id"}`), for live updates. An index write that fails leaves the mail call's answer as it was (logged as
  `mail_index_write_failed`, with its exception's type only); the next sync puts the rows right.
- **Reads.** A list (with its subfolders too) and a search without `text` or `gmail_query` (over
  one account or all) read the index when every folder they involve is on it and `ready`; else
  they read the server as before the index, the whole request. The folder listing's counts come
  from it folder by folder ([above](#get-accountsaccount_idfolders)). Pages
  keep their shape and order (newest first by arrival, then folder and UID), with a cursor of
  version 3 that keeps that place. On the index `from`, `to` and `subject` match a substring of
  the sender's and To's names and addresses and of the subject, in any case; `since` and `before`
  compare the arrival time with the start of that day in the user's time zone (UTC until they
  set one); `has_attachment` is the message's structure (an attachment part), not the
  `multipart/mixed` approximation. A message's `date` keeps its Date header's UTC offset, as a
  live read gives it (a row indexed before this release has no offset and reads in UTC until its
  folder is indexed again). A folder indexed under an age limit holds only the mail since its
  day (the server's, taken as UTC's): once a list or search has shown that, it goes on live,
  `BEFORE` that day, unless the search's own `since` starts no earlier than that day. That live
  read keeps the user's days too: it asks the server a day more on each side of `since` and
  `before` and keeps what arrived within them, so a search from the age limit's own day by a
  user east of UTC still finds, once, the mail of their first hours of it. On Gmail, INBOX and the label folders
  are read from All Mail's rows by label (INBOX `\Inbox`, Sent `\Sent`, Drafts `\Draft`,
  Starred `\Starred`, Important `\Important`, any other the folder's path), each message with
  its **All Mail ref** and the folder it was listed under (`listed_folder`). A move, archive,
  trash or spam report of such a ref with a source folder (its `source_folders` entry, else
  `source_folder`, the view acted from: INBOX or a label's folder) acts on the message in that
  folder, found by
  its X-GM-MSGID (`UID SEARCH X-GM-MSGID`), so exactly that view's label comes off and the others
  stay; one that folder does not hold, or with no index to tell its id, is acted on as given.
  From All Mail itself (`source_folder` All Mail's, or none), a move only adds the destination's
  label (a copy into its folder), but into Trash or Spam; an archive changes nothing and is
  refused as such (`invalid_argument`); trash and spam move it out of All Mail. Flags act on All
  Mail, where Gmail gives them the same effect; other accounts ignore `source_folder`. A read of
  every folder (`*`) leaves out, off Gmail, a virtual folder off the index (where mailroom-core
  reads `\All` alone, the index reads the folders it repeats). A label folder's counts are still
  STATUS's. MCP reads
  through the same service. When the index cannot be read at all, a first page and the counts go
  live (logged as `mail_index_read_failed`, with the exception's type only).
- **Web.** The *Profile* page's *Index* section (with the mail client) sets the age limit (empty:
  none) and, per account, shows the folders as a tree of checkboxes (nested lists, built as the
  folder pane's tree: split on each folder's `delimiter`, roles first, a level the server did not
  list added): a filter box above the accounts keeps the folders whose path has its text (any
  case) and the levels above them; a parent's checkbox ticks or unticks it with every switchable
  folder shown under it, one call per folder changed (a line counting them while there are more
  than one, stopping at the first refused), and shows mixed (`indeterminate`) when only some are
  on; *All* and *None* by each account's name switch every folder matching the filter. Switching
  more than one folder off asks first, since it drops their indexed mail. Each checkbox is named
  by the folder's whole path: each one's switch (a Gmail label's and a live folder's disabled,
  showing its own state even with switchable folders under it; a label reads from All Mail while
  All Mail is on, else live) and a line with its state, `indexed` out of `total` while it syncs
  (the list read again every 15 seconds while a folder waits or syncs), its counts, day and last
  sync once ready, or the failure's code in words. Saving the age limit reads every account's list
  again. Above the message list, while a folder the list or search reads is waiting for or in its
  first sync (on Gmail, All Mail for a label folder; with subfolders, each listed; in an
  all-accounts search, each account's), a quiet line says how far it has come, from the route
  below with `totals=waiting` (*Indexing: 1,240 of 8,000 messages*, summed over those folders, the
  total only when each one's is known; read again every 15 seconds); nothing once each is ready,
  for a folder off the index, for a search of the text or with Gmail's syntax (always live), or
  when the status cannot be read.

Routes (under `/api/v1/mail`, as the rest of the [REST contract](#rest-contract)):

- `GET /index/accounts/{account_id}`: lists the account's folders (with STATUS, one command per
  folder), giving each one first seen its row, and answers `{"account_id", "gmail",
  "max_age_days", "max_age_cap_days", "folders"}`: the age limit in force and the cap, then each
  selectable folder in the listing's order as `{"path", "delimiter", "role", "indexed_as",
  "enabled", "total", "state", "error", "indexed", "unread", "oldest_indexed", "last_synced_at"}`.
  `indexed_as` is `folder` (indexed on its own), `label` (Gmail, read from All Mail: `enabled` and
  the sync fields are All Mail's, `indexed` and `unread` null) or `live` (never indexed, `enabled`
  false). `delimiter` is the server's hierarchy delimiter (null when it gives none); `total` is
  the server's count (null when its STATUS failed); `state` is `pending`, `syncing`, `ready` or
  `failed` (with `error`, a mailbox failure's code); `indexed` and `unread` count what the index
  holds, so `indexed` out of `total` is a first sync's progress (short of it under an age limit);
  `oldest_indexed` is the age limit's day the last sync kept to. `state` and the fields after it
  are null while the folder is off. With `?totals=waiting` (the message list's indexing line) only
  the folders waiting for or in their sync are asked STATUS (the others' `total` null) and no row
  is written. Errors as the folder listing's.
- `PUT /index/accounts/{account_id}/folders`, `{"folder": "Archive", "enabled": false}` (a JSON
  boolean; unknown fields 422; `INBOX` in any case): `{"account_id", "folder", "enabled"}`. A
  folder the index has not seen yet is looked up on the server first: one it does not list is 404
  `mailbox_missing`; on Gmail, any folder but All Mail is 409 `index_gmail_label`.
- `PUT /api/v1/auth/me/index-max-age`, `{"index_max_age_days": 365}` (a JSON integer, or null):
  sets the user's own limit; `auth/me`'s `user` carries it.

Logged as `mail_index_folders` (the listing, as the other mail calls), `mail_index_folder_switched`
(`user_id`, `account_id`, `enabled` and how many messages' rows went) and `index_max_age_changed`;
never a folder's path.

## Live updates

The index's changes reach the signed-in user's open browsers as server-sent events, while a
Mailroom tab is open: no push service.

- `GET /api/v1/mail/events?account=3&folder=Archive` (a user session, as every mail route; 401
  without) answers `200 text/event-stream` with `Cache-Control: private, no-store` and
  `X-Accel-Buffering: no`, and stays open until the browser goes or its session ends (checked at
  each heartbeat): a comment `: open` first, a comment `: heartbeat` every 25 s, and the events.
  `account` and `folder` (both, or neither; `INBOX` in any case) name the folder the browser
  views: while the stream lasts it holds that folder's viewing interest
  (`app.mail_view_interest`, 90 s past each heartbeat, announced on `mailroom_interest`), so the
  worker watches it over IDLE ([Index](#index), *Freshness*), and its close deletes it unless
  another of the user's streams on that API process views the same folder. A folder that is not
  one of the user's own on the index is not held, and the stream works as without one. Viewing
  another folder is a new stream.
- Events, each the user's own, their data JSON (mail-derived text raw, never HTML):
  - `folder_changed` `{"account", "folder", "unread", "total"}`: an indexed folder's rows changed
    (new mail, flags, expunges, the app's own writes) or its sync was queued; its counts as the
    index holds them. On Gmail the folder is All Mail: INBOX and the label folders read from it
    change with it.
  - `sync` `{"account", "folder", "state", "progress"}`: while a folder is not `ready`
    (`progress`, the messages it holds: the index status's `indexed`), and once as it becomes
    `ready`.
  - `new_mail` `{"account", "folder", "count", "messages"}`: a sync stored new mail still unread
    (above, *Sync*); `count` of it and, for the newest 3, `{"ref", "sender", "subject"}` (the
    sender's name, else address; each text cut at 200 characters), for the user's own browser to
    notify. On Gmail, only All Mail's messages labelled `\Inbox`, as INBOX's, with All Mail's
    refs.
- Refusals: 400 `invalid_interest` for `account` without `folder` or the other way round; 503
  `events_unavailable` without the database; 429 `too_many_streams` past 10 open streams of one
  user on one API process. A stream that falls 100 events behind is closed. Events are not
  replayed: a browser that connects again (EventSource does by itself) reads what it shows
  again.
- The web client opens one stream while a user is signed in on an instance with the mail client
  (closed at sign-out), naming the folder the mail screen shows (none for a search), and opens it
  again when that changes, then reads the folders' counts again (events are not replayed across the
  switch). A failure closes it and opens it again after 1 s, doubled each time up to a minute;
  meanwhile a quiet line above the list says *Reconnecting to live updates…*, and once open again
  it reads the folders, lists and index statuses again. `folder_changed` sets the folder's counts
  in the sidebar (on Gmail, All Mail's: the account's folders are read again), and they stand over
  the counts the screen guesses from an action's answer: a folder whose live count arrived after
  the action's request started keeps it, as it already holds that change. `folder_changed` and
  `new_mail` for a folder the list reads (with its subfolders, every folder of an all-folders
  search, any on Gmail through All Mail) read its first page again: new rows come in at the top and
  the rows loaded below stay (those new mail pushed off the first page too; those gone from it are
  dropped; a next page answered after it for a cursor it no longer ends at is dropped too, and
  read again from where the list now ends), as do the selection, the open message and the rows in
  view, which stay where they are unless the list is scrolled to its very top, where new mail
  shows; the rows read again carry their flags as
  changed elsewhere, the open message's too. A search the index never answers (a body's text,
  Gmail's syntax) is not read again. `sync` moves the indexing line (*Index*) on, and takes it away
  once the folder is ready.
- How: the worker's sync and the API's own writes announce each folder change on Postgres's
  `mailroom_index` (ids only, and the new mail's UID range), delivered at their commit; each API
  process LISTENs on one connection of its own and, for a user with a stream open there, reads
  the folder's row and the new rows to build the events
  ([`nonfunctional-requirements.md`](nonfunctional-requirements.md) — *Mail client*).

## Desktop notifications

New mail shows as the browser's own notifications (the Notification API) while a Mailroom tab is
open: no push service, no service worker. They come from the [live updates](#live-updates)'
`new_mail` events.

- Which folders notify is kept per user and account on the server
  (`app.mail_notification_folders`; no row: INBOX alone, so every account's INBOX notifies until
  the user chooses). `GET /api/v1/mail/notifications` gives `{"accounts": [{"account_id",
  "folders"}]}` for each of the user's accounts; `PUT
  /api/v1/mail/notifications/accounts/{account_id}` `{"folders": [...]}` (at most 200, `INBOX` in
  any case, kept once each) sets all of one account's at once and answers
  `{"account_id", "folders"}`; another user's account is 404 `not_found`. A folder renamed in
  Mailroom ([Folder management](#folder-management)) stays chosen under its new path, its
  subfolders too; one deleted in Mailroom leaves the choice with its subfolders. That database
  step is best-effort, like a rename's rule sets: its failure is logged
  (`folder_notifications_failed`, no paths) and the folder change stands; the web reads the
  choice again after either. A folder renamed or removed outside Mailroom stays in the choice and
  no longer notifies. Removing the account forgets its choice.
- The Profile's *Desktop notifications* section asks the browser's permission only from its
  *Allow desktop notifications* button, never on load, or says the browser shows them, blocks
  them (to allow in its site settings) or cannot: no Notification API, or one whose constructor
  throws (Chrome for Android leaves notifications to a service worker, which Mailroom has none
  of), tried before the permission is granted, when it shows nothing, or learnt from a
  notification that failed. Per account it lists the folders whose new mail can notify, each
  with a checkbox: those on the index (another server's `\All` folder too), and on Gmail INBOX
  instead of All Mail (read from All Mail, whose `new_mail` is INBOX's).
- With the permission granted, a `new_mail` event for a chosen folder shows one notification per
  message it names (the newest 3): the sender as its title and the subject as its body, nothing
  else (*(unknown sender)*, *(no subject)* when empty), tagged with the ref so that two open tabs
  show it once; when `count` is more, one more says *N new messages*. None shows for the folder
  the focused tab shows, this tab or another: the tab that is visible and focused announces the
  account and folder it shows over `BroadcastChannel('mailroom-viewing')` on focus, on a
  visibility change and when it opens another folder, and null on blur, when hidden or unloaded
  (only the tab that announced a folder takes it back); every tab skips that folder as well as its
  own while focused. Without BroadcastChannel only the tab's own counts. Clicking one focuses the
  tab and opens the message in its folder (the summary, the folder). The choice is read when an
  event first needs it, and again after a minute.

## Views

A view is a list of the user's mail kept in the sidebar: some folders of any of their accounts,
or a search in the [search syntax](#search-syntax), read from the [index](#index) only. *All
inboxes* is built in: INBOX of every account (on Gmail, All Mail's rows labelled INBOX). The
others are the user's saved views (`app.saved_view`: name, folders or search, position), theirs
alone and gone with them; removing an account takes its folders out of every view (one left
with none lists nothing).

- `GET /api/v1/mail/views` gives `{"views": [{"id", "name", "sources", "query", "position"}]}`
  in the user's order, *All inboxes* not among them; `sources` is `[{"account_id", "folder"}]`
  (`INBOX` in capitals) or null for a search, `query` the search's line or null.
- `POST /api/v1/mail/views` `{"name", "sources"}` or `{"name", "query"}` (exactly one) creates
  one, last, `201`; `PATCH /api/v1/mail/views/{id}` renames it (`name`) or gives it other folders
  or another search, which it then is; `DELETE` removes it, `204`, the rest closing up; `PUT
  /api/v1/mail/views/order` `{"view_ids": [...]}`, every view once, orders them (else 400
  `invalid_order`). A name is 1-100 characters, trimmed, one per user (else 409
  `view_name_taken`); 1-100 folders, of the user's own accounts (else 404 `not_found`); at most
  100 views (else 400 `too_many_views`). A search must parse and must not search the text (bare
  words), which the index cannot answer (400 `invalid_argument`). Another user's view is 404.
- `GET /api/v1/mail/views/{id}/messages?limit=25&cursor=` (`id` `all_inboxes` for the built-in)
  answers as [a list](#get-accountsaccount_idmessagesfolderinboxlimit25cursorsubfoldersfalse),
  plus `not_indexed`: the view's folders off the index or not ready, left out, on the first page
  (`folder` `*`: an account's every folder). Its folders' rows are merged newest first on the
  index's cursor; nothing is read live, not even what an age limit leaves out. A search reads
  `in:`'s folder (`*`: every folder, as a search's), else INBOX, of `account:`'s account, else of
  every account, as `POST /search` does; an `account:` that no longer names one is 404
  `not_found`, a line that no longer parses (the syntax changed since) 400 `invalid_argument`.
  The server is asked only what the index cannot say (which folder is Gmail's All
  Mail, which label a folder is; a search's every folder): an account that cannot answer is a
  failure of the page for that folder. A cursor whose folder changed is 409 `stale_reference`.
  Each Gmail row names the folder it was listed under (`listed_folder`), which an action on it
  sends back (`source_folders`), so it leaves that folder's label only. `group=thread` groups it
  [by conversation](#conversations), every folder of the view together. MCP has no views.

## Conversations

A list, a search or a view read from the [index](#index) can be grouped by conversation with
`group=thread` (`POST /search`: `"group": "thread"`). A conversation is the source's messages
(those matching the search) sharing a key within one account, which the index sets as it stores
each message (`mailroom.mail.index.threads`): on Gmail its thread (`X-GM-THRID`); elsewhere the
key of the message it answers, its References root, else its first In-Reply-To id, but only when
the index already holds that message **in the same account** and both have the same subject
once reply and forward prefixes (`Re:`, `Fwd:`, `Fw:`, `AW:`, `WG:`, `SV:`, `R:`, `I:`... also
numbered or repeated), whitespace and case are set aside; else its own Message-ID. A message
with no Message-ID is alone. References are the sender's to write: a stranger naming a
conversation's Message-ID with a subject of their own gets a row of their own. A folder is
indexed newest first, so a reply often lands before what it answers: once that is stored, its
replies (and what was keyed under them) join its conversation. The page holds one message per
conversation, its latest, newest first by it and paged on the index's cursor by it, and
`conversations` beside `messages`, in the same order:

```jsonc
{
  "count": 3,                    // its messages in the source, the user's own copies included
  "unread": 1,                   // so is this, and the three below
  "participants": ["Ada Lovelace", "bob@example.org"],  // senders, first written first, at most 4
  "has_attachments": true,       // any of them
  "flagged": false,              // any of them
  "refs": ["WzIsMSwiSU5CT1gi…"], // newest first: what an action on the row takes
  "listed_folders": {"WzIsMSwiSU5CT1gi…": "INBOX"},  // as each one's listed_folder
  "latest_from_new_sender": false
}
```

- `refs` leave out the user's own copies: messages in a folder with the role Sent or Drafts, and
  on Gmail those labelled `\Sent` or `\Draft`, so archiving, moving, trashing or reporting a
  conversation never takes what the user wrote; they are still counted and read with it (`GET
  /messages/{ref}/thread`). A conversation of nothing else (the Sent folder listed) keeps them.
  The folders' roles are asked of the server only for a conversation spread over several
  folders that are not Gmail labels.
- `latest_from_new_sender` is true when the latest message's sender address (any case) is
  neither the sender nor a To or Cc recipient of any other of its messages (a copy of the latest
  one, by Message-ID, is not another); false for a conversation of one, or a sender without an
  address. The web asks before acting on such a conversation whole.

A source read live (off the index, or a search of the text or with Gmail's syntax) is not
grouped: `conversations` is null and the page is of messages, as without `group`. What an age
limit leaves out of a folder, read live once its rows run out, comes one message to a
conversation. Grouping changes no ref, and an action on a conversation's refs checks each one
live, as any other.

- `GET /messages/{ref}/thread?limit=30` (`limit` 1-50): the conversation the message belongs to,
  read live as MCP's `thread_read` reads it (its folder and Sent, matched by Message-ID,
  References and In-Reply-To; on Gmail its thread from All Mail), newest first, as a page without
  a cursor. Errors as `GET /messages/{ref}`.
- **Setting.** Each user's `conversations` (`app.user`, true by default) says whether the web
  groups their lists ([*Conversations*](#slice-a-read) there):
  `PUT /api/v1/auth/me/conversations`, `{"conversations": false}` (a JSON boolean), answers the
  user; `auth/me`'s `user` carries it; logged as `conversations_changed`.

## Mailbox quota

`GET /api/v1/accounts/{id}/quota` (the signed-in user's own account; mounted in every edition)
gives the quota the account's INBOX falls under, from the server's `GETQUOTAROOT` (RFC 2087):
`{"account_id", "storage", "messages"}`, each `{"used", "limit"}` or null, `storage` in bytes.
Both are null when the server offers no `QUOTA`, answers `NO` to `GETQUOTAROOT INBOX` (it
advertises `QUOTA` but keeps none for this mailbox) or limits neither. An answer that names no
quota root (Mailu's Dovecot sends no `QUOTAROOT` line) counts every `QUOTA` line it carries; a `NO [UNAVAILABLE]` is a
temporary failure instead, and a `BAD` a server error. An answer, a null one too, is kept for 10
minutes per account (in memory, per process), and asked afresh once the user's accounts change;
a failure is not kept and answers as the mail routes do (`mail_status`). The accounts listing
asks no server, and MCP's `accounts_list` gives no quota.

The web's *Accounts* page asks each account's quota on its own, so a slow or failing server holds
up no other row: a bar each for storage and messages (used, limit, share), marked *Nearly full* in
the destructive colour from 90%. Past the limit, or with a limit of 0, it reads *Over quota* with
the real share (110%; none for a limit of 0), while the bar and its `aria-valuenow` (a share out of
100) stop at full. "Unknown" when the server reports neither; a failure as a muted line with
**Check again**, never an alert.

## Account autoconfiguration

`GET /api/v1/accounts/autoconfig?address=<email>` (the signed-in user; mounted in every edition)
suggests an account's IMAP and SMTP servers for an address
(`mailroom.accounts.autoconfig`). It stores nothing: no account, no address, no log line naming
either. It asks, in this order, and answers with the first that gives both servers:

1. The domain's own autoconfig document, `https://autoconfig.<domain>/mail/config-v1.1.xml`, then
   `https://<domain>/.well-known/autoconfig/mail/config-v1.1.xml`, without the address.
2. DNS SRV records (RFC 6186): `_imaps._tcp` (implicit TLS), else `_imap._tcp` (STARTTLS); and
   `_submissions._tcp` (implicit), else `_submission._tcp` (STARTTLS). The lowest priority wins,
   then the highest weight; a target `.` means the service is not offered. Login: the address.
3. The domain's MX against known providers: Google (Google Workspace) suggests Gmail; Microsoft
   (`*.protection.outlook.com`) suggests Outlook, marked unsupported.
4. Thunderbird's ISPDB, `https://autoconfig.thunderbird.net/v1.1/<domain>`, which learns only the
   domain.
5. Built-in presets by domain: Gmail, iCloud, Fastmail and Yahoo (each wanting an app password),
   Outlook and the other Microsoft domains (unsupported until OAuth2).

A provider's `authserv_id` is the authserv-id its Authentication-Results fields carry (RFC 8601),
for the account's own (*Sender authentication* in [`GLOSSARY.md`](../GLOSSARY.md)). Only Gmail has
one, `mx.google.com`. iCloud, Fastmail, Yahoo and Microsoft are left without: their receiving hosts
each write their own name, or split the results across several, so no one name can be trusted for
them, and the user enters it if they know it. A suggestion's is only that, editable like the rest:
reading a message uses the account's `authserv_id` as stored and never picks one by the IMAP
host.

Every fetch goes through `safe_fetch` ([`nonfunctional-requirements.md`](nonfunctional-requirements.md)
— *Security*), two redirects at most, 64 KiB and 8 s each. The XML is parsed by lxml with no
entity resolved, no DTD loaded and no network. From a document, only IMAP and SMTP servers with
implicit TLS or STARTTLS, a password login (or none named) and a username of `%EMAILADDRESS%` or
`%EMAILLOCALPART%` count, the first IMAP server with the first SMTP server of the same login. DNS
goes through dnspython, each query within 3 s. The whole lookup has 20 s, after which only a
preset answers. An answer is kept for 10 minutes per domain (in memory, per process, at most
1024 domains), and a timed-out lookup is not kept.

The answer: `{"domain", "suggestion"}`, `domain` lower case and IDNA-encoded, `suggestion` null
when nothing answered, else `source` (`autoconfig`, `srv`, `mx`, `ispdb` or `preset`), `origin`
(the document's URL, the SRV names or the MX host; null for a preset), `provider` (a known one's
name, also when its servers came from a document, else null), `supported` (false for Microsoft),
`note` (`app_password`, `oauth_required` or null), `login` (`address` or `local_part`),
`imap` and `smtp` as `{"host", "port", "tls", "outside_domain"}`, and `authserv_id`: the known
provider's authentication server, wherever the suggestion came from, null for any other. Not an
address: 400 `invalid_address`. Each user may look up 20 addresses a minute (429
`autoconfig_busy`). Logged as
`account_autoconfig` with `user_id` and the `outcome` (the source, or `none`).

A host the domain's own autoconfig document or its SRV records name that is neither the domain nor
a name under it (`imap.hoster.example` for `example.org`; `badexample.org` is not under it either)
is kept, since domains often hand their mail to a hoster, but its endpoint says
`"outside_domain": true`, so that the address's owner can check it before giving it the
password. A known provider's own hosts are not outside: when the IMAP host names a provider
(`provider` set), that provider's IMAP and SMTP hosts are never flagged, though another host
beside them still is. Hosts from the ISPDB, the MX match and the presets are never flagged
(`outside_domain` is false), since Mailroom or Thunderbird chose them, not the domain.

The web's *Accounts* form adds an account starting from the address alone, with **Find settings**
(or Enter) and **Enter settings manually**. A suggestion fills the login, the IMAP and SMTP
servers and the authentication server (emptied when it has none), and the form says where it
came from (the document's URL, the DNS records, the provider
the MX points to, Thunderbird's database, Mailroom's own presets) and when the provider wants an
app password; everything stays editable. Nothing found, or a failed lookup, offers the supported
presets (Gmail, iCloud, Fastmail, Yahoo, as the web's own copy of the API's list). An unsupported
provider (Microsoft) is named as not supported yet and fills nothing. Entering the settings by
hand drops a lookup still running, and changing the address forgets what the last one found
(the fields it filled stay). Editing an account does not look anything up. A suggestion with an
endpoint `outside_domain` fills nothing at first: the form names the hosts outside the domain,
says the password would go to them, and asks **Use** (fill it, then focus the password) or
**Don't use** (fill nothing, and offer the presets and manual entry as when nothing is found).

## MCP

The same service layer backs the `mail_read` tool group
([`mcp.md`](mcp.md)): `messages_list`, `messages_search`, `message_read`, `thread_read`,
`attachment_read`. The REST routes carry the structured shapes above; the tools return
mailroom-core's text inside the untrusted envelope, its hints naming the app's tools, its dates
in the user's time zone (named on the answer's last line), and a collapsed quote as a marker line
that says how to read it. The web reads a thread over REST ([Conversations](#conversations)).

## Later slices

- Threaded list view ([`../REQUIREMENTS.md`](../REQUIREMENTS.md)).
