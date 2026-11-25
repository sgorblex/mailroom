import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SignedInSession } from '@/api/sessions.ts'
import { expectNoAxeViolations } from '@/test/axe.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { Sessions } from './sessions.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const PATH = '/api/v1/auth/sessions'

function session(id: number, fields: Partial<SignedInSession> = {}): SignedInSession {
  return {
    id,
    current: false,
    device: 'Safari · iOS',
    ip: '203.0.113.x',
    created_at: '2026-11-20T10:00:00Z',
    last_seen_at: '2026-11-23T09:00:00Z',
    expires_at: '2026-12-04T10:00:00Z',
    ...fields,
  }
}

const HERE = session(1, { current: true, device: 'Firefox · Linux', last_seen_at: '2026-11-24T10:00:00Z' })

function answer(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), { status })
}

/** A fake API holding `sessions`; ending any answers `endStatus`, which ends it unless it is a 500. */
function stubApi(sessions: SignedInSession[], endStatus = 204) {
  let held = sessions
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === PATH) {
      return Promise.resolve(answer(200, held))
    }
    const one = /^\/api\/v1\/auth\/sessions\/(\d+)$/.exec(path)
    const ends = (one !== null && init?.method === 'DELETE') || (path === `${PATH}/end-others` && init?.method === 'POST')
    if (!ends) {
      return Promise.resolve(answer(405, { detail: 'Method Not Allowed' }))
    }
    if (endStatus !== 500) {
      held = held.filter((item) => item.current || (one !== null && String(item.id) !== one[1]))
    }
    return Promise.resolve(answer(endStatus, { detail: endStatus === 404 ? 'No such session' : 'Server error' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function changes(fetchMock: ReturnType<typeof stubApi>): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method !== 'GET')
    .map(([path, init]) => `${init?.method ?? ''} ${path}`)
}

async function rows(): Promise<HTMLElement[]> {
  return within(await screen.findByRole('list', { name: 'Sessions' })).getAllByRole('listitem')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Sessions', () => {
  it('lists this device first, which has no sign-out of its own here', async () => {
    stubApi([HERE, session(2), session(3, { device: null, ip: null })])
    renderSignedIn(<Sessions reloadKey={0} />, ALICE)
    const [here, phone, unknown] = await rows()
    if (here === undefined || phone === undefined || unknown === undefined) {
      throw new Error('three sessions expected')
    }
    expect(within(here).getByText('Firefox · Linux')).toBeInTheDocument()
    expect(within(here).getByText('This device')).toBeInTheDocument()
    expect(within(here).queryByRole('button')).not.toBeInTheDocument()
    expect(within(phone).getByText(/^Last seen .* · signed in .* · IP 203\.0\.113\.x$/)).toBeInTheDocument()
    expect(within(phone).queryByText('This device')).not.toBeInTheDocument()
    expect(within(unknown).getByText('Unknown browser')).toBeInTheDocument()
    expect(within(unknown).queryByText(/IP/)).not.toBeInTheDocument()
    await expectNoAxeViolations()
  })

  it('signs one out after a confirm', async () => {
    const fetchMock = stubApi([HERE, session(2)])
    renderSignedIn(<Sessions reloadKey={0} />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: /^Sign out Safari · iOS, last seen/ }))
    expect(changes(fetchMock)).toEqual([])
    expect(screen.getByText('Sign out Safari · iOS?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    await vi.waitFor(() => {
      expect(screen.getAllByRole('listitem')).toHaveLength(1)
    })
    expect(changes(fetchMock)).toEqual([`DELETE ${PATH}/2`])
    expect(screen.getByRole('heading', { name: 'Sessions' })).toHaveFocus()
    // Nothing else left to sign out.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps it when the confirm is cancelled', async () => {
    const fetchMock = stubApi([HERE, session(2)])
    renderSignedIn(<Sessions reloadKey={0} />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: /^Sign out Safari/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: /^Sign out Safari/ })).toHaveFocus()
    })
    expect(changes(fetchMock)).toEqual([])
  })

  it('signs every other one out after a confirm', async () => {
    const fetchMock = stubApi([HERE, session(2), session(3)])
    renderSignedIn(<Sessions reloadKey={0} />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out all other sessions' }))
    expect(screen.getByText('Sign out every session but this one?')).toBeInTheDocument()
    expect(changes(fetchMock)).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    await vi.waitFor(() => {
      expect(screen.getAllByRole('listitem')).toHaveLength(1)
    })
    expect(changes(fetchMock)).toEqual([`POST ${PATH}/end-others`])
    expect(screen.getByText('This device')).toBeInTheDocument()
  })

  it('takes one already gone as signed out', async () => {
    stubApi([HERE, session(2)], 404)
    renderSignedIn(<Sessions reloadKey={0} />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: /^Sign out Safari/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    await vi.waitFor(() => {
      expect(screen.getAllByRole('listitem')).toHaveLength(1)
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says when signing out fails', async () => {
    stubApi([HERE, session(2)], 500)
    renderSignedIn(<Sessions reloadKey={0} />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: /^Sign out Safari/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Server error')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('offers to try again when they do not load', async () => {
    const fetchMock = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')))
    vi.stubGlobal('fetch', fetchMock)
    renderSignedIn(<Sessions reloadKey={0} />, ALICE)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load your sessions.')
    stubApi([HERE])
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await rows()).toHaveLength(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
