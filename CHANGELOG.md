# Changelog

## [0.6.0](https://github.com/sgorblex/mailroom/compare/v0.5.1...v0.6.0) (2026-11-09)


### Features

* add MAILROOM_FEATURES to choose which features an instance runs ([#66](https://github.com/sgorblex/mailroom/issues/66)) ([63268d3](https://github.com/sgorblex/mailroom/commit/63268d34d78f667f2e5c8aefdd95fdd3aacb06fe))
* let users set their own mail limits from the Profile page ([#71](https://github.com/sgorblex/mailroom/issues/71)) ([fbda8e7](https://github.com/sgorblex/mailroom/commit/fbda8e72036c99b30c9b1ad9024fb73cb3308692))
* **web:** edit every identity of an account in the account form ([#72](https://github.com/sgorblex/mailroom/issues/72)) ([9f2b8bd](https://github.com/sgorblex/mailroom/commit/9f2b8bd483f7a085f8ad92fb09668a86ab07ed53))
* **web:** show only the features an instance runs ([#68](https://github.com/sgorblex/mailroom/issues/68)) ([0d6cb18](https://github.com/sgorblex/mailroom/commit/0d6cb184aeec605d3cafd26a472fe1f93b27a697))
* **worker:** exit at startup unless the filters feature is enabled ([#67](https://github.com/sgorblex/mailroom/issues/67)) ([713dd66](https://github.com/sgorblex/mailroom/commit/713dd6624a93841015486c1818716e5694839a2b))

## [0.5.1](https://github.com/sgorblex/mailroom/compare/v0.5.0...v0.5.1) (2026-11-09)


### Bug Fixes

* **mail:** add name attributes to message list and search inputs ([#58](https://github.com/sgorblex/mailroom/issues/58)) ([1604a93](https://github.com/sgorblex/mailroom/commit/1604a937f6d82b7574493cb6380e05a567250816))
* **mail:** keep composer text selectable while a send is running ([#57](https://github.com/sgorblex/mailroom/issues/57)) ([95b7755](https://github.com/sgorblex/mailroom/commit/95b775562560edac817223f40cc95abf04307e5e))

## [0.5.0](https://github.com/sgorblex/mailroom/compare/v0.4.0...v0.5.0) (2026-11-07)


### Features

* add drafts and send REST API for the mail client ([#52](https://github.com/sgorblex/mailroom/issues/52)) ([2ac9424](https://github.com/sgorblex/mailroom/commit/2ac9424db5d1c2fe23c0707254c54bf01a0591d1))
* add mail_draft and mail_send tools to the MCP server ([#54](https://github.com/sgorblex/mailroom/issues/54)) ([ced4f5e](https://github.com/sgorblex/mailroom/commit/ced4f5e5bd0a0469861d884cc84a0af708128acd))
* **mail:** compose, reply, forward and send in the web client ([#53](https://github.com/sgorblex/mailroom/issues/53)) ([e458b81](https://github.com/sgorblex/mailroom/commit/e458b81c67b2e68433fc8aa6ef95076f8959084d))

## [0.4.0](https://github.com/sgorblex/mailroom/compare/v0.3.0...v0.4.0) (2026-11-05)


### Features

* add mail organize REST API (move, archive, trash, flag) ([#42](https://github.com/sgorblex/mailroom/issues/42)) ([16bf612](https://github.com/sgorblex/mailroom/commit/16bf61211ea0e2580cd9251ce980de087e2d49bc))
* add mail_organize tools to the MCP server ([#44](https://github.com/sgorblex/mailroom/issues/44)) ([e341dc1](https://github.com/sgorblex/mailroom/commit/e341dc1e103f486111ed7b9578b6aaf42e56e1cc))
* add web mail triage with toolbar, multi-select, and keyboard shortcuts ([#43](https://github.com/sgorblex/mailroom/issues/43)) ([60df4fc](https://github.com/sgorblex/mailroom/commit/60df4fcd2b31e5308efd0f2220aaec53ab07e558))
* move, archive, trash and flag mail through the REST API ([16bf612](https://github.com/sgorblex/mailroom/commit/16bf61211ea0e2580cd9251ce980de087e2d49bc))
* **web:** triage mail: select, archive, trash, move, star and mark read, with shortcuts ([60df4fc](https://github.com/sgorblex/mailroom/commit/60df4fcd2b31e5308efd0f2220aaec53ab07e558))


### Bug Fixes

* search Gmail accounts for attachments through X-GM-RAW ([fc2e6b9](https://github.com/sgorblex/mailroom/commit/fc2e6b90a1160568bbc4721742123c798b02b0ed))
* use Gmail's has:attachment in all-accounts search ([#45](https://github.com/sgorblex/mailroom/issues/45)) ([fc2e6b9](https://github.com/sgorblex/mailroom/commit/fc2e6b90a1160568bbc4721742123c798b02b0ed))

## [0.3.0](https://github.com/sgorblex/mailroom/compare/v0.2.0...v0.3.0) (2026-11-03)


### Features

* add a web mail client with folders, message list, search, and sandboxed HTML view ([#38](https://github.com/sgorblex/mailroom/issues/38)) ([707d641](https://github.com/sgorblex/mailroom/commit/707d641e3a045e9c516644ed2c8d7cad428aacb5))
* add mail_read tools to the MCP server ([#39](https://github.com/sgorblex/mailroom/issues/39)) ([763dc8d](https://github.com/sgorblex/mailroom/commit/763dc8d0f532f6ea4bab2ebad4376f6ade8aee7f))
* add read-only mail client REST API ([#37](https://github.com/sgorblex/mailroom/issues/37)) ([7c63744](https://github.com/sgorblex/mailroom/commit/7c637441042eadb05660665a955a5353cd235d60))


### Bug Fixes

* set dry run created_at to its start time ([#31](https://github.com/sgorblex/mailroom/issues/31)) ([df099d3](https://github.com/sgorblex/mailroom/commit/df099d3f048b0c46ef966efe7d3992cbd6101e90))
* show every filter action in plain words in run reports ([#34](https://github.com/sgorblex/mailroom/issues/34)) ([e187850](https://github.com/sgorblex/mailroom/commit/e187850401c47ded11e0cd058f55465b6372232b))

## [0.2.0](https://github.com/sgorblex/mailroom/compare/v0.1.0...v0.2.0) (2026-10-26)


### Features

* accept external OIDC issuer JWTs on the MCP endpoint ([#27](https://github.com/sgorblex/mailroom/issues/27)) ([ebe6c3f](https://github.com/sgorblex/mailroom/commit/ebe6c3fdd24354705832822f691afbdfd68726f9))
* add a built-in OAuth server for MCP, with a consent screen ([#25](https://github.com/sgorblex/mailroom/issues/25)) ([de534ab](https://github.com/sgorblex/mailroom/commit/de534aba1756740e8e590ea49b5c71114b0b852e))
* add an MCP server at /mcp with filter tools ([#24](https://github.com/sgorblex/mailroom/issues/24)) ([8147a56](https://github.com/sgorblex/mailroom/commit/8147a56878bd0d77d0510969845134a0e8db002a))
* add OIDC single sign-on with linked sign-ins ([#23](https://github.com/sgorblex/mailroom/issues/23)) ([c65bf35](https://github.com/sgorblex/mailroom/commit/c65bf35538bc0bdf574ad186d8e3ae24d18c5d5a))
* let users list and revoke their connected apps ([#26](https://github.com/sgorblex/mailroom/issues/26)) ([88f7134](https://github.com/sgorblex/mailroom/commit/88f713462bf1264a7937c01d0ec73dfee8ef894c))


### Bug Fixes

* override dev defaults in mise.local.toml and isolate the migration check ([#28](https://github.com/sgorblex/mailroom/issues/28)) ([5c74f7f](https://github.com/sgorblex/mailroom/commit/5c74f7fde9e8eb66ae07a682b4cecae447d29d46))
* polish OIDC/MCP issuer handling, fake IdP algorithms, and confirm-removal focus ([#30](https://github.com/sgorblex/mailroom/issues/30)) ([e6db9f8](https://github.com/sgorblex/mailroom/commit/e6db9f825c77421c805ee5e1fa97fd8872a2c73c))

## 0.1.0 (2026-10-25)


### Features

* add filters API with rule sets, dry runs and queued runs ([#18](https://github.com/sgorblex/mailroom/issues/18)) ([d83d580](https://github.com/sgorblex/mailroom/commit/d83d58013f0868ec6dd4569ef6f55db72b4baddb))
* add the filters screens ([#20](https://github.com/sgorblex/mailroom/issues/20)) ([52c6f5b](https://github.com/sgorblex/mailroom/commit/52c6f5bdce078202b65db51a5c0d1342f5aa15be))
* depend on mailroom-core and imap-thingy, with a task for local editable checkouts ([#15](https://github.com/sgorblex/mailroom/issues/15)) ([bc273ab](https://github.com/sgorblex/mailroom/commit/bc273abb3d74f2c1c3d07aeff17949b88ac0ff6b))
* let admins manage users and users change their password ([#8](https://github.com/sgorblex/mailroom/issues/8)) ([559ed52](https://github.com/sgorblex/mailroom/commit/559ed52ab5708700fbdef80fe8806d2f06a40490))
* let users manage their mail accounts ([#17](https://github.com/sgorblex/mailroom/issues/17)) ([d5a05e4](https://github.com/sgorblex/mailroom/commit/d5a05e496e5a667db5b26fbd029b41300e0ba810))
* run queued filter runs in the worker, with scheduled and IDLE new-mail triggers ([#19](https://github.com/sgorblex/mailroom/issues/19)) ([4050c51](https://github.com/sgorblex/mailroom/commit/4050c51ced777a6de811cc545ec722d0676f22a5))
* scaffold a procrastinate background worker ([#16](https://github.com/sgorblex/mailroom/issues/16)) ([d36878f](https://github.com/sgorblex/mailroom/commit/d36878f939e2f38e8af3b01b627c0b1c3fb2ac03))
* sign users in with a password session ([#6](https://github.com/sgorblex/mailroom/issues/6)) ([bc4309f](https://github.com/sgorblex/mailroom/commit/bc4309f4b2a9fe472c2214bde14094c96ab4010f))
