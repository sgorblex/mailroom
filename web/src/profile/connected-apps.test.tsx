import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ConnectedApp, ConnectedApps as Listing } from '@/api/oauth.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { ConnectedApps } from './connected-apps.tsx'

const ALICE = { username: 'alice', is_admin: false }

function app(id: number, name: string | null, lastUsed: string | null = null): ConnectedApp {
  return {
    id,
    client_name: name,
    client_host: name === 'Claude' ? 'claude.ai' : null,
    created_at: '2026-10-25T12:00:00Z',
    last_used_at: lastUsed,
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fake API holding `grants`; DELETE removes one, or fails to reach the API when `failRevoke`. */
function stubApi(auth: Listing['auth'], grants: ConnectedApp[], { failRevoke = false } = {}) {
  let held = [...grants]
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/v1/oauth/grants') {
      return Promise.resolve(json(200, { auth, grants: held }))
    }
    const revoked = /^\/api\/v1\/oauth\/grants\/(\d+)$/.exec(path)
    if (revoked !== null && init?.method === 'DELETE') {
      if (failRevoke) {
        return Promise.reject(new TypeError('Failed to fetch'))
      }
      held = held.filter((item) => String(item.id) !== revoked[1])
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    return Promise.resolve(json(404, { detail: 'Not Found' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function deleted(fetchMock: ReturnType<typeof stubApi>): unknown[][] {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')
}

function listed(fetchMock: ReturnType<typeof stubApi>): unknown[][] {
  return fetchMock.mock.calls.filter(([path]) => path === '/api/v1/oauth/grants')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ConnectedApps', () => {
  it('lists the apps and disconnects one', async () => {
    const fetchMock = stubApi('builtin', [
      app(1, 'Claude', '2026-10-25T13:00:00Z'),
      app(2, null),
    ])
    renderSignedIn(<ConnectedApps />, ALICE)
    const list = await screen.findByRole('list', { name: 'Connected apps' })
    const [claude, unnamed, ...rest] = within(list).getAllByRole('listitem')
    expect(rest).toHaveLength(0)
    if (claude === undefined || unnamed === undefined) {
      throw new Error('two apps expected')
    }
    expect(within(claude).getByText('claude.ai')).toBeInTheDocument()
    expect(within(claude).getByText(/last used/)).toBeInTheDocument()
    expect(within(unnamed).getByText('An app with no name')).toBeInTheDocument()
    expect(within(unnamed).getByText(/never used/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Disconnect Claude' }))
    expect(within(claude).getByText(/Disconnect Claude\?/)).toBeInTheDocument()
    expect(deleted(fetchMock)).toHaveLength(0)
    const confirm = within(claude).getByRole('button', { name: 'Disconnect' })
    expect(confirm).toHaveFocus()
    await userEvent.click(confirm)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/oauth/grants/1',
      expect.objectContaining({ method: 'DELETE' }),
    )
    await vi.waitFor(() => {
      expect(screen.getAllByRole('listitem')).toHaveLength(1)
    })
    expect(screen.queryByRole('button', { name: 'Disconnect Claude' })).not.toBeInTheDocument()
  })

  it('keeps the app when the confirm is cancelled', async () => {
    const fetchMock = stubApi('builtin', [app(1, 'Claude')])
    renderSignedIn(<ConnectedApps />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Disconnect Claude' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByText(/Disconnect Claude\?/)).not.toBeInTheDocument()
    const trigger = screen.getByRole('button', { name: 'Disconnect Claude' })
    await vi.waitFor(() => {
      expect(trigger).toHaveFocus()
    })
    expect(deleted(fetchMock)).toHaveLength(0)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('says when a disconnect fails and shows what is left', async () => {
    const fetchMock = stubApi('builtin', [app(1, 'Claude')], { failRevoke: true })
    renderSignedIn(<ConnectedApps />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Disconnect Claude' }))
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not disconnect it.')
    expect(deleted(fetchMock)).toHaveLength(1)
    await vi.waitFor(() => {
      expect(listed(fetchMock)).toHaveLength(2)
    })
    expect(screen.getByRole('button', { name: 'Disconnect Claude' })).toBeInTheDocument()
  })

  it('says so while nothing is connected', async () => {
    stubApi('builtin', [])
    renderSignedIn(<ConnectedApps />, ALICE)
    expect(await screen.findByText(/None yet/)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('points to the identity provider with an external issuer', async () => {
    stubApi('external', [app(3, 'Claude')])
    renderSignedIn(<ConnectedApps />, ALICE)
    expect(await screen.findByText(/Your identity provider manages/)).toBeInTheDocument()
    // Left from the built-in server, still revocable.
    expect(screen.getByRole('button', { name: 'Disconnect Claude' })).toBeInTheDocument()
  })

  it('stays out of the way while the MCP server is off and nothing is connected', async () => {
    const fetchMock = stubApi('off', [])
    renderSignedIn(<ConnectedApps />, ALICE)
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  })
})
