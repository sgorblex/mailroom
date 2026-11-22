import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE } from '@/api/auth.ts'

import { useAppearance } from './auth-context.ts'
import { AuthProvider } from './auth-provider.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

function Look(): ReactElement {
  const { appearance, save } = useAppearance()
  return (
    <>
      <output>{JSON.stringify(appearance)}</output>
      <button type="button" onClick={() => void save({ theme: 'dark' }).catch(() => undefined)}>
        Dark
      </button>
    </>
  )
}

const shown = (): unknown => JSON.parse(screen.getByRole('status').textContent)

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useAppearance', () => {
  it('is the defaults outside an AuthProvider, and saves nothing', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<Look />)
    expect(shown()).toEqual(DEFAULT_APPEARANCE)
    await userEvent.click(screen.getByRole('button', { name: 'Dark' }))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("is the signed-in user's, the defaults from an API that has none, and follows a save", async () => {
    const fetchMock = vi.fn((path: string, init?: RequestInit) => {
      if (path === '/api/v1/auth/me') {
        return Promise.resolve(json(200, { user: ALICE, features: ['mail'] }))
      }
      const change = JSON.parse(init?.body as string) as Record<string, string>
      return Promise.resolve(json(200, { ...ALICE, appearance: { ...DEFAULT_APPEARANCE, accent: 'violet', ...change } }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <AuthProvider>
        <Look />
      </AuthProvider>,
    )
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me', expect.anything())
    })
    expect(shown()).toEqual(DEFAULT_APPEARANCE)

    await userEvent.click(screen.getByRole('button', { name: 'Dark' }))
    await waitFor(() => {
      expect(shown()).toEqual({ ...DEFAULT_APPEARANCE, theme: 'dark', accent: 'violet' })
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/auth/me/appearance',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ theme: 'dark' }) }),
    )
  })
})
