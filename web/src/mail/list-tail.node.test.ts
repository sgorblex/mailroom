import { describe, expect, it } from 'vitest'

import type { MessageItem, MessagePage } from '@/api/mail.ts'

import { keptRows, keptTail, withKept, type Tail } from './list-tail.ts'

const row = (uid: number): MessageItem => ({
  ref: `r${String(uid)}`,
  account_id: 1,
  folder: 'INBOX',
  date: null,
  from: { name: '', address: '' },
  subject: '',
  flags: { seen: true, flagged: false, answered: false, forwarded: false, draft: false, handled: false },
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

  it('drops a conversation loaded below once the first page holds it, as its newer message', () => {
    const conversation = (uid: number, refs: number[]): MessageItem => ({
      ...row(uid),
      conversation: {
        count: refs.length,
        unread: 0,
        participants: [],
        has_attachments: false,
        flagged: false,
        refs: refs.map((each) => `r${String(each)}`),
        listed_folders: {},
        latest_from_new_sender: false,
      },
    })
    const later: Tail = { rows: [conversation(7, [7, 3]), row(6)], failures: [], cursor: 'c6' }
    const next: MessagePage = { ...page([10, 9, 8], 'c8'), messages: [conversation(11, [11, 7, 3]), row(10), row(8)] }
    expect(refs(keptTail(page([10, 9, 8], 'c8'), later, next))).toEqual(['r6'])
  })

  it("needs none when the first page reaches past what was loaded", () => {
    expect(keptTail(page([10, 9], 'c9'), null, page([10, 9, 8], 'c8'))).toBeNull()
  })

  it('starts over when the first page holds none of what was shown, or on a first read', () => {
    expect(keptTail(page([10, 9], 'c9'), null, page([20, 19], 'c19'))).toBeNull()
    expect(keptTail(undefined, null, page([10]))).toBeNull()
  })
})

describe('keptRows and withKept', () => {
  const uids = (rows: MessageItem[]): string[] => rows.map((each) => each.ref)

  it('keep the rows a read again lacks in their place, each after the row above it', () => {
    const shown = [10, 9, 8, 7, 6].map(row)
    const next = [11, 10, 8, 6].map(row)
    const kept = keptRows(shown, next, (each) => each.ref === 'r9' || each.ref === 'r7')
    expect(kept.map((each) => [each.row.ref, each.after])).toEqual([
      ['r9', 'r10'],
      ['r7', 'r8'],
    ])
    expect(uids(withKept(next, kept))).toEqual(['r11', 'r10', 'r9', 'r8', 'r7', 'r6'])
  })

  it('chain kept rows, put one with nothing above it at the top, and drop what is not kept', () => {
    const shown = [10, 9, 8, 7].map(row)
    const next = [7].map(row)
    const kept = keptRows(shown, next, (each) => each.ref !== 'r9')
    expect(uids(withKept(next, kept))).toEqual(['r10', 'r8', 'r7'])
  })

  it('give a row back to the list once it holds it again', () => {
    const kept = keptRows([row(10), row(9)], [row(10)], () => true)
    expect(uids(withKept([row(10), row(9)], kept))).toEqual(['r10', 'r9'])
    expect(uids(withKept([row(10)], []))).toEqual(['r10'])
  })
})
