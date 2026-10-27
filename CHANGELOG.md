# Changelog

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
