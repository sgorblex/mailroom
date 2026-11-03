import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ConsentRequest } from '@/api/oauth.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import itCommon from '@/locales/it/common.json'
import { RootLayout } from '@/auth/root-layout.tsx'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { ConnectScreen } from './connect-screen.tsx'

const ALICE = { username: 'alice', is_admin: false }
const REQUEST_PATH = '/api/v1/oauth/requests/req-1'
const CALLBACK = 'https://claude.ai/api/mcp/auth_callback'

const CLAUDE: ConsentRequest = {
  client_name: 'Claude',
  client_host: 'claude.ai',
  redirect_host: 'claude.ai',
  scopes: ['mailroom'],
  expires_at: '2026-10-25T12:10:00Z',
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fake API holding one request, `req-1`, until it is decided. */
function stubApi(request: ConsentRequest | null = CLAUDE) {
  let held = request
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/v1/auth/oidc/config') {
      return Promise.resolve(json(200, { enabled: true, name: 'Authentik' }))
    }
    if (path === REQUEST_PATH && held !== null) {
      if (init?.method === 'POST') {
        const { decision } = JSON.parse(init.body as string) as { decision: string }
        held = null
        const query = decision === 'approve' ? 'code=c0de&state=s' : 'error=access_denied&state=s'
        return Promise.resolve(json(200, { redirect_to: `${CALLBACK}?${query}` }))
      }
      return Promise.resolve(json(200, held))
    }
    return Promise.resolve(json(404, { detail: 'No such authorization request' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

describe('ConnectScreen', () => {
  it('shows who asks and where approving goes back to, then approves', async () => {
    const fetchMock = stubApi()
    const leave = vi.fn()
    renderSignedIn(<ConnectScreen requestId="req-1" leave={leave} />, ALICE)
    expect(
      await screen.findByRole('heading', { name: 'Claude wants to use Mailroom as alice' }),
    ).toBeInTheDocument()
    expect(screen.getByText('The app is described by claude.ai.')).toBeInTheDocument()
    expect(screen.getByText('claude.ai', { selector: 'strong' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(fetchMock).toHaveBeenCalledWith(
      REQUEST_PATH,
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ decision: 'approve' }) }),
    )
    expect(leave).toHaveBeenCalledWith(`${CALLBACK}?code=c0de&state=s`)
    expect(await screen.findByRole('status')).toHaveTextContent('Taking you back to claude.ai')
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('says what approving gives, reading mail and attachments included', async () => {
    stubApi()
    renderSignedIn(<ConnectScreen requestId="req-1" leave={vi.fn()} />, ALICE)
    expect(
      await screen.findByText(/your mail accounts, their folders and your filters/),
    ).toHaveTextContent('reading your messages and their attachments')
    // Italian says as much.
    expect(itCommon.connect.access).toMatch(/messaggi.*allegati/)
  })

  it('denies, going back with the refusal', async () => {
    stubApi({ ...CLAUDE, client_name: null, client_host: null, redirect_host: '127.0.0.1:6274' })
    const leave = vi.fn()
    renderSignedIn(<ConnectScreen requestId="req-1" leave={leave} />, ALICE)
    expect(
      await screen.findByRole('heading', { name: 'An app with no name wants to use Mailroom as alice' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/registered itself/)).toBeInTheDocument()
    expect(screen.getByText('127.0.0.1:6274')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(leave).toHaveBeenCalledWith(`${CALLBACK}?error=access_denied&state=s`)
  })

  it.each([
    ['an unknown or expired request', 'req-2'],
    ['no request at all', null],
  ])('says so for %s', async (_case, requestId) => {
    stubApi()
    renderSignedIn(<ConnectScreen requestId={requestId} leave={vi.fn()} />, ALICE)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This request is unknown, has expired or was already answered.',
    )
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('says so when the request went away before the answer', async () => {
    const fetchMock = stubApi()
    const leave = vi.fn()
    renderSignedIn(<ConnectScreen requestId="req-1" leave={leave} />, ALICE)
    await screen.findByRole('button', { name: 'Approve' })
    fetchMock.mockImplementation(() =>
      Promise.resolve(json(404, { detail: 'No such authorization request' })),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('was already answered')
    expect(leave).not.toHaveBeenCalled()
    // Nothing left to decide.
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument()
  })

  it('keeps the buttons when the answer did not reach the API', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<ConnectScreen requestId="req-1" leave={vi.fn()} />, ALICE)
    await screen.findByRole('button', { name: 'Deny' })
    fetchMock.mockImplementation(() => Promise.reject(new TypeError('Failed to fetch')))
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeEnabled()
  })

  it('signed out, offers the password and the provider, coming back to the request', async () => {
    window.history.replaceState(null, '', '/connect?request=req-1')
    stubApi()
    const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
    render(
      <AuthContext
        value={{ status: 'signed-out', signIn: reject, signOut: reject, recheck: vi.fn() }}
      >
        <RootLayout />
      </AuthContext>,
    )
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    const button = await screen.findByRole('link', { name: 'Sign in with Authentik' })
    const target = new URL(button.getAttribute('href') ?? '', 'https://mailroom.test')
    expect(target.searchParams.get('intent')).toBe('login')
    expect(target.searchParams.get('return_to')).toBe('/connect?request=req-1')
  })
})
