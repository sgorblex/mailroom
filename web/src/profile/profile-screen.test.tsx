import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { ProfileScreen } from './profile-screen.tsx'

const ALICE = { username: 'alice', is_admin: false }
const CURRENT = 'correct horse battery staple'
const NEXT = 'a brand new passphrase'

const PASSWORD_PATH = '/api/v1/auth/password'
const LIMITS_PATH = '/api/v1/me/limits'

/**
 * Anything but the password change: the default limits, no connected apps (the MCP server off),
 * and nothing else found (the linked sign-ins section's requests).
 */
function otherSections(path: string): Promise<Response> {
  if (path === LIMITS_PATH) {
    const limits = { send_per_hour: 20, max_recipients: 10, body_chars: 6000, attachment_max_mb: 10, move_max_mb: 25 }
    return Promise.resolve(new Response(JSON.stringify(limits), { status: 200 }))
  }
  if (path === '/api/v1/oauth/grants') {
    return Promise.resolve(new Response('{"auth":"off","grants":[]}', { status: 200 }))
  }
  return Promise.resolve(new Response('{"detail":"Not Found"}', { status: 404 }))
}

function stubApi(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path !== PASSWORD_PATH) {
      return otherSections(path)
    }
    const { current_password } = JSON.parse(init?.body as string) as { current_password: string }
    if (current_password !== CURRENT) {
      return Promise.resolve(
        new Response('{"detail":{"code":"wrong_password","message":"Wrong password"}}', {
          status: 400,
        }),
      )
    }
    return Promise.resolve(new Response(null, { status: 204 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function fill(current: string, next: string, repeated: string): Promise<void> {
  for (const [label, value] of [
    ['Current password', current],
    ['New password', next],
    ['New password again', repeated],
  ] as const) {
    const field = screen.getByLabelText(label)
    await userEvent.clear(field)
    await userEvent.type(field, value)
  }
  await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ProfileScreen', () => {
  it('asks to wait after too many wrong passwords', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path === PASSWORD_PATH
          ? Promise.resolve(new Response('{"detail":"Too many failed attempts"}', { status: 429 }))
          : otherSections(path),
      ),
    )
    renderSignedIn(<ProfileScreen />, ALICE)
    await fill(CURRENT, NEXT, NEXT)
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many failed attempts.')
  })

  it('refuses new passwords that differ without asking the API', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE)
    await fill(CURRENT, NEXT, `${NEXT}!`)
    expect(screen.getByRole('alert')).toHaveTextContent('The new passwords differ.')
    expect(fetchMock).not.toHaveBeenCalledWith(PASSWORD_PATH, expect.anything())
  })

  it('says when the current password is wrong, then changes it', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE)
    await fill('not my password', NEXT, NEXT)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your current password is wrong.',
    )
    expect(screen.getByLabelText('Current password')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('New password again')).toHaveAttribute('aria-invalid', 'false')

    await fill(CURRENT, NEXT, NEXT)
    expect(await screen.findByRole('status')).toHaveTextContent('Password changed.')
    expect(screen.getByLabelText('Current password')).toHaveValue('')
    expect(fetchMock).toHaveBeenLastCalledWith(
      PASSWORD_PATH,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ current_password: CURRENT, new_password: NEXT }),
      }),
    )
  })

  it.each([
    { features: ['mcp' as const], asked: true },
    { features: ['mail' as const, 'filters' as const], asked: false },
  ])('asks for connected apps only with mcp ($features)', async ({ features, asked }) => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE, features)
    expect(await screen.findByRole('heading', { name: 'Change your password' })).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 0))
    const grants = fetchMock.mock.calls.some(([path]) => path === '/api/v1/oauth/grants')
    expect(grants).toBe(asked)
  })

  it.each([
    { features: ['mail' as const], shown: true },
    { features: ['mcp' as const], shown: true },
    { features: ['filters' as const], shown: false },
  ])('shows the mail limits only where the instance sends ($features)', async ({ features, shown }) => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE, features)
    expect(await screen.findByRole('heading', { name: 'Change your password' })).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('heading', { name: 'Mail limits' }) !== null).toBe(shown)
    expect(fetchMock.mock.calls.some(([path]) => path === LIMITS_PATH)).toBe(shown)
  })
})
