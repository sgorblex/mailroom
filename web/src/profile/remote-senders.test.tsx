import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { RemoteSenders } from './remote-senders.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null }
const LIST = '/api/v1/mail/remote-senders'
const REMOVE = '/api/v1/mail/remote-senders/remove'

/** The remembered senders, `removeStatus` answering each removal (and taking the sender out on 204). */
function stubApi(addresses: string[], removeStatus = 204): ReturnType<typeof vi.fn> {
  let senders = addresses.map((address) => ({ address, created_at: '2026-11-18T10:00:00+00:00' }))
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === LIST) {
      return Promise.resolve(new Response(JSON.stringify({ senders }), { status: 200 }))
    }
    if (path === REMOVE) {
      const { address } = JSON.parse(init?.body as string) as { address: string }
      if (removeStatus === 404) {
        // Removed elsewhere just before.
        senders = senders.filter((sender) => sender.address !== address)
        return Promise.resolve(new Response('{"detail":"No such sender."}', { status: 404 }))
      }
      if (removeStatus !== 204) {
        return Promise.resolve(new Response('{"detail":"Server error"}', { status: removeStatus }))
      }
      senders = senders.filter((sender) => sender.address !== address)
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    return Promise.resolve(new Response('{"detail":"Not Found"}', { status: 404 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('RemoteSenders', () => {
  it('lists the remembered senders and removes one', async () => {
    const fetchMock = stubApi(['news@shop.example', 'team@club.example'])
    renderSignedIn(<RemoteSenders />, ALICE)

    const list = await screen.findByRole('list', { name: 'Remote images' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(within(list).getByText('news@shop.example')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Remove news@shop.example' }))
    expect(fetchMock).toHaveBeenCalledWith(
      REMOVE,
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ address: 'news@shop.example' }) }),
    )
    expect(await screen.findByRole('button', { name: 'Remove team@club.example' })).toBeInTheDocument()
    expect(screen.queryByText('news@shop.example')).not.toBeInTheDocument()
  })

  it('says how to add one when there are none', async () => {
    stubApi([])
    renderSignedIn(<RemoteSenders />, ALICE)
    expect(await screen.findByText(/No senders yet/)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('takes a sender already removed elsewhere as removed, with no error', async () => {
    stubApi(['news@shop.example'], 404)
    renderSignedIn(<RemoteSenders />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Remove news@shop.example' }))
    expect(await screen.findByText(/No senders yet/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says when a sender could not be removed', async () => {
    stubApi(['news@shop.example'], 500)
    renderSignedIn(<RemoteSenders />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Remove news@shop.example' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('news@shop.example')).toBeInTheDocument()
  })
})
