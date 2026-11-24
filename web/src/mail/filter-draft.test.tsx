import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Feature, UserJson } from '@/api/auth.ts'
import { type FakeMailState, messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const ALICE: UserJson = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const item = messageItem(WORK, 'INBOX', 1, { subject: 'Weekly' })

function state(): FakeMailState {
  const plain = messageView(item)
  const list = {
    id: 'weekly.news.example',
    name: 'Weekly',
    archive: null,
    post: null,
    help: null,
    unsubscribe_https: 'https://news.example/u',
    unsubscribe_mailto: null,
    one_click: false,
    unsubscribe_fields: 1,
    unsubscribe_post_fields: 0,
  }
  return {
    accounts: [WORK],
    folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }] },
    messages: { '1/INBOX': [item] },
    views: { [item.ref]: { ...plain, headers: { ...plain.headers, mailing_list: list } } },
  }
}

const withFilters: { user: UserJson; features: Feature[] } = { user: ALICE, features: ['mail', 'filters'] }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('drafting a filter from the mail screen', () => {
  it('opens the filter drafted from the open message', async () => {
    stubMailApi(state())
    const history = renderMailApp(`/mail?message=${item.ref}`, withFilters)

    await userEvent.click(await screen.findByRole('button', { name: 'Filter messages like this' }))
    expect(await screen.findByText('Filter draft page')).toBeInTheDocument()
    const search = new URLSearchParams(history.location.search)
    expect(search.get('account')).toBe(String(WORK))
    expect(search.get('message')).toBe(item.ref)
  })

  it('points Filter instead at the drafted filter', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${item.ref}`, withFilters)

    const link = await screen.findByRole('link', { name: 'Filter instead' })
    const href = new URL(link.getAttribute('href') ?? '', 'http://localhost')
    expect(href.pathname).toBe('/filters/new')
    expect(href.searchParams.get('message')).toBe(item.ref)
  })

  it('turns the search shown into a filter', async () => {
    stubMailApi(state())
    const query = { account: String(WORK), folder: 'INBOX', from: 'news@example.com', text: 'weekly' }
    const history = renderMailApp(`/mail?q=${encodeURIComponent(JSON.stringify(query))}`, withFilters)

    await userEvent.click(await screen.findByRole('link', { name: 'Create filter from this search' }))
    expect(await screen.findByText('Filter draft page')).toBeInTheDocument()
    expect(history.location.pathname).toBe('/filters/new')
    expect(JSON.parse(new URLSearchParams(history.location.search).get('q') ?? '{}')).toEqual(query)
  })

  it('offers no filter where the instance runs none', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${item.ref}&q=${encodeURIComponent('{"from":"news"}')}`, { user: ALICE })

    const search = await screen.findByRole('search')
    await screen.findByRole('button', { name: 'Print' })
    expect(screen.queryByRole('button', { name: 'Filter messages like this' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Filter instead' })).not.toBeInTheDocument()
    expect(within(search).queryByRole('link', { name: 'Create filter from this search' })).not.toBeInTheDocument()
  })
})
