import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { stubEventSource } from '@/test/fake-event-source.ts'
import { bodiesTo, HOME, messageItem, messageView, queriesTo, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const ASK = '/api/v1/mail/refresh'
const old = messageItem(WORK, 'INBOX', 1, { subject: 'Old' })
const fresh = messageItem(WORK, 'INBOX', 2, { subject: 'Fresh' })

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'INBOX/Lists', role: null, unread: 0, total: 0 },
        { path: 'Archive', role: 'archive', unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': [old], '1/INBOX/Lists': [], '1/Archive': [messageItem(WORK, 'Archive', 1, { subject: 'Filed' })], '2/INBOX': [] },
    views: { [old.ref]: messageView(old) },
    ...fields,
  }
}

const button = (): HTMLElement => screen.getByRole('button', { name: 'Refresh' })

async function idle(): Promise<void> {
  await waitFor(
    () => {
      expect(button()).toHaveAttribute('aria-disabled', 'false')
    },
    { timeout: 4000 },
  )
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('refreshing the list', () => {
  it('syncs the folder shown, busy until that is done, then reads the list again', async () => {
    stubEventSource()
    const fake = state({ refreshing: 1 })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail?account=1&folder=INBOX')
    await screen.findByRole('link', { name: /Old/ })

    await userEvent.click(button())
    expect(button()).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('Refreshing…')).toHaveAttribute('role', 'status')
    expect(button()).toHaveFocus()
    // No second one meanwhile.
    await userEvent.click(button())
    expect(bodiesTo(fetchMock, ASK)).toEqual([{ account: 1, folder: 'INBOX' }])

    fake.messages['1/INBOX'] = [fresh, old]
    expect(await screen.findByRole('link', { name: /Fresh/ }, { timeout: 4000 })).toBeInTheDocument()
    await idle()
    expect(screen.queryByText('Refreshing…')).not.toBeInTheDocument()
  })

  it('reads a folder off the index again at once', async () => {
    const fake = state({ refreshing: null })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail?account=1&folder=INBOX&sub=true')
    await screen.findByRole('link', { name: /Old/ })

    fake.messages['1/INBOX'] = [fresh, old]
    await userEvent.keyboard('{Shift>}R{/Shift}')
    expect(await screen.findByRole('link', { name: /Fresh/ })).toBeInTheDocument()
    await idle()
    expect(bodiesTo(fetchMock, ASK)).toEqual([{ account: 1, folder: 'INBOX', subfolders: true }])
    expect(queriesTo(fetchMock, `${ASK}/7a0f8f1e-0000-4000-8000-000000000001`)).toEqual([])
  })

  it.each([
    ['a search of every account', `q=${encodeURIComponent('{"from":"ada"}')}`, { account: '*' }],
    ['a search of one folder', `q=${encodeURIComponent('{"from":"ada","account":"1","folder":"*"}')}`, { account: 1, folder: '*' }],
    ['a folder under a quick filter', `account=1&folder=Archive&only=${encodeURIComponent('["unread"]')}`, { account: 1, folder: 'Archive' }],
    ['a view', 'view=all_inboxes', { view: 'all_inboxes' }],
  ])('asks for the scope of %s, without its criteria', async (_name, search, body) => {
    const fetchMock = stubMailApi(state({ refreshing: null }))
    renderMailApp(`/mail?${search}`)
    await screen.findByRole('list', { name: 'Views' })
    await userEvent.click(await screen.findByRole('button', { name: 'Refresh' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, ASK)).toEqual([body])
    })
    await idle()
  })

  it('is offered by the command palette', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state({ refreshing: null }))
    renderMailApp('/mail')
    await screen.findByRole('link', { name: /Old/ })
    await user.keyboard('{Control>}k{/Control}')
    await user.type(screen.getByRole('combobox', { name: 'Go to or run' }), 'refresh')
    await user.keyboard('{Enter}')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, ASK)).toHaveLength(1)
    })
    await idle()
  })

  it.each([
    [429, 'refresh_limited', /Too many refreshes/],
    [404, 'not_found', /This mail account does not exist/],
  ])('says why it was refused (%i %s)', async (status, code, worded) => {
    stubMailApi(state({ refuse: { [`POST ${ASK}`]: { status, body: { detail: { code, message: 'x' } } } } }))
    renderMailApp('/mail')
    await screen.findByRole('link', { name: /Old/ })
    await userEvent.click(button())
    // The toast, and its announcement.
    expect(await screen.findAllByText(worded)).not.toHaveLength(0)
    await idle()
    expect(screen.getByRole('link', { name: /Old/ })).toBeInTheDocument()
  })

  it('lets go of a refresh when another list is shown, which can be refreshed itself', async () => {
    const fetchMock = stubMailApi(state({ refreshing: 100 }))
    const history = renderMailApp('/mail?account=1&folder=INBOX')
    await screen.findByRole('link', { name: /Old/ })
    await userEvent.click(button())
    act(() => {
      history.push('/mail?account=1&folder=Archive')
    })
    await screen.findByRole('link', { name: /Filed/ })
    await idle()
    await userEvent.click(button())
    expect(button()).toHaveAttribute('aria-disabled', 'true')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, ASK)).toEqual([
        { account: 1, folder: 'INBOX' },
        { account: 1, folder: 'Archive' },
      ])
    })
  })

  it('stops waiting for one that takes long, and says so', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    stubMailApi(state({ refreshing: 1000 }))
    renderMailApp('/mail')
    await screen.findByRole('link', { name: /Old/ })
    await user.click(button())
    await act(() => vi.advanceTimersByTimeAsync(46_000))
    expect(await screen.findAllByText(/The refresh is taking a while/)).not.toHaveLength(0)
    expect(button()).toHaveAttribute('aria-disabled', 'false')
  })
})
