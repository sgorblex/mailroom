# Changelog

## [0.15.0](https://github.com/sgorblex/mailroom/compare/v0.14.0...v0.15.0) (2026-11-22)


### Features

* **core:** compose HTML bodies with inline images ([#158](https://github.com/sgorblex/mailroom/issues/158)) ([5ddaafc](https://github.com/sgorblex/mailroom/commit/5ddaafc829ed6f486ae26d07e8ad9f9c628a7b70))
* **mail:** a reply quotes only the text selected in the message ([#161](https://github.com/sgorblex/mailroom/issues/161)) ([3205c02](https://github.com/sgorblex/mailroom/commit/3205c021445164e491454e8c815bb6bd0d7ba079))
* **mail:** a signature per identity, in new messages, replies, forwards and agents' mail ([#162](https://github.com/sgorblex/mailroom/issues/162)) ([4205ded](https://github.com/sgorblex/mailroom/commit/4205ded6ca40e137974fa355b260bf4d4f180f93))
* **mail:** answer new mail with each account's vacation reply ([#168](https://github.com/sgorblex/mailroom/issues/168)) ([696d0ad](https://github.com/sgorblex/mailroom/commit/696d0ad1735b6b62ae3f954b08760a62a26328c1))
* **mail:** hold a draft for the worker to send later through the send gate ([#164](https://github.com/sgorblex/mailroom/issues/164)) ([03f1d99](https://github.com/sgorblex/mailroom/commit/03f1d990f25ba35ee9ed0bd172626c453c2cdbde))
* **mail:** hold the web's Send for an Undo, sent by the worker ([#165](https://github.com/sgorblex/mailroom/issues/165)) ([ef52c17](https://github.com/sgorblex/mailroom/commit/ef52c1734f6760fa8126343a4b433c4148a43e3f))
* **mail:** import a designed message as a draft, kept as built and checked ([#169](https://github.com/sgorblex/mailroom/issues/169)) ([bd6fd35](https://github.com/sgorblex/mailroom/commit/bd6fd353c8f088a45bdbfb53c91eed5c6d6ae334))
* **mail:** save, read and send HTML drafts through the API and MCP ([#159](https://github.com/sgorblex/mailroom/issues/159)) ([fc541ab](https://github.com/sgorblex/mailroom/commit/fc541ab7cff1497a4ad6d93a681a742a3fa4f482))
* **mail:** send a draft later, from the composer or an agent ([#166](https://github.com/sgorblex/mailroom/issues/166)) ([c2c1658](https://github.com/sgorblex/mailroom/commit/c2c1658180ae9c99ea04dc1313ab16e89575cd8f))
* **mail:** snooze a message until a time, brought back by the worker ([#167](https://github.com/sgorblex/mailroom/issues/167)) ([d0e17cd](https://github.com/sgorblex/mailroom/commit/d0e17cdf1f33b8ba3e4989d6d2c35ab7e5e36a0b))
* **mail:** suggest recipients from the mail index as they are typed ([#163](https://github.com/sgorblex/mailroom/issues/163)) ([24dd2a8](https://github.com/sgorblex/mailroom/commit/24dd2a843d92996bbae2e92c63fbfac670e77604))
* **web:** import designed mail, preview it, check it and send a test ([#170](https://github.com/sgorblex/mailroom/issues/170)) ([d2d14bd](https://github.com/sgorblex/mailroom/commit/d2d14bd9f7b4c246661a43ec901d74890204fb81))
* **web:** write mail in a rich-text editor with inline images ([#160](https://github.com/sgorblex/mailroom/issues/160)) ([f63ee36](https://github.com/sgorblex/mailroom/commit/f63ee36da6a88cb0275f02284fc74a5edddd6277))


### Bug Fixes

* **mail:** a forward's prefill starts from the identity the message arrived at ([#156](https://github.com/sgorblex/mailroom/issues/156)) ([e7727a4](https://github.com/sgorblex/mailroom/commit/e7727a44141e44ebfcce31b452c6ab881ad38465))
* **mail:** compare index dates by the user's day in their time zone ([#153](https://github.com/sgorblex/mailroom/issues/153)) ([cefc739](https://github.com/sgorblex/mailroom/commit/cefc739a611fb4f8a24862ab716a4cf163a598ff))
* **mail:** count Gmail label folders from All Mail's index rows ([#152](https://github.com/sgorblex/mailroom/issues/152)) ([7fc8c86](https://github.com/sgorblex/mailroom/commit/7fc8c863dc096e134b834dd2fa55e61110dc9129))
* **mail:** keep the Date header's offset on index rows ([#154](https://github.com/sgorblex/mailroom/issues/154)) ([b736bff](https://github.com/sgorblex/mailroom/commit/b736bff96432b343d8b3feecd35734568c3c3011))
* **mail:** notification folders follow a renamed or deleted folder ([#155](https://github.com/sgorblex/mailroom/issues/155)) ([bc9eca0](https://github.com/sgorblex/mailroom/commit/bc9eca002c4147c943405dede40415b5419b23f2))

## [0.14.0](https://github.com/sgorblex/mailroom/compare/v0.13.0...v0.14.0) (2026-11-21)


### Features

* **core:** fetch what the mail index keeps per message ([#130](https://github.com/sgorblex/mailroom/issues/130)) ([a7a1313](https://github.com/sgorblex/mailroom/commit/a7a13131b8ae1af93a935b6739bc67ab59fdda2b))
* **core:** read folder UIDs and flag changes with CONDSTORE ([#129](https://github.com/sgorblex/mailroom/issues/129)) ([127a05b](https://github.com/sgorblex/mailroom/commit/127a05bb2857f4fa1ada6e07f9d9ccdfca060802))
* **index:** add the mail index tables ([#131](https://github.com/sgorblex/mailroom/issues/131)) ([92111bd](https://github.com/sgorblex/mailroom/commit/92111bd1ecc963cd945a97277371221552d12256))
* **index:** choose which folders the index keeps, and how far back ([#132](https://github.com/sgorblex/mailroom/issues/132)) ([f04ded7](https://github.com/sgorblex/mailroom/commit/f04ded7259d5f7913015c0c6e0146944d43e17f3))
* **index:** read lists, searches and folder counts from the mail index ([#136](https://github.com/sgorblex/mailroom/issues/136)) ([39e6a1a](https://github.com/sgorblex/mailroom/commit/39e6a1a1ab524dc36875956a0b6703107a051104))
* **index:** update the mail index from the app's own writes ([#135](https://github.com/sgorblex/mailroom/issues/135)) ([ad4edf5](https://github.com/sgorblex/mailroom/commit/ad4edf5ac0838947dbbba067afb03256e14bb389))
* **mail:** group lists, searches and views by conversation on the index ([#147](https://github.com/sgorblex/mailroom/issues/147)) ([d232b52](https://github.com/sgorblex/mailroom/commit/d232b52af10ede89000002cc48f773fab83b00a9))
* **mail:** keep saved views and read All inboxes from the index ([#145](https://github.com/sgorblex/mailroom/issues/145)) ([7b7b058](https://github.com/sgorblex/mailroom/commit/7b7b0583e452ddbf595fd51965efd0e92f2d1778))
* **mail:** parse a search syntax and take it in MCP's messages_search ([#142](https://github.com/sgorblex/mailroom/issues/142)) ([6e1eacf](https://github.com/sgorblex/mailroom/commit/6e1eacf93e1430ec8c4624a405805f2c6e232459))
* **mail:** push the index's changes to the browser as server-sent events ([#139](https://github.com/sgorblex/mailroom/issues/139)) ([36ce60f](https://github.com/sgorblex/mailroom/commit/36ce60f56dc2a1fd09ad10ee3b027e78607e5fe1))
* **web:** follow the index's live updates in the mail client ([#140](https://github.com/sgorblex/mailroom/issues/140)) ([a44d1b2](https://github.com/sgorblex/mailroom/commit/a44d1b2892d7347fe4a23e67bd43c7c7742dafed))
* **web:** quick filters above the message list ([#144](https://github.com/sgorblex/mailroom/issues/144)) ([f41f162](https://github.com/sgorblex/mailroom/commit/f41f1620e7aab794989b1aee5244cbeb38fec918))
* **web:** read the search syntax in the search box, with chips ([#143](https://github.com/sgorblex/mailroom/issues/143)) ([515fa96](https://github.com/sgorblex/mailroom/commit/515fa966ae8cf04ef85c8fa1265251628dd36e56))
* **web:** say above the message list how far a folder's indexing has come ([#137](https://github.com/sgorblex/mailroom/issues/137)) ([5469a88](https://github.com/sgorblex/mailroom/commit/5469a881b6f51d853736491e437f6eaba8ac6b2e))
* **web:** set the mail index's age limit and folders on the Profile page ([#133](https://github.com/sgorblex/mailroom/issues/133)) ([ee6c04c](https://github.com/sgorblex/mailroom/commit/ee6c04c86680f1083ddcb55dee5080c453aecc03))
* **web:** show lists by conversation, read and act on a conversation whole ([#148](https://github.com/sgorblex/mailroom/issues/148)) ([62f8235](https://github.com/sgorblex/mailroom/commit/62f8235bc86c01e3d47ef6d036a72107f5dfc43c))
* **web:** show new mail as desktop notifications ([#141](https://github.com/sgorblex/mailroom/issues/141)) ([0563b22](https://github.com/sgorblex/mailroom/commit/0563b22c85ba78ebc84f75de92cba7c2b5871a6f))
* **web:** views in the sidebar, and save a search as one ([#146](https://github.com/sgorblex/mailroom/issues/146)) ([271fd3d](https://github.com/sgorblex/mailroom/commit/271fd3d846a36bb2f8897e62d0bf1ef8d292395b))
* **web:** virtualised message list that reads its pages as it is scrolled ([#149](https://github.com/sgorblex/mailroom/issues/149)) ([9bd9e77](https://github.com/sgorblex/mailroom/commit/9bd9e77ec37191f149964b15ab72d04134292732))
* **worker:** keep the index fresh over IMAP IDLE ([#138](https://github.com/sgorblex/mailroom/issues/138)) ([6044338](https://github.com/sgorblex/mailroom/commit/60443389c0f86b180f67c5e9e5ab3d6c5b6b4c87))
* **worker:** sync the mail index's folders ([#134](https://github.com/sgorblex/mailroom/issues/134)) ([2e12b92](https://github.com/sgorblex/mailroom/commit/2e12b927971969b026afd6bbe83483f7a26777b8))


### Bug Fixes

* **core:** accept a From under header.from as DMARC aligned ([#126](https://github.com/sgorblex/mailroom/issues/126)) ([efc34ac](https://github.com/sgorblex/mailroom/commit/efc34acd8c310cb91eb15b6e51fa13554fd11ada))
* **web:** keep password managers off the command palette input ([#123](https://github.com/sgorblex/mailroom/issues/123)) ([9bcbc77](https://github.com/sgorblex/mailroom/commit/9bcbc7783f08b48b4036b2bcd0329092eb3db5cd))
* **web:** name the subfolders switch by what it shows ([#124](https://github.com/sgorblex/mailroom/issues/124)) ([8ba5b24](https://github.com/sgorblex/mailroom/commit/8ba5b24d6e50cbcf3c9cb6244a02e0597b0e0729))
* **web:** widen the command palette and build its combobox on Downshift ([#125](https://github.com/sgorblex/mailroom/issues/125)) ([0671b4b](https://github.com/sgorblex/mailroom/commit/0671b4b80ffdb834551475c054bf9044c79ab355))

## [0.13.0](https://github.com/sgorblex/mailroom/compare/v0.12.0...v0.13.0) (2026-11-20)


### Features

* **accounts:** name each account's authentication server ([#96](https://github.com/sgorblex/mailroom/issues/96)) ([18a03ec](https://github.com/sgorblex/mailroom/commit/18a03ec371ba2f5a64a8855a8f3938f45cc2b8d9))
* **api:** count and act on every message a search matches ([#92](https://github.com/sgorblex/mailroom/issues/92)) ([25c5983](https://github.com/sgorblex/mailroom/commit/25c59834f024aac5efebcef85172ab0a3554476e))
* **api:** mark a folder read up to the UIDNEXT the user saw ([#90](https://github.com/sgorblex/mailroom/issues/90)) ([315615b](https://github.com/sgorblex/mailroom/commit/315615b3e8958d6ca45f6b25a069698a38df08cd))
* **core:** a filter action that moves to another account ([#119](https://github.com/sgorblex/mailroom/issues/119)) ([ca2d2e6](https://github.com/sgorblex/mailroom/commit/ca2d2e6b0e36341ee490d7bd2e7662f35da04784))
* **core:** add the To sort keyword ($MailroomSort) ([#82](https://github.com/sgorblex/mailroom/issues/82)) ([12177f6](https://github.com/sgorblex/mailroom/commit/12177f616882b1b5a6ed23d0fda67888ef457265))
* **core:** filter on list, sender domain and thread; junk action ([#108](https://github.com/sgorblex/mailroom/issues/108)) ([21cb714](https://github.com/sgorblex/mailroom/commit/21cb714c78c3bdc6efd7207ee39dc6eba3f7c6dd))
* **core:** parse list and authentication headers on every read ([#95](https://github.com/sgorblex/mailroom/issues/95)) ([30fb44b](https://github.com/sgorblex/mailroom/commit/30fb44b99bf7f3a6025e82f948cccc3a1016ecbb))
* **core:** show each link's real host, mark misleading links and count remote images ([#99](https://github.com/sgorblex/mailroom/issues/99)) ([d74dcf1](https://github.com/sgorblex/mailroom/commit/d74dcf1140c47bd7218a17a0a9b9e8901d3c6848))
* **filters:** count each filter's matches over 30 days ([#113](https://github.com/sgorblex/mailroom/issues/113)) ([809043b](https://github.com/sgorblex/mailroom/commit/809043bf7c024c18a2c154bdc8b6a3566f825448))
* **filters:** draft a filter from a message or a search ([#109](https://github.com/sgorblex/mailroom/issues/109)) ([0dea801](https://github.com/sgorblex/mailroom/commit/0dea801bbbe30837da080c14939633bd86a6c898))
* **filters:** filters that move to another account ([#120](https://github.com/sgorblex/mailroom/issues/120)) ([bbcd428](https://github.com/sgorblex/mailroom/commit/bbcd42893d5b62ba8a4a9f283bbbc80384a38f20))
* **filters:** mute a thread ([#115](https://github.com/sgorblex/mailroom/issues/115)) ([05eb2fb](https://github.com/sgorblex/mailroom/commit/05eb2fb14808435d98dc81dc636c848114be6d9d))
* **filters:** rule sets for all accounts ([#117](https://github.com/sgorblex/mailroom/issues/117)) ([715649c](https://github.com/sgorblex/mailroom/commit/715649c9c250358ed721da1fff6e4eb4db6b9c4a))
* let a rule set limit itself to messages marked To sort ([#83](https://github.com/sgorblex/mailroom/issues/83)) ([959ab74](https://github.com/sgorblex/mailroom/commit/959ab74263725be5f3a4293534414b200d6b820d))
* **mail:** check a message's sender, recipient and authentication on read ([#97](https://github.com/sgorblex/mailroom/issues/97)) ([4fdb9f3](https://github.com/sgorblex/mailroom/commit/4fdb9f3641abeb6b836b8e1c5afabd6d4eb5fb20))
* **mail:** mark a forwarded original $Forwarded and show the marks on rows ([#106](https://github.com/sgorblex/mailroom/issues/106)) ([012dce4](https://github.com/sgorblex/mailroom/commit/012dce4ad47fa8c871366addbfc751b8d7db92f8))
* **mail:** print a message and download its .eml source ([#107](https://github.com/sgorblex/mailroom/issues/107)) ([c0391a1](https://github.com/sgorblex/mailroom/commit/c0391a164f64969bbe12c0a87b6781cdd9108b27))
* **mail:** remember the senders whose remote content the user shows ([#101](https://github.com/sgorblex/mailroom/issues/101)) ([65d44c8](https://github.com/sgorblex/mailroom/commit/65d44c8dc2ddbd8e489e8cbebc9f0a532fff07a3))
* **mail:** report spam to the account's Junk folder ([#111](https://github.com/sgorblex/mailroom/issues/111)) ([5e61f43](https://github.com/sgorblex/mailroom/commit/5e61f43a93d19c9b97a3250e3e35a7bb792a80be))
* **mail:** unsubscribe from a list in one click or by a drafted mail ([#104](https://github.com/sgorblex/mailroom/issues/104)) ([8ce6c96](https://github.com/sgorblex/mailroom/commit/8ce6c96c6dff90b25a35e243a19fc63a8a3433f1))
* **net:** let safe_fetch POST a small form, with no redirect and no referrer ([#103](https://github.com/sgorblex/mailroom/issues/103)) ([a13bb30](https://github.com/sgorblex/mailroom/commit/a13bb309bf45a74559dded7dbb5f77c2d8ea6561))
* **web:** command palette on Ctrl/Cmd+K ([#94](https://github.com/sgorblex/mailroom/issues/94)) ([2e92e4f](https://github.com/sgorblex/mailroom/commit/2e92e4fa60cbf37c2e6e02cb6122e380ce283bfd))
* **web:** count the remote images beside the gate, blocked or shown ([#100](https://github.com/sgorblex/mailroom/issues/100)) ([5bd9a85](https://github.com/sgorblex/mailroom/commit/5bd9a85f213095143343bd5db62f7c4eeca2089b))
* **web:** draft a filter from a message or a search ([#110](https://github.com/sgorblex/mailroom/issues/110)) ([be376bd](https://github.com/sgorblex/mailroom/commit/be376bd36179b91a23967bbae8e0ec95608bb949))
* **web:** drag messages onto a folder to move them ([#89](https://github.com/sgorblex/mailroom/issues/89)) ([0260b92](https://github.com/sgorblex/mailroom/commit/0260b926a2b835f3342810a5c6bcea74e32fa06b))
* **web:** list the keyboard shortcuts on ? ([#87](https://github.com/sgorblex/mailroom/issues/87)) ([94b72f5](https://github.com/sgorblex/mailroom/commit/94b72f56e7a564bcbe38deb56d27489d02cd4814))
* **web:** mark all of a folder as read from its menu ([#91](https://github.com/sgorblex/mailroom/issues/91)) ([ab0a1a2](https://github.com/sgorblex/mailroom/commit/ab0a1a21b01f974f7f43308a6366657bf0a18323))
* **web:** mark mail To sort from the mail screen ([#84](https://github.com/sgorblex/mailroom/issues/84)) ([9064923](https://github.com/sgorblex/mailroom/commit/9064923949520a39d757bce17949c40c2343fb4e))
* **web:** mute a conversation and list the muted ones ([#116](https://github.com/sgorblex/mailroom/issues/116)) ([fbea1b6](https://github.com/sgorblex/mailroom/commit/fbea1b658b4974ed6c20f91a13bc2a6589f5d0fd))
* **web:** offer Undo in a toast after moving or flagging mail ([#88](https://github.com/sgorblex/mailroom/issues/88)) ([b97abde](https://github.com/sgorblex/mailroom/commit/b97abde709396fc4282a96bf17d49642236a31d2))
* **web:** pick the account and folder of a move to another account ([#121](https://github.com/sgorblex/mailroom/issues/121)) ([a2613a1](https://github.com/sgorblex/mailroom/commit/a2613a1b4745e19c3800fb0bb23d1c4f6d6f526c))
* **web:** report spam, and offer a filter sending the sender to Junk ([#112](https://github.com/sgorblex/mailroom/issues/112)) ([07407ca](https://github.com/sgorblex/mailroom/commit/07407ca6d5e9f243decaf7a32c4e76a006dc3093))
* **web:** rule sets for all accounts ([#118](https://github.com/sgorblex/mailroom/issues/118)) ([54b63cd](https://github.com/sgorblex/mailroom/commit/54b63cdda9265b0b9e0ce4daf122ff4cf7453f93))
* **web:** select and act on every message a list or search matches ([#93](https://github.com/sgorblex/mailroom/issues/93)) ([f976a3b](https://github.com/sgorblex/mailroom/commit/f976a3b9d512ea22f2270776cd47e5fc2ae3c455))
* **web:** share one action registry and add context menus ([#86](https://github.com/sgorblex/mailroom/issues/86)) ([f22c214](https://github.com/sgorblex/mailroom/commit/f22c214c78cca7c82fa4dacb5d66be747434ddcc))
* **web:** show a message's mailing list, with its archive and unsubscribe ([#105](https://github.com/sgorblex/mailroom/issues/105)) ([838984b](https://github.com/sgorblex/mailroom/commit/838984b38ba9f001b25e33fb370796248eae97e8))
* **web:** show a trusted sender's remote images and list them on Profile ([#102](https://github.com/sgorblex/mailroom/issues/102)) ([499cdde](https://github.com/sgorblex/mailroom/commit/499cdde83d3ca728bbbc3321a9974085bed1df33))
* **web:** show each filter's matches over 30 days and its last edit ([#114](https://github.com/sgorblex/mailroom/issues/114)) ([00908ce](https://github.com/sgorblex/mailroom/commit/00908ce04f237f5e23878b7b8c6281e87b99e223))
* **web:** show sender checks, Sent to and a recipient summary in the message view ([#98](https://github.com/sgorblex/mailroom/issues/98)) ([715b32f](https://github.com/sgorblex/mailroom/commit/715b32fa18453497bb2b4eb77c976d6b449ab697))


### Bug Fixes

* **accounts:** repair duplicate account names and refuse only new ones ([#81](https://github.com/sgorblex/mailroom/issues/81)) ([f0b8c00](https://github.com/sgorblex/mailroom/commit/f0b8c00cb096f97eb1d30bbf466553a21872197d))
* **core:** carry an account's failure in the page cursor ([#80](https://github.com/sgorblex/mailroom/issues/80)) ([e368538](https://github.com/sgorblex/mailroom/commit/e368538b9242eeaa763ffad68a03403adbbe7403))
* **core:** read a quota answer that names no quota root ([#77](https://github.com/sgorblex/mailroom/issues/77)) ([d2832c4](https://github.com/sgorblex/mailroom/commit/d2832c4f7f3fa54e432c5198e261f2c37d809af4))
* **mcp:** take an account_id sent as a string of digits ([#79](https://github.com/sgorblex/mailroom/issues/79)) ([777c257](https://github.com/sgorblex/mailroom/commit/777c257a3b51a1cadcb46f49700a77d991a5a028))

## [0.12.0](https://github.com/sgorblex/mailroom/compare/v0.11.1...v0.12.0) (2026-11-18)


### ⚠ BREAKING CHANGES

* number account ids instead of user-chosen slugs ([#67](https://github.com/sgorblex/mailroom/issues/67))

### Features

* an MCP grant records the tool groups its user approved, and its client uses only those ([#54](https://github.com/sgorblex/mailroom/issues/54)) ([7b02bd9](https://github.com/sgorblex/mailroom/commit/7b02bd98c8693fa63c19ad9a02fb794392ea34ce))
* give each account an optional name and colour ([#65](https://github.com/sgorblex/mailroom/issues/65)) ([2572f4c](https://github.com/sgorblex/mailroom/commit/2572f4c6260e43993349408345e375f614d2c08e))
* keep a time zone per user and write their dates in it ([#63](https://github.com/sgorblex/mailroom/issues/63)) ([9f8030f](https://github.com/sgorblex/mailroom/commit/9f8030fa6cee465e6638865491984ac8e707df11))
* list and search a folder with its subfolders over REST and MCP ([#61](https://github.com/sgorblex/mailroom/issues/61)) ([43a6465](https://github.com/sgorblex/mailroom/commit/43a646580ab74dbd93a677bf085d0864c367133a))
* show each account's mailbox quota ([#71](https://github.com/sgorblex/mailroom/issues/71)) ([5793286](https://github.com/sgorblex/mailroom/commit/57932867f18162e6813ea2a3466dbd12e5199666))
* suggest an account's servers from its address ([#69](https://github.com/sgorblex/mailroom/issues/69)) ([bc33655](https://github.com/sgorblex/mailroom/commit/bc3365594cf2e55eedf87f74805a2b5a8fda82f2))
* **web:** choose an app's tool groups on the consent screen and change them under Connected apps ([#55](https://github.com/sgorblex/mailroom/issues/55)) ([aabaffc](https://github.com/sgorblex/mailroom/commit/aabaffc13ed019e4595838bce2ed3ce7874dedb1))
* **web:** find an account's settings from its address ([#70](https://github.com/sgorblex/mailroom/issues/70)) ([d84f833](https://github.com/sgorblex/mailroom/commit/d84f83379f4bbf7885b49a20504f05936bc99fc0))
* **web:** list a folder with its subfolders from the folder tree ([#62](https://github.com/sgorblex/mailroom/issues/62)) ([9e0d707](https://github.com/sgorblex/mailroom/commit/9e0d7074a57ec7ce3b9c9e3bfb9ca8ec890f762a))
* **web:** pick a time zone on Profile and write dates in it ([#64](https://github.com/sgorblex/mailroom/issues/64)) ([fb8b15e](https://github.com/sgorblex/mailroom/commit/fb8b15ece1634cc1367f95cb371c6a72d40f94e3))
* **web:** show each account by its name and colour ([#66](https://github.com/sgorblex/mailroom/issues/66)) ([c058364](https://github.com/sgorblex/mailroom/commit/c058364d18a42e57c343ab28d43d501f71421a17))
* **web:** show each account's mailbox usage ([#72](https://github.com/sgorblex/mailroom/issues/72)) ([70ed3c1](https://github.com/sgorblex/mailroom/commit/70ed3c1b8b60ea0539562664e8c2470f95144e64))
* **web:** star a message from its row in the list ([#75](https://github.com/sgorblex/mailroom/issues/75)) ([44da350](https://github.com/sgorblex/mailroom/commit/44da3504592c2cef144d9f7086a19042fd897e83))


### Bug Fixes

* **core:** check exact address criteria on the fetched message ([#59](https://github.com/sgorblex/mailroom/issues/59)) ([91df7b6](https://github.com/sgorblex/mailroom/commit/91df7b6c8250fad79096fb0a46035b7a6fb8ac26))
* **core:** drop a failing folder from the page cursor ([#60](https://github.com/sgorblex/mailroom/issues/60)) ([24b3f0e](https://github.com/sgorblex/mailroom/commit/24b3f0e538df2a702dbbc29bdb978bf87b43f6b6))
* fetch client metadata documents through the SSRF-safe fetch ([#76](https://github.com/sgorblex/mailroom/issues/76)) ([e591fb0](https://github.com/sgorblex/mailroom/commit/e591fb0417e3a3374b6c0d0e3a2c174dc5b75145))


### Code Refactoring

* number account ids instead of user-chosen slugs ([#67](https://github.com/sgorblex/mailroom/issues/67)) ([25b248d](https://github.com/sgorblex/mailroom/commit/25b248dc8ce55c16494bef6090cff9b7e7287dde))

## [0.11.1](https://github.com/sgorblex/mailroom/compare/v0.11.0...v0.11.1) (2026-11-14)


### Bug Fixes

* **core:** find a message by Message-ID without the server's header search ([#47](https://github.com/sgorblex/mailroom/issues/47)) ([dd9abee](https://github.com/sgorblex/mailroom/commit/dd9abee2999aa1c127018d738ddae03b3655e1fe))
* **core:** follow a conversation where the server cannot search its headers ([#48](https://github.com/sgorblex/mailroom/issues/48)) ([da73f24](https://github.com/sgorblex/mailroom/commit/da73f249da49629246a4a9039b60717276d58848))

## [0.11.0](https://github.com/sgorblex/mailroom/compare/v0.10.0...v0.11.0) (2026-11-14)


### Features

* **core:** move messages to another account ([#42](https://github.com/sgorblex/mailroom/issues/42)) ([bcbad1e](https://github.com/sgorblex/mailroom/commit/bcbad1e86094ac9cb1ebd4ffc68722a2d1c915cc))
* move messages to another of the user's accounts over REST ([#43](https://github.com/sgorblex/mailroom/issues/43)) ([ab72b87](https://github.com/sgorblex/mailroom/commit/ab72b87e600c5c16182e276ba231f34011851f98))
* the mail_transfer MCP group moves mail to another account ([#45](https://github.com/sgorblex/mailroom/issues/45)) ([8647d21](https://github.com/sgorblex/mailroom/commit/8647d21e03e853a95d90ae94919672812cd646e5))
* **web:** move messages to another account from the mail toolbar ([#44](https://github.com/sgorblex/mailroom/issues/44)) ([71866d2](https://github.com/sgorblex/mailroom/commit/71866d2acd501739c9f25e1dcda3731493099240))


### Bug Fixes

* an SSO sign-in lands signed in, and one on /connect claims its request ([#36](https://github.com/sgorblex/mailroom/issues/36)) ([aed322d](https://github.com/sgorblex/mailroom/commit/aed322dda25ec9cba66909c264e4162314cd309d))
* **web:** a lazy screen opened directly no longer logs a use() error ([#37](https://github.com/sgorblex/mailroom/issues/37)) ([f97571d](https://github.com/sgorblex/mailroom/commit/f97571d96dcf872d302e8d8ecf804e3e435147c3))

## [0.10.0](https://github.com/sgorblex/mailroom/compare/v0.9.1...v0.10.0) (2026-11-13)


### ⚠ BREAKING CHANGES

* **core:** drop the retired mailroom-mcp's leftovers ([#31](https://github.com/sgorblex/mailroom/issues/31))
* **core:** filters read mail through core's MIME handling; an HTML body matches as text ([#30](https://github.com/sgorblex/mailroom/issues/30))

### Features

* MCP clients connect through an SSO sign-in (MAILROOM_MCP_AUTH=oidc) ([#35](https://github.com/sgorblex/mailroom/issues/35)) ([86a9691](https://github.com/sgorblex/mailroom/commit/86a9691957927f632d3b5d6caa14deb792afb521))


### Bug Fixes

* **core:** filters read mail through core's MIME handling; an HTML body matches as text ([#30](https://github.com/sgorblex/mailroom/issues/30)) ([15d18ce](https://github.com/sgorblex/mailroom/commit/15d18cef53b7e97eb195d2c3e03e6408f3f2a6a8))


### Code Refactoring

* **core:** drop the retired mailroom-mcp's leftovers ([#31](https://github.com/sgorblex/mailroom/issues/31)) ([5a19302](https://github.com/sgorblex/mailroom/commit/5a193024ddff0cefd1cc08b7e1bcb3cd168a1d5c))

## [0.9.1](https://github.com/sgorblex/mailroom/compare/v0.9.0...v0.9.1) (2026-11-13)


### Bug Fixes

* **core:** a missing folder's refusal no longer names a retired tool ([#27](https://github.com/sgorblex/mailroom/issues/27)) ([e2b78a0](https://github.com/sgorblex/mailroom/commit/e2b78a0e5f3efcf942c4f019a442fbbcb017f44c))

## [0.9.0](https://github.com/sgorblex/mailroom/compare/v0.8.2...v0.9.0) (2026-11-13)


### Features

* **core:** create, rename and delete an account's folders ([#19](https://github.com/sgorblex/mailroom/issues/19)) ([850c6c6](https://github.com/sgorblex/mailroom/commit/850c6c61e52cbe781a81d4c9cba267cf53247ca3))
* create, rename and delete folders over REST, rule sets following a rename ([#20](https://github.com/sgorblex/mailroom/issues/20)) ([7f92116](https://github.com/sgorblex/mailroom/commit/7f9211694f229d2c60265b66a6bc140885a43690))
* the mail_folders MCP group ([#21](https://github.com/sgorblex/mailroom/issues/21)) ([3ba5ac9](https://github.com/sgorblex/mailroom/commit/3ba5ac9bb41b4f2452e151b2f92901836e208fe3))
* **web:** create, rename and delete folders from the mail sidebar ([#23](https://github.com/sgorblex/mailroom/issues/23)) ([6e0409e](https://github.com/sgorblex/mailroom/commit/6e0409e6afbb7bfc586ae1ba9408b750108eb52d))
* **web:** the mail sidebar's folders as a tree ([#22](https://github.com/sgorblex/mailroom/issues/22)) ([ff9645b](https://github.com/sgorblex/mailroom/commit/ff9645b784fe5c8b63c7b3a43746d595bf298ed4))

## [0.8.2](https://github.com/sgorblex/mailroom/compare/v0.8.1...v0.8.2) (2026-11-12)


### Bug Fixes

* a worker that loses its database exits 1, not 0 ([#17](https://github.com/sgorblex/mailroom/issues/17)) ([824571b](https://github.com/sgorblex/mailroom/commit/824571b6d3b7f9ded1c506066fed6a352160b12a))

## [0.8.1](https://github.com/sgorblex/mailroom/compare/v0.8.0...v0.8.1) (2026-11-12)


### Bug Fixes

* structured core logs, and a temporary login failure is not a rejected login ([#15](https://github.com/sgorblex/mailroom/issues/15)) ([9a933ec](https://github.com/sgorblex/mailroom/commit/9a933ecc580dbe240a3f64e02d789b5be40d56d8))

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
