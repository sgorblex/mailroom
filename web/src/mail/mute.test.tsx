import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Feature, User } from '@/api/auth.ts'
import { type FakeMailState, messageItem, messageView, refusal, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const ALICE: User = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true }
const withFilters: { user: User; features: Feature[] } = { user: ALICE, features: ['mail', 'filters'] }
const item = messageItem(WORK, 'INBOX', 1, { subject: 'Endless thread' })
const MUTE = `POST /api/v1/mail/messages/${item.ref}/mute`

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }] },
    messages: { '1/INBOX': [item] },
    views: { [item.ref]: messageView(item) },
    ...overrides,
  }
}

function region(id: 'toast-status' | 'toast-alert'): HTMLElement {
  const found = document.querySelector<HTMLElement>(`#${id}`)
  if (found === null) {
    throw new Error(`no ${id} region`)
  }
  return found
}

const actions = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Reply, forward, print and download' })

/** The requests sent to the mute routes. */
function muteRequests(fetchMock: ReturnType<typeof stubMailApi>): string[] {
  return fetchMock.mock.calls
    .map(([path, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${new URL(path as string, 'http://mailroom.test').pathname}`)
    .filter((request) => /\/mute$|\/muted\//.test(request))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('muting a conversation', () => {
  it('mutes from the open message, says Run filters is off, and unmutes it there', async () => {
    const fake = state()
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?message=${item.ref}`, withFilters)

    await userEvent.click(within(await actions()).getByRole('button', { name: 'Mute conversation' }))
    await waitFor(() => {
      expect(region('toast-status')).toHaveTextContent(
        'Conversation muted: its new messages in the inbox will arrive marked read. Run filters is off for work: muting has no effect until it is on.',
      )
    })
    expect(screen.getByText(/This conversation is muted/)).toHaveTextContent(/Run filters is off for work/)
    expect(fake.muted).toHaveLength(1)

    await userEvent.click(within(await actions()).getByRole('button', { name: 'Unmute conversation' }))
    await waitFor(() => {
      expect(region('toast-status')).toHaveTextContent('Conversation unmuted.')
    })
    expect(within(await actions()).getByRole('button', { name: 'Mute conversation' })).toBeInTheDocument()
    expect(screen.queryByText(/This conversation is muted/)).not.toBeInTheDocument()
    expect(muteRequests(fetchMock)).toEqual([MUTE, 'DELETE /api/v1/mail/muted/1'])
    expect(fake.muted).toEqual([])
  })

  it('says nothing of Run filters when it is on, and Undo unmutes', async () => {
    const fake = state({ runFilters: { [WORK]: true } })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${item.ref}`, withFilters)

    await userEvent.click(within(await actions()).getByRole('button', { name: 'Mute conversation' }))
    await waitFor(() => {
      expect(region('toast-status')).toHaveTextContent('Conversation muted')
    })
    expect(region('toast-status')).not.toHaveTextContent('Run filters')
    expect(screen.getByText(/This conversation is muted/)).not.toHaveTextContent('Run filters')

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(region('toast-status')).toHaveTextContent('Conversation unmuted.')
    })
    expect(fake.muted).toEqual([])
  })

  it('says why a message without a Message-ID cannot be muted', async () => {
    stubMailApi(state({ refuse: { [MUTE]: refusal(400, 'nothing_to_mute') } }))
    renderMailApp(`/mail?message=${item.ref}`, withFilters)

    await userEvent.click(within(await actions()).getByRole('button', { name: 'Mute conversation' }))
    await waitFor(() => {
      expect(region('toast-alert')).toHaveTextContent(
        'This message has no Message-ID to follow, so its conversation cannot be muted.',
      )
    })
    expect(within(await actions()).getByRole('button', { name: 'Mute conversation' })).toBeInTheDocument()
  })

  it('takes the muted state off every message of the conversation unmuted', async () => {
    const reply = messageItem(WORK, 'INBOX', 2, { subject: 'Endless reply' })
    const fake = state({
      messages: { '1/INBOX': [reply, item] },
      views: { [item.ref]: messageView(item), [reply.ref]: messageView(reply) },
      conversations: [[item.ref, reply.ref]],
    })
    stubMailApi(fake)
    renderMailApp('/mail', withFilters)

    const list = await screen.findByRole('list', { name: 'Messages' })
    for (const subject of ['Endless thread', 'Endless reply']) {
      fireEvent.contextMenu(within(list).getByRole('link', { name: new RegExp(subject) }))
      const menu = screen.getByRole('menu', { name: `Actions for ${subject}` })
      await userEvent.click(within(menu).getByRole('menuitem', { name: 'Mute conversation' }))
      await waitFor(() => {
        expect(region('toast-status')).toHaveTextContent('Conversation muted')
      })
    }
    expect(fake.muted).toHaveLength(1)

    await userEvent.click(within(list).getByRole('link', { name: /Endless reply/ }))
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Unmute conversation' }))
    await waitFor(() => {
      expect(region('toast-status')).toHaveTextContent('Conversation unmuted.')
    })
    await userEvent.click(within(list).getByRole('link', { name: /Endless thread/ }))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Endless thread' })).toBeInTheDocument()
    })
    expect(within(await actions()).getByRole('button', { name: 'Mute conversation' })).toBeInTheDocument()
    expect(screen.queryByText(/This conversation is muted/)).not.toBeInTheDocument()
  })

  it('takes a conversation unmuted elsewhere as unmuted', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp(`/mail?message=${item.ref}`, withFilters)

    await userEvent.click(within(await actions()).getByRole('button', { name: 'Mute conversation' }))
    await waitFor(() => {
      expect(region('toast-status')).toHaveTextContent('Conversation muted')
    })
    fake.muted = []
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Unmute conversation' }))
    await waitFor(() => {
      expect(region('toast-status')).toHaveTextContent('Conversation unmuted.')
    })
    expect(within(await actions()).getByRole('button', { name: 'Mute conversation' })).toBeInTheDocument()
  })

  it('offers muting in the row menu', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail', withFilters)

    const list = await screen.findByRole('list', { name: 'Messages' })
    fireEvent.contextMenu(within(list).getByRole('link', { name: /Endless thread/ }))
    const menu = screen.getByRole('menu', { name: 'Actions for Endless thread' })
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Mute conversation' }))
    await waitFor(() => {
      expect(fake.muted).toHaveLength(1)
    })
  })

  it('is not offered where the instance runs no filters', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${item.ref}`, { user: ALICE })

    expect(within(await actions()).getByRole('button', { name: 'Print' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mute conversation' })).not.toBeInTheDocument()
  })
})
