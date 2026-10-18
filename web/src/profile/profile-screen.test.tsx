import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { ProfileScreen } from './profile-screen.tsx'

const ALICE = { username: 'alice', is_admin: false }
const CURRENT = 'correct horse battery staple'
const NEXT = 'a brand new passphrase'

function stubApi(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
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
      vi.fn(() =>
        Promise.resolve(new Response('{"detail":"Too many failed attempts"}', { status: 429 })),
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
    expect(fetchMock).not.toHaveBeenCalled()
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
      '/api/v1/auth/password',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ current_password: CURRENT, new_password: NEXT }),
      }),
    )
  })
})
