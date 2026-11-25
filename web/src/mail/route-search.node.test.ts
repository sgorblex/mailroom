import { describe, expect, it } from 'vitest'

import { listQuery, validateMailSearch } from './route-search.ts'

describe('the mail URL', () => {
  it('takes the account as its id, a positive integer, whether the router parsed it or not', () => {
    expect(validateMailSearch({ account: 3 })).toEqual({ account: 3 })
    expect(validateMailSearch({ account: '12' })).toEqual({ account: 12 })
  })

  it('drops an account that is no id, as a link from before ids were numbers has', () => {
    for (const account of ['work', '0', '-1', '1.5', 0, 1.5, 2 ** 53]) {
      expect(validateMailSearch({ account, folder: 'INBOX' })).toEqual({ folder: 'INBOX' })
    }
  })

  it("keeps a search's account only as an id or every account", () => {
    expect(validateMailSearch({ q: { account: '3', from: 'ana' } })).toEqual({ q: { account: '3', from: 'ana' } })
    expect(validateMailSearch({ q: { account: 3 } })).toEqual({ q: { account: '3' } })
    expect(validateMailSearch({ q: { account: '*' } })).toEqual({ q: { account: '*' } })
    expect(validateMailSearch({ q: { account: 'work', from: 'ana' } })).toEqual({ q: { from: 'ana' } })
  })

  it('keeps a search in a view with that view open only, its account and folder dropped', () => {
    expect(validateMailSearch({ view: 4, q: { view: 4, account: '3', folder: 'INBOX', from: 'ana' } })).toEqual({
      view: 4,
      q: { view: 4, from: 'ana' },
    })
    expect(validateMailSearch({ view: 5, q: { view: 4, from: 'ana' } })).toEqual({ view: 5, q: { from: 'ana' } })
    expect(validateMailSearch({ q: { view: 4, from: 'ana' } })).toEqual({ q: { from: 'ana' } })
    expect(validateMailSearch({ view: 'all_inboxes', q: { view: 'all_inboxes' } })).toEqual({ view: 'all_inboxes', q: {} })
  })

  it('keeps a flag set false only for unread, which the syntax writes as is:read', () => {
    expect(validateMailSearch({ q: { unread: false, flagged: false, has_attachment: true } })).toEqual({
      q: { unread: false, has_attachment: true },
    })
  })

  it('keeps the quick filters it knows, once each and in their own order', () => {
    expect(validateMailSearch({ only: ['has_attachment', 'unread', 'unread', 'starred'] })).toEqual({
      only: ['unread', 'has_attachment'],
    })
    expect(validateMailSearch({ only: 'flagged' })).toEqual({ only: ['flagged'] })
    expect(validateMailSearch({ only: ['starred'] })).toEqual({})
    expect(validateMailSearch({ only: [] })).toEqual({})
  })

  it('keeps the Snoozed or Scheduled list, and no other', () => {
    expect(validateMailSearch({ list: 'snoozed' })).toEqual({ list: 'snoozed' })
    expect(validateMailSearch({ list: 'scheduled' })).toEqual({ list: 'scheduled' })
    expect(validateMailSearch({ list: 'drafts' })).toEqual({})
  })

  it('keeps the conversation an open message was opened in, with the message only', () => {
    expect(validateMailSearch({ message: 'b', thread: 'a' })).toEqual({ message: 'b', thread: 'a' })
    expect(validateMailSearch({ thread: 'a' })).toEqual({})
  })

  it('keeps a view as All inboxes or a saved view id', () => {
    expect(validateMailSearch({ view: 'all_inboxes' })).toEqual({ view: 'all_inboxes' })
    expect(validateMailSearch({ view: 7 })).toEqual({ view: 7 })
    expect(validateMailSearch({ view: '7' })).toEqual({ view: 7 })
    for (const view of ['inbox', '0', -1, 1.5]) {
      expect(validateMailSearch({ view })).toEqual({})
    }
  })
})

describe('the search the list runs', () => {
  it('is the folder as a search, or the search, with each quick filter set', () => {
    expect(listQuery(3, 'INBOX', undefined, undefined)).toBeUndefined()
    expect(listQuery(3, 'INBOX', { from: 'ana' }, undefined)).toEqual({ from: 'ana' })
    expect(listQuery(3, 'Archive', undefined, ['unread', 'has_attachment'])).toEqual({
      account: '3',
      folder: 'Archive',
      unread: true,
      has_attachment: true,
    })
    expect(listQuery(3, 'INBOX', { account: '*', unread: false }, ['unread', 'flagged'])).toEqual({
      account: '*',
      unread: true,
      flagged: true,
    })
  })
})
