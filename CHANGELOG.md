# Changelog

## [0.8.0](https://github.com/sgorblex/mailroom/compare/v0.7.0...v0.8.0) (2026-11-12)


### ⚠ BREAKING CHANGES

* **core:** the filter engine's public API changes. `Account` takes `(connections: MailConnections, account_id)` instead of a name, host, port, credentials, address, delimiter, SSL context and timeout; `.name` is now `.account_id`, the connection attributes and `Account.connect()` / `Folder.connect()` are gone, and `delimiter()` is a method that asks the server (was an attribute). `folders()` (was `list_folders`) lists special-use folders first, then by path, split on each folder's own delimiter. `Folder.run`, `run_rules` and `folders()` raise `MailError` with core's codes instead of imaplib or socket errors, and a Trash action with no Trash folder is `MailError("unsupported")` (was `ValueError`). `run_rules(parallel=True)` shares the account's pool between its folders, so with a pool of one they wait for each other and may fail `busy`. `monitor` also runs on every connect and ends a watch on a refused login or a missing folder. `mailroom_core.filters.idle` is removed. `Folder.run` takes `dry_run` and `newest` (at least 1) as keywords. An `Action`'s function is `(selected: Selected, uids)` and `Action.execute(selected, uids)`, with `Selected` exported. `IMAPQuery.build()` is replaced by `criteria()`, core's search criteria. `FolderReport` gains `limited_to_newest`.
* mailbox failure codes are renamed in the API, the MCP tools' answers and the recorded runs: `login_failed` becomes `auth_failed` (409 in the folder listing and dry run, was 502); `tls_failed` becomes `unreachable`, whose message ends with "Its TLS certificate did not verify." when that is the reason; `imap_error` becomes `auth_failed`, `mailbox_missing` (404), `unsupported`, `unreachable` or `server_error`. Every message is core's fixed text, never the server's. `timeout` and `unreachable` keep their codes; `password_missing`, `secrets_key_missing` and `starttls_unsupported` stay the app's own.

### Features

* **core:** connections from plain config ([#9](https://github.com/sgorblex/mailroom/issues/9)) ([f78295f](https://github.com/sgorblex/mailroom/commit/f78295f8539426ac744828fb0bacc11732e332da))
* **core:** IDLE and a login test in mail ([#8](https://github.com/sgorblex/mailroom/issues/8)) ([eb18dd9](https://github.com/sgorblex/mailroom/commit/eb18dd96373c679d66b10eae7343ad14feee0479))
* **core:** operations on the selected folder, organize built on them ([#7](https://github.com/sgorblex/mailroom/issues/7)) ([4847cb7](https://github.com/sgorblex/mailroom/commit/4847cb7f4749cce2c44d618d5f181f24fbffee81))
* **core:** the filter engine on core's connections ([#13](https://github.com/sgorblex/mailroom/issues/13)) ([2057875](https://github.com/sgorblex/mailroom/commit/20578752a2ed48b862de406785cab07293ae0ad4))
* mailbox failures use core's error codes ([#10](https://github.com/sgorblex/mailroom/issues/10)) ([07b8f52](https://github.com/sgorblex/mailroom/commit/07b8f5215bbb76c8463f19c5e1a9634df97e386a))
* test login and the folder picker on the mail pool ([#11](https://github.com/sgorblex/mailroom/issues/11)) ([6be325e](https://github.com/sgorblex/mailroom/commit/6be325e38b2c4b480b8fe31daee620a8c89f8e92))
* the worker watches folders with core's IdleWatch ([#12](https://github.com/sgorblex/mailroom/issues/12)) ([3a77f89](https://github.com/sgorblex/mailroom/commit/3a77f89a8c7426e85fff32dbcede15a81f1ba457))

## [0.7.0](https://github.com/sgorblex/mailroom/compare/v0.6.0...v0.7.0) (2026-11-10)


### Features

* keep an audit trail of mail writes with configurable retention ([5b5f333](https://github.com/sgorblex/mailroom/commit/5b5f333da6eb6de07e67eaa7b4dc1c780bff390a))
* list the audit trail through a REST endpoint and an MCP tool ([9792d1b](https://github.com/sgorblex/mailroom/commit/9792d1b89e995523d012f62b93fb840e61855409))
* **web:** add an Activity page to review the audit trail ([0e70046](https://github.com/sgorblex/mailroom/commit/0e70046918291cd2216cbc71fafde455920f1c95))
* **worker:** record filter runs in the audit trail ([1486aa0](https://github.com/sgorblex/mailroom/commit/1486aa0b22c8566a8f8ee43b8e0fc30971012b3e))

## [0.6.0](https://github.com/sgorblex/mailroom/compare/v0.5.1...v0.6.0) (2026-11-09)


### Features

* add MAILROOM_FEATURES to choose which features an instance runs ([be5c033](https://github.com/sgorblex/mailroom/commit/be5c03387e15bdb962f7a92e66a0f4814915eadf))
* let users set their own mail limits from the Profile page ([f4c104c](https://github.com/sgorblex/mailroom/commit/f4c104cf15b75300bf78a147272862b5cb66e4d2))
* **web:** edit every identity of an account in the account form ([3bf9420](https://github.com/sgorblex/mailroom/commit/3bf9420bd2ae93d0dfe20d087d239521c29e7eae))
* **web:** show only the features an instance runs ([58ce154](https://github.com/sgorblex/mailroom/commit/58ce154846d21d17cefb287fe59bab167f598da3))
* **worker:** exit at startup unless the filters feature is enabled ([5b0b49e](https://github.com/sgorblex/mailroom/commit/5b0b49e8c1e88602849309718c3002acf59f7e09))

## [0.5.1](https://github.com/sgorblex/mailroom/compare/v0.5.0...v0.5.1) (2026-11-09)


### Bug Fixes

* **mail:** add name attributes to message list and search inputs ([b701832](https://github.com/sgorblex/mailroom/commit/b70183272a332b0252f38830b970b0a2adeffc70))
* **mail:** keep composer text selectable while a send is running ([6802776](https://github.com/sgorblex/mailroom/commit/68027768384240e41649b8c6f1601a1d0e9c68ed))

## [0.5.0](https://github.com/sgorblex/mailroom/compare/v0.4.0...v0.5.0) (2026-11-07)


### Features

* add drafts and send REST API for the mail client ([2813eed](https://github.com/sgorblex/mailroom/commit/2813eed30ab4423e2dcd3e755cc292e790c41de7))
* add mail_draft and mail_send tools to the MCP server ([6fac09b](https://github.com/sgorblex/mailroom/commit/6fac09b663f3c2660b176acb6dc93fea490430cd))
* **mail:** compose, reply, forward and send in the web client ([a75a197](https://github.com/sgorblex/mailroom/commit/a75a197b9bdaa82a7ccb86da72cc5185a739d1cf))

## [0.4.0](https://github.com/sgorblex/mailroom/compare/v0.3.0...v0.4.0) (2026-11-05)


### Features

* add mail organize REST API (move, archive, trash, flag) ([27eee0c](https://github.com/sgorblex/mailroom/commit/27eee0cc837ef32feb420657fcbddd517ff9cfd4))
* add mail_organize tools to the MCP server ([77772bf](https://github.com/sgorblex/mailroom/commit/77772bfc319a6ae9b3b31a1b5640f9030aeece9b))
* add web mail triage with toolbar, multi-select, and keyboard shortcuts ([6ba0289](https://github.com/sgorblex/mailroom/commit/6ba02895f484657743b1a29f0012aba3b3bd3b51))
* move, archive, trash and flag mail through the REST API ([27eee0c](https://github.com/sgorblex/mailroom/commit/27eee0cc837ef32feb420657fcbddd517ff9cfd4))
* **web:** triage mail: select, archive, trash, move, star and mark read, with shortcuts ([6ba0289](https://github.com/sgorblex/mailroom/commit/6ba02895f484657743b1a29f0012aba3b3bd3b51))


### Bug Fixes

* search Gmail accounts for attachments through X-GM-RAW ([391a92f](https://github.com/sgorblex/mailroom/commit/391a92f8a86e8540507776f96025789066c5bad7))
* use Gmail's has:attachment in all-accounts search ([391a92f](https://github.com/sgorblex/mailroom/commit/391a92f8a86e8540507776f96025789066c5bad7))

## [0.3.0](https://github.com/sgorblex/mailroom/compare/v0.2.0...v0.3.0) (2026-11-03)


### Features

* add a web mail client with folders, message list, search, and sandboxed HTML view ([2c88bae](https://github.com/sgorblex/mailroom/commit/2c88baeeae79e167aaa9d69ac93a3b5570c59981))
* add mail_read tools to the MCP server ([69aa7a1](https://github.com/sgorblex/mailroom/commit/69aa7a1b6e8f68ae2803baf63f98c433b1218c1d))
* add read-only mail client REST API ([e33bb39](https://github.com/sgorblex/mailroom/commit/e33bb394864953d420216b93a388b3486e5f9aa7))


### Bug Fixes

* set dry run created_at to its start time ([a630784](https://github.com/sgorblex/mailroom/commit/a63078420aee20ab7ab2437c87c3bf917fc3548b))
* show every filter action in plain words in run reports ([7801fa0](https://github.com/sgorblex/mailroom/commit/7801fa0b3f0ec67d0d4ec576668541cb58654866))

## [0.2.0](https://github.com/sgorblex/mailroom/compare/v0.1.0...v0.2.0) (2026-10-26)


### Features

* accept external OIDC issuer JWTs on the MCP endpoint ([b80f01f](https://github.com/sgorblex/mailroom/commit/b80f01ff0c0229b785d4f85c2a51a7cd7c24bf7f))
* add a built-in OAuth server for MCP, with a consent screen ([ddc4362](https://github.com/sgorblex/mailroom/commit/ddc4362280afed574f3628444d9bf11c4cfb0d84))
* add an MCP server at /mcp with filter tools ([52559af](https://github.com/sgorblex/mailroom/commit/52559af21e2e12b5e6b2ef6eddeace16147d91bb))
* add OIDC single sign-on with linked sign-ins ([7394dd7](https://github.com/sgorblex/mailroom/commit/7394dd78050111bc2afb36b9efbea8c64945d7f2))
* let users list and revoke their connected apps ([310f09d](https://github.com/sgorblex/mailroom/commit/310f09d70b601b7b746546a37742d0e0c706d083))


### Bug Fixes

* override dev defaults in mise.local.toml and isolate the migration check ([5e8f576](https://github.com/sgorblex/mailroom/commit/5e8f576da0e4acc88eb5cf93ddf4f21f32312880))
* polish OIDC/MCP issuer handling, fake IdP algorithms, and confirm-removal focus ([6bfbc81](https://github.com/sgorblex/mailroom/commit/6bfbc8145d73054d5af624015dbe6425ceb37f3c))

## 0.1.0 (2026-10-25)


### Features

* add filters API with rule sets, dry runs and queued runs ([631e027](https://github.com/sgorblex/mailroom/commit/631e027d12b4993f6e1e77e3c611e7fe2f4a1c6a))
* add the filters screens ([7331a91](https://github.com/sgorblex/mailroom/commit/7331a91efe72922a5d6bbfe5c12bd01eb6c93050))
* depend on mailroom-core and imap-thingy, with a task for local editable checkouts ([df4c17a](https://github.com/sgorblex/mailroom/commit/df4c17ac72d2ecde67e8d90961703e0b0f752605))
* let admins manage users and users change their password ([a261823](https://github.com/sgorblex/mailroom/commit/a261823fbc09638bcb44db37d290f675f84671d0))
* let users manage their mail accounts ([1a7133f](https://github.com/sgorblex/mailroom/commit/1a7133f73652e01978fd8d4a24b82711f6defa78))
* run queued filter runs in the worker, with scheduled and IDLE new-mail triggers ([d25370b](https://github.com/sgorblex/mailroom/commit/d25370b84182184e148ffeec8be6b48e56258184))
* scaffold a procrastinate background worker ([8f99a96](https://github.com/sgorblex/mailroom/commit/8f99a96ecbc655418be5c231ee0c4f17ef309185))
* sign users in with a password session ([494150a](https://github.com/sgorblex/mailroom/commit/494150a9c1b22ba7aeeef3dfbbe10d13c8c477cd))
