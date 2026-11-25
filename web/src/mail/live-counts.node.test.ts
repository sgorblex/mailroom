import { describe, expect, it } from 'vitest'

import type { MailFolder } from '@/api/mail.ts'

import { isLiveCountNewer, liveCountClock, liveCountReach, recordLiveCount } from './live-counts.ts'

const folder = (path: string, role: MailFolder['role'] = null): MailFolder =>
  ({ path, role, unread: 0, total: 0, selectable: true }) as MailFolder

const changed = (path: string) => ({ type: 'folder_changed' as const, account: 1, folder: path, unread: 1, total: 2 })

describe('liveCountReach', () => {
  const folders = [folder('INBOX', 'inbox'), folder('Archive', 'archive'), folder('[Gmail]/All Mail', 'all')]

  it('is the folder the event names, INBOX in any case', () => {
    expect(liveCountReach(folders, changed('Archive'))).toEqual({ kind: 'folder', folder: folders[1] })
    expect(liveCountReach(folders, changed('inbox'))).toEqual({ kind: 'folder', folder: folders[0] })
  })

  it("is the whole account for All Mail, whose change the event's counts do not say", () => {
    expect(liveCountReach(folders, changed('[Gmail]/All Mail'))).toEqual({ kind: 'account' })
  })

  it('is nothing for folders not loaded, or a folder they do not hold', () => {
    expect(liveCountReach(undefined, changed('INBOX'))).toBeNull()
    expect(liveCountReach(folders, changed('Gone'))).toBeNull()
  })
})

describe('isLiveCountNewer', () => {
  it('holds for a folder whose count arrived after the tick, and only for it', () => {
    const clock = liveCountClock()
    const asked = clock.tick
    recordLiveCount(clock, 1, 'Archive')
    expect(isLiveCountNewer(clock, 1, 'Archive', asked)).toBe(true)
    expect(isLiveCountNewer(clock, 1, 'INBOX', asked)).toBe(false)
    expect(isLiveCountNewer(clock, 2, 'Archive', asked)).toBe(false)
  })

  it('does not hold for a count that arrived before the tick', () => {
    const clock = liveCountClock()
    recordLiveCount(clock, 1, 'INBOX')
    expect(isLiveCountNewer(clock, 1, 'INBOX', clock.tick)).toBe(false)
  })

  it('takes INBOX in any case as one folder, but other names as they are', () => {
    const clock = liveCountClock()
    recordLiveCount(clock, 1, 'Inbox')
    recordLiveCount(clock, 1, 'Lists')
    expect(isLiveCountNewer(clock, 1, 'INBOX', 0)).toBe(true)
    expect(isLiveCountNewer(clock, 1, 'lists', 0)).toBe(false)
  })

  it("holds for every folder of an account whose counts are all read again", () => {
    const clock = liveCountClock()
    const asked = clock.tick
    recordLiveCount(clock, 1, null)
    expect(isLiveCountNewer(clock, 1, 'Projects', asked)).toBe(true)
    expect(isLiveCountNewer(clock, 2, 'Projects', asked)).toBe(false)
  })
})
