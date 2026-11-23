import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { UserJson } from '@/api/auth.ts'
import { bodiesTo, type FakeMailState, messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { openMessageMenu, renderMailApp } from '@/test/mail-app.tsx'

const ALICE: UserJson = { username: 'alice', is_admin: false, time_zone: 'Europe/Rome', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const item = messageItem(WORK, 'INBOX', 1, { subject: 'Read me later' })
const SNOOZE = `/api/v1/mail/messages/${item.ref}/snooze`

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }] },
    messages: { '1/INBOX': [item] },
    views: { [item.ref]: messageView(item) },
    ...overrides,
  }
}

function toast(): HTMLElement {
  const found = document.querySelector<HTMLElement>('#toast-status')
  if (found === null) {
    throw new Error('no toast region')
  }
  return found
}


afterEach(() => {
  vi.unstubAllGlobals()
})

describe('snoozing a message', () => {
  it('snoozes the open message until a time picked, and Undo brings it back', async () => {
    const fake = state()
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?message=${item.ref}`, { user: ALICE, features: ['mail'] })

    await userEvent.click(within(await openMessageMenu()).getByRole('menuitem', { name: 'Snooze' }))
    const dialog = await screen.findByRole('dialog', { name: 'Snooze until' })
    expect(within(dialog).getByText('Times are in Europe/Rome.')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: /^Tomorrow/ }))

    await waitFor(() => {
      expect(toast()).toHaveTextContent(/^Snoozed until .*: it waits in Snoozed and comes back unread\./)
    })
    const [body] = bodiesTo(fetchMock, SNOOZE) as { until: string }[]
    expect(new Date(body?.until ?? '').getTime()).toBeGreaterThan(Date.now())
    expect(fake.snoozes).toHaveLength(1)
    await waitFor(() => {
      expect(screen.queryByRole('group', { name: 'Message actions' })).not.toBeInTheDocument()
    })

    const snoozeId = fake.snoozes?.[0]?.snooze_id ?? ''

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(toast()).toHaveTextContent('Coming back now.')
    })
    expect(bodiesTo(fetchMock, `/api/v1/mail/snoozes/${snoozeId}/wake`)).toEqual([null])
    expect(fake.snoozes).toEqual([])
  })

  it('lists the snoozed messages in the sidebar and brings one back now', async () => {
    const fake = state({
      snoozes: [
        {
          snooze_id: '7',
          account: WORK,
          subject: 'Invoice',
          sender: 'ana@example.org',
          folder: 'INBOX',
          until: '2026-11-23T07:00:00+00:00',
          code: null,
        },
      ],
    })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail', { user: ALICE, features: ['mail'] })

    const entry = await screen.findByRole('button', { name: /^Snoozed.*1 snoozed/ })
    await userEvent.click(entry)
    const dialog = await screen.findByRole('dialog', { name: 'Snoozed' })
    expect(within(dialog).getByText('Invoice')).toBeInTheDocument()
    expect(within(dialog).getByText(/^From ana@example.org, in work/)).toBeInTheDocument()
    expect(within(dialog).getByText(/^Back .*, to INBOX$/)).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Bring back “Invoice” now' }))

    await waitFor(() => {
      expect(within(dialog).getByRole('status')).toHaveTextContent('Coming back now.')
    })
    expect(bodiesTo(fetchMock, '/api/v1/mail/snoozes/7/wake')).toEqual([null])
    expect(await within(dialog).findByText('Nothing is snoozed.')).toBeInTheDocument()
  })

  it('opens the Snoozed list from the address, as Home links to it, and closes it there', async () => {
    stubMailApi(
      state({
        snoozes: [
          { snooze_id: '7', account: WORK, subject: 'Invoice', sender: null, folder: 'INBOX', until: '2026-11-23T07:00:00+00:00', code: null },
        ],
      }),
    )
    const history = renderMailApp('/mail?list=snoozed', { user: ALICE, features: ['mail'] })
    const dialog = await screen.findByRole('dialog', { name: 'Snoozed' })
    expect(await within(dialog).findByText('Invoice')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Snoozed' })).not.toBeInTheDocument()
    })
    expect(history.location.search).not.toContain('list')
  })
})
