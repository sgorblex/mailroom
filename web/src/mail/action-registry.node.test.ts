import { describe, expect, it, vi } from 'vitest'

import type { MailFolder } from '@/api/mail.ts'

import {
  dropAction,
  listedActions,
  MAIL_ACTIONS,
  mailAction,
  runnable,
  shortcutAction,
  unavailableReason,
  type ActionContext,
  type MailAction,
} from './action-registry.ts'
import { NO_TRIAGE_ACTIONS, type TriageActions } from './triage-actions.ts'
import type { Target } from './triage-context.ts'

const target: Target = { ref: 'r1', account_id: 1, folder: 'INBOX', flags: { seen: true, flagged: false, to_sort: false } }

function context(actions: Partial<TriageActions> = {}, overrides: Partial<ActionContext> = {}): ActionContext {
  return {
    actions: { ...NO_TRIAGE_ACTIONS, ...actions },
    message: null,
    account: null,
    compose: { start: vi.fn(), canCompose: () => true },
    openPicker: vi.fn(),
    ...overrides,
  }
}

/** Every triage action offered, on read targets none of which is starred or To sort. */
const offered: Partial<TriageActions> = {
  targets: [target],
  archive: vi.fn(),
  trash: vi.fn(),
  star: { flagged: true, run: vi.fn() },
  toSort: { toSort: true, unsupported: false, run: vi.fn() },
  read: { seen: false, run: vi.fn() },
  markUnread: vi.fn(),
  moveChoices: [],
  otherAccounts: [2],
}

const ids = (group: MailAction['group'], each: ActionContext): string[] =>
  listedActions(group, each).map((action) => action.id)

describe('the action registry', () => {
  it('greys out all but the flag changes, the move and the trash when every matching message is selected', () => {
    const matching = context({ ...offered, matching: 1200 })
    const greyed = listedActions('triage', matching)
      .filter((action) => unavailableReason(action, matching) !== null)
      .map((action) => action.id)
    expect(greyed).toEqual(['archive', 'moveToAccount'])
    expect(shortcutAction('e', matching, { busy: false, composing: false })).toBeUndefined()
    expect(shortcutAction('#', matching, { busy: false, composing: false })?.id).toBe('trash')
  })

  it('lists both ways of each flag change when every matching message is selected', () => {
    const both = context({ ...offered, read: { seen: true, run: vi.fn() }, matching: 1200, unstar: vi.fn(), markSorted: vi.fn() })
    expect(ids('triage', both)).toEqual([
      'archive',
      'trash',
      'move',
      'moveToAccount',
      'star',
      'unstar',
      'toSort',
      'markSorted',
      'markRead',
      'markUnread',
    ])
  })

  it('has one action per id and per key', () => {
    const keys = MAIL_ACTIONS.flatMap((action) => action.shortcut ?? [])
    expect(new Set(MAIL_ACTIONS.map((action) => action.id)).size).toBe(MAIL_ACTIONS.length)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it('lists no triage action without targets, and each one the targets allow, in toolbar order', () => {
    expect(ids('triage', context())).toEqual([])
    expect(ids('triage', context(offered))).toEqual([
      'archive',
      'trash',
      'move',
      'moveToAccount',
      'star',
      'toSort',
      'markUnread',
    ])
    expect(ids('triage', context({ ...offered, archive: null, otherAccounts: null, read: { seen: true, run: vi.fn() } }))).toEqual([
      'trash',
      'move',
      'star',
      'toSort',
      'markRead',
    ])
  })

  it('names the toggles by what they set', () => {
    const starred = context({ ...offered, star: { flagged: false, run: vi.fn() }, toSort: { toSort: false, unsupported: false, run: vi.fn() } })
    const label = (id: string, each: ActionContext): string | undefined =>
      MAIL_ACTIONS.find((action) => action.id === id)?.label(each)
    expect(label('star', context(offered))).toBe('triage.star')
    expect(label('star', starred)).toBe('triage.unstar')
    expect(label('toSort', context(offered))).toBe('triage.toSort')
    expect(label('toSort', starred)).toBe('triage.sorted')
  })

  it('offers a reply only on a message, and a new message only on an account', () => {
    expect(ids('reply', context())).toEqual([])
    expect(ids('reply', context({}, { message: target }))).toEqual(['reply', 'replyAll', 'forward'])
    expect(ids('compose', context())).toEqual([])
    expect(ids('compose', context({}, { account: 1 }))).toEqual(['compose'])
  })

  it('prints and downloads only a message, even while composing', () => {
    expect(ids('message', context())).toEqual([])
    const open = context({}, { message: target })
    expect(ids('message', open)).toEqual(['print', 'downloadSource'])
    for (const action of listedActions('message', open)) {
      expect(runnable(action, open, { busy: true, composing: true })).toBe(true)
    }
  })

  it('opens the printable page in a new tab and saves the source', () => {
    const links: Record<string, string>[] = []
    vi.stubGlobal('document', {
      createElement: () => {
        const link: Record<string, string> & { click?: () => void } = {}
        link.click = () => {
          links.push({ ...link })
        }
        return link
      },
    })
    try {
      const open = context({}, { message: target })
      mailAction('print').run(open)
      mailAction('downloadSource').run(open)
    } finally {
      vi.unstubAllGlobals()
    }
    expect(links).toEqual([
      expect.objectContaining({ href: expect.stringMatching(/\/messages\/r1\/print$/) as unknown, target: '_blank', rel: 'noopener noreferrer' }),
      expect.objectContaining({ href: expect.stringMatching(/\/messages\/r1\/source$/) as unknown, download: '' }),
    ])
  })

  it('runs a key only when its action can run now', () => {
    const idle = { busy: false, composing: false }
    expect(shortcutAction('e', context(offered), idle)?.id).toBe('archive')
    expect(shortcutAction('e', context(), idle)).toBeUndefined()
    expect(shortcutAction('e', context(offered), { busy: true, composing: false })).toBeUndefined()
    // `u` marks unread even where the toolbar offers Mark as read.
    expect(shortcutAction('u', context({ ...offered, read: { seen: true, run: vi.fn() } }), idle)?.id).toBe('markUnread')
    expect(shortcutAction('t', context({ ...offered, toSort: { toSort: true, unsupported: true, run: vi.fn() } }), idle)).toBeUndefined()

    const replying = context({}, { message: target, account: 1 })
    expect(shortcutAction('r', replying, idle)?.id).toBe('reply')
    expect(shortcutAction('c', replying, idle)?.id).toBe('compose')
    expect(shortcutAction('r', replying, { busy: true, composing: false })?.id).toBe('reply')
    expect(shortcutAction('r', replying, { busy: false, composing: true })).toBeUndefined()
    const off = context({}, { message: target, account: 1, compose: { start: vi.fn(), canCompose: () => false } })
    expect(shortcutAction('r', off, idle)).toBeUndefined()
    expect(shortcutAction('c', off, idle)).toBeUndefined()
  })

  it('runs through the context: a picker opens, a reply starts on the message', () => {
    const each = context(offered, { message: target })
    shortcutAction('v', each, { busy: false, composing: false })?.run(each)
    expect(each.openPicker).toHaveBeenCalledWith('move')
    shortcutAction('a', each, { busy: false, composing: false })?.run(each)
    expect(each.compose.start).toHaveBeenCalledWith({ kind: 'reply_all', account: 1, ref: 'r1' })
  })
})

describe('dropAction', () => {
  const projects: MailFolder = { path: 'Projects', role: null, unread: 0, total: 0, delimiter: '/', selectable: true, uid_validity: 1, uid_next: 1 }
  const sent: MailFolder = { ...projects, path: 'Sent', role: 'sent' }
  const drop = (actions: Partial<TriageActions>, account: number, folder: MailFolder): string | undefined =>
    dropAction({ ...context({ ...offered, ...actions }), into: { account, folder } })?.id

  it('moves within the targets account to a folder they can go to, else to another account', () => {
    expect(drop({ moveChoices: [projects] }, 1, projects)).toBe('move')
    expect(drop({ moveChoices: [] }, 1, projects)).toBeUndefined()
    expect(drop({}, 2, projects)).toBe('moveToAccount')
    expect(drop({}, 2, sent)).toBeUndefined()
    expect(drop({ otherAccounts: null }, 2, projects)).toBeUndefined()
  })

  it('refuses a level that holds only folders, and any folder for targets of several accounts', () => {
    const level: MailFolder = { ...projects, path: 'Clients', selectable: false }
    expect(drop({ moveChoices: [projects] }, 1, level)).toBeUndefined()
    expect(drop({}, 2, level)).toBeUndefined()
    // The targets' accounts are never other accounts to move to.
    const mixed = { targets: [target, { ...target, ref: 'r2', account_id: 2 }], moveChoices: null, otherAccounts: [3] }
    expect(drop(mixed, 1, projects)).toBeUndefined()
    expect(drop(mixed, 2, projects)).toBeUndefined()
  })

  it('runs the move on the folder dropped on, and opens the account picker there', () => {
    const move = vi.fn()
    const openPicker = vi.fn()
    const into = { account: 1, folder: projects }
    mailAction('move').run(context({ ...offered, move }, { into, openPicker }))
    expect(move).toHaveBeenCalledWith(projects)
    expect(openPicker).not.toHaveBeenCalled()
    mailAction('moveToAccount').run(context(offered, { into, openPicker }))
    expect(openPicker).toHaveBeenCalledWith('account', into)
  })
})
