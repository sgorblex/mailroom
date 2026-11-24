import { describe, expect, it } from 'vitest'

import type { Account } from '@/api/accounts.ts'
import type { MailFolder, SavedView } from '@/api/mail.ts'

import { savableLine, viewFolder, viewScopes, viewUnread } from './views.ts'

const folder = (path: string, unread: number): MailFolder => ({
  path,
  role: null,
  unread,
  total: unread,
  delimiter: '/',
  selectable: true,
  uid_validity: 1,
  uid_next: 1,
})

const view = (fields: Partial<SavedView>): SavedView => ({ id: 1, name: 'V', sources: null, query: null, position: 0, ...fields })

const accounts = [
  { id: 1, name: 'work', login: 'me@work.example' },
  { id: 2, name: 'home', login: 'me@home.example' },
] as Account[]

describe('a search kept as a view', () => {
  it('is its line, the account by id', () => {
    expect(savableLine({ account: '1', folder: 'INBOX', from: 'ada', unread: true })).toBe('from:ada is:unread in:INBOX account:1')
    expect(savableLine({ account: '*', folder: 'Old mail', unread: false })).toBe('is:read in:"Old mail" account:*')
  })

  it('cannot search words, use Gmail search or hold a double quote', () => {
    expect(savableLine({ account: '1', text: 'invoice' })).toBeNull()
    expect(savableLine({ account: '1', gmail_query: 'label:x' })).toBeNull()
    expect(savableLine({ account: '1', subject: 'say "hi"' })).toBeNull()
  })
})

describe("a view's unread count", () => {
  const lists: Record<number, MailFolder[]> = { 1: [folder('INBOX', 3), folder('Lists', 4)], 2: [folder('INBOX', 2)] }

  it('adds up its folders as listed, INBOX in any case', () => {
    expect(viewUnread('all_inboxes', [1, 2], (account) => lists[account])).toBe(5)
    const sources = [
      { account_id: 1, folder: 'Lists' },
      { account_id: 2, folder: 'inbox' },
      { account_id: 2, folder: 'Gone' },
    ]
    expect(viewUnread(view({ sources }), [1, 2], (account) => lists[account])).toBe(6)
  })

  it("adds INBOX's subfolders to All inboxes when taken in, none special or under a special one", () => {
    const special = (path: string, role: MailFolder['role']): MailFolder => ({ ...folder(path, 100), role })
    const deep: Record<number, MailFolder[]> = {
      1: [
        folder('INBOX', 3),
        folder('INBOX/Lists', 4),
        folder('INBOX/Lists/Deep', 5),
        special('INBOX/Trash', 'trash'),
        folder('INBOX/Trash/Old', 100),
        special('INBOX/Starred', 'flagged'),
        folder('Lists', 100),
      ],
      2: [folder('INBOX', 2)],
    }
    expect(viewUnread('all_inboxes', [1, 2], (account) => deep[account], true)).toBe(14)
    expect(viewUnread('all_inboxes', [1, 2], (account) => deep[account])).toBe(5)
    expect(viewScopes('all_inboxes', accounts, (account) => deep[account]).map((scope) => scope.folder)).toEqual([
      'INBOX',
      'INBOX/Lists',
      'INBOX/Lists/Deep',
      'INBOX',
    ])
    expect(viewUnread('all_inboxes', [1, 2, 3], (account) => deep[account], true)).toBeNull()
  })

  it('is not known for a search, or while an account is not listed', () => {
    expect(viewUnread(view({ query: 'is:unread' }), [1, 2], (account) => lists[account])).toBeNull()
    expect(viewUnread('all_inboxes', [1, 2, 3], (account) => lists[account])).toBeNull()
  })
})

describe('what a view reads, for live updates', () => {
  it('is each of its folders, or every INBOX', () => {
    expect(viewScopes('all_inboxes', accounts)).toEqual([
      { accounts: [1], folder: 'INBOX', subfolders: false, delimiter: null },
      { accounts: [2], folder: 'INBOX', subfolders: false, delimiter: null },
    ])
    expect(viewScopes(view({ sources: [{ account_id: 2, folder: 'Lists' }] }), accounts)).toEqual([
      { accounts: [2], folder: 'Lists', subfolders: false, delimiter: null },
    ])
  })

  it("is a search's folder of its account, by default INBOX of every account", () => {
    expect(viewScopes(view({ query: 'from:ada in:Archive account:HOME' }), accounts)).toEqual([
      { accounts: [2], folder: 'Archive', subfolders: false, delimiter: null },
    ])
    expect(viewScopes(view({ query: 'is:unread' }), accounts)).toEqual([
      { accounts: [1, 2], folder: 'INBOX', subfolders: false, delimiter: null },
    ])
    expect(viewScopes(view({ query: 'account:nobody' }), accounts)).toEqual([])
  })
})

describe('the folder a view acts from', () => {
  it('is INBOX for All inboxes, a search\'s folder, or the one its folders share', () => {
    expect(viewFolder('all_inboxes')).toBe('INBOX')
    expect(viewFolder('all_inboxes', true)).toBeUndefined()
    expect(viewFolder(view({ query: 'is:unread' }))).toBe('INBOX')
    expect(viewFolder(view({ query: 'in:Archive' }))).toBe('Archive')
    expect(viewFolder(view({ query: 'in:*' }))).toBeUndefined()
    const inboxes = [
      { account_id: 1, folder: 'INBOX' },
      { account_id: 2, folder: 'inbox' },
    ]
    expect(viewFolder(view({ sources: inboxes }))).toBe('INBOX')
    expect(viewFolder(view({ sources: [...inboxes, { account_id: 2, folder: 'Lists' }] }))).toBeUndefined()
    expect(viewFolder(undefined)).toBeUndefined()
  })
})
