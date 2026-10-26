import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Identity } from '@/api/sso.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { LinkedSignIn } from './linked-sign-in.tsx'

const ALICE = { username: 'alice', is_admin: false }

function identity(id: number, name: string | null, issuer = 'https://id.example.test'): Identity {
  return {
    id,
    issuer,
    subject: `sub-${String(id)}`,
    name,
    created_at: '2026-10-25T12:00:00Z',
    last_used_at: null,
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fake API holding `identities`; DELETE removes one. */
function stubApi(sso: { enabled: boolean; name: string | null }, identities: Identity[]) {
  let held = [...identities]
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/v1/auth/oidc/config') {
      return Promise.resolve(json(200, sso))
    }
    if (path === '/api/v1/me/identities') {
      return Promise.resolve(json(200, held))
    }
    const removed = /^\/api\/v1\/me\/identities\/(\d+)$/.exec(path)
    if (removed !== null && init?.method === 'DELETE') {
      held = held.filter((item) => String(item.id) !== removed[1])
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    return Promise.resolve(json(404, { detail: 'Not Found' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The DELETE calls `fetchMock` saw. */
function deleted(fetchMock: ReturnType<typeof stubApi>): unknown[][] {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

describe('LinkedSignIn', () => {
  it('offers to link the provider while nothing is linked, back to this page', async () => {
    window.history.replaceState(null, '', '/profile?section=sign-in')
    stubApi({ enabled: true, name: 'Authentik' }, [])
    renderSignedIn(<LinkedSignIn />, ALICE)
    expect(await screen.findByText(/None yet/)).toBeInTheDocument()
    const link = await screen.findByRole('link', { name: 'Link Authentik' })
    const target = new URL(link.getAttribute('href') ?? '', 'https://mailroom.test')
    expect(target.pathname).toBe('/api/v1/auth/oidc/start')
    expect(target.searchParams.get('intent')).toBe('link')
    expect(target.searchParams.get('return_to')).toBe('/profile?section=sign-in')
  })

  it('lists linked sign-ins and unlinks one', async () => {
    const fetchMock = stubApi({ enabled: true, name: 'Authentik' }, [
      identity(1, 'Authentik'),
      identity(2, null, 'https://old.example.test'),
    ])
    renderSignedIn(<LinkedSignIn />, ALICE)
    const list = await screen.findByRole('list', { name: 'Linked sign-ins' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(within(list).getByText('https://old.example.test')).toBeInTheDocument()
    // Already linked to this provider: nothing more to link.
    expect(screen.queryByRole('link', { name: 'Link Authentik' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Unlink Authentik' }))
    expect(screen.getByText(/Unlink Authentik\?/)).toBeInTheDocument()
    expect(deleted(fetchMock)).toHaveLength(0)
    const confirm = screen.getByRole('button', { name: 'Unlink' })
    expect(confirm).toHaveFocus()
    await userEvent.click(confirm)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/me/identities/1',
      expect.objectContaining({ method: 'DELETE' }),
    )
    expect(await screen.findByRole('link', { name: 'Link Authentik' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('keeps the sign-in when the confirm is cancelled', async () => {
    const fetchMock = stubApi({ enabled: true, name: 'Authentik' }, [identity(1, 'Authentik')])
    renderSignedIn(<LinkedSignIn />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Unlink Authentik' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByText(/Unlink Authentik\?/)).not.toBeInTheDocument()
    const trigger = screen.getByRole('button', { name: 'Unlink Authentik' })
    await vi.waitFor(() => {
      expect(trigger).toHaveFocus()
    })
    expect(deleted(fetchMock)).toHaveLength(0)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('says why a link came back refused', async () => {
    window.history.replaceState(null, '', '/profile?sso_error=identity_taken')
    stubApi({ enabled: true, name: 'Authentik' }, [])
    renderSignedIn(<LinkedSignIn />, ALICE)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That sign-in is already linked to another user.',
    )
  })

  it('stays out of the way while SSO is off and nothing is linked', async () => {
    const fetchMock = stubApi({ enabled: false, name: null }, [])
    renderSignedIn(<LinkedSignIn />, ALICE)
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  })
})
