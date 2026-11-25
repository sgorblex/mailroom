import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import { messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: 'Asia/Tokyo', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// Built with React Compiler, a row keeps the date it drew until one of its inputs changes.
it("shows the date once the day is over in the user's time zone, in the list and in a conversation", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  vi.setSystemTime('2026-10-30T14:55:00Z') // 23:55 in Tokyo
  const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) })
  const dates = { 2: '2026-10-30T05:20:00Z', 1: '2026-10-30T04:10:00Z' }
  const messages = [2, 1].map((uid) => messageItem(WORK, 'INBOX', uid, { date: dates[uid as 1 | 2] }))
  stubMailApi({
    accounts: [WORK],
    folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 2 }] },
    messages: { '1/INBOX': messages },
    views: Object.fromEntries(messages.map((each) => [each.ref, messageView(each)])),
    threads: Object.fromEntries(messages.map((each) => [each.ref, 'plan'])),
  })
  renderMailApp('/mail', { user: ALICE })
  const row = await within(await screen.findByRole('list', { name: 'Messages' })).findByRole('link')
  expect(row).toHaveTextContent('2:20 PM')
  await user.click(row)
  const thread = await screen.findByRole('navigation', { name: 'Conversation' })
  expect(await within(thread).findByText('1:10 PM')).toBeInTheDocument()

  await act(() => vi.advanceTimersByTimeAsync(4 * 60_000))
  expect(row).toHaveTextContent('2:20 PM')

  await act(() => vi.advanceTimersByTimeAsync(60_000))
  expect(row).toHaveTextContent('Oct 30, 2026')
  expect(within(thread).getAllByText('Oct 30, 2026')).toHaveLength(2)
})
