import { describe, expect, it } from 'vitest'

import type { MessageItem, MessagePage } from '@/api/mail.ts'

import { keptTail, type Tail } from './list-tail.ts'

const row = (uid: number): MessageItem => ({
  ref: `r${String(uid)}`,
  account_id: 1,
  folder: 'INBOX',
  date: null,
  from: { name: '', address: '' },
  subject: '',
  flags: { seen: true, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false },
  has_attachments: false,
  preview: '',
  size: 0,
})

const page = (uids: number[], cursor: string | null = null): MessagePage => ({
  messages: uids.map(row),
  next_cursor: cursor,
  failures: [],
})

const refs = (tail: Tail | null): string[] | null => tail?.rows.map((each) => each.ref) ?? null

describe('keptTail', () => {
  it('keeps the rows new mail pushed off the first page, with the cursor after them', () => {
    const tail = keptTail(page([10, 9, 8], 'c8'), null, page([12, 11, 10], 'c10'))
    expect(refs(tail)).toEqual(['r9', 'r8'])
    expect(tail?.cursor).toBe('c8')
  })

  it('keeps the pages loaded later, dropping the rows gone from the first page', () => {
    const later: Tail = { rows: [row(7), row(6)], failures: [], cursor: 'c6' }
    const tail = keptTail(page([10, 9, 8], 'c8'), later, page([11, 10, 8], 'c8'))
    expect(refs(tail)).toEqual(['r7', 'r6'])
    expect(tail?.cursor).toBe('c6')
  })

  it("needs none when the first page reaches past what was loaded", () => {
    expect(keptTail(page([10, 9], 'c9'), null, page([10, 9, 8], 'c8'))).toBeNull()
  })

  it('starts over when the first page holds none of what was shown, or on a first read', () => {
    expect(keptTail(page([10, 9], 'c9'), null, page([20, 19], 'c19'))).toBeNull()
    expect(keptTail(undefined, null, page([10]))).toBeNull()
  })
})
