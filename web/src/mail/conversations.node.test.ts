import { describe, expect, it } from 'vitest'

import type { Conversation, MessageItem } from '@/api/mail.ts'
import { messageItem } from '@/test/fake-mail-api.ts'

import { asksFirst, isConversation, listedFolderOf, listRow, mixedFlags, opened, refsOf, shownOnly } from './conversations.ts'

const UNREAD = { seen: false, flagged: false, answered: false, forwarded: false, draft: false, handled: false }

function grouped(fields: Partial<Conversation>, item: MessageItem = messageItem(1, 'INBOX', 3)): MessageItem {
  return {
    ...item,
    conversation: {
      count: 3,
      unread: 0,
      participants: ['Ann'],
      has_attachments: false,
      flagged: false,
      refs: ['a', 'b', 'c'],
      listed_folders: {},
      latest_from_new_sender: false,
      ...fields,
    },
  }
}

describe('a conversation row', () => {
  it('is read only when none of it is unread, and starred when one of it is', () => {
    expect(listRow(grouped({ unread: 1 })).flags.seen).toBe(false)
    expect(listRow(grouped({ unread: 0 }, messageItem(1, 'INBOX', 3, { flags: UNREAD }))).flags.seen).toBe(true)
    expect(listRow(grouped({ flagged: true })).flags.flagged).toBe(true)
  })

  it('takes every message of it, and opens as a thread', () => {
    const row = grouped({})
    expect(refsOf(row)).toEqual(['a', 'b', 'c'])
    expect(opened(row)).toEqual({ message: row.ref, thread: row.ref })
  })

  it('of one message is a message', () => {
    const single = grouped({ count: 1, unread: 1, refs: ['only'] })
    expect(isConversation(single)).toBe(false)
    expect(listRow(single)).toBe(single)
    expect(opened(single)).toEqual({ message: single.ref, thread: undefined })
    expect(mixedFlags(single)).toEqual([])
  })

  it('says which of its flags it does not say for every message', () => {
    expect(mixedFlags(grouped({ unread: 1 }))).toEqual(['handled', 'seen'])
    expect(mixedFlags(grouped({ unread: 3 }))).toEqual(['handled'])
    expect(mixedFlags(grouped({ flagged: true }))).toEqual(['handled', 'flagged'])
  })

  it('names the folder each of its messages was listed under, the row its own', () => {
    const row = { ...grouped({ listed_folders: { a: 'Work', b: 'INBOX' } }), listed_folder: 'Work' }
    expect(['a', 'b', 'c'].map((ref) => listedFolderOf(row, ref))).toEqual(['Work', 'INBOX', undefined])
    const single = { ...messageItem(1, '[Gmail]/All Mail', 9), listed_folder: 'Work' }
    expect(listedFolderOf(single, single.ref)).toBe('Work')
  })

  it('asks before a move-like action when its newest sender is new to it, and can stand for that message alone', () => {
    const row = grouped({ latest_from_new_sender: true })
    expect(asksFirst({ kind: 'trash' }, [row])).toBe(true)
    expect(asksFirst({ kind: 'junk' }, [grouped({}), row])).toBe(true)
    expect(asksFirst({ kind: 'flag', change: { seen: true } }, [row])).toBe(false)
    expect(asksFirst({ kind: 'archive' }, [grouped({ count: 1, latest_from_new_sender: true })])).toBe(false)
    expect(refsOf(shownOnly(row))).toEqual([row.ref])
  })
})
